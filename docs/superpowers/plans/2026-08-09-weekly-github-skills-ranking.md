# Weekly GitHub Skills Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python project that discovers public GitHub repositories containing `SKILL.md`, records weekly Star snapshots, classifies design-related Skills, and publishes four Chinese Markdown rankings every Monday through GitHub Actions.

**Architecture:** A small `src/skill_radar` package separates GitHub access, discovery, classification, snapshot/ranking, persistence, reporting, and orchestration. The workflow authenticates global public discovery with `PUBLIC_GITHUB_TOKEN`, uses the built-in `GITHUB_TOKEN` only to push generated files to the current repository, and preserves the last successful snapshot on fatal failures.

**Tech Stack:** Python 3.12, requests 2.32.5, PyYAML 6.0.2, pytest 8.4.1, GitHub REST API version 2026-03-10, GitHub Actions.

## Global Constraints

- Run every Monday at UTC 00:00, which is Beijing time Monday 08:00, and support `workflow_dispatch`.
- Rank 10 overall, 10 art/design, 5 Photoshop, and 5 Illustrator repositories; cross-list duplicates are allowed.
- Treat a repository, not an individual `SKILL.md`, as one ranking unit.
- First successful run is a total-Star baseline; later runs rank by current Star total minus the previous successful snapshot.
- Use `PUBLIC_GITHUB_TOKEN` only for global public search/read access and never print it; use the built-in `GITHUB_TOKEN` only to update the current repository.
- Do not access private repositories and do not grant `PUBLIC_GITHUB_TOKEN` write permissions.
- Generate `reports/YYYY-MM-DD.md` and copy the complete newest report to `LATEST.md`.
- Preserve the previous report and snapshot on fatal discovery, authentication, parsing, validation, or unrecoverable rate-limit failure.
- Use Chinese report headings and explanations; preserve an original non-Chinese repository description rather than calling a translation service.
- Professional lists may contain fewer than their target counts; never fill them with unrelated repositories.
- Pin Python runtime and dependencies; test with deterministic fixtures rather than the live GitHub API.
- Follow the approved design in `docs/superpowers/specs/2026-08-09-weekly-github-skills-ranking-design.md`.
- GitHub references: [code-search syntax](https://docs.github.com/en/search-github/github-code-search/understanding-github-code-search-syntax), [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), [workflow scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), and [`GITHUB_TOKEN` security](https://docs.github.com/en/actions/concepts/security/github_token).

---

## File Structure

```text
.github/workflows/weekly-ranking.yml   Weekly schedule, tests, generation, commit, push
.gitignore                             Python and local-secret exclusions
README.md                              Setup, token, manual run, configuration, troubleshooting
LATEST.md                              Pre-run notice, replaced by the latest generated report
config/categories.yml                 Explainable weighted classification rules
config/search_queries.yml             Global and domain-specific code-search queries
data/candidates.json                   Persisted candidate index, schema version 1
data/snapshot.json                     Last successful weekly snapshot, schema version 1
pyproject.toml                         Package metadata, pinned dependencies, pytest settings
src/skill_radar/__init__.py            Package version
src/skill_radar/__main__.py            `python -m skill_radar` entry point
src/skill_radar/classifier.py          Weighted category matching and reasons
src/skill_radar/cli.py                 Argument parsing and exit behavior
src/skill_radar/config.py              YAML loading and validation
src/skill_radar/discovery.py           Query merge, candidate refresh, repository collection
src/skill_radar/github.py              REST calls, pagination, auth, retry, rate-limit handling
src/skill_radar/models.py              Shared immutable dataclasses and serialization
src/skill_radar/pipeline.py             End-to-end orchestration without UI concerns
src/skill_radar/ranking.py             Baseline/growth calculation and stable list selection
src/skill_radar/report.py              Chinese Markdown rendering
src/skill_radar/storage.py             Versioned JSON reads and atomic output writes
tests/fixtures/*.json                  Fixed GitHub API and state examples
tests/test_classifier.py               Category behavior and ambiguity tests
tests/test_cli.py                      Environment/argument validation and exit codes
tests/test_config.py                   Configuration schema tests
tests/test_discovery.py                Search deduplication and repository lifecycle tests
tests/test_github.py                   REST retry, pagination, auth, and rate-limit tests
tests/test_pipeline.py                 Successful/fatal orchestration tests
tests/test_ranking.py                  Baseline, growth, duplicate, and tie tests
tests/test_report.py                   Markdown content/count tests
tests/test_storage.py                  Schema and atomic-write tests
```

### Task 1: Project Foundation, Models, and Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/skill_radar/__init__.py`
- Create: `src/skill_radar/models.py`
- Create: `src/skill_radar/config.py`
- Create: `config/categories.yml`
- Create: `config/search_queries.yml`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: YAML files from `config/`.
- Produces: `CategoryRule`, `CategoryMatch`, `SearchHit`, `Candidate`, `RepositoryMetadata`, `RepositoryRecord`, `RankedRepository`, `Rankings`, `RunStats`, `SnapshotEntry`, and `Snapshot` dataclasses; `load_category_rules(path: Path) -> dict[str, CategoryRule]`; `load_search_queries(path: Path) -> list[str]`.

- [ ] **Step 1: Add package metadata and pinned dependencies**

```toml
[build-system]
requires = ["setuptools==80.9.0"]
build-backend = "setuptools.build_meta"

[project]
name = "weekly-github-skill-radar"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["requests==2.32.5", "PyYAML==6.0.2"]

[project.optional-dependencies]
test = ["pytest==8.4.1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.setuptools.packages.find]
where = ["src"]
```

`.gitignore` must contain `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, `.env`, and `.coverage`.

- [ ] **Step 2: Write failing configuration tests**

```python
from pathlib import Path
import pytest
from skill_radar.config import ConfigError, load_category_rules, load_search_queries

def test_loads_required_categories_and_queries():
    rules = load_category_rules(Path("config/categories.yml"))
    queries = load_search_queries(Path("config/search_queries.yml"))
    assert set(rules) == {"art_design", "photoshop", "illustrator"}
    assert all(rule.threshold > 0 for rule in rules.values())
    assert "filename:SKILL.md" in queries
    assert any("photoshop" in query.lower() for query in queries)

def test_rejects_category_without_positive_threshold(tmp_path: Path):
    path = tmp_path / "bad.yml"
    path.write_text("categories:\n  art_design:\n    threshold: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="threshold"):
        load_category_rules(path)
```

- [ ] **Step 3: Run the tests and verify the import/configuration failure**

Run: `python -m pip install -e ".[test]" && python -m pytest tests/test_config.py -v`

Expected: FAIL because `skill_radar.config` and configuration files do not exist.

- [ ] **Step 4: Implement immutable shared models and strict loaders**

```python
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

@dataclass(frozen=True)
class CategoryMatch:
    category: str
    score: int
    reasons: tuple[str, ...]

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
```

Add the remaining dataclasses with these exact fields:

- `SearchHit(repo_id, full_name, repo_url, path)`.
- `Candidate(repo_id, full_name, url, skill_paths, discovered_at, last_seen_at, last_checked_at, active)`.
- `RepositoryMetadata(repo_id, full_name, url, description, topics, stars, updated_at, default_branch)`.
- `RankedRepository(repo_id, full_name, url, description, stars, weekly_growth, updated_at, skill_paths, category_matches)`.
- `Rankings(is_baseline, overall, art_design, photoshop, illustrator)` where each list is a tuple of `RankedRepository`.
- `RunStats(observed_from, observed_to, discovered_count, active_count, collected_count, skipped_count, warnings)`.
- `SnapshotEntry(stars, updated_at, skill_paths, content_sha256, category_matches, checked_at)`.
- `Snapshot(captured_at, classification_config_sha256, repositories)` with a read-only `stars_by_repo` property derived from `repositories`.

JSON-backed types must provide `to_dict()`/`from_dict()` methods. `load_category_rules` must require the three category keys, positive integer thresholds and `strong_bonus`, lists of strings, and weights for `name`, `description`, `topics`, `path`, and `content`. `load_search_queries` must return a non-empty deduplicated list and reject entries that do not include `filename:SKILL.md`.

- [ ] **Step 5: Add editable bilingual category/search configuration**

`config/categories.yml` must set strong terms such as `photoshop`, `adobe photoshop`, `psd`, `illustrator`, `adobe illustrator`, `视觉设计`, `平面设计`, `插画`, and `矢量`; weak abbreviations `ps` and `ai` must never appear in `strong_terms`. Define field weights as `name: 5`, `description: 4`, `topics: 4`, `path: 3`, `content: 1`, with `strong_bonus: 3` and thresholds `art_design: 6`, `photoshop: 6`, and `illustrator: 6`.

`config/search_queries.yml` must contain these deduplicated queries:

```yaml
queries:
  - "filename:SKILL.md"
  - "agent filename:SKILL.md"
  - "codex filename:SKILL.md"
  - "claude filename:SKILL.md"
  - "design filename:SKILL.md"
  - "art filename:SKILL.md"
  - "photoshop filename:SKILL.md"
  - "illustrator filename:SKILL.md"
```

- [ ] **Step 6: Run the configuration tests**

Run: `python -m pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 7: Commit the foundation**

```powershell
git add pyproject.toml .gitignore src/skill_radar config tests/test_config.py
git commit -m "feat: add project models and configuration"
```

### Task 2: Explainable Keyword Classifier

**Files:**
- Create: `src/skill_radar/classifier.py`
- Create: `tests/test_classifier.py`

**Interfaces:**
- Consumes: `RepositoryRecord` and `dict[str, CategoryRule]` from Task 1.
- Produces: `classify_repository(record: RepositoryRecord, rules: Mapping[str, CategoryRule]) -> dict[str, CategoryMatch]`.

- [ ] **Step 1: Write failing strong-term and ambiguity tests**

```python
def test_photoshop_strong_term_matches(sample_record, rules):
    record = replace(sample_record, description="Automate Adobe Photoshop PSD retouching")
    matches = classify_repository(record, rules)
    assert matches["photoshop"].score >= rules["photoshop"].threshold
    assert any("photoshop" in reason.lower() for reason in matches["photoshop"].reasons)

def test_ps_and_ai_alone_do_not_match(sample_record, rules):
    record = replace(sample_record, description="PS AI helper")
    matches = classify_repository(record, rules)
    assert "photoshop" not in matches
    assert "illustrator" not in matches

def test_one_repository_can_match_all_professional_categories(sample_record, rules):
    record = replace(sample_record, description="平面设计：Photoshop 修图与 Illustrator 矢量插画")
    assert set(classify_repository(record, rules)) == {"art_design", "photoshop", "illustrator"}
```

- [ ] **Step 2: Run the classifier tests and verify failure**

Run: `python -m pytest tests/test_classifier.py -v`

Expected: FAIL because `classify_repository` does not exist.

- [ ] **Step 3: Implement normalization, field weighting, exclusions, and reasons**

```python
def classify_repository(
    record: RepositoryRecord,
    rules: Mapping[str, CategoryRule],
) -> dict[str, CategoryMatch]:
    fields = {
        "name": record.full_name.casefold(),
        "description": record.description.casefold(),
        "topics": " ".join(record.topics).casefold(),
        "path": " ".join(record.skill_paths).casefold(),
        "content": record.skill_text.casefold(),
    }
    # Score each unique term once per field. Strong terms add strong_bonus.
    # Weak terms score only when at least one context term exists anywhere.
    # Exclusion terms suppress only the affected category.
    # Return only matches whose score reaches the configured threshold.
```

Reasons must be deterministic strings such as `简介命中“photoshop” (+7)` sorted by field order then term. Match terms case-insensitively; for ASCII terms of two characters or fewer require token boundaries so substrings do not trigger.

- [ ] **Step 4: Add tests for Chinese terms, exclusions, deduplication, and stable reasons**

```python
def test_repeated_term_scores_once_per_field(sample_record, rules):
    record = replace(sample_record, description="Photoshop photoshop PHOTOSHOP")
    match = classify_repository(record, rules)["photoshop"]
    assert match.reasons.count('简介命中“photoshop” (+7)') == 1

def test_exclusion_suppresses_category(sample_record, rules):
    blocked = replace(rules["photoshop"], exclude_terms=("stock price",))
    record = replace(sample_record, description="Photoshop stock price")
    assert "photoshop" not in classify_repository(record, {"photoshop": blocked})
```

- [ ] **Step 5: Run classifier tests**

Run: `python -m pytest tests/test_classifier.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the classifier**

```powershell
git add src/skill_radar/classifier.py tests/test_classifier.py config/categories.yml
git commit -m "feat: add explainable skill classification"
```

### Task 3: Snapshot Growth and Stable Rankings

**Files:**
- Create: `src/skill_radar/ranking.py`
- Create: `tests/test_ranking.py`

**Interfaces:**
- Consumes: current `Sequence[RepositoryRecord]`, previous `Mapping[int, int]` of repository ID to Stars, and classification matches by repository ID.
- Produces: `calculate_growth(current_stars: int, previous_stars: int | None) -> int | None`; `build_rankings(records, previous_stars, matches) -> Rankings`.

- [ ] **Step 1: Write failing baseline, growth, tie, and list-size tests**

```python
def test_first_run_is_baseline(records, matches):
    result = build_rankings(records, {}, matches)
    assert result.is_baseline is True
    assert [item.stars for item in result.overall] == sorted(
        (record.stars for record in records), reverse=True
    )[:10]

def test_later_run_uses_net_growth(records, matches):
    previous = {record.repo_id: record.stars - record.repo_id for record in records}
    result = build_rankings(records, previous, matches)
    assert result.is_baseline is False
    assert [item.weekly_growth for item in result.overall] == sorted(
        (record.repo_id for record in records), reverse=True
    )[:10]

def test_cross_list_duplicates_are_allowed(design_photoshop_record, matches):
    result = build_rankings([design_photoshop_record], {design_photoshop_record.repo_id: 0}, matches)
    assert result.art_design[0].repo_id == result.photoshop[0].repo_id
```

- [ ] **Step 2: Run ranking tests and verify failure**

Run: `python -m pytest tests/test_ranking.py -v`

Expected: FAIL because `skill_radar.ranking` does not exist.

- [ ] **Step 3: Implement ranking rules exactly**

```python
LIMITS = {"overall": 10, "art_design": 10, "photoshop": 5, "illustrator": 5}

def calculate_growth(current_stars: int, previous_stars: int | None) -> int | None:
    return None if previous_stars is None else current_stars - previous_stars

def _sort_key(item: RankedRepository, baseline: bool) -> tuple:
    unknown_growth = not baseline and item.weekly_growth is None
    primary = item.stars if baseline else int(item.weekly_growth or 0)
    updated_epoch = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00")).timestamp()
    return (unknown_growth, -primary, -item.stars, -updated_epoch, item.full_name.casefold())
```

`build_rankings` must treat the whole run as baseline only when no previous repository Star map exists. A newly discovered repository in a later run has `weekly_growth=None` and sorts after repositories with known growth; it is still eligible so it can appear when a list lacks enough known-growth items. Category lists filter only on an actual `CategoryMatch` and slice to their configured limit.

- [ ] **Step 4: Add tests for negative growth, new candidates, and deterministic ordering**

```python
def test_negative_growth_is_preserved(record, matches):
    result = build_rankings([record], {record.repo_id: record.stars + 3}, matches)
    assert result.overall[0].weekly_growth == -3

def test_name_breaks_complete_tie(record_factory, matches):
    records = [record_factory(full_name="z/repo"), record_factory(full_name="a/repo")]
    ranked = build_rankings(records, {r.repo_id: r.stars for r in records}, matches)
    assert [item.full_name for item in ranked.overall] == ["a/repo", "z/repo"]
```

- [ ] **Step 5: Run ranking tests**

Run: `python -m pytest tests/test_ranking.py -v`

Expected: PASS.

- [ ] **Step 6: Commit ranking behavior**

```powershell
git add src/skill_radar/ranking.py tests/test_ranking.py
git commit -m "feat: calculate weekly skill rankings"
```

### Task 4: Authenticated GitHub REST Client

**Files:**
- Create: `src/skill_radar/github.py`
- Create: `tests/fixtures/search_page_1.json`
- Create: `tests/fixtures/search_page_2.json`
- Create: `tests/fixtures/repository.json`
- Create: `tests/fixtures/skill_content.json`
- Create: `tests/test_github.py`

**Interfaces:**
- Consumes: a non-empty public-read token and an optional injected `requests.Session`, clock, sleep function, and random function.
- Produces: `GitHubClient`; `GitHubError`, `GitHubAuthError`, `GitHubNotFound`, and `GitHubRateLimitError`; methods `search_code(query: str, max_pages: int = 10) -> list[SearchHit]`, `get_repository(full_name: str) -> RepositoryMetadata`, and `get_text_file(full_name: str, path: str, ref: str) -> tuple[str, str]` returning decoded UTF-8 text and content SHA.

- [ ] **Step 1: Write failing header, pagination, and decoding tests**

```python
def test_client_sends_required_headers(fake_session):
    client = GitHubClient("public-token", session=fake_session)
    client.search_code("filename:SKILL.md", max_pages=1)
    request = fake_session.requests[0]
    assert request.headers["Authorization"] == "Bearer public-token"
    assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert request.headers["User-Agent"] == "weekly-github-skill-radar"

def test_search_code_paginates_and_deduplicates(fake_session):
    client = GitHubClient("public-token", session=fake_session)
    hits = client.search_code("filename:SKILL.md", max_pages=10)
    assert [(hit.full_name, hit.path) for hit in hits] == [
        ("owner/one", "SKILL.md"), ("owner/two", ".agents/SKILL.md")
    ]

def test_get_text_file_decodes_base64(fake_session):
    text, sha = GitHubClient("public-token", session=fake_session).get_text_file(
        "owner/one", "SKILL.md", "main"
    )
    assert text.startswith("---\nname:")
    assert sha == "fixture-sha"
```

- [ ] **Step 2: Run client tests and verify failure**

Run: `python -m pytest tests/test_github.py -v`

Expected: FAIL because `skill_radar.github` does not exist.

- [ ] **Step 3: Implement requests, pagination, timeouts, and typed failures**

```python
class GitHubClient:
    API_ROOT = "https://api.github.com"

    def __init__(self, token, session=None, sleep=time.sleep, random_value=random.random):
        if not token or not token.strip():
            raise GitHubAuthError("缺少 PUBLIC_GITHUB_TOKEN")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "weekly-github-skill-radar",
        })
