import json
import traceback
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

from skill_radar.github import GitHubAuthError, GitHubClient, GitHubError, GitHubNotFound, GitHubRateLimitError


FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    params: dict[str, Any] | None
    headers: dict[str, str]
    timeout: tuple[int, int]


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        json_error: ValueError | None = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._json_error = json_error

    def json(self) -> dict[str, Any]:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    """A deterministic Session boundary with real GitHub REST response fixtures."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requests: list[RecordedRequest] = []
        self._statuses: deque[tuple[int, dict[str, str]]] = deque()
        self._exceptions: deque[requests.RequestException] = deque()
        self._json_errors: deque[ValueError] = deque()
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

    def queue_exceptions(self, *exceptions: requests.RequestException) -> None:
        self._exceptions.extend(exceptions)

    def queue_json_errors(self, *errors: ValueError) -> None:
        self._json_errors.extend(errors)

    def use_wrapped_content_fixture(self) -> None:
        self._content = self._fixture("skill_content_wrapped.json")

    def use_full_search_pages(self, count: int) -> None:
        template = self._search_pages[1]["items"][0]
        self._search_pages = {}
        for page in range(1, count + 1):
            items = []
            for offset in range(100):
                repo_id = (page - 1) * 100 + offset + 1
                item = deepcopy(template)
                item["path"] = f"skills/{repo_id}/SKILL.md"
                item["repository"]["id"] = repo_id
                item["repository"]["full_name"] = f"owner/repo-{repo_id}"
                item["repository"]["html_url"] = f"https://github.com/owner/repo-{repo_id}"
                items.append(item)
            self._search_pages[page] = {"total_count": count * 100, "incomplete_results": False, "items": items}

    def use_repeated_full_search_pages(self, count: int) -> None:
        template = self._search_pages[1]["items"][0]
        self._search_pages = {
            page: {"total_count": count * 100, "incomplete_results": False, "items": [deepcopy(template) for _ in range(100)]}
            for page in range(1, count + 1)
        }

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: tuple[int, int],
    ) -> FakeResponse:
        self.requests.append(RecordedRequest("GET", url, params, dict(self.headers), timeout))
        if self._exceptions:
            raise self._exceptions.popleft()
        status, headers = self._statuses.popleft() if self._statuses else (200, {})
        if "/search/code" in url:
            payload = self._search_pages.get((params or {}).get("page", 1), {"total_count": 0, "incomplete_results": False, "items": []})
        elif "/contents/" in url:
            payload = self._content
        else:
            payload = self._repository
        json_error = self._json_errors.popleft() if self._json_errors else None
        return FakeResponse(status, payload, headers, json_error)


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


@pytest.mark.parametrize(
    ("private", "visibility"),
    [(True, "public"), (False, "internal")],
)
def test_search_code_rejects_non_public_repository_hits(fake_session, private, visibility):
    """Catches a broadly scoped token admitting a non-public code-search result."""
    item = deepcopy(fake_session._search_pages[1]["items"][0])
    item["repository"]["private"] = private
    item["repository"]["visibility"] = visibility
    fake_session._search_pages = {
        1: {"total_count": 1, "incomplete_results": False, "items": [item]},
    }

    hits = GitHubClient("public-token", session=fake_session).search_code(
        "filename:SKILL.md", max_pages=1
    )

    assert hits == []


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


@pytest.mark.parametrize(
    ("private", "visibility"),
    [(True, "public"), (False, "private")],
)
def test_get_repository_rejects_non_public_metadata(fake_session, private, visibility):
    """Catches non-public metadata crossing the public-only client boundary."""
    fake_session._repository["private"] = private
    fake_session._repository["visibility"] = visibility

    with pytest.raises(GitHubError) as error:
        GitHubClient("public-token", session=fake_session).get_repository("owner/one")

    assert type(error.value).__name__ == "GitHubPublicOnlyError"
    assert "owner/one" not in str(error.value)


def test_get_text_file_decodes_base64_and_sends_ref(fake_session):
    """Catches returning encoded API content or dropping the requested revision."""
    text, sha = GitHubClient("public-token", session=fake_session).get_text_file("owner/one", "SKILL.md", "main")

    assert text == "---\nname: fixture-skill\n---\nHello, skill!\n"
    assert sha == "fixture-sha"
    assert fake_session.requests[0].params == {"ref": "main"}


def test_get_text_file_decodes_github_wrapped_base64(fake_session):
    """Catches strict Base64 decoding that rejects GitHub's line-wrapped content."""
    fake_session.use_wrapped_content_fixture()

    text, sha = GitHubClient("public-token", session=fake_session).get_text_file("owner/one", "SKILL.md", "main")

    assert text == "---\nname: fixture-skill\n---\nHello, skill!\n"
    assert sha == "fixture-sha"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retries_each_transient_status_with_injected_sleep(fake_session, status):
    """Catches a transient status missing the deterministic retry policy."""
    sleeps: list[float] = []
    fake_session.queue_statuses(status, 200)

    GitHubClient("public-token", session=fake_session, sleep=sleeps.append, random_value=lambda: 0).search_code(
        "filename:SKILL.md", max_pages=1
    )

    assert sleeps == [1]
    assert len(fake_session.requests) == 2


