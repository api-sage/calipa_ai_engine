from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pydantic import SecretStr

from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.gateway.snapshot import (
    PeriodNotClosed,
    SnapshotIntegrityError,
    extract_gl_snapshot,
)
from tests.fake_fcy import PASSWORD, USER, FakeFcy, entry, real_gl_entries

LAGOS = ZoneInfo("Africa/Lagos")
OCT25 = date(2026, 10, 25)
LATER = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def server() -> FakeFcy:
    return FakeFcy(real_gl_entries())


@pytest.fixture
def fcy(conn: psycopg.Connection, server: FakeFcy) -> Iterator[FcyClient]:
    with ReadOnlyClient(
        conn,
        base_url="https://fcy.test",
        username=SecretStr(USER),
        password=SecretStr(PASSWORD),
        transport=server.transport(),
        sleep=lambda _: None,
    ) as http:
        yield FcyClient(http)


def snapshot(fcy: FcyClient, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "period_start": OCT25,
        "period_end": OCT25,
        "tz": LAGOS,
        "now": LATER,
    }
    args.update(overrides)
    return extract_gl_snapshot(fcy, **args)


def test_real_day_is_complete_and_ordered(fcy: FcyClient) -> None:
    snap = snapshot(fcy)
    assert snap.status == "complete" and snap.limitations == ()
    assert len(snap.entries) == 8
    keys = [(e.created_at, str(e.id)) for e in snap.entries]
    assert keys == sorted(keys)
    # One extra API day on each side is read because the API's day timezone is unknown.
    assert {p.day for p in snap.pages} == {date(2026, 10, 24), OCT25, date(2026, 10, 26)}


def test_snapshot_hash_is_reproducible(fcy: FcyClient) -> None:
    first, second = snapshot(fcy), snapshot(fcy)
    assert first.sha256 == second.sha256
    assert [p.blob_sha256 for p in first.pages] == [p.blob_sha256 for p in second.pages]


def test_open_period_is_refused(fcy: FcyClient, server: FakeFcy) -> None:
    # 25 Oct closes at midnight Lagos time plus the 6h settling delay: 05:00 UTC on 26 Oct.
    with pytest.raises(PeriodNotClosed):
        snapshot(fcy, now=datetime(2026, 10, 26, 4, 59, tzinfo=UTC))
    assert server.requests == []
    snapshot(fcy, now=datetime(2026, 10, 26, 5, 0, tzinfo=UTC))


def test_entries_are_trimmed_by_bank_timezone(fcy: FcyClient, server: FakeFcy) -> None:
    server.entries = [
        entry(1, "2026-10-24T23:30:00Z"),  # 25 Oct 00:30 in Lagos: inside
        entry(2, "2026-10-25T12:00:00Z"),  # inside
        entry(3, "2026-10-25T23:30:00Z"),  # 26 Oct 00:30 in Lagos: outside
    ]
    snap = snapshot(fcy)
    assert [str(e.id)[-1] for e in snap.entries] == ["1", "2"]


def test_ties_across_page_boundaries_are_not_lost(fcy: FcyClient, server: FakeFcy) -> None:
    # Five entries in the same second, pages of two, stable order: all are read.
    server.entries = [entry(i, "2026-10-25T10:00:00Z") for i in range(1, 6)]
    snap = snapshot(fcy, page_size=2)
    assert snap.status == "complete" and len(snap.entries) == 5


def test_unstable_tie_order_is_detected_not_hidden(fcy: FcyClient, server: FakeFcy) -> None:
    # The order of same-second entries flips between requests, so offset paging
    # returns some entries twice and skips others. The count check catches it and
    # the snapshot is marked partial instead of silently missing entries.
    server.entries = [entry(i, "2026-10-25T10:00:00Z") for i in range(1, 6)]
    server.unstable_ties = True
    snap = snapshot(fcy, page_size=2)
    assert snap.status == "partial"
    assert "2026-10-25" in snap.limitations[0]
    assert "distinct entries" in snap.limitations[0]


def test_entry_inserted_mid_read_triggers_a_clean_retry(fcy: FcyClient, server: FakeFcy) -> None:
    server.entries = [entry(i, f"2026-10-25T10:00:0{i}Z") for i in range(1, 6)]
    inserted = False

    def insert_once(fake: FakeFcy, call_number: int) -> None:
        nonlocal inserted
        # After the first page of 25 Oct (calls 0-2 are the 24th: page 1 + recheck).
        if not inserted and call_number == 3:
            fake.entries.append(entry(99, "2026-10-25T11:00:00Z"))
            inserted = True

    server.before_gl_page = insert_once
    snap = snapshot(fcy, page_size=2)
    assert inserted
    assert snap.status == "complete"
    assert len(snap.entries) == 6
    assert any(p.attempt == 2 for p in snap.pages)  # the day was read again


def test_same_id_with_different_content_stops_the_run(fcy: FcyClient, server: FakeFcy) -> None:
    original = entry(1, "2026-10-25T10:00:00Z", amount="10.00")
    changed = entry(1, "2026-10-25T10:00:00Z", amount="10.01")
    server.entries = [original, entry(2, "2026-10-25T10:00:00Z")]

    def swap(fake: FakeFcy, call_number: int) -> None:
        if call_number == 4:  # the re-read of page 1 for 25 Oct
            fake.entries = [changed, entry(2, "2026-10-25T10:00:00Z")]

    server.before_gl_page = swap
    with pytest.raises(SnapshotIntegrityError):
        snapshot(fcy, page_size=1)


def test_checkpoints_are_recorded(fcy: FcyClient) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    snapshot(fcy, record=lambda kind, payload: events.append((kind, payload)))
    assert [e[0] for e in events] == ["extraction_checkpoint"] * 3
    assert all(e[1]["consistent"] for e in events)


def test_period_limits(fcy: FcyClient) -> None:
    with pytest.raises(ValueError, match="after"):
        snapshot(fcy, period_start=OCT25, period_end=OCT25 - timedelta(days=1))
    with pytest.raises(ValueError, match="longer"):
        snapshot(fcy, period_start=date(2026, 1, 1), period_end=date(2026, 6, 30))