```

Use `(5, 30)` connect/read timeouts. Map 401 to `GitHubAuthError`; 404 to `GitHubNotFound`; retry 429, 500, 502, 503, and 504 up to three times with `min(60, 2 ** (attempt - 1) + random_value())`, where the first retry has `attempt == 1`. For 403/429 with `x-ratelimit-remaining: 0`, wait until `x-ratelimit-reset` only when the delay is at most 300 seconds; otherwise raise `GitHubRateLimitError`. `search_code` uses `per_page=100` and stops at the first short page, 10 pages, or the smaller configured `max_pages`, respecting GitHub's 1,000-result cap. Never include request headers or token text in exception messages.

- [ ] **Step 4: Add deterministic retry and rate-limit tests**

```python
def test_retries_server_error_with_injected_sleep(fake_session):
    sleeps = []
    fake_session.queue_statuses(503, 200)
    GitHubClient("public-token", session=fake_session, sleep=sleeps.append, random_value=lambda: 0).search_code(
        "filename:SKILL.md", max_pages=1
    )
    assert sleeps == [1]

def test_does_not_leak_token_on_auth_failure(fake_session):
    fake_session.queue_statuses(401)
    with pytest.raises(GitHubAuthError) as error:
        GitHubClient("secret-value", session=fake_session).search_code("filename:SKILL.md", 1)
    assert "secret-value" not in str(error.value)
