"""Builds engine requests from raw API-shaped entries and the recorded rate table."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from calipa.engine.contract import RateQuote, ReconciliationRequest
from calipa.gateway.fcy import GLEntry, Rate
from calipa.money import loads_decimal
from calipa.reference import load_account_catalog, load_tariff
from tests.fake_fcy import fixture_bytes

REQUEST_ID = UUID("00000000-0000-4000-8000-0000000000aa")


def recorded_rates() -> list[RateQuote]:
    data = loads_decimal(fixture_bytes("rates.json"))["data"]
    return [
        RateQuote(
            from_currency=r.from_currency,
            to_currency=r.to_currency,
            rate_date=r.rate_date,
            rate=r.rate,
            source=f"fixture:{r.id}",
        )
        for r in (Rate.model_validate(item) for item in data)
    ]


def build_request(
    raw_entries: list[dict[str, Any]], rates: list[RateQuote] | None = None
) -> ReconciliationRequest:
    catalog, tariff = load_account_catalog(), load_tariff()
    entries = [GLEntry.model_validate(loads_decimal(_dumps(e))) for e in raw_entries]
    return ReconciliationRequest(
        request_id=REQUEST_ID,
        snapshot_sha256="0" * 64,
        bank_timezone="Africa/Lagos",
        entries=entries,
        account_roles={number: role.value for number, role in catalog.roles.items()},
        account_catalog_sha256=catalog.sha256,
        tariff_sha256=tariff.sha256,
        rates=recorded_rates() if rates is None else rates,
    )


def _dumps(entry: dict[str, Any]) -> str:
    return json.dumps(entry, default=str)
