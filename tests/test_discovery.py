from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from skill_radar.github import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFound,
    GitHubPublicOnlyError,
    GitHubRateLimitError,
)
from skill_radar.models import Candidate, RepositoryMetadata, SearchHit, Snapshot, SnapshotEntry


NOW = "2026-08-09T00:00:00Z"


class FakeGitHubClient:
    """A deterministic boundary fake; discovery code remains fully real."""

    def __init__(self) -> None:
        self.metadata = RepositoryMetadata(
            repo_id=101,
            full_name="owner/skill-repo",
            url="https://github.com/owner/skill-repo",
            description="A useful skill",
            topics=("skills",),
            stars=42,
            updated_at="2026-08-08T00:00:00Z",
            default_branch="main",
        )
        self.searches = {
            "filename:SKILL.md": [
                SearchHit(101, "owner/skill-repo", "https://github.com/owner/skill-repo", "SKILL.md"),
            ],
            "design filename:SKILL.md": [
                SearchHit(101, "owner/skill-repo", "https://github.com/owner/skill-repo", ".agents/SKILL.md"),
            ],
        }
        self.files = {"SKILL.md": ("Hello, skill!", "skill-sha")}
        self.fail_with: Exception | None = None
        self.file_failure: Exception | None = None
        self.text_file_calls: list[str] = []
        self.search_calls: list[str] = []

    def search_code(self, query: str, max_pages: int = 10) -> list[SearchHit]:
        self.search_calls.append(query)
        return list(self.searches.get(query, ()))

    def get_repository(self, full_name: str) -> RepositoryMetadata:
        if self.fail_with is not None:
            raise self.fail_with
        return self.metadata

    def get_text_file(self, full_name: str, path: str, ref: str) -> tuple[str, str]:
        self.text_file_calls.append(path)
        if self.fail_with is not None:
            raise self.fail_with
        if self.file_failure is not None:
            raise self.file_failure
        try:
            return self.files[path]
        except KeyError:
            raise GitHubNotFound("not found") from None

    def fail_repository_with(self, error: Exception) -> None:
        self.fail_with = error

    def fail_skill_download_with(self, error: Exception) -> None:
        self.file_failure = error

    def all_known_paths_missing_and_repo_search_empty(self) -> None:
        self.files = {}
        self.searches.clear()

    def move_skill(self, old_path: str, new_path: str) -> None:
        self.files[new_path] = self.files.pop(old_path)
        self.searches[f"repo:{self.metadata.full_name} filename:SKILL.md"] = [
            SearchHit(self.metadata.repo_id, self.metadata.full_name, self.metadata.url, new_path)
        ]


@pytest.fixture
def fake_client():
    return FakeGitHubClient()


@pytest.fixture
def candidate():
    return Candidate(
        repo_id=101,
        full_name="owner/skill-repo",
        url="https://github.com/owner/skill-repo",
        skill_paths=("SKILL.md",),
        discovered_at="2026-08-01T00:00:00Z",
        last_seen_at="2026-08-01T00:00:00Z",
        last_checked_at="2026-08-01T00:00:00Z",
        active=True,
    )


@pytest.fixture
def existing_candidates(candidate):
    return {candidate.repo_id: candidate}


@pytest.fixture
def empty_snapshot():
    return Snapshot(NOW, "config-sha", MappingProxyType({}))


@pytest.fixture
def cached_snapshot():
    return Snapshot(
        "2026-08-02T00:00:00Z",
        "config-sha",
        MappingProxyType(
            {
                101: SnapshotEntry(
                    stars=40,
                    updated_at="2026-08-08T00:00:00Z",
                    skill_paths=("SKILL.md",),
                    content_sha256="cached-digest",
                    category_matches=(),
                    checked_at="2026-08-02T00:00:00Z",
                )
            }
        ),
    )


def test_discovery_merges_queries_paths_and_existing(fake_client, existing_candidates):
    """Catches query results overwriting an existing candidate's known paths."""
    from skill_radar.discovery import discover_candidates

    result = discover_candidates(
        fake_client,
        ["filename:SKILL.md", "design filename:SKILL.md"],
        existing_candidates,
        NOW,
    )

    candidate = result[101]
    assert candidate.skill_paths == (".agents/SKILL.md", "SKILL.md")
    assert candidate.active is True
    assert candidate.last_seen_at == NOW
    assert candidate.discovered_at == "2026-08-01T00:00:00Z"