```

- [ ] **Step 5: Run client tests**

Run: `python -m pytest tests/test_github.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the API client**

```powershell
git add src/skill_radar/github.py tests/test_github.py tests/fixtures
git commit -m "feat: add resilient GitHub API client"
```

### Task 5: Candidate Discovery and Repository Collection

**Files:**
- Create: `src/skill_radar/discovery.py`
- Create: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `GitHubClient`, search queries, `Mapping[int, Candidate]`, previous `Snapshot`, category-cache permission, and ISO-8601 run timestamp.
- Produces: `discover_candidates(client, queries, existing, now, max_pages=10) -> dict[int, Candidate]`; `collect_repositories(client, candidates, previous_snapshot, allow_cached_classification, now, max_skill_files=5, max_content_bytes=524288) -> CollectionResult` where `CollectionResult` contains `records`, updated `candidates`, and warning strings.

- [ ] **Step 1: Write failing discovery merge and lifecycle tests**

```python
def test_discovery_merges_queries_paths_and_existing(fake_client, existing_candidates):
    result = discover_candidates(fake_client, ["filename:SKILL.md", "design filename:SKILL.md"], existing_candidates, NOW)
    candidate = result[101]
    assert candidate.skill_paths == (".agents/SKILL.md", "SKILL.md")
    assert candidate.active is True
    assert candidate.last_seen_at == NOW

def test_temporary_failure_keeps_candidate_active(fake_client, candidate, empty_snapshot):
    fake_client.fail_repository_with(GitHubError("temporary"))
    result = collect_repositories(fake_client, {candidate.repo_id: candidate}, empty_snapshot, False, NOW)
    assert result.candidates[candidate.repo_id].active is True
    assert result.records == ()
    assert "temporary" in result.warnings[0]

def test_confirmed_missing_skill_marks_candidate_inactive(fake_client, candidate, empty_snapshot):
    fake_client.all_known_paths_missing_and_repo_search_empty()
    result = collect_repositories(fake_client, {candidate.repo_id: candidate}, empty_snapshot, False, NOW)
    assert result.candidates[candidate.repo_id].active is False
```

