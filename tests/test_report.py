"""Chinese Markdown report contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skill_radar.models import CategoryMatch, RankedRepository, Rankings, RunStats
from skill_radar.report import render_report


BEIJING_NOW = datetime(2026, 8, 9, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def stats():
    return RunStats(
        observed_from="2026-08-02T16:30:00Z",
        observed_to="2026-08-09T16:30:00Z",
        discovered_count=12,
        active_count=10,
        collected_count=8,
        skipped_count=2,
        warnings=("owner/slow: timeout",),
    )


@pytest.fixture
def professional_entry():
    return RankedRepository(
        repo_id=1,
        full_name="owner/visual|skill",
        url="https://github.com/owner/visual-skill",
        description="Photoshop\nworkflow | tools",
        stars=31,
        weekly_growth=4,
        updated_at="2026-08-09T12:00:00Z",
        skill_paths=("tools/SKILL.md", "other\nSKILL.md"),
        category_matches=(
            CategoryMatch("art_design", 9, ("简介命中“design”",)),
            CategoryMatch("photoshop", 9, ('简介命中“photoshop”',)),
        ),
    )


@pytest.fixture
def baseline_rankings(professional_entry):
    return Rankings(True, (professional_entry,), (professional_entry,), (professional_entry,), ())


@pytest.fixture
def growth_rankings(professional_entry):
    return Rankings(False, (professional_entry,), (professional_entry,), (professional_entry,), ())


def test_baseline_report_is_clearly_marked(baseline_rankings, stats):
    """Catches presenting first-run totals as weekly growth."""
    report = render_report(baseline_rankings, stats, BEIJING_NOW)
    assert "基线初始化，非周增长榜" in report
    assert "全站热门 Skill Top 10" in report
    assert "| — |" in report


def test_professional_entry_shows_reason(growth_rankings, stats):
    """Catches losing the classifier's human-readable evidence in category tables."""
    report = render_report(growth_rankings, stats, BEIJING_NOW)
    assert "分类依据" in report
    assert "简介命中“photoshop”" in report
    assert "SKILL.md" in report


def test_report_has_four_bounded_headings_and_empty_professional_marker(growth_rankings, stats):
    """Catches missing rankings or inventing placeholder professional repositories."""
    report = render_report(growth_rankings, stats, BEIJING_NOW)
    assert report.count("## ") == 4
    assert "## 全站热门 Skill Top 10" in report
    assert "## 艺术设计相关 Skill Top 10" in report
    assert "## Photoshop 专项 Skill Top 5" in report
    assert "## Illustrator 专项 Skill Top 5" in report
    assert "本期无符合条件的项目" in report


def test_report_escapes_external_table_text_and_includes_warning_summary(growth_rankings, stats):
    """Catches GitHub metadata creating extra Markdown cells or rows."""
    report = render_report(growth_rankings, stats, BEIJING_NOW)
    assert "visual\\|skill" in report
    assert "Photoshop<br>workflow \\| tools" in report
    assert "other<br>SKILL.md" in report
    assert "警告摘要" in report
    assert "owner/slow: timeout" in report


def test_report_uses_beijing_date_and_utc_generation_time(growth_rankings, stats):
    """Catches report names/dates following the runner's timezone rather than Beijing."""
    report = render_report(growth_rankings, stats, BEIJING_NOW)
    assert "报告日期：2026-08-10（北京时间）" in report
    assert "生成时间：2026-08-09T16:30:00Z" in report


def test_report_limits_each_list_to_its_declared_size(stats):
    """Catches rendering more than the published list-size promise."""
    entries = tuple(
        RankedRepository(index, f"owner/{index}", f"https://github.com/owner/{index}", "", index, index, "2026-08-09T00:00:00Z", ("SKILL.md",), ())
        for index in range(1, 13)
    )
    report = render_report(Rankings(False, entries, entries, entries, entries), stats, BEIJING_NOW)
    headings = ("## 全站热门 Skill Top 10", "## 艺术设计相关 Skill Top 10", "## Photoshop 专项 Skill Top 5", "## Illustrator 专项 Skill Top 5")
    sections = {}
    for index, heading in enumerate(headings):
        after_heading = report.split(heading, 1)[1]
        sections[heading] = after_heading.split(headings[index + 1], 1)[0] if index + 1 < len(headings) else after_heading
    assert sections["## 全站热门 Skill Top 10"].count("https://github.com/") == 10
    assert sections["## 艺术设计相关 Skill Top 10"].count("https://github.com/") == 10
    assert sections["## Photoshop 专项 Skill Top 5"].count("https://github.com/") == 5
    assert sections["## Illustrator 专项 Skill Top 5"].count("https://github.com/") == 5
