"""Authenticated, deterministic-boundary access to the GitHub REST API."""

from __future__ import annotations

import base64
import random
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from .models import RepositoryMetadata, SearchHit


class GitHubError(RuntimeError):
    """A GitHub REST operation could not be completed."""


class GitHubAuthError(GitHubError):
    """The public-read GitHub token is missing or was rejected."""


class GitHubNotFound(GitHubError):
    """GitHub could not find the requested public resource."""


class GitHubRateLimitError(GitHubError):
    """GitHub's rate-limit reset is too far away for this job to wait."""


class GitHubClient:
    API_ROOT = "https://api.github.com"
    TIMEOUT = (5, 30)
    MAX_RETRIES = 3
    MAX_RATE_LIMIT_WAIT_SECONDS = 300

    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if not token or not token.strip():
            raise GitHubAuthError("缺少 PUBLIC_GITHUB_TOKEN")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "weekly-github-skill-radar",
            }
        )
        self._clock = clock
        self._sleep = sleep
        self._random_value = random_value

    def search_code(self, query: str, max_pages: int = 10) -> list[SearchHit]:
        """Search public code and return each repository/path pair at most once."""
        if max_pages < 1:
            raise ValueError("max_pages must be positive")

        hits: list[SearchHit] = []
        seen: set[tuple[int, str]] = set()
        page_limit = min(max_pages, 10)
        for page in range(1, page_limit + 1):
            payload = self._get_json(
                "/search/code",
                {"q": query, "page": page, "per_page": 100},
            )
            items = payload.get("items", [])
            for item in items:
                repository = item["repository"]
                key = (int(repository["id"]), str(item["path"]))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    SearchHit(
                        repo_id=key[0],
                        full_name=str(repository["full_name"]),
                        repo_url=str(repository["html_url"]),
                        path=key[1],
                    )
                )
                if len(hits) >= 1000:
                    return hits
            if len(items) < 100:
                break
        return hits

    def get_repository(self, full_name: str) -> RepositoryMetadata:
        """Fetch the public repository metadata used in rankings."""
        payload = self._get_json(f"/repos/{full_name}")
        return RepositoryMetadata(
            repo_id=int(payload["id"]),
            full_name=str(payload["full_name"]),
            url=str(payload["html_url"]),
            description=str(payload.get("description") or ""),
            topics=tuple(str(topic) for topic in payload.get("topics", [])),
            stars=int(payload["stargazers_count"]),
            updated_at=str(payload["updated_at"]),
            default_branch=str(payload["default_branch"]),
        )

    def get_text_file(self, full_name: str, path: str, ref: str) -> tuple[str, str]:
        """Fetch and decode a UTF-8 file from a named repository revision."""
        payload = self._get_json(f"/repos/{full_name}/contents/{path}", {"ref": ref})
        try:
            encoded = "".join(character for character in payload["content"] if character not in " \t\r\n\f\v")
            content = base64.b64decode(encoded, validate=True)
            return content.decode("utf-8"), str(payload["sha"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError):
            raise GitHubError("GitHub returned invalid file content") from None

    def _get_json(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.API_ROOT}{path}"
        for retry_attempt in range(self.MAX_RETRIES + 1):
            request_failed = False
            try:
                response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            except requests.RequestException:
                request_failed = True
            if request_failed:
                if retry_attempt == self.MAX_RETRIES:
                    raise GitHubError("GitHub request failed")
                self._sleep(self._retry_delay(retry_attempt + 1))
                continue

            status = response.status_code
            if status == 401:
                raise GitHubAuthError("GitHub rejected the public-read token")
            if status == 404:
                raise GitHubNotFound("GitHub resource was not found")
            if status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
                if retry_attempt == self.MAX_RETRIES:
                    raise GitHubRateLimitError("GitHub rate limit did not recover")
                self._wait_for_rate_limit(response.headers)
                continue
            if status == 429:
                if retry_attempt == self.MAX_RETRIES:
                    raise GitHubRateLimitError("GitHub rate limit did not recover")
                self._sleep(self._retry_delay(retry_attempt + 1))
                continue
            if status in (500, 502, 503, 504):
                if retry_attempt == self.MAX_RETRIES:
                    raise GitHubError(f"GitHub request failed with status {status}")
                self._sleep(self._retry_delay(retry_attempt + 1))
                continue
            if status >= 400:
                raise GitHubError(f"GitHub request failed with status {status}")
            invalid_json = False
            try:
                payload = response.json()
            except ValueError:
                invalid_json = True
            if invalid_json:
                raise GitHubError("GitHub returned invalid JSON")
            return payload
        raise GitHubError("GitHub request failed")

    def _retry_delay(self, attempt: int) -> float:
        return min(60, 2 ** (attempt - 1) + self._random_value())

    def _wait_for_rate_limit(self, headers: Mapping[str, str]) -> None:
        try:
            reset_at = float(headers["x-ratelimit-reset"])
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubRateLimitError("GitHub rate limit reset time is unavailable") from error
        delay = max(0.0, reset_at - self._clock())
        if delay > self.MAX_RATE_LIMIT_WAIT_SECONDS:
            raise GitHubRateLimitError("GitHub rate limit reset is too far away")
        self._sleep(delay)
