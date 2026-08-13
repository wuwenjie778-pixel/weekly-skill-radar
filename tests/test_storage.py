"""Persistence contracts for versioned state and output transactions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from skill_radar.discovery import CollectionResult
from skill_radar.models import Candidate, CategoryMatch, RepositoryRecord, Snapshot, SnapshotEntry
from skill_radar.ranking import build_rankings
from skill_radar.storage import StateError, commit_outputs, load_candidates, load_snapshot, prepare_outputs


NOW = datetime(2026, 8, 9, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def prepared_inputs():
    retained = SnapshotEntry(
        stars=17,
        updated_at="2026-08-02T00:00:00Z",
        skill_paths=("SKILL.md",),
        content_sha256="retained-digest",
        category_matches=(),
        checked_at="2026-08-02T00:00:00Z",
    )
    previous_snapshot = Snapshot("2026-08-02T00:00:00Z", "old-config", {7: retained})
    record = RepositoryRecord(
        repo_id=8,
        full_name="owner/active",
        url="https://github.com/owner/active",
        description="A useful skill",
        topics=("skill",),
        stars=42,
        updated_at="2026-08-09T10:00:00Z",
        default_branch="main",
        skill_paths=("skills/SKILL.md",),
        skill_text="",
        content_sha256="current-digest",
        content_reused=False,
    )
    candidates = {
        7: Candidate(7, "owner/temporary", "https://github.com/owner/temporary", ("SKILL.md",), "2026-08-01T00:00:00Z", "2026-08-09T00:00:00Z", "2026-08-02T00:00:00Z", True),
        8: Candidate(8, "owner/active", "https://github.com/owner/active", ("skills/SKILL.md",), "2026-08-09T00:00:00Z", "2026-08-09T00:00:00Z", "2026-08-09T16:30:00Z", True),
    }
    collection = CollectionResult((record,), candidates, ("owner/temporary: timeout",))
    matches = {8: {"photoshop": CategoryMatch("photoshop", 9, ('简介命中“photoshop”',))}}
    return {
        "generated_at": NOW,
        "classification_config_sha256": "new-config",
        "previous_snapshot": previous_snapshot,
        "collection": collection,
        "matches": matches,
        "rankings": build_rankings(collection.records, previous_snapshot.stars_by_repo, matches),
        "temporarily_failed_repo_id": 7,
    }


def test_empty_bootstrap_files_load_as_version_one(tmp_path):
    """Catches treating an absent first-run state file as corrupt state."""
    assert load_candidates(tmp_path / "missing-candidates.json") == {}
    assert load_snapshot(tmp_path / "missing-snapshot.json").stars_by_repo == {}


@pytest.mark.parametrize(
    ("filename", "payload", "loader"),
    [
        ("candidates.json", {"schema_version": 99, "candidates": {}}, load_candidates),
        ("snapshot.json", {"schema_version": 99, "captured_at": "", "classification_config_sha256": "", "repositories": {}}, load_snapshot),
    ],
)
def test_rejects_unknown_schema_version(tmp_path, filename, payload, loader):
    """Catches silently accepting a state format this version cannot interpret."""
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StateError, match="schema_version"):
        loader(path)


def test_rejects_noncanonical_state_shape(tmp_path):
    """Catches accepting unknown fields that would make a version-one read ambiguous."""
    path = tmp_path / "candidates.json"
    path.write_text('{"schema_version": 1, "candidates": {}, "extra": true}', encoding="utf-8")
    with pytest.raises(StateError, match="unexpected"):
        load_candidates(path)


def test_rejects_unknown_nested_snapshot_fields(tmp_path):
    """Catches accepting a repository entry from a newer incompatible schema."""
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_at": "2026-08-09T00:00:00Z",
                "classification_config_sha256": "digest",
                "repositories": {
                    "1": {
                        "stars": 1,
                        "updated_at": "2026-08-09T00:00:00Z",
                        "skill_paths": [],
                        "content_sha256": "digest",
                        "category_matches": [],
                        "checked_at": "2026-08-09T00:00:00Z",
                        "future_field": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="unexpected"):
        load_snapshot(path)


def test_rejects_candidate_with_non_list_skill_paths(tmp_path):
    """Catches accepting malformed v1 state that changes a path string into characters."""
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidates": {
                    "1": {
                        "repo_id": 1,
                        "full_name": "owner/repo",
                        "url": "https://github.com/owner/repo",
                        "skill_paths": "SKILL.md",
                        "discovered_at": "2026-08-09T00:00:00Z",
                        "last_seen_at": "2026-08-09T00:00:00Z",
                        "last_checked_at": "2026-08-09T00:00:00Z",
                        "active": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StateError, match="invalid candidate"):
        load_candidates(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("stars", "42"), ("skill_paths", "SKILL.md"), ("updated_at", []), ("category_matches", [{"category": 1, "score": "9", "reasons": "x"}])],
)
def test_rejects_snapshot_entries_with_wrong_field_types(tmp_path, field, value):
    """Catches malformed state that would otherwise fail later in ranking or rendering."""
    entry = {
        "stars": 42,
        "updated_at": "2026-08-09T00:00:00Z",
        "skill_paths": ["SKILL.md"],
        "content_sha256": "digest",
        "category_matches": [],
        "checked_at": "2026-08-09T00:00:00Z",
    }
    entry[field] = value
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"schema_version": 1, "captured_at": "", "classification_config_sha256": "", "repositories": {"1": entry}}), encoding="utf-8")
    with pytest.raises(StateError, match="invalid"):
        load_snapshot(path)


def test_rejects_boolean_schema_version(tmp_path):
    """Catches Python treating JSON true as integer schema version one."""
    path = tmp_path / "snapshot.json"
    path.write_text('{"schema_version": true, "captured_at": "", "classification_config_sha256": "", "repositories": {}}', encoding="utf-8")
    with pytest.raises(StateError, match="schema_version"):
        load_snapshot(path)


def test_prepare_accepts_cached_category_match_tuples(tmp_path, prepared_inputs):
    """Catches cached classification from SnapshotEntry crashing before state publication."""
    inputs = dict(prepared_inputs)
    inputs.pop("temporarily_failed_repo_id")
    record = inputs["collection"].records[0]
    inputs["collection"] = CollectionResult(
        (RepositoryRecord(**{**record.to_dict(), "content_reused": True, "skill_text": ""}),),
        inputs["collection"].candidates,
        inputs["collection"].warnings,
    )
    inputs["matches"] = {8: (CategoryMatch("photoshop", 9, ("cached",)),)}
    outputs = prepare_outputs(root=tmp_path, **inputs)
    snapshot = json.loads(outputs.files[tmp_path / "data/snapshot.json"])
    assert snapshot["repositories"]["8"]["category_matches"][0]["reasons"] == ["cached"]


def test_prepare_does_not_touch_final_files_until_commit(tmp_path, prepared_inputs):
    """Catches report/state writes escaping the transaction preparation stage."""
    latest = tmp_path / "LATEST.md"
    latest.write_text("old", encoding="utf-8")
    inputs = dict(prepared_inputs)
    inputs.pop("temporarily_failed_repo_id")
    outputs = prepare_outputs(root=tmp_path, **inputs)
    assert latest.read_text(encoding="utf-8") == "old"
    commit_outputs(outputs)
    assert latest.read_text(encoding="utf-8").startswith("# GitHub Skill 每周热门榜")


def test_prepare_preserves_last_entry_after_temporary_repository_failure(tmp_path, prepared_inputs):
    """Catches dropping a prior snapshot entry when collection merely timed out."""
    inputs = dict(prepared_inputs)
    failed_repo_id = str(inputs.pop("temporarily_failed_repo_id"))
    previous = inputs["previous_snapshot"]
    outputs = prepare_outputs(root=tmp_path, **inputs)
    snapshot_json = json.loads(outputs.files[tmp_path / "data/snapshot.json"])
    assert snapshot_json["repositories"][failed_repo_id] == previous.repositories[int(failed_repo_id)].to_dict()


def test_prepare_removes_snapshot_entry_for_confirmed_inactive_candidate(tmp_path, prepared_inputs):
    """Catches retaining state after collection positively confirmed a repository is inactive."""
    inputs = dict(prepared_inputs)
    inactive = inputs["collection"].candidates[7]
    inputs["collection"] = CollectionResult(
        inputs["collection"].records,
        {**inputs["collection"].candidates, 7: Candidate(**{**inactive.to_dict(), "active": False})},
        inputs["collection"].warnings,
    )
    inputs.pop("temporarily_failed_repo_id")
    outputs = prepare_outputs(root=tmp_path, **inputs)
    snapshot_json = json.loads(outputs.files[tmp_path / "data/snapshot.json"])
    assert "7" not in snapshot_json["repositories"]


def test_commit_rolls_back_every_replaced_file_when_later_replacement_fails(tmp_path, monkeypatch):
    """Catches a partial output transaction leaving an earlier replacement published."""
    from skill_radar.storage import PreparedOutputs

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("before-first", encoding="utf-8")
    second.write_text("before-second", encoding="utf-8")
    outputs = PreparedOutputs({first: "after-first", second: "after-second"}, tmp_path / "report.md")
    original_replace = Path.replace

    def fail_second_replace(self, target):
        if Path(target) == second and self.name == ".second.txt.tmp":
            raise OSError("simulated replacement failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)
    with pytest.raises(OSError, match="simulated"):
        commit_outputs(outputs)
    assert first.read_text(encoding="utf-8") == "before-first"
    assert second.read_text(encoding="utf-8") == "before-second"
    assert not list(tmp_path.glob(".*.tmp"))


def test_prepared_json_is_utf8_deterministic_and_has_trailing_newline(tmp_path, prepared_inputs):
    """Catches non-repeatable state output or escaped Chinese state data."""
    inputs = dict(prepared_inputs)
    inputs.pop("temporarily_failed_repo_id")
    first = prepare_outputs(root=tmp_path, **inputs)
    second = prepare_outputs(root=tmp_path, **inputs)
    candidate_json = first.files[tmp_path / "data/candidates.json"]
    assert candidate_json == second.files[tmp_path / "data/candidates.json"]
    assert candidate_json.endswith("\n")
    assert "\\u" not in candidate_json
    assert list(json.loads(candidate_json)["candidates"]) == ["7", "8"]
