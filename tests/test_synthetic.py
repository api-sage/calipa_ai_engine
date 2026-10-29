"""The synthetic data must (1) look exactly like the real API and (2) make the
engine produce exactly the planted exceptions, no more, no fewer."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pydantic import SecretStr

from calipa.canonical import hash_value
from calipa.engine.standin import StandInEngine
from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.gateway.snapshot import extract_gl_snapshot
from calipa.reference import load_account_catalog, load_tariff
from calipa.synthetic import Anomaly, Dataset, generate
from tests.engine_helpers import build_request
from tests.fake_fcy import PASSWORD, USER, FakeFcy

LAGOS = ZoneInfo("Africa/Lagos")
DAY = date(2026, 10, 25)
EVERY_ANOMALY = {anomaly: 3 for anomaly in Anomaly} | {Anomaly.CLEAN: 20}


def dataset(seed: int = 42, counts: dict[Anomaly, int] | None = None) -> Dataset:
    return generate(
        seed,
        day=DAY,
        counts=counts or EVERY_ANOMALY,
        catalog=load_account_catalog(),
        tariff=load_tariff(),
        tz=LAGOS,
    )


def exceptions_by_transfer(data: Dataset, entries: list[dict[str, object]] | None = None):  # type: ignore[no-untyped-def]
    request = build_request(entries if entries is not None else data.entries, rates=data.rates)
    result = StandInEngine(load_tariff()).reconcile(request)
    found = defaultdict(set)
    for e in result.exceptions:
        found[e.transfer_id].add(e.type)
    return found


def test_same_seed_same_bytes() -> None:
    assert hash_value(dataset(1).entries) == hash_value(dataset(1).entries)
    assert hash_value(dataset(1).entries) != hash_value(dataset(2).entries)


@pytest.mark.parametrize("seed", [1, 42, 2026])
def test_engine_finds_exactly_what_was_planted(seed: int) -> None:
    data = dataset(seed)
    found = exceptions_by_transfer(data)
    for label in data.labels:
        assert found.get(label.transfer_id, set()) == label.expected, label.anomaly
    planted_ids = {label.transfer_id for label in data.labels}
    assert set(found) <= planted_ids


def test_clean_data_produces_no_exceptions() -> None:
    data = dataset(7, {Anomaly.CLEAN: 50})
    assert exceptions_by_transfer(data) == {}


@pytest.fixture
def fcy_with_dataset(conn: psycopg.Connection) -> Iterator[tuple[FcyClient, Dataset]]:
    data = dataset(99)
    server = FakeFcy(data.entries)
    with ReadOnlyClient(
        conn,
        base_url="https://fcy.test",
        username=SecretStr(USER),
        password=SecretStr(PASSWORD),
        transport=server.transport(),
        sleep=lambda _: None,
    ) as http:
        yield FcyClient(http), data


def test_end_to_end_through_the_gateway(fcy_with_dataset: tuple[FcyClient, Dataset]) -> None:
    fcy, data = fcy_with_dataset
    snap = extract_gl_snapshot(
        fcy,
        period_start=DAY,
        period_end=DAY,
        tz=LAGOS,
        now=datetime(2026, 10, 27, tzinfo=UTC),
        page_size=7,  # many pages, with same-second ties across page boundaries
    )
    assert snap.status == "complete"
    assert len(snap.entries) == len(data.entries)
    raw = [entry.model_dump(mode="json", by_alias=True) for entry in snap.entries]
    found = exceptions_by_transfer(data, raw)
    for label in data.labels:
        assert found.get(label.transfer_id, set()) == label.expected, label.anomaly
