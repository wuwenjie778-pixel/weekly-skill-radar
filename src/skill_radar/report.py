"""Deterministic Chinese Markdown rendering for weekly rankings."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import RankedRepository, Rankings, RunStats
from .ranking import LIMITS


_SECTIONS = (
    ("overall", "全站热门 Skill Top 10", None),
    ("art_design", "艺术设计相关 Skill Top 10", "art_design"),
    ("photoshop", "Photoshop 专项 Skill Top 5", "photoshop"),
    ("illustrator", "Illustrator 专项 Skill Top 5", "illustrator"),
)


def render_report(rankings: Rankings, stats: RunStats, generated_at: datetime) -> str:
    """Render four bounded ranking tables without trusting external text as Markdown."""
    generated_utc = _utc_iso(generated_at)
    report_date = generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    status = "基线初始化，非周增长榜" if rankings.is_baseline else "周增长榜"
    lines = [
        "# GitHub Skill 每周热门榜",
        "",
        f"- 报告日期：{report_date}（北京时间）",
        f"- 观测窗口：{stats.observed_from} 至 {stats.observed_to}",
        f"- 生成时间：{generated_utc}",
        f"- 发现候选：{stats.discovered_count}",
        f"- 活跃候选：{stats.active_count}",
        f"- 成功采集：{stats.collected_count}",
        f"- 跳过记录：{stats.skipped_count}",
        f"- 非致命警告：{len(stats.warnings)}",
        f"- 榜单状态：{status}",
    ]
    for attribute, title, category in _SECTIONS:
        lines.extend(("", f"## {title}", ""))
        entries = getattr(rankings, attribute)[:LIMITS[attribute]]
        if category is not None and not entries:
            lines.append("本期无符合条件的项目")
            continue
        lines.extend(_table_lines(entries, rankings.is_baseline, category))
    lines.extend(("", "**警告摘要**"))
    if stats.warnings:
        lines.extend(f"- {_escape_text(warning)}" for warning in stats.warnings)
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def _table_lines(entries: tuple[RankedRepository, ...], baseline: bool, category: str | None) -> list[str]:
    columns = ["排名", "仓库", "周增长", "总 Stars", "简介", "Skill 路径"]
    if category is not None:
        columns.append("分类依据")
    columns.append("仓库更新时间")
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for rank, entry in enumerate(entries, start=1):
        cells = [
            str(rank),
            _repository_link(entry),
            "—" if baseline or entry.weekly_growth is None else str(entry.weekly_growth),
            str(entry.stars),
            _escape_text(entry.description),
            _escape_text("<br>".join(entry.skill_paths)),
        ]
        if category is not None:
            cells.append(_escape_text(_category_reasons(entry, category)))
        cells.append(_escape_text(entry.updated_at))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _repository_link(entry: RankedRepository) -> str:
    label = _escape_text(entry.full_name)
    url = entry.url.replace("\r", "").replace("\n", "").replace("|", "%7C").replace(")", "%29")
    return f"[{label}]({url})"


def _category_reasons(entry: RankedRepository, category: str) -> str:
    for match in entry.category_matches:
        if match.category == category:
            return "；".join(match.reasons)
    return "—"


def _escape_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>").replace("|", "\\|")


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("generated_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
