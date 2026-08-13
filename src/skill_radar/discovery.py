"""Candidate discovery and bounded collection of repository Skill files."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Mapping, Sequence

from .github import GitHubAuthError, GitHubError, GitHubNotFound, GitHubRateLimitError
from .models import Candidate, RepositoryRecord, Snapshot


@dataclass(frozen=True)
class CollectionResult:
    records: tuple[RepositoryRecord, ...]
    candidates: dict[int, Candidate]
    warnings: tuple[str, ...]


def discover_candidates(
    client: object,
    queries: Sequence[str],
    existing: Mapping[int, Candidate],
    now: str,
    max_pages: int = 10,
) -> dict[int, Candidate]:
    """Merge deterministic code-search hits into the persistent candidate index."""
    merged = dict(existing)
    for query in queries:
        for hit in client.search_code(query, max_pages=max_pages):
            current = merged.get(hit.repo_id)
            paths = set(current.skill_paths if current else ())
            paths.add(hit.path)
            merged[hit.repo_id] = Candidate(
                repo_id=hit.repo_id,
                full_name=hit.full_name,
                url=hit.repo_url,
                skill_paths=tuple(sorted(paths)),
                discovered_at=current.discovered_at if current else now,
                last_seen_at=now,
                last_checked_at=current.last_checked_at if current else "",
                active=True,
            )
    return merged


def collect_repositories(
    client: object,
    candidates: Mapping[int, Candidate],
    previous_snapshot: Snapshot,
    allow_cached_classification: bool,
    now: str,
    max_skill_files: int = 5,
    max_content_bytes: int = 524288,
) -> CollectionResult:
    """Collect one current record per active repository without deleting on transient errors."""
    if max_skill_files < 1 or max_content_bytes < 1:
        raise ValueError("collection bounds must be positive")

    updated_candidates = dict(candidates)
    records: list[RepositoryRecord] = []
    warnings: list[str] = []
    processed_repo_ids: set[int] = set()

    for repo_id in sorted(candidates):
        candidate = candidates[repo_id]
        if not candidate.active:
            continue
        current_repo_id = repo_id
        try:
            metadata = client.get_repository(candidate.full_name)
            current_repo_id = metadata.repo_id
            if current_repo_id in processed_repo_ids:
                if current_repo_id != repo_id:
                    updated_candidates.pop(repo_id, None)
                continue
            processed_repo_ids.add(current_repo_id)
            if current_repo_id != repo_id:
                current_candidate = updated_candidates.get(current_repo_id)
                updated_candidates.pop(repo_id, None)
                if current_candidate is None:
                    candidate = replace(candidate, repo_id=current_repo_id)
                else:
                    candidate = Candidate(
                        repo_id=current_repo_id,
                        full_name=metadata.full_name,
                        url=metadata.url,
                        skill_paths=tuple(
                            sorted(set(candidate.skill_paths) | set(current_candidate.skill_paths))
                        ),
                        discovered_at=min(candidate.discovered_at, current_candidate.discovered_at),
                        last_seen_at=max(candidate.last_seen_at, current_candidate.last_seen_at),
                        last_checked_at=max(candidate.last_checked_at, current_candidate.last_checked_at),
                        active=candidate.active or current_candidate.active,
                    )
                updated_candidates[current_repo_id] = candidate
            cached = previous_snapshot.repositories.get(current_repo_id)
            can_reuse = (
                allow_cached_classification
                and cached is not None
                and cached.updated_at == metadata.updated_at
                and bool(cached.skill_paths)
                and bool(cached.content_sha256)
                and _cached_paths_are_reusable(cached.skill_paths, max_skill_files)
            )
            if can_reuse:
                paths = cached.skill_paths
                skill_text = ""
                content_sha256 = cached.content_sha256
            else:
                paths, skill_text = _read_skill_files(
                    client,
                    candidate.full_name,
                    metadata.default_branch,
                    candidate.skill_paths,
                    max_skill_files,
                    max_content_bytes,
                )
                if not paths:
                    rediscovered = client.search_code(
                        f"repo:{candidate.full_name} filename:SKILL.md", max_pages=1
                    )
                    if not rediscovered:
                        updated_candidates[current_repo_id] = replace(
                            candidate, active=False, last_checked_at=now
                        )
                        continue
                    paths, skill_text = _read_skill_files(
                        client,
                        candidate.full_name,
                        metadata.default_branch,
                        tuple(sorted({hit.path for hit in rediscovered})),
                        max_skill_files,
                        max_content_bytes,
                    )
                    if not paths:
                        warnings.append(f"{candidate.full_name}: rediscovered Skill file could not be read")
                        continue
                content_sha256 = sha256(skill_text.encode("utf-8")).hexdigest()
        except (GitHubAuthError, GitHubRateLimitError):
            raise
        except GitHubNotFound:
            updated_candidates[current_repo_id] = replace(
                candidate, active=False, last_checked_at=now
            )
            continue
        except GitHubError as error:
            warnings.append(f"{candidate.full_name}: {error}")
            continue

        updated_candidates[current_repo_id] = Candidate(
            repo_id=metadata.repo_id,
            full_name=metadata.full_name,
            url=metadata.url,
            skill_paths=paths,
            discovered_at=candidate.discovered_at,
            last_seen_at=candidate.last_seen_at,
            last_checked_at=now,
            active=True,
        )
        records.append(
            RepositoryRecord(
                repo_id=metadata.repo_id,
                full_name=metadata.full_name,
                url=metadata.url,
                description=metadata.description,
                topics=metadata.topics,
                stars=metadata.stars,
                updated_at=metadata.updated_at,
                default_branch=metadata.default_branch,
                skill_paths=paths,
                skill_text=skill_text,
                content_sha256=content_sha256,
                content_reused=can_reuse,
            )
        )

    return CollectionResult(tuple(records), updated_candidates, tuple(warnings))


def _read_skill_files(
    client: object,
    full_name: str,
    default_branch: str,
    paths: Sequence[str],
    max_skill_files: int,
    max_content_bytes: int,
) -> tuple[tuple[str, ...], str]:
    collected_paths: list[str] = []
    sections: list[str] = []
    remaining = max_content_bytes
    for path in sorted(paths):
        if len(collected_paths) >= max_skill_files:
            break
        try:
            text, _ = client.get_text_file(full_name, path, default_branch)
        except GitHubNotFound:
            continue
        encoded = text.encode("utf-8")
        if len(encoded) > remaining:
            text = encoded[:remaining].decode("utf-8", errors="ignore")
            encoded = text.encode("utf-8")
        collected_paths.append(path)
        sections.append(f"<!-- path: {path} -->\n{text}")
        remaining -= len(encoded)
        if remaining == 0:
            break
    return tuple(collected_paths), "\n".join(sections)


def _cached_paths_are_reusable(paths: Sequence[str], max_skill_files: int) -> bool:
    """Ensure a cached path set already satisfies the current record contract."""
    return (
        len(paths) <= max_skill_files
        and tuple(sorted(paths)) == tuple(paths)
        and len(set(paths)) == len(paths)
    )
