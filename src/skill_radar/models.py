"""Immutable data structures shared across the radar pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


def _tuple(values: Any) -> tuple[Any, ...]:
    return tuple(values)


@dataclass(frozen=True)
class CategoryRule:
    name_zh: str
    threshold: int
    strong_bonus: int
    strong_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    weights: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))

    def to_dict(self) -> dict[str, Any]:
        return {"name_zh": self.name_zh, "threshold": self.threshold, "strong_bonus": self.strong_bonus, "strong_terms": list(self.strong_terms), "weak_terms": list(self.weak_terms), "context_terms": list(self.context_terms), "exclude_terms": list(self.exclude_terms), "weights": dict(self.weights)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CategoryRule:
        return cls(data["name_zh"], data["threshold"], data["strong_bonus"], _tuple(data["strong_terms"]), _tuple(data["weak_terms"]), _tuple(data["context_terms"]), _tuple(data["exclude_terms"]), MappingProxyType(dict(data["weights"])))


@dataclass(frozen=True)
class CategoryMatch:
    category: str
    score: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "score": self.score, "reasons": list(self.reasons)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CategoryMatch:
        return cls(data["category"], data["score"], _tuple(data["reasons"]))


@dataclass(frozen=True)
class SearchHit:
    repo_id: int
    full_name: str
    repo_url: str
    path: str

    def to_dict(self) -> dict[str, Any]: return self.__dict__.copy()
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchHit: return cls(**data)


@dataclass(frozen=True)
class Candidate:
    repo_id: int
    full_name: str
    url: str
    skill_paths: tuple[str, ...]
    discovered_at: str
    last_seen_at: str
    last_checked_at: str
    active: bool

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy(); data["skill_paths"] = list(self.skill_paths); return data
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Candidate:
        payload = dict(data); payload["skill_paths"] = _tuple(payload["skill_paths"]); return cls(**payload)


@dataclass(frozen=True)
class RepositoryMetadata:
    repo_id: int
    full_name: str
    url: str
    description: str
    topics: tuple[str, ...]
    stars: int
    updated_at: str
    default_branch: str

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy(); data["topics"] = list(self.topics); return data
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RepositoryMetadata:
        payload = dict(data); payload["topics"] = _tuple(payload["topics"]); return cls(**payload)


@dataclass(frozen=True)
class RepositoryRecord:
    repo_id: int
    full_name: str
    url: str
    description: str
    topics: tuple[str, ...]
    stars: int
    updated_at: str
    default_branch: str
    skill_paths: tuple[str, ...]
    skill_text: str
    content_sha256: str
    content_reused: bool

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy(); data["topics"] = list(self.topics); data["skill_paths"] = list(self.skill_paths); return data
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RepositoryRecord:
        payload = dict(data); payload["topics"] = _tuple(payload["topics"]); payload["skill_paths"] = _tuple(payload["skill_paths"]); return cls(**payload)


@dataclass(frozen=True)
class RankedRepository:
    repo_id: int
    full_name: str
    url: str
    description: str
    stars: int
    weekly_growth: int | None
    updated_at: str
    skill_paths: tuple[str, ...]
    category_matches: tuple[CategoryMatch, ...]

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy(); data["skill_paths"] = list(self.skill_paths); data["category_matches"] = [match.to_dict() for match in self.category_matches]; return data
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RankedRepository:
        payload = dict(data); payload["skill_paths"] = _tuple(payload["skill_paths"]); payload["category_matches"] = tuple(CategoryMatch.from_dict(match) for match in payload["category_matches"]); return cls(**payload)


@dataclass(frozen=True)
class Rankings:
    is_baseline: bool
    overall: tuple[RankedRepository, ...]
    art_design: tuple[RankedRepository, ...]
    photoshop: tuple[RankedRepository, ...]
    illustrator: tuple[RankedRepository, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"is_baseline": self.is_baseline, **{name: [item.to_dict() for item in getattr(self, name)] for name in ("overall", "art_design", "photoshop", "illustrator")}}
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Rankings:
        return cls(data["is_baseline"], *(tuple(RankedRepository.from_dict(item) for item in data[name]) for name in ("overall", "art_design", "photoshop", "illustrator")))


@dataclass(frozen=True)
class RunStats:
    observed_from: str
    observed_to: str
    discovered_count: int
    active_count: int
    collected_count: int
    skipped_count: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy(); data["warnings"] = list(self.warnings); return data
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunStats:
        payload = dict(data); payload["warnings"] = _tuple(payload["warnings"]); return cls(**payload)


@dataclass(frozen=True)
class SnapshotEntry:
    stars: int
    updated_at: str
    skill_paths: tuple[str, ...]
    content_sha256: str
    category_matches: tuple[CategoryMatch, ...]
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"stars": self.stars, "updated_at": self.updated_at, "skill_paths": list(self.skill_paths), "content_sha256": self.content_sha256, "category_matches": [match.to_dict() for match in self.category_matches], "checked_at": self.checked_at}
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SnapshotEntry:
        return cls(data["stars"], data["updated_at"], _tuple(data["skill_paths"]), data["content_sha256"], tuple(CategoryMatch.from_dict(match) for match in data["category_matches"]), data["checked_at"])


@dataclass(frozen=True)
class Snapshot:
    captured_at: str
    classification_config_sha256: str
    repositories: Mapping[int, SnapshotEntry]

    def __post_init__(self) -> None:
        object.__setattr__(self, "repositories", MappingProxyType(dict(self.repositories)))

    @property
    def stars_by_repo(self) -> Mapping[int, int]:
        return MappingProxyType({repo_id: entry.stars for repo_id, entry in self.repositories.items()})

    def to_dict(self) -> dict[str, Any]:
        return {"captured_at": self.captured_at, "classification_config_sha256": self.classification_config_sha256, "repositories": {str(repo_id): entry.to_dict() for repo_id, entry in self.repositories.items()}}
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Snapshot:
        return cls(data["captured_at"], data["classification_config_sha256"], MappingProxyType({int(repo_id): SnapshotEntry.from_dict(entry) for repo_id, entry in data["repositories"].items()}))
