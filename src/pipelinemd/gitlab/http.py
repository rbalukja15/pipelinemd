"""A very small JSON/text HTTP client built on the standard library.

pipelinemd's core deliberately has no third-party dependencies so it can be
``pip install``ed into a runner image without pulling a tree behind it. The
GitLab REST calls it makes are simple enough that urllib is sufficient - all
this adds is auth headers, retries with backoff, and page following.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..errors import AuthError, GitLabError, NotFoundError

USER_AGENT = "pipelinemd/0.1 (+https://github.com/rbalukja15/pipelinemd)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3


@dataclass(slots=True)
class HttpClient:
    """Authenticated GitLab API access with bounded retries."""

    base_url: str
    token: str | None = None
    token_header: str = "PRIVATE-TOKEN"
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    backoff: float = 1.0
    _opener: urllib.request.OpenerDirector = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # ProxyHandler with no arguments reads HTTP(S)_PROXY from the
        # environment, which is what a runner behind a proxy needs.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler())

    # -- internals ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.token:
            headers[self.token_header] = self.token
        return headers

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url.rstrip('/')}/api/v4/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"
        return url

    def _raise_for_status(self, status: int, body: str, url: str) -> None:
        detail = body.strip()[:400]
        if status in (401, 403):
            raise AuthError(
                f"GitLab refused the request ({status}). Check the token's validity "
                f"and scopes (needs `read_api`). {detail}",
                status=status,
            )
        if status == 404:
            raise NotFoundError(
                f"Not found (404): {url}. Either it does not exist or the token "
                f"cannot see it. {detail}",
                status=status,
            )
        raise GitLabError(f"GitLab returned {status} for {url}. {detail}", status=status)

    def _request(self, url: str) -> tuple[int, str, dict[str, str]]:
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    return int(response.status), body, headers
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code not in RETRY_STATUSES or attempt == self.retries:
                    self._raise_for_status(exc.code, body, url)
                # Honour Retry-After when the server sends one.
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = _parse_retry_after(retry_after)
                time.sleep(delay if delay is not None else self.backoff * (2**attempt))
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise GitLabError(f"Could not reach {url}: {exc}") from exc
                time.sleep(self.backoff * (2**attempt))
                last_error = exc

        raise GitLabError(f"Giving up on {url} after {self.retries} retries: {last_error}")

    # -- public API --------------------------------------------------------

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path, params)
        status, body, _headers = self._request(url)
        if status >= 400:
            self._raise_for_status(status, body, url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GitLabError(f"Expected JSON from {url}, got: {body[:200]!r}") from exc

    def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = self._url(path, params)
        status, body, _headers = self._request(url)
        if status >= 400:
            self._raise_for_status(status, body, url)
        return body

    def paginate(
        self, path: str, params: dict[str, Any] | None = None, *, per_page: int = 100
    ) -> Iterator[dict[str, Any]]:
        """Yield every item across pages, following GitLab's X-Next-Page header."""
        page: str | int = 1
        while page:
            merged = dict(params or {}, per_page=per_page, page=page)
            url = self._url(path, merged)
            status, body, headers = self._request(url)
            if status >= 400:
                self._raise_for_status(status, body, url)
            try:
                items = json.loads(body)
            except json.JSONDecodeError as exc:
                raise GitLabError(f"Expected JSON from {url}") from exc
            if not isinstance(items, list):
                raise GitLabError(f"Expected a list from {url}, got {type(items).__name__}")
            yield from items
            page = headers.get("x-next-page", "").strip()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        # Cap it: a server suggesting a very long wait should not hang the CLI.
        return min(float(value), 30.0)
    except ValueError:
        return None
