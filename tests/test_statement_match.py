"""GL-to-statement matching: the four breaks, clean matches and determinism."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from calipa.engine.contract import ExceptionType
from calipa.engine.statement_match import match_statements, unmatched_transfer_id
from calipa.gateway.fcy import GLEntry
from calipa.ingest.excel import StatementLine

T1 = UUID("828972ad-6b3d-4f16-b230-4ef2f4875b33")
T2 = UUID("ca650882-cc3c-462c-ad71-81ae5182e945")
AT = datetime(2026, 10, 25, 20, 36, 31, tzinfo=UTC)


def leg(id_suffix: int, ref: str, transfer: UUID, **overrides: Any) -> GLEntry:
    values: dict[str, Any] = {
        "id": UUID(f"00000000-0000-4000-8000-{id_suffix:012d}"),
        "transferId": transfer,
        "externalReference": ref,
        "debitedAccount": "0123456890",
        "creditedAccount": "6307882400",
        "entryType": "DEBIT",
        "currency": "USD",
        "amount": Decimal("13.58"),
        "createdAt": AT,
    }
    return GLEntry.model_validate(values | overrides)


def line(statement_id: str, ref: str, **overrides: Any) -> StatementLine:
    values: dict[str, Any] = {
        "id": unmatched_transfer_id(statement_id),
        "statementId": statement_id,
        "externalReference": ref,
        "debitedAccount": "0123456890",
        "creditedAccount": "6307882400",
        "entryType": "DEBIT",
        "currency": "USD",
        "amount": Decimal("13.58"),
        "valueDate": AT,
    }
    return StatementLine.model_validate(values | overrides)


def by_type(exceptions: Any) -> dict[ExceptionType, Any]:
    return {e.type: e for e in exceptions}


def test_clean_match_has_no_breaks() -> None:
    matched = match_statements([leg(1, "REF-1", T1)], [line("STMT-0001", "REF-1")])
    assert matched.exceptions == ()
    assert matched.gl_entries_compared == 1 and matched.statement_lines_compared == 1
    assert matched.references_compared == 1
    assert all(c.passed for c in matched.checks)


def test_amount_mismatch_points_at_both_sides() -> None:
    matched = match_statements(
        [leg(1, "REF-1", T1, amount=Decimal("1.02"))],
        [line("STMT-0003", "REF-1", amount=Decimal("1.20"))],
    )
    (exception,) = matched.exceptions
    assert exception.type is ExceptionType.STATEMENT_AMOUNT_MISMATCH
    assert exception.transfer_id == T1
    assert exception.record_ids == [UUID("00000000-0000-4000-8000-000000000001")]
    assert exception.expected is not None and exception.actual is not None
    assert (exception.expected.amount, exception.actual.amount) == (
        Decimal("1.02"),
        Decimal("1.20"),
    )
    assert exception.difference is not None and exception.difference.amount == Decimal("0.18")
    assert exception.details["statement_id"] == "STMT-0003"


def test_leg_with_no_statement_line_is_missing() -> None:
    matched = match_statements([leg(1, "REF-1", T1)], [line("STMT-0001", "OTHER")])
    missing = by_type(matched.exceptions)[ExceptionType.MISSING_FROM_STATEMENT]
    assert missing.details["statement_id"] == "none"
    assert missing.expected is not None and missing.expected.amount == Decimal("13.58")
    assert ExceptionType.MISSING_FROM_GL in by_type(matched.exceptions)


def test_reference_missing_from_the_gl_entirely() -> None:
    matched = match_statements([], [line("STMT-0009", "UNKNOWN")])
    (exception,) = matched.exceptions
    assert exception.type is ExceptionType.MISSING_FROM_GL
    assert exception.transfer_id == unmatched_transfer_id("STMT-0009")
    assert exception.record_ids == []
    assert exception.actual is not None and exception.actual.amount == Decimal("13.58")
    assert exception.details["statement_id"] == "STMT-0009"


def test_repeated_statement_line_is_a_duplicate() -> None:
    matched = match_statements(
        [leg(1, "REF-1", T1)],
        [line("STMT-0007", "REF-1"), line("STMT-0008", "REF-1")],
    )
    (exception,) = matched.exceptions
    assert exception.type is ExceptionType.DUPLICATE_ON_STATEMENT
    assert exception.transfer_id == T1
    assert exception.details["statement_id"] == "STMT-0008"
    assert exception.details["duplicate_of"] == "STMT-0007"


def test_matching_is_deterministic_regardless_of_order() -> None:
    legs = [leg(1, "REF-1", T1), leg(2, "REF-2", T2, amount=Decimal("5"))]
    lines = [line("STMT-0002", "REF-2", amount=Decimal("6")), line("STMT-0001", "REF-1")]
    first = match_statements(legs, lines)
    second = match_statements(list(reversed(legs)), list(reversed(lines)))
    assert [e.exception_id for e in first.exceptions] == [e.exception_id for e in second.exceptions]
    assert {e.type for e in first.exceptions} == {ExceptionType.STATEMENT_AMOUNT_MISMATCH}
    assert any(not c.passed for c in first.checks)


def test_sample_breaks_reproduce() -> None:
    """The four breaks from the bank's reconciliation test data, in miniature."""
    legs = [
        leg(
            1, "REF-2", T1, creditedAccount="0125548976", entryType="CREDIT", amount=Decimal("1.02")
        ),
        leg(
            2,
            "REF-2",
            T1,
            creditedAccount="0125548976",
            entryType="DEBIT",
            currency="GBP",
            amount=Decimal("1.47"),
        ),
        leg(3, "REF-1", T2),
    ]
    lines = [
        line(
            "STMT-0003",
            "REF-2",
            creditedAccount="0125548976",
            entryType="CREDIT",
            amount=Decimal("1.20"),
        ),
        line("STMT-0007", "REF-1"),
        line("STMT-0008", "REF-1"),
        line("STMT-0009", "REF-3"),
    ]
    matched = match_statements(legs, lines)
    found = by_type(matched.exceptions)
    assert set(found) == {
        ExceptionType.STATEMENT_AMOUNT_MISMATCH,
        ExceptionType.MISSING_FROM_STATEMENT,
        ExceptionType.DUPLICATE_ON_STATEMENT,
        ExceptionType.MISSING_FROM_GL,
    }
    assert found[ExceptionType.MISSING_FROM_STATEMENT].expected is not None
    assert found[ExceptionType.MISSING_FROM_STATEMENT].expected.amount == Decimal("1.47")
