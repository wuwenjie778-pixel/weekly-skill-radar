"""Offline checks for deployment automation and operator guidance."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_weekly_workflow_limits_token_scope_and_publishes_only_generated_outputs():
    """Catches a workflow that broadens access or exposes the public token."""
    workflow_path = ROOT / ".github/workflows/weekly-ranking.yml"
    text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    assert workflow[True] == {
        "workflow_dispatch": None,
        "schedule": [{"cron": "0 0 * * 1"}],
    }
    assert workflow["concurrency"] == {
        "group": "weekly-skill-ranking",
        "cancel-in-progress": False,
    }
    assert workflow["permissions"] == {"contents": "write"}
    assert "pull_request_target" not in text

    steps = workflow["jobs"]["update-ranking"]["steps"]
    generation = next(step for step in steps if step.get("name") == "Generate ranking")
    assert generation["env"] == {"PUBLIC_GITHUB_TOKEN": "${{ secrets.PUBLIC_GITHUB_TOKEN }}"}
    assert sum("PUBLIC_GITHUB_TOKEN" in str(step) for step in steps) == 1

    commit = next(step for step in steps if step.get("name") == "Commit generated files")
    assert "git diff --quiet -- LATEST.md reports data" in commit["run"]
    assert "git add -- LATEST.md reports data" in commit["run"]


def test_operator_guide_covers_safe_first_run_and_recovery():
    """Catches a README that omits required deployment or security guidance."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "PUBLIC_GITHUB_TOKEN",
        "Settings → Secrets and variables → Actions",
        "GITHUB_TOKEN",
        "Weekly GitHub Skill Ranking",
        "Monday 08:00 Beijing",
        "default branch",
        "60 days",
        "Python 3.12",
        "python -m pytest -v",
        "python -m skill_radar --root .",
        "config/categories.yml",
        "config/search_queries.yml",
        "data/candidates.json",
        "data/snapshot.json",
        ".env",
        "rotate",
    ):
        assert phrase in text


def test_latest_is_valid_before_or_after_the_first_successful_run():
    """Catches either fabricated pre-run rows or an invalid generated ranking."""
    text = (ROOT / "LATEST.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    ranking_rows = [line for line in lines if line.startswith("|") and line.split("|", 2)[1].strip().isdigit()]

    if not ranking_rows:
        assert "baseline" in text.lower()
        assert "PUBLIC_GITHUB_TOKEN" in text
        assert "manually" in text.lower()
        assert "|" not in text
        return

    sections = (
        ("## 全站热门 Skill Top 10", 10),
        ("## 艺术设计相关 Skill Top 10", 10),
        ("## Photoshop 专项 Skill Top 5", 5),
        ("## Illustrator 专项 Skill Top 5", 5),
    )
    heading_positions = [lines.index(heading) for heading, _ in sections]
    assert heading_positions == sorted(heading_positions)
    for index, (_, limit) in enumerate(sections):
        end = heading_positions[index + 1] if index + 1 < len(sections) else len(lines)
        section_rows = [
            line
            for line in lines[heading_positions[index] + 1 : end]
            if line.startswith("|") and line.split("|", 2)[1].strip().isdigit()
        ]
        assert 0 < len(section_rows) <= limit
