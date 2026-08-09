"""Stable weekly rankings built from repository snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from .models import CategoryMatch, RankedRepository, Rankings, RepositoryRecord


LIMITS = {"overall": 10, "art_design": 10, "photoshop": 5, "illustrator": 5}


def calculate_growth(current_stars: int, previous_stars: int | None) -> int | None:
    """Return net star growth, or ``None`` when no prior observation exists."""
    return None if previous_stars is None else current_stars - previous_stars


def _sort_key(item: RankedRepository, baseline: bool) -> tuple[bool, int, int, float, str]:
    unknown_growth = not baseline and item.weekly_growth is None
    primary = item.stars if baseline else int(item.weekly_growth or 0)
    updated_epoch = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00")).timestamp()
    return (unknown_growth, -primary, -item.stars, -updated_epoch, item.full_name.casefold())


def _ranked_record(
    record: RepositoryRecord,
    previous_stars: Mapping[int, int],
    matches: Mapping[str, CategoryMatch],
) -> RankedRepository:
    return RankedRepository(
        repo_id=record.repo_id,
        full_name=record.full_name,
        url=record.url,
        description=record.description,
        stars=record.stars,
        weekly_growth=calculate_growth(record.stars, previous_stars.get(record.repo_id)),
        updated_at=record.updated_at,
        skill_paths=record.skill_paths,
        category_matches=tuple(matches.values()),
    )


def build_rankings(
    records: Sequence[RepositoryRecord],
    previous_stars: Mapping[int, int],
    matches: Mapping[int, Mapping[str, CategoryMatch]],
) -> Rankings:
    """Build bounded overall and professional rankings for one collection run."""
    baseline = not previous_stars
    ranked = [
        _ranked_record(record, previous_stars, matches.get(record.repo_id, {}))
        for record in records
    ]
    ordered = tuple(sorted(ranked, key=lambda item: _sort_key(item, baseline)))

    lists = {
        "overall": ordered[: LIMITS["overall"]],
        **{
            category: tuple(
                item
                for item in ordered
                if any(match.category == category for match in item.category_matches)
            )[: LIMITS[category]]
            for category in ("art_design", "photoshop", "illustrator")
        },
    }
    return Rankings(baseline, **lists)