- [ ] **Step 2: Run discovery tests and verify failure**

Run: `python -m pytest tests/test_discovery.py -v`

Expected: FAIL because `skill_radar.discovery` does not exist.

- [ ] **Step 3: Implement query merge and bounded collection**

```python
def discover_candidates(client, queries, existing, now, max_pages=10):
    merged = dict(existing)
    for query in queries:
        for hit in client.search_code(query, max_pages=max_pages):
            current = merged.get(hit.repo_id)
            paths = set(current.skill_paths if current else ())
            paths.add(hit.path)
            merged[hit.repo_id] = Candidate(
                repo_id=hit.repo_id,
                full_name=hit.full_name,
                url=hit.repo_url,
                skill_paths=tuple(sorted(paths)),
                discovered_at=current.discovered_at if current else now,
                last_seen_at=now,
                last_checked_at=current.last_checked_at if current else "",
                active=True,
            )
    return merged
```

Fetch repository metadata for every active candidate. When `allow_cached_classification` is true, repository `updated_at` equals its previous `SnapshotEntry.updated_at`, and cached paths/hash/matches exist, return a record with cached paths/hash, empty `skill_text`, and `content_reused=True`; this refreshes Stars without downloading unchanged Skill content. Otherwise fetch at most five sorted Skill paths and set `content_reused=False`. If every stored path returns 404, call `search_code(f"repo:{full_name} filename:SKILL.md", max_pages=1)` once to detect a moved file. Decode up to 524,288 bytes total per repository, concatenate files with the literal marker `<!-- path: {skill_path} -->`, calculate SHA-256 over paths plus text, and return one `RepositoryRecord` per repository. A 404 repository or confirmed empty per-repository search marks inactive; transport/auth/rate failures never mark inactive. Authentication and unrecoverable rate-limit failures propagate as fatal.