def test_temporary_failure_keeps_candidate_active(fake_client, candidate, empty_snapshot):
    """Catches treating a temporary repository error as proof that it disappeared."""
    from skill_radar.discovery import collect_repositories

    fake_client.fail_repository_with(GitHubError("temporary"))
    result = collect_repositories(fake_client, {candidate.repo_id: candidate}, empty_snapshot, False, NOW)

    assert result.candidates[candidate.repo_id] == candidate
    assert result.records == ()
    assert "temporary" in result.warnings[0]


def test_confirmed_missing_skill_marks_candidate_inactive(fake_client, candidate, empty_snapshot):
    """Catches retaining a candidate after both stored paths and repo search confirm absence."""
    from skill_radar.discovery import collect_repositories

    fake_client.all_known_paths_missing_and_repo_search_empty()
    result = collect_repositories(fake_client, {candidate.repo_id: candidate}, empty_snapshot, False, NOW)

    assert result.candidates[candidate.repo_id].active is False
    assert result.candidates[candidate.repo_id].last_checked_at == NOW
    assert result.records == ()


def test_collects_one_record_for_multiple_skill_files(fake_client, candidate, empty_snapshot):
    """Catches ranking each SKILL.md separately instead of once per repository."""
    from skill_radar.discovery import collect_repositories

    multi_path_candidate = replace(candidate, skill_paths=("b/SKILL.md", "a/SKILL.md"))
    fake_client.files = {
        "a/SKILL.md": ("A", "sha-a"),
        "b/SKILL.md": ("B", "sha-b"),
    }
    result = collect_repositories(fake_client, {101: multi_path_candidate}, empty_snapshot, False, NOW)

    assert len(result.records) == 1
    assert result.records[0].skill_paths == ("a/SKILL.md", "b/SKILL.md")
    assert result.records[0].skill_text == "<!-- path: a/SKILL.md -->\nA\n<!-- path: b/SKILL.md -->\nB"


def test_moved_skill_path_is_rediscovered(fake_client, candidate, empty_snapshot):
    """Catches a stale candidate path being discarded without its one allowed rediscovery."""
    from skill_radar.discovery import collect_repositories

    stale_candidate = replace(candidate, skill_paths=("old/SKILL.md",))
    fake_client.files = {"old/SKILL.md": ("moved skill", "sha")}
    fake_client.move_skill("old/SKILL.md", "new/SKILL.md")
    result = collect_repositories(fake_client, {101: stale_candidate}, empty_snapshot, False, NOW)

    assert result.records[0].skill_paths == ("new/SKILL.md",)
    assert result.candidates[101].skill_paths == ("new/SKILL.md",)
    assert fake_client.search_calls == ["repo:owner/skill-repo filename:SKILL.md"]


def test_reuses_classification_when_repository_is_unchanged(fake_client, candidate, cached_snapshot):
    """Catches downloading unchanged Skill content when a compatible snapshot already has it."""
    from skill_radar.discovery import collect_repositories

    result = collect_repositories(fake_client, {101: candidate}, cached_snapshot, True, NOW)

    assert result.records[0].content_reused is True
    assert result.records[0].skill_paths == ("SKILL.md",)
    assert result.records[0].content_sha256 == "cached-digest"
    assert result.records[0].skill_text == ""
    assert result.records[0].stars == 42
    assert fake_client.text_file_calls == []


