"""End-to-end transactional orchestration tests."""

from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skill_radar.github import GitHubAuthError, GitHubError
from skill_radar.models import Rankings, RepositoryMetadata, SearchHit


BEIJING_NOW = datetime(2026, 8, 9, 16, 30, tzinfo=timezone.utc)


class FakeGitHubClient:
    """Offline GitHub boundary fake for pipeline behaviour."""

    def __init__(self) -> None:
        self.metadata = RepositoryMetadata(
            repo_id=101,
            full_name="owner/skill-repo",
            url="https://github.com/owner/skill-repo",
            description="A Photoshop design skill",
            topics=("skills", "design"),
            stars=42,
            updated_at="2026-08-08T00:00:00Z",
            default_branch="main",
        )
        self.fail_discovery: Exception | None = None
        self.fail_collection: Exception | None = None

    def search_code(self, query: str, max_pages: int = 10) -> list[SearchHit]:
        if self.fail_discovery is not None:
            raise self.fail_discovery
        if query.startswith("repo:"):
            return []
        return [SearchHit(101, "owner/skill-repo", "https://github.com/owner/skill-repo", "SKILL.md")]

    def get_repository(self, full_name: str) -> RepositoryMetadata:
        if self.fail_collection is not None:
            raise self.fail_collection
        return self.metadata

    def get_text_file(self, full_name: str, path: str, ref: str) -> tuple[str, str]:
        return ("Photoshop design workflow", "fixture-sha")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "radar"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    source_root = Path(__file__).parents[1]
    for relative in ("config/categories.yml", "config/search_queries.yml", "data/candidates.json", "data/snapshot.json", "LATEST.md"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / relative).read_bytes())
    return root


@pytest.fixture
def fake_client() -> FakeGitHubClient:
    return FakeGitHubClient()


def _output_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        relative: (root / relative).read_bytes() if (root / relative).exists() else None
        for relative in ("data/candidates.json", "data/snapshot.json", "LATEST.md", "reports/2026-08-10.md")
    }


def test_pipeline_writes_all_outputs_after_success(project_root: Path, fake_client: FakeGitHubClient):
    """Catches publishing an incomplete run instead of the complete report/state set."""
    from skill_radar.pipeline import run_pipeline

    candidate_path = project_root / "data/candidates.json"
    seeded_candidates = candidate_path.read_bytes()
    result = run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)

    assert result.report_path == project_root / "reports/2026-08-10.md"
    assert result.report_path.read_text(encoding="utf-8") == (project_root / "LATEST.md").read_text(encoding="utf-8")
    assert json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))["captured_at"] == "2026-08-09T16:30:00Z"
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate_path.read_bytes() != seeded_candidates
    assert candidates == {
        "schema_version": 1,
        "candidates": {
            "101": {
                "repo_id": 101,
                "full_name": "owner/skill-repo",
                "url": "https://github.com/owner/skill-repo",
                "skill_paths": ["SKILL.md"],
                "discovered_at": "2026-08-09T16:30:00+00:00",
                "last_seen_at": "2026-08-09T16:30:00+00:00",
                "last_checked_at": "2026-08-09T16:30:00+00:00",
                "active": True,
            }
        },
    }
    assert result.collected_count == 1


def test_fatal_discovery_failure_preserves_previous_outputs(project_root: Path, fake_client: FakeGitHubClient):
    """Catches an authentication error after any generated file has been published."""
    from skill_radar.pipeline import run_pipeline

    fake_client.fail_discovery = GitHubAuthError("rejected")
    before = _output_bytes(project_root)

    with pytest.raises(GitHubAuthError):
        run_pipeline(project_root, "bad-token", now=BEIJING_NOW, client=fake_client)

    assert _output_bytes(project_root) == before


def test_pipeline_preserves_old_snapshot_entry_for_temporary_collection_failure(project_root: Path, fake_client: FakeGitHubClient):
    """Catches a transient repository error dropping the previous star baseline."""
    from skill_radar.pipeline import run_pipeline

    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)
    before = json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))["repositories"]["101"]
    fake_client.fail_collection = GitHubError("temporary")

    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)

    after = json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))["repositories"]["101"]
    assert after == before