- [ ] **Step 4: Add tests for content cap, moved paths, and repository deduplication**

```python
def test_collects_one_record_for_multiple_skill_files(fake_client, multi_path_candidate, empty_snapshot):
    result = collect_repositories(fake_client, {1: multi_path_candidate}, empty_snapshot, False, NOW)
    assert len(result.records) == 1
    assert result.records[0].skill_paths == ("a/SKILL.md", "b/SKILL.md")

def test_moved_skill_path_is_rediscovered(fake_client, stale_candidate, empty_snapshot):
    fake_client.move_skill("old/SKILL.md", "new/SKILL.md")
    result = collect_repositories(fake_client, {1: stale_candidate}, empty_snapshot, False, NOW)
    assert result.records[0].skill_paths == ("new/SKILL.md",)

def test_reuses_classification_when_repository_is_unchanged(fake_client, candidate, cached_snapshot):
    result = collect_repositories(fake_client, {1: candidate}, cached_snapshot, True, NOW)
    assert result.records[0].content_reused is True
    assert fake_client.text_file_calls == []
```

- [ ] **Step 5: Run discovery tests**

Run: `python -m pytest tests/test_discovery.py -v`

Expected: PASS.

- [ ] **Step 6: Commit discovery and collection**

```powershell
git add src/skill_radar/discovery.py tests/test_discovery.py
git commit -m "feat: discover and collect skill repositories"
```

### Task 6: Versioned State, Atomic Writes, and Chinese Reports

**Files:**
- Create: `src/skill_radar/storage.py`
- Create: `src/skill_radar/report.py`
- Create: `data/candidates.json`
- Create: `data/snapshot.json`
- Create: `LATEST.md`
- Create: `tests/test_storage.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: candidates, repository records, matches, rankings, run statistics, report date, and paths.
- Produces: `load_candidates(path: Path) -> dict[int, Candidate]`; `load_snapshot(path: Path) -> Snapshot`; `prepare_outputs(root: Path, generated_at: datetime, classification_config_sha256: str, previous_snapshot: Snapshot, collection: CollectionResult, matches: Mapping[int, Mapping[str, CategoryMatch]], rankings: Rankings) -> PreparedOutputs`; `commit_outputs(outputs: PreparedOutputs) -> None`; `render_report(rankings: Rankings, stats: RunStats, generated_at: datetime) -> str`.

- [ ] **Step 1: Write failing schema and atomicity tests**

```python
def test_empty_bootstrap_files_load_as_version_one(tmp_path):
    assert load_candidates(tmp_path / "missing-candidates.json") == {}
    assert load_snapshot(tmp_path / "missing-snapshot.json").stars_by_repo == {}

