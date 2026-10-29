"""HTTP access to the FCY Payment Processor API, enforced read-only twice:

1. `ReadOnlyClient` only has a `get` method, and only for allowlisted paths.
2. The transport underneath refuses any request that is not an allowlisted GET, so
   even code that reaches the inner httpx client cannot send a write.

The same credential can call transfer-funds and deposit-funds, so these checks are
the control until the bank issues a credential that is read-only on their side.

Every attempt is recorded in `api_calls`, with the response body stored byte for byte
in `raw_blobs`. Credentials travel in the Authorization header and are never stored.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import httpx2
import psycopg
from pydantic import SecretStr

from calipa.audit_trail.store import now_utc, put_blob

ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/get-gl-transactions",
        "/get-transaction",
        "/get-rates",
        "/get-charges",
        "/get-participant-banks",
    }
)
# Not allowed on purpose: get-account returns a placeholder for any account number
# and includes a customer name; every POST endpoint can create or move money.

RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class GatewayError(RuntimeError):
    pass


class ForbiddenRequest(GatewayError):
    """A write, or a path outside the allowlist, was attempted. Always a bug."""


class UpstreamUnavailable(GatewayError):
    """Retries were used up on network errors or retryable status codes."""


@dataclass(frozen=True, slots=True)
class ApiResponse:
    call_id: UUID
    url: str
    status_code: int
    content_type: str
    body: bytes
    blob_sha256: str


class _ReadOnlyTransport(httpx2.BaseTransport):
    def __init__(self, inner: httpx2.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.method != "GET" or request.url.path not in ALLOWED_PATHS:
            raise ForbiddenRequest(f"blocked {request.method} {request.url.path}")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class ReadOnlyClient:
    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        base_url: str,
        username: SecretStr,
        password: SecretStr,
        transport: httpx2.BaseTransport | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not conn.autocommit:
            raise ValueError("ReadOnlyClient needs an autocommit connection")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._conn = conn
        self._http = httpx2.Client(
            base_url=base_url,
            auth=httpx2.BasicAuth(username.get_secret_value(), password.get_secret_value()),
            transport=_ReadOnlyTransport(transport or httpx2.HTTPTransport()),
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not security

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ReadOnlyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(
        self, path: str, params: Mapping[str, str] | None = None, *, run_id: UUID | None = None
    ) -> ApiResponse:
        if path not in ALLOWED_PATHS:
            raise ForbiddenRequest(f"path {path!r} is not allowlisted")
        last_error = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            call_id = uuid4()
            started_at = now_utc()
            clock = time.monotonic()
            request = self._http.build_request("GET", path, params=dict(params or {}))
            url = str(request.url)
            try:
                response = self._http.send(request)
            except httpx2.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._record(call_id, run_id, url, None, None, last_error, started_at, clock)
            else:
                content_type = response.headers.get("content-type", "application/octet-stream")
                blob = put_blob(self._conn, response.content, content_type)
                self._record(
                    call_id, run_id, url, response.status_code, blob, None, started_at, clock
                )
                if response.status_code not in RETRYABLE_STATUS:
                    return ApiResponse(
                        call_id=call_id,
                        url=url,
                        status_code=response.status_code,
                        content_type=content_type,
                        body=response.content,
                        blob_sha256=blob,
                    )
                last_error = f"HTTP {response.status_code}"
            if attempt < self._max_attempts:
                delay = self._backoff * 2 ** (attempt - 1)
                self._sleep(delay + self._rng.uniform(0, delay))
        raise UpstreamUnavailable(
            f"GET {path} failed after {self._max_attempts} attempts: {last_error}"
        )

    def _record(
        self,
        call_id: UUID,
        run_id: UUID | None,
        url: str,
        status_code: int | None,
        blob: str | None,
        error: str | None,
        started_at: datetime,
        clock: float,
    ) -> None:
        self._conn.execute(
            """
            insert into api_calls
                (call_id, run_id, method, url, status_code, response_blob, error,
                 started_at, duration_ms)
            values (%s, %s, 'GET', %s, %s, %s, %s, %s, %s)
            """,
            (
                call_id,
                run_id,
                url,
                status_code,
                blob,
                error,
                started_at,
                int((time.monotonic() - clock) * 1000),
            ),
        )