def test_changed_category_config_disables_cached_content_reuse(project_root: Path, fake_client: FakeGitHubClient):
    """Catches reusing cached classification after its rules have changed."""
    from skill_radar.pipeline import run_pipeline

    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)
    categories = project_root / "config/categories.yml"
    categories.write_text(categories.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    fake_client.get_text_file = lambda full_name, path, ref: ("Illustrator workflow", "changed-sha")

    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)

    entry = json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))["repositories"]["101"]
    assert entry["content_sha256"] == sha256("<!-- path: SKILL.md -->\nIllustrator workflow".encode("utf-8")).hexdigest()


def test_failed_config_transition_forces_fresh_classification_on_recovery(project_root: Path, fake_client: FakeGitHubClient):
    """Catches an A-classified carry-forward entry becoming trusted as config B state."""
    import yaml

    from skill_radar.pipeline import run_pipeline

    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)
    categories = project_root / "config/categories.yml"
    rules = yaml.safe_load(categories.read_text(encoding="utf-8"))
    rules["categories"]["photoshop"]["threshold"] = 100
    categories.write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")
    fake_client.fail_collection = GitHubError("temporary")

    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)

    carried = json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))
    assert carried["classification_config_sha256"].startswith("stale:")
    fake_client.fail_collection = None
    run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)

    recovered = json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))["repositories"]["101"]
    assert all(match["category"] != "photoshop" for match in recovered["category_matches"])


def test_validation_rejects_duplicate_repository_links_in_rendered_section(tmp_path: Path):
    """Catches trusting ranking inputs when the prepared report itself has duplicate rows."""
    from skill_radar.discovery import CollectionResult
    from skill_radar.pipeline import validate_outputs
    from skill_radar.storage import PreparedOutputs

    headings = (
        "## 全站热门 Skill Top 10",
        "## 艺术设计相关 Skill Top 10",
        "## Photoshop 专项 Skill Top 5",
        "## Illustrator 专项 Skill Top 5",
    )
    report = "\n".join(
        (
            headings[0],
            "| 排名 | 仓库 |",
            "| --- | --- |",
            "| 1 | [one](https://github.com/owner/repo) |",
            "| 2 | [two](https://github.com/owner/repo) |",
            *headings[1:],
            "",
        )
    )
    files = {
        tmp_path / "reports" / "today.md": report,
        tmp_path / "LATEST.md": report,
        tmp_path / "data" / "candidates.json": '{"schema_version": 1, "candidates": {}}',
        tmp_path / "data" / "snapshot.json": '{"schema_version": 1, "repositories": {}}',
    }

    with pytest.raises(ValueError, match="duplicate"):
        validate_outputs(PreparedOutputs(files, tmp_path / "reports" / "today.md"), Rankings(True, (), (), (), ()), CollectionResult((), {}, ()))


def test_validation_ignores_github_urls_and_headings_inside_table_descriptions(tmp_path: Path):
    """Catches prose being mistaken for a repository cell or a structural heading."""
    from skill_radar.discovery import CollectionResult
    from skill_radar.pipeline import validate_outputs
    from skill_radar.report import _SECTIONS
    from skill_radar.storage import PreparedOutputs

    headings = [f"## {title}" for _, title, _ in _SECTIONS]
    report = "\n".join(
        (
            headings[0],
            "| 排名 | 仓库 | 简介 |",
            "| --- | --- | --- |",
            f"| 1 | [repo](https://github.com/owner/repo) | see https://github.com/owner/repo after {headings[1]} |",
            *headings[1:],
            "",
        )
    )
    files = {
        tmp_path / "reports" / "today.md": report,
        tmp_path / "LATEST.md": report,
        tmp_path / "data" / "candidates.json": '{"schema_version": 1, "candidates": {}}',
        tmp_path / "data" / "snapshot.json": '{"schema_version": 1, "repositories": {}}',
    }

    validate_outputs(PreparedOutputs(files, tmp_path / "reports" / "today.md"), Rankings(True, (), (), (), ()), CollectionResult((), {}, ()))