def test_retries_network_failure_with_injected_sleep(fake_session):
    """Catches approved transport retries being accidentally removed."""
    sleeps: list[float] = []
    fake_session.queue_exceptions(requests.ConnectionError("temporary network failure"))

    GitHubClient("public-token", session=fake_session, sleep=sleeps.append, random_value=lambda: 0).search_code(
        "filename:SKILL.md", max_pages=1
    )

    assert sleeps == [1]
    assert len(fake_session.requests) == 2


def test_exhausted_transient_statuses_raise_their_contract_error_without_terminal_sleep(fake_session):
    """Catches a fourth retry or the wrong exception class after all transient attempts fail."""
    sleeps: list[float] = []
    fake_session.queue_statuses(429, 429, 429, 429)

    with pytest.raises(GitHubRateLimitError):
        GitHubClient("public-token", session=fake_session, sleep=sleeps.append, random_value=lambda: 0).search_code(
            "filename:SKILL.md", max_pages=1
        )

    assert sleeps == [1, 2, 4]
    assert len(fake_session.requests) == 4


def test_exhausted_server_error_raises_generic_error_without_terminal_sleep(fake_session):
    """Catches retries beyond the three permitted retry delays for 5xx failures."""
    sleeps: list[float] = []
    fake_session.queue_statuses(503, 503, 503, 503)

    with pytest.raises(GitHubError):
        GitHubClient("public-token", session=fake_session, sleep=sleeps.append, random_value=lambda: 0).search_code(
            "filename:SKILL.md", max_pages=1
        )

    assert sleeps == [1, 2, 4]
    assert len(fake_session.requests) == 4


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


def test_exhausted_rate_limit_reset_does_not_sleep_after_terminal_response(fake_session):
    """Catches sleeping after the retry budget has already been exhausted."""
    sleeps: list[float] = []
    fake_session.queue_statuses(
        403, 403, 403, 403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1120"},
    )

    with pytest.raises(GitHubRateLimitError):
        GitHubClient("public-token", session=fake_session, clock=lambda: 1000, sleep=sleeps.append).search_code(
            "filename:SKILL.md", max_pages=1
        )

    assert sleeps == [120, 120, 120]
    assert len(fake_session.requests) == 4


def test_sanitized_transport_failure_has_no_secret_context(fake_session):
    """Catches a hidden transport exception retaining Authorization data through __context__."""
    token = "secret" + "-value"
    transport_message = f"Authorization: Bearer {token}"
    fake_session.queue_exceptions(*(requests.ConnectionError(transport_message) for _ in range(4)))
    with pytest.raises(GitHubError) as transport_error:
        GitHubClient(token, session=fake_session, sleep=lambda _: None).search_code("filename:SKILL.md", 1)
    assert transport_error.value.__cause__ is None
    assert transport_error.value.__context__ is None
    assert "secret-value" not in str(transport_error.value)
    assert "secret-value" not in "".join(traceback.format_exception(transport_error.value))


def test_sanitized_json_failure_has_no_raw_body_context(fake_session):
    """Catches a JSON decoder exception retaining its untrusted raw response through __context__."""
    token = "secret" + "-value"
    raw_body = "raw-body" + "-secret"
    fake_session.queue_json_errors(ValueError(f'{{"message":"{raw_body}"}}'))
    with pytest.raises(GitHubError) as json_error:
        GitHubClient(token, session=fake_session).search_code("filename:SKILL.md", 1)
    assert json_error.value.__cause__ is None
    assert json_error.value.__context__ is None
    formatted = "".join(traceback.format_exception(json_error.value))
    assert raw_body not in str(json_error.value)
    assert raw_body not in formatted
    assert token not in formatted


def test_search_caps_requests_at_ten_pages_and_results_at_one_thousand(fake_session):
    """Catches bypassing GitHub's ten-page, one-thousand-result search boundary."""
    fake_session.use_full_search_pages(11)

    hits = GitHubClient("public-token", session=fake_session).search_code("filename:SKILL.md", max_pages=99)

    assert len(hits) == 1000
    assert [request.params["page"] for request in fake_session.requests] == list(range(1, 11))


def test_search_does_not_let_deduplication_bypass_the_ten_page_cap(fake_session):
    """Catches allowing more than ten full pages when duplicate hits keep the result count below 1,000."""
    fake_session.use_repeated_full_search_pages(11)

    hits = GitHubClient("public-token", session=fake_session).search_code("filename:SKILL.md", max_pages=99)

    assert len(hits) == 1
    assert [request.params["page"] for request in fake_session.requests] == list(range(1, 11))


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
