"""The evidence builder: turns engine exceptions into bundles for P2.

Exceptions are grouped by transfer, and transfers the engine links (duplicate
instruction candidates) are merged into one group. Each group becomes a bundle of
evidence items with short ids (E1, E2, ...):

- engine_exception: what the engine flagged, with its numbers;
- gl_entry: the group's ledger entries, with account numbers replaced by role labels;
- derived_fact: comparisons computed here, so P2 never does arithmetic;
- rate_snapshot, tariff_version, account_catalog: the reference data that applies.

Every value is a string in one canonical form (Decimal without trailing zeros, UTC
ISO timestamps, currency codes), which is also the form V2 compares quotes against.
The same inputs in any order give the same bundle, byte for byte.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from calipa.analyst.categories import (
    CATEGORIES,
    NOT_ANALYSED,
    allowed_categories,
    unavailable_categories,
)
from calipa.analyst.schema import EvidenceType, RootCause
from calipa.analyst.support import ruled_out
from calipa.canonical import hash_value, normalize
from calipa.engine.contract import (
    Amount,
    EngineException,
    ExceptionType,
    ReconciliationRequest,
    Severity,
)
from calipa.gateway.fcy import GLEntry
from calipa.money import Currency, canonical_decimal
from calipa.reference import AccountCatalog, AccountRole, Tariff

SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}

STANDING_DATA_GAPS = (
    "Reversal records are not available to Calipa (the bank has no reversal API yet).",
    "Payment switch logs are not available to Calipa.",
    "Settlement records are not available to Calipa.",
    "Operations journals and records of manual adjustments are not available to Calipa.",
    "The bank has not confirmed what entryType means.",
    "The bank has not confirmed what charge and VAT entries in the destination currency mean.",
    "The tariff and the account roles were inferred from live data and are not yet "
    "confirmed by Finance.",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceItem(_Model):
    evidence_id: str
    type: EvidenceType
    fields: dict[str, str]


class GroupException(_Model):
    evidence_id: str  # the engine_exception item for it
    exception_id: str
    type: ExceptionType
    severity: Severity
    allowed_categories: list[RootCause]
    ruled_out: dict[RootCause, str]  # candidates the evidence contradicts, and why


class EvidenceBundle(_Model):
    """Exactly what P2 sees for one group. No account numbers."""

    group_id: str
    transfer_ids: list[UUID]
    exceptions: list[GroupException]
    items: list[EvidenceItem]
    known_data_gaps: list[str]

    def item(self, evidence_id: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.evidence_id == evidence_id), None)

    @property
    def severity(self) -> Severity:
        return min((e.severity for e in self.exceptions), key=SEVERITY_ORDER.__getitem__)

    @property
    def analysable(self) -> bool:
        return any(e.type not in NOT_ANALYSED for e in self.exceptions)

    @property
    def available_categories(self) -> list[RootCause]:
        seen: dict[RootCause, None] = {}
        for e in self.exceptions:
            seen.update(dict.fromkeys(e.allowed_categories))
        return list(seen)


@dataclass(frozen=True, slots=True)
class ExceptionGroup:
    bundle: EvidenceBundle
    labels: dict[str, str]  # role label -> account number; kept by code, never sent

    @property
    def sha256(self) -> str:
        return hash_value(self.bundle)


def build_groups(
    exceptions: Sequence[EngineException],
    request: ReconciliationRequest,
    *,
    tariff: Tariff,
    catalog: AccountCatalog,
    data_gaps: Iterable[str] = (),
) -> list[ExceptionGroup]:
    """One group per set of linked transfers, most severe first."""
    by_transfer: dict[UUID, list[GLEntry]] = defaultdict(list)
    for entry in request.entries:
        by_transfer[entry.transfer_id].append(entry)

    parent: dict[UUID, UUID] = {}

    def find(x: UUID) -> UUID:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in exceptions:
        for related in e.related_transfer_ids:
            a, b = sorted((find(e.transfer_id), find(related)), key=str)
            parent[b] = a
        find(e.transfer_id)

    members: dict[UUID, set[UUID]] = defaultdict(set)
    for transfer_id in parent:
        members[find(transfer_id)].add(transfer_id)
    flagged: dict[UUID, list[EngineException]] = defaultdict(list)
    for e in exceptions:
        flagged[find(e.transfer_id)].append(e)

    builder = _Builder(request, tariff=tariff, catalog=catalog, data_gaps=tuple(data_gaps))
    groups = [
        builder.build(sorted(members[root], key=str), flagged[root], by_transfer)
        for root in flagged
    ]
    return sorted(groups, key=lambda g: (SEVERITY_ORDER[g.bundle.severity], g.bundle.group_id))


class _Builder:
    def __init__(
        self,
        request: ReconciliationRequest,
        *,
        tariff: Tariff,
        catalog: AccountCatalog,
        data_gaps: tuple[str, ...],
    ) -> None:
        self.request = request
        self.tz = ZoneInfo(request.bank_timezone)
        self.roles = {n: AccountRole(r) for n, r in request.account_roles.items()}
        transients = [n for n, r in self.roles.items() if r is AccountRole.TRANSIENT]
        if len(transients) != 1:
            raise ValueError("exactly one TRANSIENT account is required")
        self.transient = transients[0]
        self.tariff = tariff
        self.catalog = catalog
        self.data_gaps = data_gaps

    def build(
        self,
        transfer_ids: list[UUID],
        flagged: list[EngineException],
        by_transfer: dict[UUID, list[GLEntry]],
    ) -> ExceptionGroup:
        exceptions = sorted(flagged, key=lambda e: (SEVERITY_ORDER[e.severity], e.exception_id))
        entries = sorted(
            (e for t in transfer_ids for e in by_transfer.get(t, [])),
            key=lambda e: (str(e.transfer_id), e.created_at, str(e.id)),
        )
        items: list[EvidenceItem] = []

        def add(kind: EvidenceType, fields: dict[str, str]) -> str:
            evidence_id = f"E{len(items) + 1}"
            items.append(EvidenceItem(evidence_id=evidence_id, type=kind, fields=fields))
            return evidence_id

        # Ids are fixed up front so exception items can point at their entries.
        exception_ref = {e.exception_id: f"E{i + 1}" for i, e in enumerate(exceptions)}
        entry_ref = {e.id: f"E{len(exceptions) + i + 1}" for i, e in enumerate(entries)}
        labels = self._labels(entries)
        label_of = {number: label for label, number in labels.items()}

        for e in exceptions:
            add("engine_exception", self._exception_fields(e, entry_ref))
        for entry in entries:
            add("gl_entry", self._entry_fields(entry, label_of))
        for transfer_id in transfer_ids:
            legs = by_transfer.get(transfer_id, [])
            for fields in self._transfer_facts(transfer_id, legs):
                add("derived_fact", fields)
        for e in exceptions:
            for fields in self._exception_facts(e, exception_ref[e.exception_id], by_transfer):
                add("derived_fact", fields)
        for fields in self._rates(transfer_ids, by_transfer):
            add("rate_snapshot", fields)
        add("tariff_version", self._tariff_fields())
        for role, description in sorted(self.catalog.descriptions.items()):
            add("account_catalog", {"role": role.value, "description": description})

        everything = [(i.evidence_id, i.fields) for i in items]
        group_exceptions: list[GroupException] = []
        for e in exceptions:
            evidence_id = exception_ref[e.exception_id]
            contradicted = ruled_out(
                evidence_id,
                items[int(evidence_id[1:]) - 1].fields,
                everything,
                allowed_categories(e.type),
            )
            group_exceptions.append(
                GroupException(
                    evidence_id=evidence_id,
                    exception_id=e.exception_id,
                    type=e.type,
                    severity=e.severity,
                    allowed_categories=[
                        c for c in allowed_categories(e.type) if c not in contradicted
                    ],
                    ruled_out=contradicted,
                )
            )
        bundle = EvidenceBundle(
            group_id="G-" + hash_value(sorted(e.exception_id for e in exceptions))[:16],
            transfer_ids=transfer_ids,
            exceptions=group_exceptions,
            items=items,
            known_data_gaps=[*STANDING_DATA_GAPS, *self.data_gaps, *_category_gaps(exceptions)],
        )
        return ExceptionGroup(bundle=bundle, labels=labels)

    # --- accounts ---------------------------------------------------------------------

    def _labels(self, entries: list[GLEntry]) -> dict[str, str]:
        """Role labels for accounts outside the catalog, numbered by first appearance:
        CUSTOMER_n pays into the transient account, BENEFICIARY_n is paid from it."""
        labels: dict[str, str] = {}
        seen: set[str] = set()
        counters: Counter[str] = Counter()
        for e in entries:
            for number, kind in (
                (
                    e.debited_account,
                    "CUSTOMER" if e.credited_account == self.transient else "OTHER",
                ),
                (
                    e.credited_account,
                    "BENEFICIARY" if e.debited_account == self.transient else "OTHER",
                ),
            ):
                if number in self.roles or number in seen:
                    continue
                seen.add(number)
                counters[kind] += 1
                labels[f"{kind}_{counters[kind]}"] = number
        return labels

    def _label(self, number: str, label_of: dict[str, str]) -> str:
        role = self.roles.get(number)
        return role.value if role else label_of[number]

    def _funding(self, legs: list[GLEntry]) -> list[GLEntry]:
        return [
            e
            for e in legs
            if e.credited_account == self.transient and e.debited_account not in self.roles
        ]

    def _payout(self, legs: list[GLEntry]) -> list[GLEntry]:
        return [
            e
            for e in legs
            if e.debited_account == self.transient and e.credited_account not in self.roles
        ]

    def _with_role(self, legs: list[GLEntry], role: AccountRole) -> list[GLEntry]:
        return [
            e
            for e in legs
            if self.transient in (e.debited_account, e.credited_account)
            and role in (self.roles.get(e.debited_account), self.roles.get(e.credited_account))
        ]

    # --- items ------------------------------------------------------------------------

    def _exception_fields(self, e: EngineException, entry_ref: dict[UUID, str]) -> dict[str, str]:
        fields = {
            "exception_id": e.exception_id,
            "type": e.type.value,
            "severity": e.severity.value,
            "rule_id": e.rule_id,
            "transfer_id": str(e.transfer_id),
            "records": ", ".join(entry_ref.get(r, str(r)) for r in e.record_ids),
        }
        if e.related_transfer_ids:
            fields["related_transfer_ids"] = ", ".join(str(t) for t in e.related_transfer_ids)
        for name, amount in (
            ("expected", e.expected),
            ("actual", e.actual),
            ("difference", e.difference),
        ):
            if amount is not None:
                fields[f"{name}_amount"] = canonical_decimal(amount.amount)
                fields[f"{name}_currency"] = amount.currency.value
        for key, value in sorted(e.details.items()):
            fields[f"detail_{key}"] = value
        return fields

    def _entry_fields(self, e: GLEntry, label_of: dict[str, str]) -> dict[str, str]:
        return {
            "entry_id": str(e.id),
            "transfer_id": str(e.transfer_id),
            "reference": e.external_reference,
            "debited": self._label(e.debited_account, label_of),
            "credited": self._label(e.credited_account, label_of),
            "entry_type": e.entry_type,
            "currency": e.currency.value,
            "amount": canonical_decimal(e.amount),
            "created_at": _iso(e.created_at),
        }

    def _transfer_facts(self, transfer_id: UUID, legs: list[GLEntry]) -> list[dict[str, str]]:
        facts: list[dict[str, str]] = []
        currencies = sorted({e.currency for e in legs})
        for currency in currencies:
            credits = sum(
                (
                    e.amount
                    for e in legs
                    if e.currency is currency and e.credited_account == self.transient
                ),
                Decimal(0),
            )
            debits = sum(
                (
                    e.amount
                    for e in legs
                    if e.currency is currency and e.debited_account == self.transient
                ),
                Decimal(0),
            )
            facts.append(
                {
                    "fact": "transient_movement",
                    "transfer_id": str(transfer_id),
                    "currency": currency.value,
                    "credits_to_transient": canonical_decimal(credits),
                    "debits_from_transient": canonical_decimal(debits),
                    "net": canonical_decimal(credits - debits),
                }
            )
        fee_vat = self._with_role(legs, AccountRole.FEE_INCOME) + self._with_role(
            legs, AccountRole.VAT
        )
        touching = [e for e in legs if self.transient in (e.debited_account, e.credited_account)]
        facts.append(
            {
                "fact": "leg_counts",
                "transfer_id": str(transfer_id),
                "funding_legs": str(len(self._funding(legs))),
                "payout_legs": str(len(self._payout(legs))),
                "fee_legs": str(len(self._with_role(legs, AccountRole.FEE_INCOME))),
                "vat_legs": str(len(self._with_role(legs, AccountRole.VAT))),
                "legs_not_touching_transient": str(len(legs) - len(touching)),
                "fee_and_vat_leg_currencies": ", ".join(sorted({e.currency.value for e in fee_vat}))
                or "none",
            }
        )
        if legs:
            times = sorted(e.created_at for e in legs)
            facts.append(
                {
                    "fact": "timing",
                    "transfer_id": str(transfer_id),
                    "first_entry_at": _iso(times[0]),
                    "last_entry_at": _iso(times[-1]),
                    "seconds_first_to_last": str(int((times[-1] - times[0]).total_seconds())),
                    "bank_timezone": self.request.bank_timezone,
                    "local_dates": ", ".join(
                        sorted({t.astimezone(self.tz).date().isoformat() for t in times})
                    ),
                }
            )
        return facts

    def _exception_facts(
        self,
        e: EngineException,
        evidence_id: str,
        by_transfer: dict[UUID, list[GLEntry]],
    ) -> list[dict[str, str]]:
        facts: list[dict[str, str]] = []
        if e.expected is not None and e.actual is not None:
            facts.append(_comparison(evidence_id, e.expected, e.actual, e.details.get("rate")))
        if e.type is ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE:
            for related in e.related_transfer_ids:
                facts.append(
                    self._siblings(
                        e.transfer_id,
                        related,
                        by_transfer.get(e.transfer_id, []),
                        by_transfer.get(related, []),
                    )
                )
        return facts

    def _siblings(
        self, transfer_id: UUID, related: UUID, legs: list[GLEntry], other: list[GLEntry]
    ) -> dict[str, str]:
        fact = {
            "fact": "sibling_transfers",
            "transfer_id": str(transfer_id),
            "related_transfer_id": str(related),
        }
        fund_a, fund_b = self._funding(legs), self._funding(other)
        pay_a, pay_b = self._payout(legs), self._payout(other)
        if len(fund_a) == len(fund_b) == 1:
            a, b = fund_a[0], fund_b[0]
            fact["seconds_apart"] = str(int(abs((a.created_at - b.created_at).total_seconds())))
            fact["same_customer"] = _yes(a.debited_account == b.debited_account)
            fact["same_funding_amount"] = _yes((a.currency, a.amount) == (b.currency, b.amount))
        if len(pay_a) == len(pay_b) == 1:
            a, b = pay_a[0], pay_b[0]
            fact["same_beneficiary"] = _yes(a.credited_account == b.credited_account)
            fact["same_payout_amount"] = _yes((a.currency, a.amount) == (b.currency, b.amount))
        return fact

    def _rates(
        self, transfer_ids: list[UUID], by_transfer: dict[UUID, list[GLEntry]]
    ) -> list[dict[str, str]]:
        wanted: set[tuple[Currency, Currency, str]] = set()
        charge_currency = self.tariff.flat_charge.currency
        for transfer_id in transfer_ids:
            legs = by_transfer.get(transfer_id, [])
            funding, payout = self._funding(legs), self._payout(legs)
            if len(funding) != 1:
                continue
            src = funding[0].currency
            day = funding[0].created_at.astimezone(self.tz).date().isoformat()
            if charge_currency is not src:
                wanted.add((charge_currency, src, day))
            if len(payout) == 1 and payout[0].currency is not src:
                wanted.add((src, payout[0].currency, day))
        rows = {
            (
                q.from_currency.value,
                q.to_currency.value,
                q.rate_date.isoformat(),
                canonical_decimal(q.rate),
                q.source,
            )
            for q in self.request.rates
            if (q.from_currency, q.to_currency, q.rate_date.isoformat()) in wanted
        }
        return [
            {"from_currency": a, "to_currency": b, "rate_date": d, "rate": r, "source": s}
            for a, b, d, r, s in sorted(rows)
        ]

    def _tariff_fields(self) -> dict[str, str]:
        return {
            "version": self.tariff.version,
            "flat_charge_amount": canonical_decimal(self.tariff.flat_charge.amount),
            "flat_charge_currency": self.tariff.flat_charge.currency.value,
            "vat_rate": canonical_decimal(self.tariff.vat_rate),
            "vat_base": "principal",
            "rounding": self.tariff.rounding,
        }


def _comparison(
    evidence_id: str, expected: Amount, actual: Amount, rate: str | None
) -> dict[str, str]:
    difference = actual.amount - expected.amount
    minor = Decimal(1).scaleb(-expected.currency.minor_units)
    fact = {
        "fact": "amount_comparison",
        "exception": evidence_id,
        "currency": expected.currency.value,
        "expected": canonical_decimal(expected.amount),
        "actual": canonical_decimal(actual.amount),
        "difference": canonical_decimal(difference),
        "difference_in_minor_units": canonical_decimal(difference / minor),
    }
    if expected.amount != 0:
        relative = (difference / expected.amount).quantize(Decimal("0.000001"))
        fact["relative_difference"] = canonical_decimal(relative)
        if rate is not None:
            implied = (Decimal(rate) * actual.amount / expected.amount).quantize(Decimal("1e-8"))
            fact["recorded_rate"] = rate
            fact["rate_implied_by_actual"] = canonical_decimal(implied)
    return fact


def _category_gaps(exceptions: list[EngineException]) -> list[str]:
    missing: dict[RootCause, None] = {}
    for e in exceptions:
        missing.update(dict.fromkeys(unavailable_categories(e.type)))
    return [
        f"{name} cannot be chosen: it needs {', '.join(CATEGORIES[name].required)}, "
        "which Calipa does not have."
        for name in missing
    ]


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _iso(moment: datetime) -> str:
    text = normalize(moment)  # UTC, "Z" suffix, as everywhere else Calipa hashes
    assert isinstance(text, str)
    return text