def test_collection_migrates_recreated_repository_id_and_persisted_candidate_key(
    fake_client, candidate, tmp_path
):
    """Catches a recreated same-name repository retaining its deleted repository ID key."""
    from skill_radar.discovery import collect_repositories
    from skill_radar.ranking import build_rankings
    from skill_radar.storage import commit_outputs, load_candidates, prepare_outputs

    new_repo_id = 202
    fake_client.metadata = replace(fake_client.metadata, repo_id=new_repo_id)
    snapshot = Snapshot(
        NOW,
        "config-sha",
        MappingProxyType(
            {
                new_repo_id: SnapshotEntry(
                    stars=40,
                    updated_at=fake_client.metadata.updated_at,
                    skill_paths=("SKILL.md",),
                    content_sha256="recreated-digest",
                    category_matches=(),
                    checked_at="2026-08-02T00:00:00Z",
                )
            }
        ),
    )

    result = collect_repositories(fake_client, {candidate.repo_id: candidate}, snapshot, True, NOW)

    assert list(result.candidates) == [new_repo_id]
    assert result.candidates[new_repo_id].repo_id == new_repo_id
    assert result.records[0].repo_id == new_repo_id
    assert result.records[0].content_reused is True
    assert fake_client.text_file_calls == []

    rankings = build_rankings(result.records, snapshot.stars_by_repo, {})
    outputs = prepare_outputs(
        tmp_path,
        datetime(2026, 8, 9, tzinfo=timezone.utc),
        "config-sha",
        snapshot,
        result,
        {},
        rankings,
    )
    commit_outputs(outputs)
    assert load_candidates(tmp_path / "data" / "candidates.json") == result.candidates


def test_id_migration_does_not_collect_a_replacement_candidate_twice(
    fake_client, candidate, empty_snapshot
):
    """Catches old and newly discovered IDs producing duplicate records in one run."""
    from skill_radar.discovery import collect_repositories

    new_repo_id = 202
    fake_client.metadata = replace(fake_client.metadata, repo_id=new_repo_id)
    replacement = replace(
        candidate,
        repo_id=new_repo_id,
        skill_paths=("new/SKILL.md",),
        last_seen_at=NOW,
    )
    fake_client.files = {"new/SKILL.md": ("replacement skill", "replacement-sha")}

    result = collect_repositories(
        fake_client,
        {candidate.repo_id: candidate, new_repo_id: replacement},
        empty_snapshot,
        False,
        NOW,
    )

    assert list(result.candidates) == [new_repo_id]
    assert [record.repo_id for record in result.records] == [new_repo_id]
    assert result.records[0].skill_paths == ("new/SKILL.md",)
    assert result.candidates[new_repo_id].last_seen_at == NOW


def test_confirmed_missing_repository_marks_candidate_inactive(fake_client, candidate, empty_snapshot):
    """Catches a confirmed 404 repository being retained as an active candidate."""
    from skill_radar.discovery import collect_repositories

    fake_client.fail_repository_with(GitHubNotFound("repository not found"))
    result = collect_repositories(fake_client, {101: candidate}, empty_snapshot, False, NOW)

    assert result.candidates[101].active is False
    assert result.candidates[101].last_checked_at == NOW
    assert result.records == ()
    assert result.warnings == ()


def test_non_public_repository_metadata_is_inactivated_without_a_record_or_warning(
    fake_client, candidate, empty_snapshot
):
    """Catches a private repository escaping collection after metadata validation."""
    from skill_radar.discovery import collect_repositories

    fake_client.fail_repository_with(GitHubPublicOnlyError("GitHub repository is not public"))
    result = collect_repositories(fake_client, {101: candidate}, empty_snapshot, False, NOW)

    assert result.candidates[101].active is False
    assert result.records == ()
    assert result.warnings == ()


def test_content_collection_stops_at_the_total_byte_cap(fake_client, candidate, empty_snapshot):
    """Catches content retrieval exceeding the explicit per-repository byte budget."""
    from skill_radar.discovery import collect_repositories

    fake_client.files = {"SKILL.md": ("abcdefghijk", "sha")}
    result = collect_repositories(
        fake_client,
        {101: candidate},
        empty_snapshot,
        False,
        NOW,
        max_content_bytes=10,
    )

    assert result.records[0].skill_text == "<!-- path: SKILL.md -->\nabcdefghij"


def test_temporary_skill_download_failure_preserves_candidate_history(fake_client, candidate, empty_snapshot):
    """Catches a transient file read failure changing lifecycle state after metadata succeeded."""
    from skill_radar.discovery import collect_repositories

    fake_client.fail_skill_download_with(GitHubError("temporary file download"))
    result = collect_repositories(fake_client, {101: candidate}, empty_snapshot, False, NOW)

    assert result.candidates[101] == candidate
    assert result.records == ()
    assert result.warnings == ("owner/skill-repo: temporary file download",)


