"""A fake FCY Payment Processor that behaves like the real one where it matters:
Basic auth, offset pages sorted newest first by a one-second timestamp, the real
envelope and error shapes. Recorded real responses live in tests/fixtures/fcy/."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx2

FIXTURES = Path(__file__).parent / "fixtures" / "fcy"
USER, PASSWORD = "calipa-test", "test-password"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


def real_gl_entries() -> list[dict[str, Any]]:
    return list(fixture_json("gl_2026-10-25_p1.json")["data"]["items"])


def entry(
    id_suffix: int,
    created_at: str,
    *,
    amount: str = "1.00",
    currency: str = "USD",
    entry_type: str = "DEBIT",
    transfer: str = "00000000-0000-4000-8000-000000000001",
) -> dict[str, Any]:
    """A raw GL entry dict as the API would send it (amount is spliced in as a JSON
    number when served)."""
    return {
        "id": f"00000000-0000-4000-8000-{id_suffix:012d}",
        "transferId": transfer,
        "externalReference": f"REF{id_suffix:06d}",
        "debitedAccount": "0123456890",
        "creditedAccount": "6307882400",
        "entryType": entry_type,
        "currency": currency,
        "amount": amount,
        "createdAt": created_at,
    }


def _json_with_numbers(value: Any) -> bytes:
    """Serialize like the real API: amounts as bare JSON numbers."""
    text = json.dumps(value, separators=(",", ":"))
    for item in _walk_amounts(value):
        text = text.replace(f'"amount":"{item}"', f'"amount":{item}', 1)
    return text.encode()


def _walk_amounts(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "amount" and isinstance(v, str):
                found.append(v)
            else:
                found.extend(_walk_amounts(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(_walk_amounts(v))
    return found


class FakeFcy:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = list(entries or [])
        self.requests: list[httpx2.Request] = []
        # Hooks to simulate a live system: called before each GL page is served.
        self.before_gl_page: Callable[[FakeFcy, int], None] | None = None
        # Flip the order of entries that share a timestamp on every call, the way a
        # database without a tiebreaker may.
        self.unstable_ties = False
        self._gl_calls = 0
        self.overrides: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx2.Response(
                401, content=b"unauthorized", headers={"content-type": "text/plain"}
            )
        override = self.overrides.get(request.url.path)
        if override is not None:
            return override(request)
        if request.url.path == "/get-gl-transactions":
            return self._gl(request)
        if request.url.path == "/get-rates":
            return httpx2.Response(200, content=fixture_bytes("rates.json"), headers=_JSON)
        if request.url.path == "/get-charges":
            return httpx2.Response(200, content=fixture_bytes("charges_10_GBP.json"), headers=_JSON)
        if request.url.path == "/get-transaction":
            ref = request.url.params["transactionReference"]
            items = [e for e in self.entries if e["externalReference"] == ref]
            items.sort(key=lambda e: e["createdAt"])
            body = {"transactionReference": ref, "items": items, "total": len(items)}
            return _ok(body, "Transactions fetched successfully")
        return httpx2.Response(404, content=b"404 page not found\n")

    def _gl(self, request: httpx2.Request) -> httpx2.Response:
        params = request.url.params
        page = int(params.get("page", "1"))
        page_size = int(params.get("pageSize", "20"))
        if not 1 <= page_size <= 100:
            return httpx2.Response(
                400, content=fixture_bytes("gl_bad_pagesize.json"), headers=_JSON
            )
        if self.before_gl_page is not None:
            self.before_gl_page(self, self._gl_calls)
        self._gl_calls += 1
        start = date.fromisoformat(params["startDate"]) if "startDate" in params else None
        end = date.fromisoformat(params["endDate"]) if "endDate" in params else None

        def day_of(e: dict[str, Any]) -> date:
            # The fake filters by UTC day; the real API's timezone is unknown.
            return datetime.fromisoformat(e["createdAt"].replace("Z", "+00:00")).date()

        rows = [
            e
            for e in self.entries
            if (start is None or day_of(e) >= start) and (end is None or day_of(e) <= end)
        ]
        flip = self.unstable_ties and self._gl_calls % 2 == 0
        rows.sort(key=lambda e: e["id"], reverse=flip)  # arbitrary order within a second
        rows.sort(key=lambda e: e["createdAt"], reverse=True)  # stable: ties keep it
        total = len(rows)
        body = {
            "items": rows[(page - 1) * page_size : page * page_size],
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": math.ceil(total / page_size),
        }
        return _ok(body, "GL transactions fetched successfully")


_JSON = {"content-type": "application/json"}


def _ok(data: Any, message: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        content=_json_with_numbers({"success": True, "message": message, "data": data}),
        headers=_JSON,
    )
