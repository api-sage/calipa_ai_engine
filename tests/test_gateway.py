from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import httpx2
import psycopg
import pytest
from pydantic import SecretStr

from calipa.audit_trail.store import get_blob
from calipa.gateway.fcy import AuthError, FcyClient, RequestRejected, SchemaDrift, _data
from calipa.gateway.http import (
    ALLOWED_PATHS,
    ForbiddenRequest,
    ReadOnlyClient,
    UpstreamUnavailable,
)
from calipa.money import Currency
from tests.fake_fcy import PASSWORD, USER, FakeFcy, fixture_bytes, real_gl_entries

BASE = "https://fcy.test"


def make_client(
    conn: psycopg.Connection,
    server: FakeFcy | httpx2.MockTransport,
    *,
    password: str = PASSWORD,
    sleeps: list[float] | None = None,
) -> ReadOnlyClient:
    transport = server.transport() if isinstance(server, FakeFcy) else server
    return ReadOnlyClient(
        conn,
        base_url=BASE,
        username=SecretStr(USER),
        password=SecretStr(password),
        transport=transport,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


@pytest.fixture
def server() -> FakeFcy:
    return FakeFcy(real_gl_entries())


@pytest.fixture
def fcy(conn: psycopg.Connection, server: FakeFcy) -> Iterator[FcyClient]:
    with make_client(conn, server) as http:
        yield FcyClient(http)


def api_calls(conn: psycopg.Connection) -> list[tuple[object, ...]]:
    return conn.execute(
        "select method, url, status_code, response_blob, error from api_calls order by started_at"
    ).fetchall()


# --- read-only enforcement -------------------------------------------------------


def test_client_has_no_write_methods() -> None:
    for name in ("post", "put", "patch", "delete", "request", "send"):
        assert not hasattr(ReadOnlyClient, name)


@pytest.mark.parametrize(
    "path", ["/transfer-funds", "/deposit-funds", "/create-account", "/get-account", "/x"]
)
def test_non_allowlisted_paths_never_leave_the_process(
    conn: psycopg.Connection, server: FakeFcy, path: str
) -> None:
    with make_client(conn, server) as http, pytest.raises(ForbiddenRequest):
        http.get(path)
    assert server.requests == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_transport_blocks_writes_even_if_inner_client_is_reached(
    conn: psycopg.Connection, server: FakeFcy, method: str
) -> None:
    with make_client(conn, server) as http:
        inner = http._http  # deliberately bypass the public API
        for path in ["/transfer-funds", *sorted(ALLOWED_PATHS)]:
            with pytest.raises(ForbiddenRequest):
                inner.request(method, path, json={"debitAmount": 100})
    assert server.requests == []


# --- logging, auth, retries --------------------------------------------------------


def test_every_call_is_logged_with_raw_body_and_no_credentials(
    conn: psycopg.Connection, fcy: FcyClient
) -> None:
    before = len(api_calls(conn))
    fetched = fcy.rates()
    calls = api_calls(conn)[before:]
    assert len(calls) == 1
    method, url, status, blob, error = calls[0]
    assert (method, status, error) == ("GET", 200, None)
    assert url == f"{BASE}/get-rates"
    assert get_blob(conn, str(blob)) == fixture_bytes("rates.json") == fetched.response.body
    assert PASSWORD not in str(url) and USER not in str(url)


def test_auth_failure_is_mapped(conn: psycopg.Connection, server: FakeFcy) -> None:
    with make_client(conn, server, password="wrong") as http, pytest.raises(AuthError):
        FcyClient(http).rates()


def test_retryable_status_then_success(conn: psycopg.Connection, server: FakeFcy) -> None:
    replies = [503, 429, 200]

    def flaky(request: httpx2.Request) -> httpx2.Response:
        status = replies.pop(0)
        if status != 200:
            return httpx2.Response(status, content=b"busy")
        return httpx2.Response(200, content=fixture_bytes("rates.json"))

    server.overrides["/get-rates"] = flaky
    sleeps: list[float] = []
    before = len(api_calls(conn))
    with make_client(conn, server, sleeps=sleeps) as http:
        rates = FcyClient(http).rates().data
    assert len(rates) == 24
    assert [c[2] for c in api_calls(conn)[before:]] == [503, 429, 200]
    # Exponential backoff with jitter: 0.5s base, then 1s base, each plus up to 100%.
    assert 0.5 <= sleeps[0] <= 1.0 and 1.0 <= sleeps[1] <= 2.0


def test_retries_are_bounded(conn: psycopg.Connection, server: FakeFcy) -> None:
    server.overrides["/get-rates"] = lambda r: httpx2.Response(502, content=b"bad gateway")
    with make_client(conn, server) as http, pytest.raises(UpstreamUnavailable):
        FcyClient(http).rates()


def test_network_errors_are_retried_and_logged(conn: psycopg.Connection, server: FakeFcy) -> None:
    failures = [httpx2.ConnectError("reset")]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if failures:
            raise failures.pop()
        return server.handle(request)

    before = len(api_calls(conn))
    with make_client(conn, httpx2.MockTransport(handler)) as http:
        assert len(FcyClient(http).rates().data) == 24
    calls = api_calls(conn)[before:]
    assert calls[0][2] is None and "ConnectError" in str(calls[0][4])
    assert calls[1][2] == 200


def test_client_errors_are_not_retried(conn: psycopg.Connection, server: FakeFcy) -> None:
    before = len(api_calls(conn))
    with make_client(conn, server) as http, pytest.raises(RequestRejected) as rejected:
        # Bypass FcyClient's own page-size check to see the API's 400 shape.
        _data(http.get("/get-gl-transactions", {"pageSize": "500"}))
    assert rejected.value.errors == ["pageSize must be between 1 and 100"]
    assert len(api_calls(conn)) - before == 1


# --- parsing -----------------------------------------------------------------------


def test_gl_entries_parse_to_decimal_and_aware_datetimes(fcy: FcyClient) -> None:
    page = fcy.gl_page(start=date(2026, 10, 25), end=date(2026, 10, 25), page=1).data
    assert page.total == 8 and len(page.items) == 8
    amounts = {str(e.id)[:8]: e.amount for e in page.items}
    assert amounts["8af5b26b"] == Decimal("2") and amounts["79d279e1"] == Decimal("12.22")
    assert all(isinstance(e.amount, Decimal) for e in page.items)
    assert all(e.created_at.tzinfo is not None for e in page.items)
    assert {e.currency for e in page.items} == {Currency.GBP, Currency.USD}


def test_charges_keep_full_precision(fcy: FcyClient) -> None:
    charges = fcy.charges(Decimal("10"), Currency.GBP).data
    assert charges.vat == Decimal("0.7500000038011815")
    assert charges.sum_total == Decimal("12.2230266038011815")


def test_unknown_reference_is_empty_not_an_error(fcy: FcyClient) -> None:
    lookup = fcy.transaction("NOPE").data
    assert lookup.total == 0 and lookup.items == []


def test_unexpected_fields_are_schema_drift(conn: psycopg.Connection, server: FakeFcy) -> None:
    drifted = real_gl_entries()
    drifted[0]["status"] = "REVERSED"
    server.entries = drifted
    with make_client(conn, server) as http, pytest.raises(SchemaDrift, match="status"):
        FcyClient(http).gl_page(start=date(2026, 10, 25), end=date(2026, 10, 25), page=1)


def test_failure_envelope_is_an_error(conn: psycopg.Connection, server: FakeFcy) -> None:
    server.overrides["/get-rates"] = lambda r: httpx2.Response(
        200, content=b'{"success":false,"message":"maintenance","data":null}'
    )
    with make_client(conn, server) as http, pytest.raises(Exception, match="maintenance"):
        FcyClient(http).rates()
