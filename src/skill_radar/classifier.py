"""Explainable keyword classification for skill repositories."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import CategoryMatch, CategoryRule, RepositoryRecord


_FIELD_LABELS = {
    "name": "仓库名",
    "description": "简介",
    "topics": "标签",
    "path": "路径",
    "content": "内容",
}


def _contains(text: str, term: str) -> bool:
    """Match a case-folded term, protecting short ASCII abbreviations."""
    normalized_term = term.casefold()
    if normalized_term.isascii() and len(normalized_term) <= 2:
        return re.search(rf"(?<!\w){re.escape(normalized_term)}(?!\w)", text) is not None
    return normalized_term in text


def _fields(record: RepositoryRecord) -> dict[str, str]:
    return {
        "name": record.full_name.casefold(),
        "description": record.description.casefold(),
        "topics": " ".join(record.topics).casefold(),
        "path": " ".join(record.skill_paths).casefold(),
        "content": record.skill_text.casefold(),
    }


def _has_term(fields: Mapping[str, str], terms: tuple[str, ...]) -> bool:
    return any(_contains(text, term) for text in fields.values() for term in terms)


def classify_repository(
    record: RepositoryRecord,
    rules: Mapping[str, CategoryRule],
) -> dict[str, CategoryMatch]:
    """Return every configured category whose weighted evidence reaches its threshold."""
    fields = _fields(record)
    matches: dict[str, CategoryMatch] = {}

    for category, rule in rules.items():
        if _has_term(fields, rule.exclude_terms):
            continue

        context_present = _has_term(fields, rule.context_terms)
        strong_terms = {term.casefold(): term for term in rule.strong_terms}
        weak_terms = {
            term.casefold(): term
            for term in rule.weak_terms
            if term.casefold() not in strong_terms
        }
        score = 0
        reasons: list[str] = []

        for field, text in fields.items():
            found_terms: list[tuple[str, bool]] = []
            for normalized, term in strong_terms.items():
                if _contains(text, term):
                    found_terms.append((normalized, True))
            if context_present:
                for normalized, term in weak_terms.items():
                    if _contains(text, term):
                        found_terms.append((normalized, False))

            for normalized, is_strong in sorted(found_terms):
                points = rule.weights[field] + (rule.strong_bonus if is_strong else 0)
                score += points
                reasons.append(f'{_FIELD_LABELS[field]}命中“{normalized}” (+{points})')

        if score >= rule.threshold:
            matches[category] = CategoryMatch(category, score, tuple(reasons))

    return matches
