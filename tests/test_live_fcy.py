"""Against the real FCY API, read-only. Opt in with CALIPA_LIVE=1 and credentials in
.env (CALIPA_FCY_USERNAME / CALIPA_FCY_PASSWORD). These only use the gateway, which
can send nothing but allowlisted GETs."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from calipa.config import Settings
from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.gateway.snapshot import extract_gl_snapshot

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("CALIPA_LIVE") != "1", reason="set CALIPA_LIVE=1"),
]


def test_live_snapshot_of_25_october(conn: psycopg.Connection) -> None:
    settings = Settings()
    if settings.fcy_username is None or settings.fcy_password is None:
        pytest.skip("FCY credentials not configured")
    with ReadOnlyClient(
        conn,
        base_url=settings.fcy_base_url,
        username=settings.fcy_username,
        password=settings.fcy_password,
    ) as http:
        fcy = FcyClient(http)
        snap = extract_gl_snapshot(
            fcy,
            period_start=date(2026, 10, 25),
            period_end=date(2026, 10, 25),
            tz=ZoneInfo(settings.bank_timezone),
            now=datetime.now(UTC),
        )
        rates = fcy.rates().data
    assert snap.status == "complete", snap.limitations
    assert len(snap.entries) >= 8
    assert {"ca650882", "828972ad"} <= {str(e.transfer_id)[:8] for e in snap.entries}
    assert rates
