from datetime import date
from decimal import Decimal

import psycopg
from pydantic import SecretStr

from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.gateway.rates import rates_for, snapshot_rates
from calipa.money import Currency
from tests.fake_fcy import PASSWORD, USER, FakeFcy


def test_rate_table_is_stored_exactly(conn: psycopg.Connection) -> None:
    with ReadOnlyClient(
        conn,
        base_url="https://fcy.test",
        username=SecretStr(USER),
        password=SecretStr(PASSWORD),
        transport=FakeFcy().transport(),
    ) as http:
        snapshot_id = snapshot_rates(conn, FcyClient(http))

    count = conn.execute(
        "select rate_count from fx_rate_snapshots where snapshot_id = %s", (snapshot_id,)
    ).fetchone()
    assert count == (24,)

    usd_ngn = [
        r
        for r in rates_for(conn, Currency.USD, Currency.NGN, date(2026, 10, 26))
        if r.snapshot_id == snapshot_id
    ]
    assert len(usd_ngn) == 1
    assert usd_ngn[0].rate == Decimal("1338.380059")
    assert usd_ngn[0].source_rate_id == 349
    gbp_usd = [
        r
        for r in rates_for(conn, Currency.GBP, Currency.USD, date(2026, 10, 25))
        if r.snapshot_id == snapshot_id
    ]
    assert gbp_usd[0].rate == Decimal("1.35774874")
