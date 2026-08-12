"""Transactional orchestration for one weekly skill-radar run."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .classifier import classify_repository
from .config import load_category_rules, load_search_queries
from .discovery import CollectionResult, collect_repositories, discover_candidates
from .github import GitHubClient
from .models import CategoryMatch, Rankings
from .ranking import LIMITS, build_rankings
from .report import _SECTIONS
from .storage import SCHEMA_VERSION, PreparedOutputs, commit_outputs, load_candidates, load_snapshot, prepare_outputs


@dataclass(frozen=True)
class RunResult:
    report_path: Path
    collected_count: int
    warning_count: int
    is_baseline: bool


def run_pipeline(
    root: Path,
    token: str,
    now: datetime | None = None,
    client: GitHubClient | None = None,
) -> RunResult:
    """Collect, rank, validate, and atomically publish a weekly report."""
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        raise ValueError("now must include a timezone")
    root = Path(root)
    github = client or GitHubClient(token)
    category_path = root / "config" / "categories.yml"
    rules = load_category_rules(category_path)
    config_sha = hashlib.sha256(category_path.read_bytes()).hexdigest()
    queries = load_search_queries(root / "config" / "search_queries.yml")
    previous_candidates = load_candidates(root / "data" / "candidates.json")
    previous_snapshot = load_snapshot(root / "data" / "snapshot.json")
    observed_at = generated_at.isoformat()
    candidates = discover_candidates(github, queries, previous_candidates, observed_at)
    allow_cached = previous_snapshot.classification_config_sha256 == config_sha
    collection = collect_repositories(github, candidates, previous_snapshot, allow_cached, observed_at)
    matches = _classify_records(collection, previous_snapshot, rules)
    rankings = build_rankings(collection.records, previous_snapshot.stars_by_repo, matches)
    outputs = prepare_outputs(root, generated_at, config_sha, previous_snapshot, collection, matches, rankings)
    validate_outputs(outputs, rankings, collection)
    commit_outputs(outputs)
    return RunResult(outputs.report_path, len(collection.records), len(collection.warnings), rankings.is_baseline)


def _classify_records(
    collection: CollectionResult,
    previous_snapshot,
    rules,
) -> dict[int, Mapping[str, CategoryMatch]]:
    matches: dict[int, Mapping[str, CategoryMatch]] = {}
    for record in collection.records:
        if record.content_reused:
            cached = previous_snapshot.repositories[record.repo_id].category_matches
            matches[record.repo_id] = {match.category: match for match in cached}
        else:
            matches[record.repo_id] = classify_repository(record, rules)
    return matches


def validate_outputs(outputs: PreparedOutputs, rankings: Rankings, collection: CollectionResult) -> None:
    """Reject incomplete or malformed publication content before any final file changes."""
    try:
        for content in outputs.files.values():
            content.encode("utf-8").decode("utf-8")
    except UnicodeError as error:
        raise ValueError("prepared output is not valid UTF-8") from error

    report = outputs.files.get(outputs.report_path)
    latest_path = next((path for path in outputs.files if path.name == "LATEST.md"), None)
    if report is None or latest_path is None or outputs.files[latest_path] != report:
        raise ValueError("history report and LATEST.md must be identical")
    headings = [line for line in report.splitlines() if line.startswith("## ")]
    expected_headings = [f"## {title}" for _, title, _ in _SECTIONS]
    if headings != expected_headings:
        raise ValueError("report must contain exactly the four ranking headings")
    for index, (name, _, _) in enumerate(_SECTIONS):
        section_start = report.index(expected_headings[index]) + len(expected_headings[index])
        section_end = report.index(expected_headings[index + 1], section_start) if index + 1 < len(expected_headings) else len(report)
        repository_urls = re.findall(r"https://github\.com/[^)\s]+", report[section_start:section_end])
        if len(repository_urls) > LIMITS[name]:
            raise ValueError(f"rendered {name} exceeds its published limit")
        if len(repository_urls) != len(set(repository_urls)):
            raise ValueError(f"rendered {name} contains duplicate repositories")

    for name, limit in LIMITS.items():
        entries = getattr(rankings, name)
        if len(entries) > limit:
            raise ValueError(f"{name} exceeds its published limit")
        repo_ids = [entry.repo_id for entry in entries]
        if len(repo_ids) != len(set(repo_ids)):
            raise ValueError(f"{name} contains duplicate repositories")

    candidate_path = next((path for path in outputs.files if path.name == "candidates.json"), None)
    snapshot_path = next((path for path in outputs.files if path.name == "snapshot.json"), None)
    if candidate_path is None or snapshot_path is None:
        raise ValueError("prepared state files are missing")
    try:
        candidates_json = json.loads(outputs.files[candidate_path])
        snapshot_json = json.loads(outputs.files[snapshot_path])
    except json.JSONDecodeError as error:
        raise ValueError("prepared state is invalid JSON") from error
    if (
        candidates_json.get("schema_version") != SCHEMA_VERSION
        or snapshot_json.get("schema_version") != SCHEMA_VERSION
        or isinstance(candidates_json.get("schema_version"), bool)
        or isinstance(snapshot_json.get("schema_version"), bool)
    ):
        raise ValueError("prepared state must use schema version 1")
    repositories = snapshot_json.get("repositories")
    if not isinstance(repositories, dict):
        raise ValueError("prepared snapshot repositories must be an object")
    for record in collection.records:
        candidate = collection.candidates.get(record.repo_id)
        if candidate is not None and candidate.active and str(record.repo_id) not in repositories:
            raise ValueError("prepared snapshot is missing a collected active repository")
