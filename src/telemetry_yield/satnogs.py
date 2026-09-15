"""Small, read-only SatNOGS HTTP client with conditional caching."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import random
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


SATNOGS_NETWORK_API = "https://network.satnogs.org/api/"
SATNOGS_DB_API = "https://db.satnogs.org/api/"


class SatNOGSError(RuntimeError):
    """Base error for the read-only SatNOGS client."""


class SatNOGSHTTPError(SatNOGSError):
    def __init__(self, status: int, url: str, message: str = "") -> None:
        suffix = f": {message}" if message else ""
        super().__init__(f"SatNOGS GET {url} failed with HTTP {status}{suffix}")
        self.status = status
        self.url = url


class SatNOGSResponseTooLarge(SatNOGSError):
    """Raised before retaining a response larger than the configured cap."""


@dataclass(frozen=True)
class CachedHTTPResponse:
    url: str
    body: bytes
    etag: str | None
    last_modified: str | None
    content_type: str | None
    fetched_at: str


@dataclass(frozen=True)
class SatNOGSResponse:
    url: str
    body: bytes
    status: int
    headers: Mapping[str, str]
    from_cache: bool = False


Transport = Callable[..., Any]


def _validated_api_token(value: str) -> str:
    token = value.strip()
    if (
        not token
        or token != value
        or len(token) > 256
        or any(character.isspace() for character in token)
        or not token.isascii()
    ):
        raise ValueError("a valid explicit SatNOGS API token is required")
    return token


def read_api_token_file(path: str | os.PathLike[str]) -> str:
    """Read a private token file without following links or exposing its value."""

    token_path = Path(path)
    try:
        before = token_path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError("SatNOGS API token path must be a regular file")
        descriptor = os.open(
            token_path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ValueError("cannot securely open SatNOGS API token file") from exc
    try:
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("SatNOGS API token file changed while opening")
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("SatNOGS API token path must be a regular file")
        if opened.st_uid != os.geteuid():
            raise ValueError("SatNOGS API token file must be owned by this user")
        if stat.S_IMODE(opened.st_mode) & 0o077:
            raise ValueError("SatNOGS API token file permissions must be 0600 or stricter")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            body = stream.read(4097)
        if len(body) > 4096:
            raise ValueError("SatNOGS API token file is unexpectedly large")
        try:
            value = body.decode("ascii").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise ValueError("SatNOGS API token file must contain ASCII") from exc
        return _validated_api_token(value)
    finally:
        os.close(descriptor)


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    """Reject an off-origin redirect before urllib follows it."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base = urlparse(base_url)

    def redirect_request(
        self,
        request: Any,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Any:
        parsed = urlparse(new_url)
        if (parsed.scheme, parsed.netloc) != (self._base.scheme, self._base.netloc):
            raise SatNOGSError("SatNOGS redirect left the configured HTTPS origin")
        if not parsed.path.startswith(self._base.path):
            raise SatNOGSError("SatNOGS redirect left the configured API root")
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


class SatNOGSClient:
    """Bounded GET-only client.

    ``user_agent`` is intentionally required and has no implicit default.  An
    application should provide a project name and a suitable contact channel.
    The injected ``transport`` hook exists so unit tests never need a network.
    """

    _global_lock = threading.Lock()
    _origin_semaphores: dict[str, threading.BoundedSemaphore] = {}

    def __init__(
        self,
        *,
        user_agent: str,
        api_token: str | None = None,
        base_url: str = SATNOGS_NETWORK_API,
        cache_dir: str | os.PathLike[str] | None = None,
        max_concurrency: int = 1,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_jitter_seconds: float = 0.25,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 5_000_000,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
        shared_rate_limit_path: str | os.PathLike[str] | None = None,
        shared_minimum_interval_seconds: float = 0.0,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        user_agent = user_agent.strip()
        if not user_agent:
            raise ValueError("an explicit non-empty User-Agent is required")
        if "\r" in user_agent or "\n" in user_agent:
            raise ValueError("User-Agent must not contain newlines")
        if max_concurrency not in {1, 2}:
            raise ValueError("max_concurrency must be 1 or 2")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("timeouts and response limits must be positive")
        if (
            not math.isfinite(shared_minimum_interval_seconds)
            or shared_minimum_interval_seconds < 0
        ):
            raise ValueError("shared request interval must be finite and non-negative")
        if shared_minimum_interval_seconds and shared_rate_limit_path is None:
            raise ValueError("a shared rate-limit path is required for a positive interval")

        parsed_base = urlparse(base_url)
        if parsed_base.scheme != "https" or not parsed_base.netloc:
            raise ValueError("base_url must be an absolute HTTPS URL")
        normalized_path = parsed_base.path.rstrip("/") + "/"
        self.base_url = parsed_base._replace(path=normalized_path).geturl()
        self.user_agent = user_agent
        self._authorization = (
            f"Token {_validated_api_token(api_token)}"
            if api_token is not None
            else None
        )
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_jitter_seconds = backoff_jitter_seconds
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._redirect_handler = _SameOriginRedirectHandler(self.base_url)
        self._transport = transport or build_opener(self._redirect_handler).open
        self._sleep = sleep
        self._random = random_source
        self._wall_time = wall_time
        self._shared_rate_limit_path = (
            Path(shared_rate_limit_path)
            if shared_rate_limit_path is not None
            else None
        )
        self._shared_minimum_interval_seconds = shared_minimum_interval_seconds
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        with self._global_lock:
            self._global_semaphore = self._origin_semaphores.setdefault(
                origin, threading.BoundedSemaphore(2)
            )

    @property
    def authentication_mode(self) -> str:
        """Return a provenance-safe mode label; never return the credential."""

        return "satnogs-token" if self._authorization is not None else "anonymous"

    def _wait_for_shared_rate_slot(self) -> None:
        """Serialize an anonymous API quota across independent local processes."""

        path = self._shared_rate_limit_path
        interval = self._shared_minimum_interval_seconds
        if path is None or interval == 0:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="ascii") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw = handle.read().strip()
                if raw:
                    try:
                        last_request_at = float(raw)
                    except ValueError as exc:
                        raise SatNOGSError(
                            f"invalid shared rate-limit state: {path}"
                        ) from exc
                    if not math.isfinite(last_request_at):
                        raise SatNOGSError(
                            f"invalid shared rate-limit state: {path}"
                        )
                    current = self._wall_time()
                    elapsed = max(0.0, current - last_request_at)
                    remaining = interval - elapsed
                    if remaining > 0:
                        self._sleep(remaining)
                current = self._wall_time()
                if not math.isfinite(current):
                    raise SatNOGSError("wall clock returned a non-finite value")
                handle.seek(0)
                handle.truncate()
                handle.write(f"{current:.9f}\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _validate_response_url(self, url: str) -> None:
        base = urlparse(self.base_url)
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            raise SatNOGSError("SatNOGS redirect left the configured HTTPS origin")
        if not parsed.path.startswith(base.path):
            raise SatNOGSError("SatNOGS redirect left the configured API root")

    def _build_url(
        self, path: str, params: Mapping[str, object] | None
    ) -> str:
        if not path:
            raise ValueError("path must not be empty")
        candidate = urljoin(self.base_url, path)
        base = urlparse(self.base_url)
        parsed = urlparse(candidate)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            raise ValueError("cross-origin SatNOGS URLs are not allowed")
        if not parsed.path.startswith(base.path):
            raise ValueError("path must remain below the configured API root")
        if params:
            query = urlencode(params, doseq=True)
            separator = "&" if parsed.query else "?"
            candidate = candidate + separator + query
        return candidate

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _load_cache(self, url: str) -> CachedHTTPResponse | None:
        cache_path = self._cache_path(url)
        if cache_path is None or not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if payload["url"] != url:
                return None
            return CachedHTTPResponse(
                url=url,
                body=base64.b64decode(payload["body_base64"], validate=True),
                etag=payload.get("etag"),
                last_modified=payload.get("last_modified"),
                content_type=payload.get("content_type"),
                fetched_at=payload["fetched_at"],
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            return None

    def _store_cache(self, entry: CachedHTTPResponse) -> None:
        cache_path = self._cache_path(entry.url)
        if cache_path is None:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": entry.url,
            "body_base64": base64.b64encode(entry.body).decode("ascii"),
            "etag": entry.etag,
            "last_modified": entry.last_modified,
            "content_type": entry.content_type,
            "fetched_at": entry.fetched_at,
        }
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cache_path.parent,
                prefix=".satnogs-cache-",
                delete=False,
            ) as cache_file:
                temporary = Path(cache_file.name)
                json.dump(payload, cache_file, sort_keys=True)
                cache_file.write("\n")
                cache_file.flush()
                os.fsync(cache_file.fileno())
            os.replace(temporary, cache_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _headers_dict(headers: Any) -> dict[str, str]:
        if headers is None:
            return {}
        if hasattr(headers, "items"):
            return {str(key): str(value) for key, value in headers.items()}
        return {}

    @staticmethod
    def _header_value(headers: Mapping[str, str], name: str) -> str | None:
        target = name.casefold()
        return next(
            (value for key, value in headers.items() if key.casefold() == target),
            None,
        )

    def _read_limited(self, response: Any, url: str) -> bytes:
        body = response.read(self.max_response_bytes + 1)
        if len(body) > self.max_response_bytes:
            raise SatNOGSResponseTooLarge(
                f"SatNOGS response exceeded {self.max_response_bytes} bytes: {url}"
            )
        return body

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    target = parsedate_to_datetime(retry_after)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    return max(
                        0.0,
                        (target - datetime.now(timezone.utc)).total_seconds(),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
        return self.backoff_base_seconds * (2**attempt) + (
            self.backoff_jitter_seconds * self._random()
        )

    def get(
        self, path: str, *, params: Mapping[str, object] | None = None
    ) -> SatNOGSResponse:
        """Perform a conditional GET; no mutating HTTP methods are exposed."""

        url = self._build_url(path, params)
        cached = self._load_cache(url)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self._authorization is not None:
            headers["Authorization"] = self._authorization
        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        request = Request(url, headers=headers, method="GET")

        with self._global_semaphore, self._semaphore:
            for attempt in range(self.max_retries + 1):
                response: Any | None = None
                try:
                    self._wait_for_shared_rate_slot()
                    response = self._transport(request, timeout=self.timeout_seconds)
                    if hasattr(response, "geturl"):
                        self._validate_response_url(str(response.geturl()))
                    status = getattr(response, "status", None)
                    if status is None:
                        status = response.getcode()
                    response_headers = self._headers_dict(
                        getattr(response, "headers", None)
                    )
                    if status == 304:
                        if cached is None:
                            raise SatNOGSHTTPError(304, url, "cache entry missing")
                        return SatNOGSResponse(
                            url=url,
                            body=cached.body,
                            status=200,
                            headers={
                                "ETag": cached.etag or "",
                                "Last-Modified": cached.last_modified or "",
                                "Content-Type": cached.content_type or "",
                            },
                            from_cache=True,
                        )
                    if status == 429 or 500 <= status <= 599:
                        if attempt < self.max_retries:
                            self._sleep(
                                self._retry_delay(
                                    attempt,
                                    self._header_value(
                                        response_headers, "Retry-After"
                                    ),
                                )
                            )
                            continue
                    if not 200 <= status <= 299:
                        raise SatNOGSHTTPError(status, url)
                    body = self._read_limited(response, url)
                    entry = CachedHTTPResponse(
                        url=url,
                        body=body,
                        etag=self._header_value(response_headers, "ETag"),
                        last_modified=self._header_value(
                            response_headers, "Last-Modified"
                        ),
                        content_type=self._header_value(
                            response_headers, "Content-Type"
                        ),
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._store_cache(entry)
                    return SatNOGSResponse(
                        url=url,
                        body=body,
                        status=status,
                        headers=response_headers,
                    )
                except HTTPError as exc:
                    try:
                        if exc.code == 304 and cached is not None:
                            return SatNOGSResponse(
                                url=url,
                                body=cached.body,
                                status=200,
                                headers=self._headers_dict(exc.headers),
                                from_cache=True,
                            )
                        if (exc.code == 429 or 500 <= exc.code <= 599) and (
                            attempt < self.max_retries
                        ):
                            exc_headers = self._headers_dict(exc.headers)
                            self._sleep(
                                self._retry_delay(
                                    attempt,
                                    self._header_value(
                                        exc_headers, "Retry-After"
                                    ),
                                )
                            )
                            continue
                        raise SatNOGSHTTPError(
                            exc.code, url, str(exc.reason)
                        ) from exc
                    finally:
                        exc.close()
                except (URLError, TimeoutError) as exc:
                    if attempt < self.max_retries:
                        self._sleep(self._retry_delay(attempt, None))
                        continue
                    raise SatNOGSError(f"SatNOGS GET failed: {url}: {exc}") from exc
                finally:
                    if response is not None and hasattr(response, "close"):
                        response.close()
        raise AssertionError("retry loop exited unexpectedly")

    def get_json(
        self, path: str, *, params: Mapping[str, object] | None = None
    ) -> Any:
        response = self.get(path, params=params)
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SatNOGSError(f"invalid JSON from {response.url}") from exc

    def get_observation(self, observation_id: int) -> Any:
        if observation_id <= 0:
            raise ValueError("observation_id must be positive")
        return self.get_json("observations/", params={"id": observation_id})
