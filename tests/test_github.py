import json
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from skill_radar.github import GitHubAuthError, GitHubClient, GitHubNotFound, GitHubRateLimitError


FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    params: dict[str, Any] | None
    headers: dict[str, str]
    timeout: tuple[int, int]


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any], headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    """A deterministic Session boundary with real GitHub REST response fixtures."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requests: list[RecordedRequest] = []
        self._statuses: deque[tuple[int, dict[str, str]]] = deque()
        first_page = self._fixture("search_page_1.json")
        # GitHub uses a 100-item page to signal that another page may exist.
        # Repeating a complete fixture record lets the client exercise that
        # boundary while the expected result verifies its public deduplication.
        first_page["items"] = [deepcopy(first_page["items"][0]) for _ in range(100)]
        self._search_pages = {
            1: first_page,
            2: self._fixture("search_page_2.json"),
        }
        self._repository = self._fixture("repository.json")
        self._content = self._fixture("skill_content.json")

    @staticmethod
    def _fixture(name: str) -> dict[str, Any]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def queue_statuses(self, *statuses: int, headers: dict[str, str] | None = None) -> None:
        self._statuses.extend((status, dict(headers or {})) for status in statuses)

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: tuple[int, int],
    ) -> FakeResponse:
        self.requests.append(RecordedRequest("GET", url, params, dict(self.headers), timeout))
        status, headers = self._statuses.popleft() if self._statuses else (200, {})
        if "/search/code" in url:
            payload = self._search_pages.get((params or {}).get("page", 1), {"total_count": 0, "incomplete_results": False, "items": []})
        elif "/contents/" in url:
            payload = self._content
        else:
            payload = self._repository
        return FakeResponse(status, payload, headers)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


def test_client_sends_required_headers_and_timeout(fake_session):
    """Catches unauthenticated requests or a missing bounded network timeout."""
    GitHubClient("public-token", session=fake_session).search_code("filename:SKILL.md", max_pages=1)

    request = fake_session.requests[0]
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["Authorization"] == "Bearer public-token"
    assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert request.headers["User-Agent"] == "weekly-github-skill-radar"
    assert request.timeout == (5, 30)


def test_search_code_paginates_deduplicates_and_uses_per_page_100(fake_session):
    """Catches page loss, duplicate hits, or a search request that misses GitHub's largest page size."""
    hits = GitHubClient("public-token", session=fake_session).search_code("filename:SKILL.md", max_pages=10)

    assert [(hit.full_name, hit.path) for hit in hits] == [
        ("owner/one", "SKILL.md"),
        ("owner/two", ".agents/SKILL.md"),
    ]
    assert [request.params for request in fake_session.requests] == [
        {"q": "filename:SKILL.md", "page": 1, "per_page": 100},
        {"q": "filename:SKILL.md", "page": 2, "per_page": 100},
    ]


def test_search_stops_after_the_first_short_page(fake_session):
    """Catches needless requests after GitHub has returned fewer than one page of results."""
    GitHubClient("public-token", session=fake_session).search_code("filename:SKILL.md", max_pages=10)

    assert len(fake_session.requests) == 2


def test_search_limits_max_pages_and_rejects_non_positive_values(fake_session):
    """Catches caller page limits being ignored or invalid limits issuing requests."""
    client = GitHubClient("public-token", session=fake_session)
    assert len(client.search_code("filename:SKILL.md", max_pages=1)) == 1
    assert len(fake_session.requests) == 1
    with pytest.raises(ValueError):
        client.search_code("filename:SKILL.md", max_pages=0)


def test_get_repository_maps_the_complete_rest_response(fake_session):
    """Catches repository fields being mapped from the wrong REST keys."""
    repository = GitHubClient("public-token", session=fake_session).get_repository("owner/one")

    assert repository.repo_id == 101
    assert repository.full_name == "owner/one"
    assert repository.url == "https://github.com/owner/one"
    assert repository.description == "A complete test repository response."
    assert repository.topics == ("skills", "automation")
    assert repository.stars == 42
    assert repository.updated_at == "2026-08-08T00:00:00Z"
    assert repository.default_branch == "main"


def test_get_text_file_decodes_base64_and_sends_ref(fake_session):
    """Catches returning encoded API content or dropping the requested revision."""
    text, sha = GitHubClient("public-token", session=fake_session).get_text_file("owner/one", "SKILL.md", "main")

    assert text == "---\nname: fixture-skill\n---\nHello, skill!\n"
    assert sha == "fixture-sha"
    assert fake_session.requests[0].params == {"ref": "main"}


def test_retries_server_error_with_injected_sleep(fake_session):
    """Catches retry delays that cannot be tested deterministically or omit transient 5xx retries."""
    sleeps: list[float] = []
    fake_session.queue_statuses(503, 200)

    GitHubClient("public-token", session=fake_session, sleep=sleeps.append, random_value=lambda: 0).search_code(
        "filename:SKILL.md", max_pages=1
    )

    assert sleeps == [1]
    assert len(fake_session.requests) == 2


def test_rate_limit_waits_only_when_reset_is_within_five_minutes(fake_session):
    """Catches rate-limit handling that sleeps for an unbounded reset time."""
    sleeps: list[float] = []
    fake_session.queue_statuses(403, 200, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1120"})

    GitHubClient("public-token", session=fake_session, clock=lambda: 1000, sleep=sleeps.append).search_code(
        "filename:SKILL.md", max_pages=1
    )

    assert sleeps == [120]
    assert len(fake_session.requests) == 2


def test_rate_limit_beyond_five_minutes_raises_without_sleep(fake_session):
    """Catches indefinitely delayed jobs after a rate-limit response."""
    fake_session.queue_statuses(429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1401"})

    with pytest.raises(GitHubRateLimitError):
        GitHubClient("public-token", session=fake_session, clock=lambda: 1000, sleep=lambda _: None).search_code(
            "filename:SKILL.md", max_pages=1
        )


def test_auth_and_not_found_failures_are_typed_and_do_not_leak_token(fake_session):
    """Catches status failures losing their category or echoing authorization credentials."""
    fake_session.queue_statuses(401)
    with pytest.raises(GitHubAuthError) as error:
        GitHubClient("secret-value", session=fake_session).search_code("filename:SKILL.md", 1)
    assert "secret-value" not in str(error.value)

    fake_session.queue_statuses(404)
    with pytest.raises(GitHubNotFound):
        GitHubClient("public-token", session=fake_session).get_repository("owner/missing")


def test_blank_token_is_rejected_before_any_request(fake_session):
    """Catches accidental unauthenticated GitHub calls when configuration is empty."""
    with pytest.raises(GitHubAuthError):
        GitHubClient("   ", session=fake_session)
    assert fake_session.requests == []
