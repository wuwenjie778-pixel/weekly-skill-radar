"""Strict configuration loaders for category matching and GitHub search."""

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .models import CategoryRule


class ConfigError(ValueError):
    """Raised when a radar configuration file is invalid."""


_CATEGORY_KEYS = ("art_design", "photoshop", "illustrator")
_WEIGHT_KEYS = ("name", "description", "topics", "path", "content")
_TERM_KEYS = ("strong_terms", "weak_terms", "context_terms", "exclude_terms")


def _load_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot load configuration: {path}") from error


def _strings(value: Any, field: str, category: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{category}.{field} must be a list of strings")
    return tuple(value)


def load_category_rules(path: Path) -> dict[str, CategoryRule]:
    data = _load_yaml(path)
    if not isinstance(data, Mapping) or not isinstance(data.get("categories"), Mapping):
        raise ConfigError("categories must be a mapping")
    categories = data["categories"]
    rules: dict[str, CategoryRule] = {}
    for category in _CATEGORY_KEYS:
        if category not in categories:
            continue
        rule = categories[category]
        if not isinstance(rule, Mapping):
            raise ConfigError(f"{category} must be a mapping")
        for field in ("threshold", "strong_bonus"):
            if not isinstance(rule.get(field), int) or isinstance(rule[field], bool) or rule[field] <= 0:
                raise ConfigError(f"{category}.{field} must be a positive integer")
        if not isinstance(rule.get("name_zh"), str):
            raise ConfigError(f"{category}.name_zh must be a string")
        terms = {field: _strings(rule.get(field), field, category) for field in _TERM_KEYS}
        weights = rule.get("weights")
        if not isinstance(weights, Mapping) or set(weights) != set(_WEIGHT_KEYS) or any(not isinstance(value, int) or isinstance(value, bool) for value in weights.values()):
            raise ConfigError(f"{category}.weights must contain integer values for {', '.join(_WEIGHT_KEYS)}")
        rules[category] = CategoryRule(rule["name_zh"], rule["threshold"], rule["strong_bonus"], terms["strong_terms"], terms["weak_terms"], terms["context_terms"], terms["exclude_terms"], MappingProxyType(dict(weights)))
    if set(categories) != set(_CATEGORY_KEYS):
        raise ConfigError("categories must contain art_design, photoshop, and illustrator")
    return rules


def load_search_queries(path: Path) -> list[str]:
    data = _load_yaml(path)
    if not isinstance(data, Mapping) or not isinstance(data.get("queries"), list):
        raise ConfigError("queries must be a list")
    queries = data["queries"]
    if not queries or not all(isinstance(query, str) for query in queries):
        raise ConfigError("queries must be a non-empty list of strings")
    if len(set(queries)) != len(queries):
        raise ConfigError("queries must be deduplicated")
    if any("filename:SKILL.md" not in query for query in queries):
        raise ConfigError("every query must include filename:SKILL.md")
    return list(queries)
