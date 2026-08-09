import pytest

from skill_radar.models import CategoryMatch, RepositoryRecord
from skill_radar.ranking import build_rankings, calculate_growth


@pytest.fixture
def record_factory():
    next_repo_id = 1

    def make_record(**overrides):
        nonlocal next_repo_id
        repo_id = overrides.pop("repo_id", next_repo_id)
        next_repo_id = max(next_repo_id + 1, repo_id + 1)
        return RepositoryRecord(
            repo_id=repo_id,
            full_name=overrides.pop("full_name", f"owner/repo-{repo_id}"),
            url=overrides.pop("url", f"https://github.com/owner/repo-{repo_id}"),
            description=overrides.pop("description", "A skill repository"),
            topics=overrides.pop("topics", ("skills",)),
            stars=overrides.pop("stars", 100),
            updated_at=overrides.pop("updated_at", "2026-08-09T00:00:00Z"),
            default_branch=overrides.pop("default_branch", "main"),
            skill_paths=overrides.pop("skill_paths", ("SKILL.md",)),
            skill_text=overrides.pop("skill_text", ""),
            content_sha256=overrides.pop("content_sha256", "digest"),
            content_reused=overrides.pop("content_reused", False),
            **overrides,
        )

    return make_record


@pytest.fixture
def records(record_factory):
    return [record_factory(repo_id=repo_id, stars=100 + repo_id) for repo_id in range(1, 13)]


def test_first_run_is_baseline_and_limits_overall_to_ten(records):
    """Catches treating an empty snapshot as a growth-ranking run."""
    result = build_rankings(records, {}, {})

    assert result.is_baseline is True
    assert [item.repo_id for item in result.overall] == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]
    assert all(item.weekly_growth is None for item in result.overall)


def test_later_run_orders_known_repositories_by_net_growth(records):
    """Catches ranking a later run by total stars instead of weekly growth."""
    previous = {record.repo_id: record.stars - record.repo_id for record in records}

    result = build_rankings(records, previous, {})

    assert result.is_baseline is False
    assert [item.repo_id for item in result.overall] == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]
    assert [item.weekly_growth for item in result.overall] == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]


def test_negative_growth_is_preserved(record_factory):
    """Catches clamping star losses to zero."""
    record = record_factory(stars=40)

    result = build_rankings([record], {record.repo_id: 43}, {})

    assert result.overall[0].weekly_growth == -3
    assert calculate_growth(40, 43) == -3


def test_new_candidates_sort_after_known_growth_but_remain_eligible(record_factory):
    """Catches either ranking unknown growth as zero or excluding it entirely."""
    known_loss = record_factory(repo_id=1, full_name="owner/known-loss", stars=50)
    newcomer = record_factory(repo_id=2, full_name="owner/new", stars=5)

    result = build_rankings([newcomer, known_loss], {1: 60}, {})

    assert [item.repo_id for item in result.overall] == [1, 2]
    assert [item.weekly_growth for item in result.overall] == [-10, None]


def test_ties_break_by_stars_then_updated_at_then_casefolded_name(record_factory):
    """Catches unstable output when growth values tie."""
    records = [
        record_factory(repo_id=1, full_name="z/repo", stars=100, updated_at="2026-08-08T00:00:00Z"),
        record_factory(repo_id=2, full_name="a/repo", stars=100, updated_at="2026-08-08T00:00:00Z"),
        record_factory(repo_id=3, full_name="m/repo", stars=101, updated_at="2026-08-07T00:00:00Z"),
        record_factory(repo_id=4, full_name="n/repo", stars=101, updated_at="2026-08-09T00:00:00Z"),
    ]
    previous = {record.repo_id: record.stars for record in records}

    result = build_rankings(records, previous, {})

    assert [item.full_name for item in result.overall] == ["n/repo", "m/repo", "a/repo", "z/repo"]


def test_category_lists_require_actual_matches_allow_duplicates_and_apply_limits(record_factory):
    """Catches inferred category membership, cross-list deduplication, or wrong list limits."""
    records = [record_factory(repo_id=repo_id, stars=repo_id) for repo_id in range(1, 12)]
    matches = {
        record.repo_id: {
            "art_design": CategoryMatch("art_design", 10, ("art",)),
            "photoshop": CategoryMatch("photoshop", 10, ("ps",)),
        }
        for record in records
    }
    matches[11]["illustrator"] = CategoryMatch("illustrator", 10, ("ai",))

    result = build_rankings(records, {record.repo_id: 0 for record in records}, matches)

    assert [item.repo_id for item in result.art_design] == [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]
    assert [item.repo_id for item in result.photoshop] == [11, 10, 9, 8, 7]
    assert [item.repo_id for item in result.illustrator] == [11]
    assert result.art_design[0].repo_id == result.photoshop[0].repo_id
    assert result.photoshop[0].category_matches == (
        CategoryMatch("art_design", 10, ("art",)),
        CategoryMatch("photoshop", 10, ("ps",)),
        CategoryMatch("illustrator", 10, ("ai",)),
    )