def test_rejects_unknown_schema_version(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text('{"schema_version": 99, "stars_by_repo": {}}', encoding="utf-8")
    with pytest.raises(StateError, match="schema_version"):
        load_snapshot(path)

def test_prepare_does_not_touch_final_files_until_commit(tmp_path, prepared_inputs):
    latest = tmp_path / "LATEST.md"
    latest.write_text("old", encoding="utf-8")
    outputs = prepare_outputs(root=tmp_path, **prepared_inputs)
    assert latest.read_text(encoding="utf-8") == "old"
    commit_outputs(outputs)
    assert latest.read_text(encoding="utf-8").startswith("# GitHub Skill 每周热门榜")

def test_prepare_preserves_last_entry_after_temporary_repository_failure(tmp_path, prepared_inputs):
    inputs = dict(prepared_inputs)
    failed_repo_id = str(inputs.pop("temporarily_failed_repo_id"))
    previous = inputs["previous_snapshot"]
    outputs = prepare_outputs(root=tmp_path, **inputs)
    snapshot_json = json.loads(outputs.files[tmp_path / "data/snapshot.json"])
    assert snapshot_json["repositories"][failed_repo_id] == previous.repositories[int(failed_repo_id)].to_dict()
```

- [ ] **Step 2: Write failing report-content tests**

```python
def test_baseline_report_is_clearly_marked(baseline_rankings, stats):
    report = render_report(baseline_rankings, stats, BEIJING_NOW)
    assert "基线初始化，非周增长榜" in report
    assert "全站热门 Skill Top 10" in report

def test_professional_entry_shows_reason(growth_rankings, stats):
    report = render_report(growth_rankings, stats, BEIJING_NOW)
    assert "分类依据" in report
    assert "简介命中“photoshop”" in report
    assert "SKILL.md" in report
```

- [ ] **Step 3: Run storage/report tests and verify failure**

Run: `python -m pytest tests/test_storage.py tests/test_report.py -v`

Expected: FAIL because storage and reporting modules do not exist.

- [ ] **Step 4: Implement schema-versioned JSON and prepared atomic outputs**

```python
@dataclass(frozen=True)
class PreparedOutputs:
    files: Mapping[Path, str]
    report_path: Path

def commit_outputs(outputs: PreparedOutputs) -> None:
    staged: list[tuple[Path, Path]] = []
    originals: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for final_path, content in outputs.files.items():
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = final_path.with_name(f".{final_path.name}.tmp")
            temp_path.write_text(content, encoding="utf-8", newline="\n")
            staged.append((temp_path, final_path))
        for temp_path, final_path in staged:
            originals[final_path] = final_path.read_bytes() if final_path.exists() else None
            temp_path.replace(final_path)
            replaced.append(final_path)
    except Exception:
        for final_path in reversed(replaced):
            original = originals[final_path]
            if original is None:
                final_path.unlink(missing_ok=True)
            else:
                rollback = final_path.with_name(f".{final_path.name}.rollback")
                rollback.write_bytes(original)
                rollback.replace(final_path)
        raise
    finally:
        for temp_path, _ in staged:
            temp_path.unlink(missing_ok=True)
```

JSON must use `ensure_ascii=False`, `indent=2`, `sort_keys=True`, and a trailing newline. Store dates as UTC ISO-8601 strings. `data/candidates.json` bootstraps with an empty `candidates` collection. `data/snapshot.json` bootstraps with empty `repositories` and `classification_config_sha256` values, all under `schema_version: 1`. Each snapshot repository entry persists Stars, repository update time, Skill paths, content fingerprint, classification matches, and last successful check time.

- [ ] **Step 5: Implement deterministic Markdown rendering**

Render report header fields for Beijing report date, observation window, generation time, discovered candidates, active candidates, collected records, skipped records, warnings, and baseline status. Each list uses a Markdown table with rank, repository link, weekly growth (`—` on baseline/new candidate), total Stars, description, Skill paths, category reasons when applicable, and repository update time. Escape pipes and newlines in all external GitHub text. Render `本期无符合条件的项目` for an empty professional list and include warning summaries at the end.

- [ ] **Step 6: Run storage/report tests**

Run: `python -m pytest tests/test_storage.py tests/test_report.py -v`

Expected: PASS.

- [ ] **Step 7: Commit persistence and reports**

```powershell
git add src/skill_radar/storage.py src/skill_radar/report.py tests/test_storage.py tests/test_report.py data LATEST.md
git commit -m "feat: persist snapshots and render weekly reports"
```

### Task 7: Transactional Pipeline and Command-Line Entry Point

**Files:**
- Create: `src/skill_radar/pipeline.py`
- Create: `src/skill_radar/cli.py`
- Create: `src/skill_radar/__main__.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: project root, `PUBLIC_GITHUB_TOKEN`, injected `GitHubClient`, and optional clock.
- Produces: `run_pipeline(root: Path, token: str, now: datetime | None = None, client: GitHubClient | None = None) -> RunResult`; `main(argv: Sequence[str] | None = None) -> int`.

- [ ] **Step 1: Write failing successful-pipeline and fatal-failure tests**

```python
def test_pipeline_writes_all_outputs_after_success(project_root, fake_client):
    result = run_pipeline(project_root, "public-token", now=BEIJING_NOW, client=fake_client)
    assert result.report_path == project_root / "reports/2026-08-10.md"
    assert result.report_path.read_text(encoding="utf-8") == (project_root / "LATEST.md").read_text(encoding="utf-8")
    assert json.loads((project_root / "data/snapshot.json").read_text(encoding="utf-8"))["captured_at"]

def test_fatal_discovery_failure_preserves_previous_outputs(project_root, fatal_client):
    before = read_output_bytes(project_root)
    with pytest.raises(GitHubAuthError):
        run_pipeline(project_root, "bad-token", now=BEIJING_NOW, client=fatal_client)
    assert read_output_bytes(project_root) == before
```

- [ ] **Step 2: Run pipeline tests and verify failure**

Run: `python -m pytest tests/test_pipeline.py -v`

Expected: FAIL because `run_pipeline` does not exist.

- [ ] **Step 3: Implement the orchestration order and validation gate**

```python
def run_pipeline(root, token, now=None, client=None):
    now = now or datetime.now(timezone.utc)
    client = client or GitHubClient(token)
    category_path = root / "config/categories.yml"
    rules = load_category_rules(category_path)
    config_sha = hashlib.sha256(category_path.read_bytes()).hexdigest()
    queries = load_search_queries(root / "config/search_queries.yml")
    old_candidates = load_candidates(root / "data/candidates.json")
    old_snapshot = load_snapshot(root / "data/snapshot.json")
    candidates = discover_candidates(client, queries, old_candidates, now.isoformat())
    allow_cached = old_snapshot.classification_config_sha256 == config_sha
    collection = collect_repositories(client, candidates, old_snapshot, allow_cached, now.isoformat())
    matches = {
        record.repo_id: (
            old_snapshot.repositories[record.repo_id].category_matches
            if record.content_reused
            else classify_repository(record, rules)
        )
        for record in collection.records
    }
    rankings = build_rankings(collection.records, old_snapshot.stars_by_repo, matches)
    outputs = prepare_outputs(root, now, config_sha, old_snapshot, collection, matches, rankings)
    validate_outputs(outputs, rankings)
    commit_outputs(outputs)
    return RunResult(
        report_path=outputs.report_path,
        collected_count=len(collection.records),
        warning_count=len(collection.warnings),
        is_baseline=rankings.is_baseline,
    )
```

`prepare_outputs` must create the new `Snapshot` from `config_sha`, `previous_snapshot`, and the current records/matches. Start with previous entries for candidates that remain active but had a temporary per-repository failure, replace every successfully collected entry, and remove entries for candidates confirmed inactive. This preserves the last known Star baseline for a skipped repository. Define `RunResult(report_path: Path, collected_count: int, warning_count: int, is_baseline: bool)`. `validate_outputs` must verify UTF-8, all four headings, correct maximum counts, unique repositories within each list, identical history/`LATEST.md` content, schema version 1, and that the new snapshot includes every successfully collected active repository. It runs before `commit_outputs`.

- [ ] **Step 4: Write failing CLI token and exit-code tests**

```python
def test_cli_requires_public_token(monkeypatch, capsys):
    monkeypatch.delenv("PUBLIC_GITHUB_TOKEN", raising=False)
    assert main(["--root", "."]) == 2
    captured = capsys.readouterr()
    assert "PUBLIC_GITHUB_TOKEN" in captured.err

def test_cli_does_not_echo_token_on_failure(monkeypatch, capsys):
    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "never-print-this")
    assert main(["--root", ".", "--simulate-auth-failure"]) == 1
    assert "never-print-this" not in capsys.readouterr().err
```

Do not add `--simulate-auth-failure` to production behavior; inject a failing pipeline callable in the test through a monkeypatched module function while passing only `--root`.

- [ ] **Step 5: Implement CLI and module entry point**

Use standard-library `argparse` with `--root` defaulting to the current directory. Read only `PUBLIC_GITHUB_TOKEN` from the environment. Return exit code `2` for missing configuration and `1` for runtime failure. Print a concise success summary with the report path and counts; sanitize exceptions so token values and request headers cannot appear. `src/skill_radar/__main__.py` must call `raise SystemExit(main())`.

- [ ] **Step 6: Run pipeline and CLI tests**

Run: `python -m pytest tests/test_pipeline.py tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 7: Run the complete unit suite**

Run: `python -m pytest -v`

Expected: all tests PASS without making live network requests.

- [ ] **Step 8: Commit orchestration and CLI**

```powershell
git add src/skill_radar/pipeline.py src/skill_radar/cli.py src/skill_radar/__main__.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat: orchestrate weekly skill radar runs"
```

### Task 8: GitHub Actions, Deployment Documentation, and Final Verification

**Files:**
- Create: `.github/workflows/weekly-ranking.yml`
- Create: `README.md`
- Modify: `LATEST.md`
- Test: all files under `tests/`

**Interfaces:**
- Consumes: the `python -m skill_radar --root .` command and repository Secret `PUBLIC_GITHUB_TOKEN`.
- Produces: a scheduled/manual GitHub workflow and end-user deployment guide.

- [ ] **Step 1: Add the weekly workflow with minimal current-repository permission**

```yaml
name: Weekly GitHub Skill Ranking

on:
  workflow_dispatch:
  schedule:
    - cron: "0 0 * * 1"

concurrency:
  group: weekly-skill-ranking
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  update-ranking:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install
        run: python -m pip install --disable-pip-version-check -e ".[test]"
      - name: Test
        run: python -m pytest -v
      - name: Generate ranking
        env:
          PUBLIC_GITHUB_TOKEN: ${{ secrets.PUBLIC_GITHUB_TOKEN }}
        run: python -m skill_radar --root .
      - name: Commit generated files
        run: |
          if git diff --quiet -- LATEST.md reports data; then
            echo "No generated changes"
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -- LATEST.md reports data
          git commit -m "chore: update weekly skill rankings"
          git push
```

Do not expose `PUBLIC_GITHUB_TOKEN` outside the generation step. Do not add `pull_request_target`. The workflow must fail naturally if the Secret is missing or if `git push` conflicts.

- [ ] **Step 2: Validate the workflow structure locally**

Run:

```powershell
@'
from pathlib import Path
import yaml
data = yaml.safe_load(Path('.github/workflows/weekly-ranking.yml').read_text(encoding='utf-8'))
assert data['permissions'] == {'contents': 'write'}
assert data[True]['schedule'][0]['cron'] == '0 0 * * 1'
assert 'workflow_dispatch' in data[True]
print('workflow validation passed')
'@ | python -
```

Expected: `workflow validation passed`. PyYAML parses the unquoted YAML key `on` as boolean `True`, hence `data[True]` in this validation only.

- [ ] **Step 3: Write the complete deployment and operating guide**

README sections must include:

1. What each of the four rankings means and the first-run baseline limitation.
2. How to create a GitHub access token limited to public read access with no private-repository or write permission.
3. How to add it under repository **Settings → Secrets and variables → Actions** as `PUBLIC_GITHUB_TOKEN`.
4. Why `PUBLIC_GITHUB_TOKEN` and built-in `GITHUB_TOKEN` are separate.
5. How to push the project, open **Actions**, and manually run **Weekly GitHub Skill Ranking** for baseline creation.
6. The Monday 08:00 Beijing schedule and GitHub's warning that scheduled workflows can be delayed and run only from the default branch.
7. Local setup using Python 3.12, installation, `PUBLIC_GITHUB_TOKEN` environment variable, tests, and `python -m skill_radar --root .`.
8. How to edit `config/categories.yml` and `config/search_queries.yml`.
9. Report/state file meanings and why generated state should not be edited manually.
10. Troubleshooting for missing/invalid token, rate limits, fewer than requested professional results, push conflicts, and public-repository scheduled workflows being disabled after 60 days without activity.
11. Security warning never to commit `.env` or a token and how to rotate a leaked token.

- [ ] **Step 4: Add a pre-run notice that explains first use**

`LATEST.md` before the first live run must say the project has not established a baseline yet and instruct the owner to configure `PUBLIC_GITHUB_TOKEN` and manually trigger the workflow. It must not contain fabricated ranking data.

- [ ] **Step 5: Run all verification checks**

Run:

```powershell
python -m pytest -v
python -m compileall -q src
git diff --check
git status --short
```

Expected: all tests PASS, compilation exits 0, `git diff --check` emits no errors, and status lists only intended Task 8 files before commit.

- [ ] **Step 6: Perform a deterministic offline end-to-end run**

Use the fixed fake GitHub client from `tests/test_pipeline.py` through its integration fixture and run:

```powershell
python -m pytest tests/test_pipeline.py::test_pipeline_writes_all_outputs_after_success -v
```

Expected: PASS and assertions confirm the dated report, `LATEST.md`, candidate index, and snapshot are produced as one successful transaction.

- [ ] **Step 7: Commit the automation and documentation**

```powershell
git add .github/workflows/weekly-ranking.yml README.md LATEST.md
git commit -m "feat: automate weekly GitHub skill reports"
```

- [ ] **Step 8: Verify the clean completed branch**

Run:

```powershell
python -m pytest -v
git status --short
git log --oneline -10
```

Expected: all tests PASS, working tree is clean, and the log contains one focused commit for each implementation task.

## Live Deployment Check (performed after pushing to GitHub)

This check requires repository-level external state and is not part of offline implementation completion:

1. Add `PUBLIC_GITHUB_TOKEN` as an Actions Secret with public read-only access.
2. Run **Weekly GitHub Skill Ranking** manually.
3. Confirm the workflow creates a baseline report and commits `LATEST.md`, `reports/<date>.md`, `data/candidates.json`, and `data/snapshot.json`.
4. Confirm the log contains no token value and reports the number of discovered and collected repositories.
5. Leave the weekly schedule enabled on the default branch.