def test_collection_reads_at_most_five_sorted_skill_paths(fake_client, candidate, empty_snapshot):
    """Catches unbounded file calls or nondeterministic collection order for a repository."""
    from skill_radar.discovery import collect_repositories

    paths = ("z/SKILL.md", "a/SKILL.md", "e/SKILL.md", "b/SKILL.md", "d/SKILL.md", "c/SKILL.md")
    fake_client.files = {path: (path, f"sha-{path}") for path in paths}
    result = collect_repositories(
        fake_client,
        {101: replace(candidate, skill_paths=paths)},
        empty_snapshot,
        False,
        NOW,
    )

    assert fake_client.text_file_calls == ["a/SKILL.md", "b/SKILL.md", "c/SKILL.md", "d/SKILL.md", "e/SKILL.md"]
    assert result.records[0].skill_paths == ("a/SKILL.md", "b/SKILL.md", "c/SKILL.md", "d/SKILL.md", "e/SKILL.md")


def test_missing_paths_do_not_consume_the_five_successful_file_budget(fake_client, candidate, empty_snapshot):
    """Catches declaring a candidate absent when its sixth known path is the first live Skill file."""
    from skill_radar.discovery import collect_repositories

    paths = tuple(f"{number}/SKILL.md" for number in range(1, 7))
    live_path = "6/SKILL.md"
    fake_client.files = {live_path: ("live skill", "sha-live")}
    result = collect_repositories(
        fake_client,
        {101: replace(candidate, skill_paths=paths)},
        empty_snapshot,
        False,
        NOW,
    )

    assert result.records[0].skill_paths == (live_path,)
    assert result.candidates[101].active is True
    assert fake_client.search_calls == []
    assert fake_client.text_file_calls == list(paths)


def test_unsorted_cached_paths_fall_back_to_normalized_fresh_collection(fake_client, candidate):
    """Catches reusing a cache entry whose path order breaks deterministic record output."""
    from skill_radar.discovery import collect_repositories

    paths = ("a/SKILL.md", "b/SKILL.md")
    fake_client.files = {path: (path, f"sha-{path}") for path in paths}
    snapshot = Snapshot(
        NOW,
        "config-sha",
        MappingProxyType(
            {
                101: SnapshotEntry(
                    stars=40,
                    updated_at=fake_client.metadata.updated_at,
                    skill_paths=("b/SKILL.md", "a/SKILL.md"),
                    content_sha256="stale-digest",
                    category_matches=(),
                    checked_at="2026-08-02T00:00:00Z",
                )
            }
        ),
    )
    result = collect_repositories(
        fake_client,
        {101: replace(candidate, skill_paths=paths)},
        snapshot,
        True,
        NOW,
    )

    assert result.records[0].content_reused is False
    assert result.records[0].skill_paths == paths
    assert fake_client.text_file_calls == list(paths)


def test_over_limit_cached_paths_fall_back_to_current_collection_bound(fake_client, candidate):
    """Catches a cache entry bypassing the caller's maximum Skill-file contract."""
    from skill_radar.discovery import collect_repositories

    paths = tuple(f"{number}/SKILL.md" for number in range(1, 7))
    fake_client.files = {path: (path, f"sha-{path}") for path in paths}
    snapshot = Snapshot(
        NOW,
        "config-sha",
        MappingProxyType(
            {
                101: SnapshotEntry(
                    stars=40,
                    updated_at=fake_client.metadata.updated_at,
                    skill_paths=paths,
                    content_sha256="stale-digest",
                    category_matches=(),
                    checked_at="2026-08-02T00:00:00Z",
                )
            }
        ),
    )
    result = collect_repositories(
        fake_client,
        {101: replace(candidate, skill_paths=paths)},
        snapshot,
        True,
        NOW,
        max_skill_files=3,
    )

    assert result.records[0].content_reused is False
    assert result.records[0].skill_paths == paths[:3]
    assert fake_client.text_file_calls == list(paths[:3])


@pytest.mark.parametrize("error", [GitHubAuthError("bad token"), GitHubRateLimitError("retry later")])
def test_authentication_and_unrecoverable_rate_limits_are_fatal(fake_client, candidate, empty_snapshot, error):
    """Catches fatal GitHub boundary failures being silently converted into per-repo warnings."""
    from skill_radar.discovery import collect_repositories

    fake_client.fail_repository_with(error)

    with pytest.raises(type(error)):
        collect_repositories(fake_client, {101: candidate}, empty_snapshot, False, NOW)
