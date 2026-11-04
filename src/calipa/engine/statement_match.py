"""GL-to-statement matching: the breaks between what the ledger says and what the
statement says.

Each side is grouped by external reference. Within a reference, legs and lines are
matched greedily on accounts, direction and currency; a matched pair whose amounts
differ is an amount mismatch. Whatever is left over is missing on one side, except
that a leftover statement line identical to an already-matched line of the same
reference is a duplicate posting on the statement.

The result is engine exceptions, so statement breaks flow through the same analysis
and reporting as every other break. Statement lines carry string ids (STMT-0001),
which travel in `details`; `record_ids` holds GL entry ids only, because the
evidence bundle resolves those to ledger entries. A break with no GL leg at all
(names a reference the GL never saw) is filed under a deterministic transfer id
derived from its statement line.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from calipa.canonical import hash_value
from calipa.engine.contract import (
    Amount,
    CheckResult,
    EngineException,
    ExceptionType,
    Severity,
)
from calipa.gateway.fcy import GLEntry
from calipa.ingest.excel import StatementLine
from calipa.money import Money

RULE_AMOUNT = "R-STMT-AMOUNT"
RULE_MISSING_FROM_STATEMENT = "R-STMT-MISSING"
RULE_DUPLICATE = "R-STMT-DUPLICATE"
RULE_UNMATCHED = "R-STMT-UNMATCHED"


@dataclass(frozen=True, slots=True)
class StatementMatch:
    exceptions: tuple[EngineException, ...]
    checks: tuple[CheckResult, ...]
    gl_entries_compared: int
    statement_lines_compared: int
    references_compared: int


@dataclass(slots=True)
class _Collector:
    exceptions: list[EngineException] = field(default_factory=list)

    def raise_exception(
        self,
        *,
        rule_id: str,
        type: ExceptionType,
        severity: Severity,
        transfer_id: UUID,
        records: list[UUID],
        expected: Money | None = None,
        actual: Money | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        record_ids = sorted(set(records), key=str)
        exception_id = (
            "EX-"
            + hash_value(
                {
                    "rule": rule_id,
                    "transfer": transfer_id,
                    "records": record_ids,
                    "details": details or {},
                }
            )[:16]
        )
        difference = (
            Amount.of(actual - expected) if expected is not None and actual is not None else None
        )
        self.exceptions.append(
            EngineException(
                exception_id=exception_id,
                type=type,
                severity=severity,
                rule_id=rule_id,
                transfer_id=transfer_id,
                related_transfer_ids=[],
                record_ids=record_ids,
                expected=Amount.of(expected) if expected is not None else None,
                actual=Amount.of(actual) if actual is not None else None,
                difference=difference,
                details=details or {},
            )
        )


def _key(entry: GLEntry) -> tuple[str, str, str, str]:
    return (entry.debited_account, entry.credited_account, entry.entry_type, entry.currency.value)


def _line_key(line: StatementLine) -> tuple[str, str, str, str]:
    return (line.debited_account, line.credited_account, line.entry_type, line.currency.value)


def unmatched_transfer_id(statement_id: str) -> UUID:
    """The deterministic transfer a statement-only break is filed under."""
    return uuid5(NAMESPACE_URL, f"calipa:unmatched-statement:{statement_id}")


def match_statements(
    gl_entries: list[GLEntry], statement_lines: list[StatementLine]
) -> StatementMatch:
    """Compare GL entries to statement lines. Deterministic: the same inputs in any
    order give the same exceptions with the same ids."""
    legs: dict[str, list[GLEntry]] = defaultdict(list)
    for entry in sorted(gl_entries, key=lambda e: (e.external_reference, str(e.id))):
        legs[entry.external_reference].append(entry)
    lines: dict[str, list[StatementLine]] = defaultdict(list)
    for line in sorted(statement_lines, key=lambda s: (s.external_reference, s.statement_id)):
        lines[line.external_reference].append(line)

    out = _Collector()
    for reference in sorted(set(legs) | set(lines)):
        _match_reference(reference, legs.get(reference, []), lines.get(reference, []), out)

    broken = {e.transfer_id for e in out.exceptions}
    checks = tuple(
        CheckResult(rule_id=RULE_UNMATCHED, transfer_id=t, passed=t not in broken)
        for t in sorted({e.transfer_id for e in gl_entries}, key=str)
    ) + tuple(
        CheckResult(rule_id=RULE_UNMATCHED, transfer_id=e.transfer_id, passed=False)
        for e in out.exceptions
        if e.type is ExceptionType.MISSING_FROM_GL
    )
    exceptions = tuple(
        sorted(out.exceptions, key=lambda e: (str(e.transfer_id), e.rule_id, e.exception_id))
    )
    return StatementMatch(
        exceptions=exceptions,
        checks=checks,
        gl_entries_compared=len(gl_entries),
        statement_lines_compared=len(statement_lines),
        references_compared=len(set(legs) | set(lines)),
    )


def _match_reference(
    reference: str, legs: list[GLEntry], lines: list[StatementLine], out: _Collector
) -> None:
    if not legs:
        for line in lines:
            out.raise_exception(
                rule_id=RULE_UNMATCHED,
                type=ExceptionType.MISSING_FROM_GL,
                severity=Severity.HIGH,
                transfer_id=unmatched_transfer_id(line.statement_id),
                records=[],
                actual=Money(line.amount, line.currency),
                details=_line_details(line),
            )
        return
    if not lines:
        for entry in legs:
            out.raise_exception(
                rule_id=RULE_MISSING_FROM_STATEMENT,
                type=ExceptionType.MISSING_FROM_STATEMENT,
                severity=Severity.HIGH,
                transfer_id=entry.transfer_id,
                records=[entry.id],
                expected=Money(entry.amount, entry.currency),
                details={"reference": reference, "statement_id": "none"},
            )
        return

    remaining = list(lines)
    consumed: list[StatementLine] = []
    for entry in legs:
        match = next((line for line in remaining if _line_key(line) == _key(entry)), None)
        if match is None:
            out.raise_exception(
                rule_id=RULE_MISSING_FROM_STATEMENT,
                type=ExceptionType.MISSING_FROM_STATEMENT,
                severity=Severity.HIGH,
                transfer_id=entry.transfer_id,
                records=[entry.id],
                expected=Money(entry.amount, entry.currency),
                details={"reference": reference, "statement_id": "none"},
            )
            continue
        remaining.remove(match)
        consumed.append(match)
        if match.amount != entry.amount or match.currency != entry.currency:
            out.raise_exception(
                rule_id=RULE_AMOUNT,
                type=ExceptionType.STATEMENT_AMOUNT_MISMATCH,
                severity=Severity.HIGH,
                transfer_id=entry.transfer_id,
                records=[entry.id],
                expected=Money(entry.amount, entry.currency),
                actual=Money(match.amount, match.currency),
                details=_line_details(match),
            )
    for line in remaining:
        original = next(
            (
                seen
                for seen in consumed
                if _line_key(seen) == _line_key(line) and seen.amount == line.amount
            ),
            None,
        )
        if original is not None:
            original_leg = next(entry for entry in legs if _key(entry) == _line_key(line))
            out.raise_exception(
                rule_id=RULE_DUPLICATE,
                type=ExceptionType.DUPLICATE_ON_STATEMENT,
                severity=Severity.MEDIUM,
                transfer_id=original_leg.transfer_id,
                records=[original_leg.id],
                expected=Money(original.amount, original.currency),
                actual=Money(line.amount, line.currency),
                details={
                    **_line_details(line),
                    "duplicate_of": original.statement_id,
                },
            )
        else:
            out.raise_exception(
                rule_id=RULE_UNMATCHED,
                type=ExceptionType.MISSING_FROM_GL,
                severity=Severity.HIGH,
                transfer_id=unmatched_transfer_id(line.statement_id),
                records=[],
                actual=Money(line.amount, line.currency),
                details=_line_details(line),
            )


def _line_details(line: StatementLine) -> dict[str, str]:
    return {
        "statement_id": line.statement_id,
        "reference": line.external_reference,
        "accounts": f"{line.debited_account}->{line.credited_account}",
        "entry_type": line.entry_type,
        "value_date": line.value_date.isoformat(),
    }
