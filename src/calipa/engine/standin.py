"""Stand-in Reconciliation Engine.

Temporary and owned by the Intelligence Layer only until the engine team's service
exists. Its rules encode our current reading of the FCY posting pattern, observed on
live data (transfer 828972ad):

    customer   -> TRANSIENT   source currency    principal + charge + VAT   (inbound)
    TRANSIENT  -> beneficiary dest currency      principal converted         (outbound)
    TRANSIENT  -> FEE_INCOME  charge legs
    TRANSIENT  -> VAT         VAT legs

Expected amounts come from the versioned tariff (flat charge converted at the day's
rate, VAT on principal) and from saved FX rates. Every number in an exception is
computed here, deterministically; the same request always produces the same result
and the same exception ids.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from uuid import UUID
from zoneinfo import ZoneInfo

from calipa.canonical import hash_value
from calipa.engine.contract import (
    CONTRACT_VERSION,
    Amount,
    CheckResult,
    ControlTotal,
    EngineException,
    ExceptionType,
    RateQuote,
    ReconciliationRequest,
    ReconciliationResult,
    Severity,
)
from calipa.gateway.fcy import GLEntry
from calipa.money import Currency, Money, canonical_decimal
from calipa.reference import AccountRole, Tariff

ENGINE_NAME = "calipa-standin"
ENGINE_VERSION = "0.1.0"
RULESET_VERSION = "standin-r1"
DUPLICATE_INSTRUCTION_WINDOW = timedelta(minutes=5)

STANDING_LIMITATIONS = (
    "Reconciliation ran on the Intelligence Layer's stand-in engine; its rules are a draft "
    "and have not been reviewed by the Reconciliation Engine team.",
    "Account roles and the fee schedule are inferred from live data and await confirmation.",
)


@dataclass(slots=True)
class _Transfer:
    transfer_id: UUID
    inbound: GLEntry
    outbound: GLEntry


@dataclass(slots=True)
class _Collector:
    exceptions: list[EngineException] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)

    def check(self, rule_id: str, transfer_id: UUID, passed: bool) -> None:
        self.checks.append(CheckResult(rule_id=rule_id, transfer_id=transfer_id, passed=passed))

    def raise_exception(
        self,
        *,
        rule_id: str,
        type: ExceptionType,
        severity: Severity,
        transfer_id: UUID,
        records: Iterable[UUID],
        expected: Money | None = None,
        actual: Money | None = None,
        related: Iterable[UUID] = (),
        details: dict[str, str] | None = None,
    ) -> None:
        record_ids = sorted(set(records), key=str)
        exception_id = (
            "EX-"
            + hash_value({"rule": rule_id, "transfer": transfer_id, "records": record_ids})[:16]
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
                related_transfer_ids=sorted(set(related), key=str),
                record_ids=record_ids,
                expected=Amount.of(expected) if expected is not None else None,
                actual=Amount.of(actual) if actual is not None else None,
                difference=difference,
                details=details or {},
            )
        )


class StandInEngine:
    def __init__(self, tariff: Tariff) -> None:
        self._tariff = tariff

    def reconcile(self, request: ReconciliationRequest) -> ReconciliationResult:
        tz = ZoneInfo(request.bank_timezone)
        roles = {number: AccountRole(role) for number, role in request.account_roles.items()}
        transients = [n for n, r in roles.items() if r is AccountRole.TRANSIENT]
        if len(transients) != 1:
            raise ValueError("exactly one TRANSIENT account is required")
        transient = transients[0]
        rates, ambiguous = _rate_book(request.rates)

        grouped: dict[UUID, list[GLEntry]] = defaultdict(list)
        for entry in request.entries:
            grouped[entry.transfer_id].append(entry)

        out = _Collector()
        transfers: list[_Transfer] = []
        for transfer_id in sorted(grouped, key=str):
            legs = sorted(grouped[transfer_id], key=lambda e: (e.created_at, str(e.id)))
            summary = self._check_transfer(
                transfer_id,
                legs,
                transient=transient,
                roles=roles,
                tz=tz,
                rates=rates,
                ambiguous=ambiguous,
                out=out,
            )
            if summary is not None:
                transfers.append(summary)
        _check_duplicate_instructions(transfers, out)

        return ReconciliationResult(
            contract_version=CONTRACT_VERSION,
            engine_name=ENGINE_NAME,
            engine_version=ENGINE_VERSION,
            ruleset_version=RULESET_VERSION,
            request_id=request.request_id,
            snapshot_sha256=request.snapshot_sha256,
            transfers_checked=len(grouped),
            control_totals=_control_totals(request.entries, transient, roles),
            checks=sorted(out.checks, key=lambda c: (str(c.transfer_id), c.rule_id)),
            exceptions=sorted(
                out.exceptions, key=lambda e: (str(e.transfer_id), e.rule_id, e.exception_id)
            ),
            limitations=list(STANDING_LIMITATIONS),
        )

    def _check_transfer(
        self,
        transfer_id: UUID,
        legs: list[GLEntry],
        *,
        transient: str,
        roles: dict[str, AccountRole],
        tz: ZoneInfo,
        rates: dict[tuple[Currency, Currency, date], Decimal],
        ambiguous: set[tuple[Currency, Currency, date]],
        out: _Collector,
    ) -> _Transfer | None:
        def touches(e: GLEntry) -> bool:
            return transient in (e.debited_account, e.credited_account)

        def with_role(role: AccountRole) -> list[GLEntry]:
            return [
                e
                for e in legs
                if touches(e)
                and role in (roles.get(e.debited_account), roles.get(e.credited_account))
            ]

        inbound = [
            e
            for e in legs
            if e.credited_account == transient and roles.get(e.debited_account) is None
        ]
        outbound = [
            e
            for e in legs
            if e.debited_account == transient and roles.get(e.credited_account) is None
        ]
        fee_legs, vat_legs = with_role(AccountRole.FEE_INCOME), with_role(AccountRole.VAT)

        # R-DIR: entryType should say what happened to the transient account.
        wrong_direction = [
            e
            for e in legs
            if touches(e)
            and e.entry_type != ("CREDIT" if e.credited_account == transient else "DEBIT")
        ]
        out.check("R-DIR", transfer_id, not wrong_direction)
        if wrong_direction:
            out.raise_exception(
                rule_id="R-DIR",
                type=ExceptionType.ENTRY_DIRECTION_INCONSISTENT,
                severity=Severity.LOW,
                transfer_id=transfer_id,
                records=[e.id for e in wrong_direction],
                details={"note": "meaning of entryType is unconfirmed by the bank"},
            )

        # R-DUP-ENTRY: the same movement posted twice inside one transfer.
        seen: dict[tuple[str, str, Currency, Decimal, str], list[UUID]] = defaultdict(list)
        for e in legs:
            key = (e.debited_account, e.credited_account, e.currency, e.amount, e.entry_type)
            seen[key].append(e.id)
        duplicates = [ids for ids in seen.values() if len(ids) > 1]
        out.check("R-DUP-ENTRY", transfer_id, not duplicates)
        for ids in duplicates:
            out.raise_exception(
                rule_id="R-DUP-ENTRY",
                type=ExceptionType.DUPLICATE_ENTRY,
                severity=Severity.HIGH,
                transfer_id=transfer_id,
                records=ids,
            )

        # R-STRUCT: exactly one funding leg and one payout leg.
        unexplained = [e for e in legs if not touches(e)]
        structure_ok = len(inbound) == 1 and len(outbound) == 1 and not unexplained
        out.check("R-STRUCT", transfer_id, structure_ok)
        if not structure_ok:
            out.raise_exception(
                rule_id="R-STRUCT",
                type=ExceptionType.STRUCTURE_INCOMPLETE,
                severity=Severity.HIGH,
                transfer_id=transfer_id,
                records=[e.id for e in legs],
                details={
                    "inbound_legs": str(len(inbound)),
                    "outbound_legs": str(len(outbound)),
                    "legs_not_touching_transient": str(len(unexplained)),
                },
            )
            return None
        src_in, dst_out = inbound[0], outbound[0]
        src, dst = src_in.currency, dst_out.currency
        rate_date = src_in.created_at.astimezone(tz).date()

        def rate(a: Currency, b: Currency) -> Decimal | None:
            if a is b:
                return Decimal(1)
            key = (a, b, rate_date)
            if key in ambiguous:
                out.raise_exception(
                    rule_id="R-RATE",
                    type=ExceptionType.RATE_AMBIGUOUS,
                    severity=Severity.MEDIUM,
                    transfer_id=transfer_id,
                    records=[src_in.id],
                    details={"pair": f"{a}/{b}", "rate_date": rate_date.isoformat()},
                )
                return None
            if key not in rates:
                out.raise_exception(
                    rule_id="R-RATE",
                    type=ExceptionType.RATE_UNAVAILABLE,
                    severity=Severity.MEDIUM,
                    transfer_id=transfer_id,
                    records=[src_in.id],
                    details={"pair": f"{a}/{b}", "rate_date": rate_date.isoformat()},
                )
                return None
            return rates[key]

        charge = self._tariff.flat_charge
        charge_rate, fx_rate = rate(charge.currency, src), rate(src, dst)
        if charge_rate is None or fx_rate is None:
            out.check("R-RATE", transfer_id, False)
            return _Transfer(transfer_id, src_in, dst_out)
        out.check("R-RATE", transfer_id, True)

        rounding = self._tariff.rounding

        def q(value: Decimal, currency: Currency) -> Money:
            return Money(value, currency).quantize(rounding)

        expected_charge = q(charge.amount * charge_rate, src)
        principal = q((src_in.amount - expected_charge.amount) / (1 + self._tariff.vat_rate), src)
        expected_vat = q(principal.amount * self._tariff.vat_rate, src)
        expected_out = q(principal.amount * fx_rate, dst)

        fee_src = [e for e in fee_legs if e.currency is src]
        vat_src = [e for e in vat_legs if e.currency is src]
        posted_fee = _total(fee_src, src)
        posted_vat = _total(vat_src, src)

        for rule_id, missing_type, mismatch_type, posted_legs, expected in (
            (
                "R-FEE",
                ExceptionType.FEE_LEG_MISSING,
                ExceptionType.FEE_AMOUNT_MISMATCH,
                fee_src,
                expected_charge,
            ),
            (
                "R-VAT",
                ExceptionType.VAT_LEG_MISSING,
                ExceptionType.VAT_AMOUNT_MISMATCH,
                vat_src,
                expected_vat,
            ),
        ):
            if not posted_legs:
                out.check(rule_id, transfer_id, False)
                out.raise_exception(
                    rule_id=rule_id,
                    type=missing_type,
                    severity=Severity.HIGH,
                    transfer_id=transfer_id,
                    records=[src_in.id],
                    expected=expected,
                    actual=Money(Decimal(0), src),
                )
            else:
                _compare(
                    out,
                    rule_id,
                    mismatch_type,
                    transfer_id,
                    expected=expected,
                    actual=_total(posted_legs, src),
                    records=[src_in.id, *(e.id for e in posted_legs)],
                )

        _compare(
            out,
            "R-FX",
            ExceptionType.FX_CONVERSION_MISMATCH,
            transfer_id,
            expected=expected_out,
            actual=Money(dst_out.amount, dst),
            records=[src_in.id, dst_out.id],
            details={"rate": canonical_decimal(fx_rate)},
        )

        # R-RESIDUAL: source-currency money left in the transient account.
        residual = Money(src_in.amount, src) - principal - posted_fee - posted_vat
        minor = Decimal(1).scaleb(-src.minor_units)
        residual_ok = abs(residual.amount) <= minor
        out.check("R-RESIDUAL", transfer_id, residual_ok)
        if not residual_ok:
            out.raise_exception(
                rule_id="R-RESIDUAL",
                type=ExceptionType.TRANSIENT_RESIDUAL,
                severity=Severity.HIGH,
                transfer_id=transfer_id,
                records=[src_in.id, *(e.id for e in fee_src + vat_src)],
                expected=Money(Decimal(0), src),
                actual=residual,
                details={"implied_principal": canonical_decimal(principal.amount)},
            )

        # R-BOTH-CCY: fee/VAT legs also posted in the destination currency.
        if src is not dst:
            dest_legs = [e for e in fee_legs + vat_legs if e.currency is dst]
            out.check("R-BOTH-CCY", transfer_id, not dest_legs)
            if dest_legs:
                out.raise_exception(
                    rule_id="R-BOTH-CCY",
                    type=ExceptionType.FEE_VAT_IN_BOTH_CURRENCIES,
                    severity=Severity.MEDIUM,
                    transfer_id=transfer_id,
                    records=[e.id for e in dest_legs],
                    details={"note": "meaning of destination-currency fee/VAT legs is unconfirmed"},
                )
        return _Transfer(transfer_id, src_in, dst_out)


def _total(entries: list[GLEntry], currency: Currency) -> Money:
    total = Money(Decimal(0), currency)
    for e in entries:
        total = total + Money(e.amount, e.currency)
    return total


def _compare(
    out: _Collector,
    rule_id: str,
    mismatch: ExceptionType,
    transfer_id: UUID,
    *,
    expected: Money,
    actual: Money,
    records: list[UUID],
    details: dict[str, str] | None = None,
) -> None:
    difference = actual - expected
    minor = Decimal(1).scaleb(-expected.currency.minor_units)
    out.check(rule_id, transfer_id, difference.amount == 0)
    if difference.amount == 0:
        return
    is_rounding = abs(difference.amount) <= minor
    out.raise_exception(
        rule_id=rule_id,
        type=ExceptionType.ROUNDING_DIFFERENCE if is_rounding else mismatch,
        severity=Severity.LOW if is_rounding else Severity.HIGH,
        transfer_id=transfer_id,
        records=records,
        expected=expected,
        actual=actual,
        details={**(details or {}), "checked_as": mismatch.value},
    )


def _rate_book(
    quotes: list[RateQuote],
) -> tuple[dict[tuple[Currency, Currency, date], Decimal], set[tuple[Currency, Currency, date]]]:
    values: dict[tuple[Currency, Currency, date], set[Decimal]] = defaultdict(set)
    for quote in quotes:
        values[(quote.from_currency, quote.to_currency, quote.rate_date)].add(quote.rate)
    book = {key: next(iter(found)) for key, found in values.items() if len(found) == 1}
    ambiguous = {key for key, found in values.items() if len(found) > 1}
    return book, ambiguous


def _check_duplicate_instructions(transfers: list[_Transfer], out: _Collector) -> None:
    """R-DUP-INSTR: the same customer, beneficiary and amounts within a short window."""
    groups: dict[tuple[object, ...], list[_Transfer]] = defaultdict(list)
    for t in transfers:
        key = (
            t.inbound.debited_account,
            t.outbound.credited_account,
            t.inbound.currency,
            t.inbound.amount,
            t.outbound.currency,
            t.outbound.amount,
        )
        groups[key].append(t)
    for group in groups.values():
        group.sort(key=lambda t: (t.inbound.created_at, str(t.transfer_id)))
        for earlier, later in pairwise(group):
            gap = later.inbound.created_at - earlier.inbound.created_at
            if gap <= DUPLICATE_INSTRUCTION_WINDOW:
                out.raise_exception(
                    rule_id="R-DUP-INSTR",
                    type=ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE,
                    severity=Severity.HIGH,
                    transfer_id=later.transfer_id,
                    related=[earlier.transfer_id],
                    records=[earlier.inbound.id, later.inbound.id],
                    details={"seconds_apart": str(int(gap.total_seconds()))},
                )


@dataclass(slots=True)
class _Bucket:
    debits: Decimal = Decimal(0)
    credits: Decimal = Decimal(0)
    count: int = 0


def _control_totals(
    entries: list[GLEntry], transient: str, roles: dict[str, AccountRole]
) -> list[ControlTotal]:
    """Money moved out of and into the transient account, per counterpart role and
    currency. Customer and beneficiary accounts are grouped as OTHER."""
    buckets: dict[tuple[str, Currency], _Bucket] = defaultdict(_Bucket)
    for e in entries:
        if e.debited_account == transient:
            other, outgoing = e.credited_account, True
        elif e.credited_account == transient:
            other, outgoing = e.debited_account, False
        else:
            continue
        role = roles.get(other)
        bucket = buckets[(role.value if role else "OTHER", e.currency)]
        if outgoing:
            bucket.debits += e.amount
        else:
            bucket.credits += e.amount
        bucket.count += 1
    return [
        ControlTotal(
            account_role=role,
            currency=currency,
            debits_from_transient=Amount(amount=bucket.debits, currency=currency),
            credits_to_transient=Amount(amount=bucket.credits, currency=currency),
            entry_count=bucket.count,
        )
        for (role, currency), bucket in sorted(buckets.items())
    ]
