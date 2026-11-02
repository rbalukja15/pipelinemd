"""GitHub REST access on the standard library, mirroring `gitlab.http`.

The differences are GitHub's: a bearer token, an API version header, `Link`
pagination, rate limits reported as a 403 or 429, and a job log served by a
redirect to a short-lived signed URL on another host. The token must not
follow that redirect, so it is sent as an unredirected header: urllib copies
every other header to the new host.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from ..errors import GitHubAuthError, GitHubError, GitHubNotFoundError, RateLimitError
from ..gitlab.http import DEFAULT_RETRIES, DEFAULT_TIMEOUT, USER_AGENT

API_VERSION = "2022-11-28"
JSON = "application/vnd.github+json"
RAW = "application/vnd.github.raw+json"
RETRY_STATUSES = frozenset({500, 502, 503, 504})
#: Waiting longer than this for a rate limit to reset is worse than saying so.
MAX_RATE_LIMIT_WAIT = 60.0

_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')

Response = tuple[int, str, dict[str, str]]


class GitHubHttp:
    """Authenticated GitHub API access with bounded retries."""

    def __init__(
        self,
        api_url: str,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = 1.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        # ProxyHandler with no arguments reads HTTP(S)_PROXY from the
        # environment; the default chain also follows redirects.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler())

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = path if "://" in path else f"{self.api_url}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}{'&' if '?' in url else '?'}{query}"
        return url

    def _build(self, url: str, accept: str) -> urllib.request.Request:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "X-GitHub-Api-Version": API_VERSION,
            },
            method="GET",
        )
        if self.token:
            # Never forwarded to the host a redirect points at (a log's signed URL).
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return request

    def _raise_for_status(self, status: int, body: str, url: str) -> None:
        detail = body.strip()[:400]
        if status in (401, 403):
            raise GitHubAuthError(
                f"GitHub refused the request ({status}). Check the token: it needs read "
                f"access to Actions (`actions: read` for GITHUB_TOKEN). {detail}",
                status=status,
            )
        if status == 404:
            raise GitHubNotFoundError(
                f"Not found (404): {url}. Either it does not exist or the token cannot "
                f"see it. {detail}",
                status=status,
            )
        raise GitHubError(f"GitHub returned {status} for {url}. {detail}", status=status)

    def _rate_limit_wait(self, status: int, headers: dict[str, str]) -> float | None:
        """Seconds to wait when a 403/429 is a rate limit, None when it is not one.

        Raises when the reset is too far off to wait for.
        """
        if status not in (403, 429):
            return None
        retry_after = headers.get("retry-after")
        remaining = headers.get("x-ratelimit-remaining")
        if retry_after is None and remaining != "0":
            return None
        if retry_after is not None:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = MAX_RATE_LIMIT_WAIT + 1
        else:
            try:
                reset = float(headers.get("x-ratelimit-reset", ""))
            except ValueError:
                reset = time.time() + MAX_RATE_LIMIT_WAIT + 1
            wait = max(0.0, reset - time.time())
            if wait > MAX_RATE_LIMIT_WAIT:
                when = datetime.fromtimestamp(reset, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
                raise RateLimitError(
                    f"GitHub's API rate limit is used up until {when}. Pass a token "
                    "(GH_TOKEN or GITHUB_TOKEN) for a higher limit, or try again then.",
                    status=status,
                )
        if wait > MAX_RATE_LIMIT_WAIT:
            raise RateLimitError(
                f"GitHub asked to wait {wait:.0f}s before retrying (rate limit).",
                status=status,
            )
        return wait

    def _get(self, url: str, accept: str = JSON) -> Response:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self._opener.open(self._build(url, accept), timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    return int(response.status), body, headers
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
                wait = self._rate_limit_wait(exc.code, headers)
                if attempt == self.retries or (wait is None and exc.code not in RETRY_STATUSES):
                    self._raise_for_status(exc.code, body, url)
                time.sleep(wait if wait is not None else self.backoff * (2**attempt))
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise GitHubError(f"Could not reach {url}: {exc}") from exc
                time.sleep(self.backoff * (2**attempt))
                last_error = exc
        raise GitHubError(f"Giving up on {url} after {self.retries} retries: {last_error}")

    @staticmethod
    def _json(body: str, url: str) -> Any:
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GitHubError(f"Expected JSON from {url}, got: {body[:200]!r}") from exc

    # -- public API --------------------------------------------------------

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path, params)
        _status, body, _headers = self._get(url)
        return self._json(body, url)

    def get_text(
        self, path: str, params: dict[str, Any] | None = None, *, accept: str = JSON
    ) -> str:
        return self._get(self._url(path, params), accept)[1]

    def paginate(
        self, path: str, key: str, params: dict[str, Any] | None = None, *, per_page: int = 100
    ) -> Iterator[dict[str, Any]]:
        """Every item under ``key`` across pages, following the `Link` header."""
        url: str | None = self._url(path, dict(params or {}, per_page=per_page))
        while url:
            _status, body, headers = self._get(url)
            payload = self._json(body, url)
            items = payload.get(key) if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise GitHubError(f"Expected a list under {key!r} from {url}")
            yield from items
            match = _NEXT.search(headers.get("link", ""))
            url = match.group(1) if match else None
