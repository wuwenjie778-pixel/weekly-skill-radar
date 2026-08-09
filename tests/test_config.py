from pathlib import Path

import pytest

from skill_radar.config import ConfigError, load_category_rules, load_search_queries
from skill_radar.models import CategoryRule, Snapshot, SnapshotEntry


def test_loads_required_categories_and_queries():
    rules = load_category_rules(Path("config/categories.yml"))
    queries = load_search_queries(Path("config/search_queries.yml"))
    assert set(rules) == {"art_design", "photoshop", "illustrator"}
    assert all(rule.threshold > 0 for rule in rules.values())
    assert "filename:SKILL.md" in queries
    assert any("photoshop" in query.lower() for query in queries)


def test_rejects_category_without_positive_threshold(tmp_path: Path):
    path = tmp_path / "bad.yml"
    path.write_text("categories:\n  art_design:\n    threshold: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="threshold"):
        load_category_rules(path)


def test_category_rule_weights_are_immutable():
    rule = CategoryRule("艺术", 6, 3, (), (), (), (), {"name": 5})
    with pytest.raises(TypeError):
        rule.weights["name"] = 1


def test_snapshot_copies_repository_mapping_from_caller():
    repositories = {
        1: SnapshotEntry(10, "2026-08-09", (), "digest", (), "2026-08-09T00:00:00Z")
    }
    snapshot = Snapshot("2026-08-09T00:00:00Z", "config-digest", repositories)
    repositories[1] = SnapshotEntry(20, "2026-08-09", (), "digest", (), "2026-08-09T00:00:00Z")

    assert snapshot.repositories[1].stars == 10
    assert snapshot.stars_by_repo == {1: 10}
