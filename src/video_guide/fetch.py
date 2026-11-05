"""Allow-listed, size-capped HTTPS GET for the guide.

This is the only component that opens an HTTP connection itself. It refuses any
non-HTTPS URL, any host outside the allow-list (YouTube's feed/caption/channel
hosts plus the hosts of feeds the user explicitly added), and any response
larger than the byte cap. The transport is injectable so tests stay fully
offline.

YouTube caption tracks are not fetched here. Those go through a user-installed
``yt-dlp`` in ``captions.py``, subtitle flags only.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

_YOUTUBE_HOSTS: frozenset[str] = frozenset({"www.youtube.com", "youtube.com", "m.youtube.com"})
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BYTES = 5 * 1024 * 1024


class FetchError(Exception):
    """A fetch was refused by policy or failed at the transport level."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class Fetcher:
    """Perform allow-listed HTTPS GETs, aborting oversized downloads."""

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str] = frozenset(),
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._allowed = _YOUTUBE_HOSTS | allowed_hosts
        self._transport = transport
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    def get(self, url: str) -> bytes:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise FetchError(f"refusing non-https url: {url!r}")
        host = (parts.hostname or "").lower()
        if host not in self._allowed:
            raise FetchError(f"host not allow-listed: {host!r}")
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code != 200:
                    raise FetchError(
                        f"unexpected status {response.status_code} for {url!r}",
                        status_code=response.status_code,
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise FetchError(f"response exceeds {self._max_bytes} bytes: {url!r}")
                return bytes(body)
        except httpx.HTTPError as exc:
            raise FetchError(f"request failed for {url!r}: {exc}") from exc
