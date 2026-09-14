from __future__ import annotations

import email.utils
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx


ALLOWED_HOST_SUFFIXES = ("nseindia.com", "niftyindices.com")
DEFAULT_CONTENT_TYPES = frozenset(
    {
        "application/gzip",
        "application/octet-stream",
        "application/x-gzip",
        "application/x-zip-compressed",
        "application/zip",
        "text/csv",
        "text/plain",
    }
)


class OfficialSourceError(RuntimeError):
    """Sanitized provider error safe for operator and API summaries."""


class OfficialSourceAccessDenied(OfficialSourceError):
    pass


class OfficialSourceValidationError(OfficialSourceError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadedArtifact:
    content: bytes
    source_url: str
    fetched_at: datetime
    content_type: str


def validate_official_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise OfficialSourceValidationError("Official source URLs must use HTTPS")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in ALLOWED_HOST_SUFFIXES):
        raise OfficialSourceValidationError("Official source host is not allowed")
    if parsed.username or parsed.password:
        raise OfficialSourceValidationError("Credentials are not allowed in source URLs")


def _retry_after_seconds(value: str | None, *, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, min(60.0, float(value)))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, min(60.0, (retry_at - now).total_seconds()))


class OfficialHttpClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        minimum_spacing_seconds: float = 1.0,
        maximum_bytes: int = 16 * 1024 * 1024,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("max_attempts must be between 1 and 5")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.minimum_spacing_seconds = minimum_spacing_seconds
        self.maximum_bytes = maximum_bytes
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={
                "User-Agent": "AlphaDesk/0.4 personal-research official-EOD-ingestion",
                "Accept": "text/csv,application/zip,application/gzip,application/octet-stream",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OfficialHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _space_request(self) -> None:
        now = self.monotonic()
        if self._last_request_at is not None:
            remaining = self.minimum_spacing_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.monotonic()

    def fetch(
        self,
        url: str,
        *,
        allowed_content_types: frozenset[str] = DEFAULT_CONTENT_TYPES,
    ) -> DownloadedArtifact:
        validate_official_url(url)
        current_url = url
        redirects = 0
        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            self._space_request()
            try:
                with self._client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirects >= 3:
                            raise OfficialSourceValidationError("Invalid or excessive source redirect")
                        current_url = urljoin(current_url, location)
                        validate_official_url(current_url)
                        redirects += 1
                        attempt -= 1
                        continue
                    if response.status_code == 403:
                        raise OfficialSourceAccessDenied(
                            "Official source denied automated access; use local artifact import"
                        )
                    if response.status_code == 429:
                        if attempt >= self.max_attempts:
                            raise OfficialSourceError("Official source rate limit persisted")
                        delay = _retry_after_seconds(
                            response.headers.get("retry-after"), now=datetime.now(UTC)
                        )
                        self.sleep(delay if delay is not None else float(2 ** (attempt - 1)))
                        continue
                    if response.status_code >= 500:
                        if attempt >= self.max_attempts:
                            raise OfficialSourceError("Official source remained unavailable")
                        self.sleep(float(2 ** (attempt - 1)))
                        continue
                    if response.status_code != 200:
                        raise OfficialSourceError(
                            f"Official source returned HTTP {response.status_code}"
                        )

                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            exceeds_limit = int(declared_size) > self.maximum_bytes
                        except ValueError as exc:
                            raise OfficialSourceValidationError(
                                "Official source returned an invalid content length"
                            ) from exc
                        if exceeds_limit:
                            raise OfficialSourceValidationError(
                                "Artifact exceeds the response-size ceiling"
                            )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in allowed_content_types:
                        raise OfficialSourceValidationError("Unexpected official artifact content type")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.maximum_bytes:
                            raise OfficialSourceValidationError(
                                "Artifact exceeds the response-size ceiling"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content:
                        raise OfficialSourceValidationError("Official artifact is empty")
                    return DownloadedArtifact(
                        content=content,
                        source_url=current_url,
                        fetched_at=datetime.now(UTC),
                        content_type=content_type,
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.max_attempts:
                    raise OfficialSourceError("Official source request timed out or failed") from exc
                self.sleep(float(2 ** (attempt - 1)))
        raise OfficialSourceError("Official source fetch attempts exhausted")
