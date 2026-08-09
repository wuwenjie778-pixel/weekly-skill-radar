from dataclasses import replace
from pathlib import Path

import pytest

from skill_radar.classifier import classify_repository
from skill_radar.config import load_category_rules
from skill_radar.models import RepositoryRecord


@pytest.fixture
def rules():
    return load_category_rules(Path("config/categories.yml"))


@pytest.fixture
def sample_record():
    return RepositoryRecord(
        repo_id=1,
        full_name="example/skill",
        url="https://example.invalid/skill",
        description="",
        topics=(),
        stars=0,
        updated_at="2026-08-09T00:00:00Z",
        default_branch="main",
        skill_paths=(),
        skill_text="",
        content_sha256="digest",
        content_reused=False,
    )


def test_photoshop_strong_term_matches(sample_record, rules):
    """Catches removing strong-term bonus from description matches."""
    record = replace(sample_record, description="Automate Adobe Photoshop PSD retouching")
    matches = classify_repository(record, rules)
    assert matches["photoshop"].score >= rules["photoshop"].threshold
    assert any("photoshop" in reason.lower() for reason in matches["photoshop"].reasons)


def test_ps_and_ai_alone_do_not_match(sample_record, rules):
    """Catches treating ambiguous abbreviations as standalone category evidence."""
    record = replace(sample_record, description="PS AI helper")
    matches = classify_repository(record, rules)
    assert "photoshop" not in matches
    assert "illustrator" not in matches


def test_one_repository_can_match_all_professional_categories(sample_record, rules):
    """Catches stopping classification after the first category match."""
    record = replace(sample_record, description="平面设计：Photoshop 修图与 Illustrator 矢量插画")
    assert set(classify_repository(record, rules)) == {"art_design", "photoshop", "illustrator"}


def test_repeated_term_scores_once_per_field(sample_record, rules):
    """Catches multiplying a field's score for repeated keyword occurrences."""
    record = replace(sample_record, description="Photoshop photoshop PHOTOSHOP")
    match = classify_repository(record, rules)["photoshop"]
    assert match.reasons.count('简介命中“photoshop” (+7)') == 1


def test_exclusion_suppresses_category(sample_record, rules):
    """Catches exclusions being ignored after an otherwise qualifying match."""
    blocked = replace(rules["photoshop"], exclude_terms=("stock price",))
    record = replace(sample_record, description="Photoshop stock price")
    assert "photoshop" not in classify_repository(record, {"photoshop": blocked})


def test_chinese_strong_term_matches_without_ascii_boundaries(sample_record, rules):
    """Catches applying ASCII token-boundary rules to Chinese category terms."""
    record = replace(sample_record, description="适用于矢量图形工作流的技能")
    assert "illustrator" in classify_repository(record, rules)


def test_short_ascii_term_does_not_match_inside_a_longer_word(sample_record, rules):
    """Catches `ps` matching the substring in `compass` when raster supplies context."""
    record = replace(sample_record, description="compass raster utilities")
    assert "photoshop" not in classify_repository(record, rules)


def test_reasons_are_ordered_by_field_then_term(sample_record, rules):
    """Catches nondeterministic reason ordering across repository fields and terms."""
    record = replace(
        sample_record,
        full_name="example/photoshop-tool",
        description="PSD Photoshop",
        topics=("photoshop",),
    )
    reasons = classify_repository(record, rules)["photoshop"].reasons
    assert reasons == (
        '仓库名命中“photoshop” (+8)',
        '简介命中“photoshop” (+7)',
        '简介命中“psd” (+7)',
        '标签命中“photoshop” (+7)',
    )
