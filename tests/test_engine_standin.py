from __future__ import annotations

import random
from decimal import Decimal
from uuid import UUID

from calipa.canonical import hash_value
from calipa.engine.contract import (
    EngineException,
    ExceptionType,
    ReconciliationResult,
    Severity,
)
from calipa.engine.standin import StandInEngine
from calipa.reference import load_tariff
from tests.engine_helpers import build_request
from tests.fake_fcy import real_gl_entries

T0001 = UUID("ca650882-cc3c-462c-ad71-81ae5182e945")  # ref ...0001
T0002 = UUID("828972ad-6b3d-4f16-b230-4ef2f4875b33")  # ref ...0002


def run(raw: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    return StandInEngine(load_tariff()).reconcile(build_request(raw))


def by_transfer(
    result: ReconciliationResult, transfer_id: UUID
) -> dict[ExceptionType, EngineException]:
    return {e.type: e for e in result.exceptions if e.transfer_id == transfer_id}


def test_real_transfer_0001_is_missing_fee_and_vat() -> None:
    found = by_transfer(run(real_gl_entries()), T0001)
    assert set(found) == {
        ExceptionType.FEE_LEG_MISSING,
        ExceptionType.VAT_LEG_MISSING,
        ExceptionType.TRANSIENT_RESIDUAL,
    }
    fee = found[ExceptionType.FEE_LEG_MISSING].expected
    vat = found[ExceptionType.VAT_LEG_MISSING].expected
    residual = found[ExceptionType.TRANSIENT_RESIDUAL]
    assert fee is not None and vat is not None and residual.actual is not None
    assert fee.amount == Decimal("1.47")
    assert vat.amount == Decimal("0.75")
    assert (residual.actual.amount, residual.actual.currency.value) == (Decimal("2.22"), "GBP")
    assert residual.details["implied_principal"] == "10"


def test_real_transfer_0002_balances_but_has_unconfirmed_patterns() -> None:
    result = run(real_gl_entries())
    found = by_transfer(result, T0002)
    assert set(found) == {
        ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE,
        ExceptionType.FEE_VAT_IN_BOTH_CURRENCIES,
        ExceptionType.ENTRY_DIRECTION_INCONSISTENT,
    }
    dup = found[ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE]
    assert dup.related_transfer_ids == [T0001]
    assert dup.details["seconds_apart"] == "18"
    # The USD fee and VAT legs (8af5b26b, d817d930) carry the "wrong" entryType.
    direction = found[ExceptionType.ENTRY_DIRECTION_INCONSISTENT]
    assert {str(r)[:8] for r in direction.record_ids} == {"8af5b26b", "d817d930"}
    assert direction.severity is Severity.LOW
    passed = {c.rule_id for c in result.checks if c.transfer_id == T0002 and c.passed}
    assert {"R-FEE", "R-VAT", "R-FX", "R-RESIDUAL", "R-STRUCT"} <= passed


def test_control_totals() -> None:
    totals = {(t.account_role, t.currency.value): t for t in run(real_gl_entries()).control_totals}
    assert totals[("OTHER", "GBP")].credits_to_transient.amount == Decimal("24.44")
    assert totals[("OTHER", "USD")].debits_from_transient.amount == Decimal("27.16")
    assert totals[("FEE_INCOME", "GBP")].debits_from_transient.amount == Decimal("1.47")


def test_result_is_deterministic_and_order_independent() -> None:
    raw = real_gl_entries()
    shuffled = list(raw)
    random.Random(7).shuffle(shuffled)
    a, b = run(raw), run(shuffled)
    assert hash_value(a.model_dump(mode="python")) == hash_value(b.model_dump(mode="python"))
    assert [e.exception_id for e in a.exceptions] == [e.exception_id for e in b.exceptions]


def test_missing_rate_is_reported_not_guessed() -> None:
    result = StandInEngine(load_tariff()).reconcile(build_request(real_gl_entries(), rates=[]))
    types = {e.type for e in result.exceptions}
    assert ExceptionType.RATE_UNAVAILABLE in types
    assert ExceptionType.FX_CONVERSION_MISMATCH not in types
