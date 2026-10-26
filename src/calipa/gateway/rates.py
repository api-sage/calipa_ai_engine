"""Daily FX rate snapshots, kept by Calipa because the API does not keep history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg

from calipa.audit_trail.store import now_utc
from calipa.gateway.fcy import FcyClient
from calipa.money import Currency


@dataclass(frozen=True, slots=True)
class StoredRate:
    snapshot_id: UUID
    source_rate_id: int
    from_currency: Currency
    to_currency: Currency
    rate: Decimal
    rate_date: date
    fetched_at: datetime


def snapshot_rates(conn: psycopg.Connection, fcy: FcyClient) -> UUID:
    """Fetch the current rate table and store it. Returns the snapshot id."""
    fetched = fcy.rates()
    snapshot_id = uuid4()
    with conn.transaction():
        conn.execute(
            """
            insert into fx_rate_snapshots
                (snapshot_id, api_call_id, response_blob, rate_count, fetched_at)
            values (%s, %s, %s, %s, %s)
            """,
            (
                snapshot_id,
                fetched.response.call_id,
                fetched.response.blob_sha256,
                len(fetched.data),
                now_utc(),
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into fx_rates
                    (snapshot_id, source_rate_id, from_currency, to_currency, rate,
                     rate_date, source_created_at)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        snapshot_id,
                        r.id,
                        r.from_currency.value,
                        r.to_currency.value,
                        r.rate,
                        r.rate_date,
                        r.created_at,
                    )
                    for r in fetched.data
                ],
            )
    return snapshot_id


def rates_for(
    conn: psycopg.Connection, from_currency: Currency, to_currency: Currency, rate_date: date
) -> list[StoredRate]:
    """Every stored rate for the pair and date, oldest snapshot first. More than one
    distinct value for the same date is itself a finding for the evidence layer."""
    rows = conn.execute(
        """
        select r.snapshot_id, r.source_rate_id, r.from_currency, r.to_currency, r.rate,
               r.rate_date, s.fetched_at
        from fx_rates r join fx_rate_snapshots s using (snapshot_id)
        where r.from_currency = %s and r.to_currency = %s and r.rate_date = %s
        order by s.fetched_at, r.source_rate_id
        """,
        (from_currency.value, to_currency.value, rate_date),
    ).fetchall()
    return [
        StoredRate(row[0], row[1], Currency(row[2]), Currency(row[3]), row[4], row[5], row[6])
        for row in rows
    ]


def rates_between(conn: psycopg.Connection, start: date, end: date) -> list[StoredRate]:
    """Every stored rate dated within [start, end], across all snapshots."""
    rows = conn.execute(
        """
        select r.snapshot_id, r.source_rate_id, r.from_currency, r.to_currency, r.rate,
               r.rate_date, s.fetched_at
        from fx_rates r join fx_rate_snapshots s using (snapshot_id)
        where r.rate_date between %s and %s
        order by r.rate_date, r.from_currency, r.to_currency, s.fetched_at, r.source_rate_id
        """,
        (start, end),
    ).fetchall()
    return [
        StoredRate(row[0], row[1], Currency(row[2]), Currency(row[3]), row[4], row[5], row[6])
        for row in rows
    ]
