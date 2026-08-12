"""Versioned local state and all-or-nothing generated output writes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .discovery import CollectionResult
from .models import Candidate, CategoryMatch, Rankings, RunStats, Snapshot, SnapshotEntry
from .report import render_report


SCHEMA_VERSION = 1
STALE_CLASSIFICATION_CONFIG_SHA256 = "stale:carried-entry"


class StateError(ValueError):
    """Raised when a persisted state file cannot be read as schema version one."""


@dataclass(frozen=True)
class PreparedOutputs:
    """Complete replacement content prepared without modifying final output files."""

    files: Mapping[Path, str]
    report_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


def load_candidates(path: Path) -> dict[int, Candidate]:
    """Load the candidate index, treating an absent first-run file as empty state."""
    if not path.exists():
        return {}
    data = _load_json(path)
    _require_exact_keys(data, {"schema_version", "candidates"})
    _require_version(data)
    candidates = data["candidates"]
    if not isinstance(candidates, dict):
        raise StateError(f"{path}: candidates must be an object")
    loaded: dict[int, Candidate] = {}
    for repo_id, payload in candidates.items():
        try:
            parsed_id = int(repo_id)
        except (TypeError, ValueError) as error:
            raise StateError(f"{path}: candidate repository ID must be an integer") from error
        if str(parsed_id) != repo_id or not isinstance(payload, dict):
            raise StateError(f"{path}: invalid candidate entry")
        try:
            _validate_candidate(payload)
            candidate = Candidate.from_dict(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise StateError(f"{path}: invalid candidate entry") from error
        if candidate.repo_id != parsed_id:
            raise StateError(f"{path}: candidate key does not match repo_id")
        loaded[parsed_id] = candidate
    return loaded


def load_snapshot(path: Path) -> Snapshot:
    """Load the snapshot, treating an absent first-run file as an empty v1 snapshot."""
    if not path.exists():
        return Snapshot("", "", {})
    data = _load_json(path)
    _require_exact_keys(data, {"schema_version", "captured_at", "classification_config_sha256", "repositories"})
    _require_version(data)
    if (
        not isinstance(data["captured_at"], str)
        or not isinstance(data["classification_config_sha256"], str)
        or not isinstance(data["repositories"], dict)
    ):
        raise StateError(f"{path}: invalid snapshot state")
    try:
        repositories: dict[int, SnapshotEntry] = {}
        for repo_id, entry in data["repositories"].items():
            if (
                not isinstance(repo_id, str)
                or not repo_id.isascii()
                or not repo_id.isdecimal()
                or str(int(repo_id)) != repo_id
            ):
                raise StateError(f"{path}: snapshot repository ID must be a canonical integer")
            if not isinstance(entry, dict):
                raise StateError(f"{path}: invalid snapshot entry")
            _require_exact_keys(entry, {"stars", "updated_at", "skill_paths", "content_sha256", "category_matches", "checked_at"})
            _validate_snapshot_entry(entry)
            for match in entry["category_matches"]:
                _validate_category_match(match)
            repositories[int(repo_id)] = SnapshotEntry.from_dict(entry)
        return Snapshot(data["captured_at"], data["classification_config_sha256"], repositories)
    except StateError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise StateError(f"{path}: invalid snapshot entry") from error


def prepare_outputs(
    root: Path,
    generated_at: datetime,
    classification_config_sha256: str,
    previous_snapshot: Snapshot,
    collection: CollectionResult,
    matches: Mapping[int, Mapping[str, CategoryMatch]],
    rankings: Rankings,
) -> PreparedOutputs:
    """Build every generated file in memory, ready for one atomic publication step."""
    generated_utc = _utc_iso(generated_at)
    repositories = {
        repo_id: entry
        for repo_id, entry in previous_snapshot.repositories.items()
        if collection.candidates.get(repo_id) is not None and collection.candidates[repo_id].active
    }
    collected_repo_ids = {record.repo_id for record in collection.records}
    carried_prior_config_entry = (
        previous_snapshot.classification_config_sha256 != classification_config_sha256
        and any(repo_id not in collected_repo_ids for repo_id in repositories)
    )
    for record in collection.records:
        repositories[record.repo_id] = SnapshotEntry(
            stars=record.stars,
            updated_at=record.updated_at,
            skill_paths=record.skill_paths,
            content_sha256=record.content_sha256,
            category_matches=_ordered_matches(matches.get(record.repo_id, {})),
            checked_at=generated_utc,
        )

    snapshot = Snapshot(
        generated_utc,
        STALE_CLASSIFICATION_CONFIG_SHA256 if carried_prior_config_entry else classification_config_sha256,
        repositories,
    )
    active_count = sum(candidate.active for candidate in collection.candidates.values())
    stats = RunStats(
        observed_from=previous_snapshot.captured_at or generated_utc,
        observed_to=generated_utc,
        discovered_count=len(collection.candidates),
        active_count=active_count,
        collected_count=len(collection.records),
        skipped_count=max(active_count - len(collection.records), 0),
        warnings=collection.warnings,
    )
    report = render_report(rankings, stats, generated_at)
    report_date = generated_at.astimezone(_beijing_timezone()).date().isoformat()
    report_path = root / "reports" / f"{report_date}.md"
    files = {
        root / "data" / "candidates.json": _json_text({
            "schema_version": SCHEMA_VERSION,
            "candidates": {str(repo_id): candidate.to_dict() for repo_id, candidate in sorted(collection.candidates.items())},
        }),
        root / "data" / "snapshot.json": _json_text({"schema_version": SCHEMA_VERSION, **snapshot.to_dict()}),
        report_path: report,
        root / "LATEST.md": report,
    }
    return PreparedOutputs(files, report_path)


def commit_outputs(outputs: PreparedOutputs) -> None:
    """Replace all prepared files, restoring every prior final file if a replacement fails."""
    staged: list[tuple[Path, Path]] = []
    originals: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for final_path, content in outputs.files.items():
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = final_path.with_name(f".{final_path.name}.tmp")
            temp_path.write_text(content, encoding="utf-8", newline="\n")
            staged.append((temp_path, final_path))
        for temp_path, final_path in staged:
            originals[final_path] = final_path.read_bytes() if final_path.exists() else None
            temp_path.replace(final_path)
            replaced.append(final_path)
    except Exception:
        for final_path in reversed(replaced):
            original = originals[final_path]
            if original is None:
                final_path.unlink(missing_ok=True)
            else:
                rollback = final_path.with_name(f".{final_path.name}.rollback")
                rollback.write_bytes(original)
                rollback.replace(final_path)
        raise
    finally:
        for temp_path, _ in staged:
            temp_path.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateError(f"{path}: invalid JSON state") from error
    if not isinstance(data, dict):
        raise StateError(f"{path}: state must be a JSON object")
    return data


def _require_exact_keys(data: Mapping[str, Any], expected: set[str]) -> None:
    missing = expected - set(data)
    unexpected = set(data) - expected
    if missing:
        raise StateError(f"state is missing required fields: {', '.join(sorted(missing))}")
    if unexpected:
        raise StateError(f"state has unexpected fields: {', '.join(sorted(unexpected))}")


def _require_version(data: Mapping[str, Any]) -> None:
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise StateError(f"unsupported schema_version: {data.get('schema_version')!r}")


def _validate_candidate(payload: Mapping[str, Any]) -> None:
    _require_exact_keys(
        payload,
        {"repo_id", "full_name", "url", "skill_paths", "discovered_at", "last_seen_at", "last_checked_at", "active"},
    )
    if (
        isinstance(payload["repo_id"], bool)
        or not isinstance(payload["repo_id"], int)
        or not all(isinstance(payload[field], str) for field in ("full_name", "url", "discovered_at", "last_seen_at", "last_checked_at"))
        or not isinstance(payload["active"], bool)
        or not isinstance(payload["skill_paths"], list)
        or not all(isinstance(path, str) for path in payload["skill_paths"])
    ):
        raise StateError("invalid candidate entry")


def _validate_snapshot_entry(entry: Mapping[str, Any]) -> None:
    if (
        isinstance(entry["stars"], bool)
        or not isinstance(entry["stars"], int)
        or not all(isinstance(entry[field], str) for field in ("updated_at", "content_sha256", "checked_at"))
        or not isinstance(entry["skill_paths"], list)
        or not all(isinstance(path, str) for path in entry["skill_paths"])
        or not isinstance(entry["category_matches"], list)
    ):
        raise StateError("invalid snapshot entry")


def _validate_category_match(match: Any) -> None:
    if not isinstance(match, dict):
        raise StateError("invalid category match")
    _require_exact_keys(match, {"category", "score", "reasons"})
    if (
        not isinstance(match["category"], str)
        or isinstance(match["score"], bool)
        or not isinstance(match["score"], int)
        or not isinstance(match["reasons"], list)
        or not all(isinstance(reason, str) for reason in match["reasons"])
    ):
        raise StateError("invalid category match")


def _ordered_matches(value: Mapping[str, CategoryMatch] | tuple[CategoryMatch, ...]) -> tuple[CategoryMatch, ...]:
    if isinstance(value, Mapping):
        return tuple(match for _, match in sorted(value.items()))
    return tuple(sorted(value, key=lambda match: match.category))


def _json_text(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("generated_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _beijing_timezone():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Shanghai")
