"""The support check: whether the evidence can bear a category's meaning at all.

V2 checks that every claim cites real evidence correctly. It cannot tell that a true
value sits under the wrong label: "the customer sent it twice" citing a sibling fact
that says same_customer: no is well-formed and wrong. These rules read the bundle's
own facts and rule a category out for an exception when the evidence contradicts
what the category means. A ruled-out category is never offered to P2, so V2's
category check rejects it, and the reason is shown to P2 and kept in the bundle.

Rules only rule out on positive contradiction. Where a fact is absent (for example
the transfer has no single funding leg) they say nothing.

Separately, `unrelated_support` fails a hypothesis that leans on a claim citing only
transfers other than the ones it explains.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal

from calipa.analyst.schema import RootCause

Fields = dict[str, str]
# (the exception's own item, every item about its transfer) -> why, or None
Rule = Callable[[Fields, list[Fields]], str | None]


def _facts(items: list[Fields], fact: str) -> list[Fields]:
    return [f for f in items if f.get("fact") == fact]


def _duplicate_instruction(exc: Fields, items: list[Fields]) -> str | None:
    siblings = [
        f
        for f in _facts(items, "sibling_transfers")
        if f["transfer_id"] == exc["transfer_id"] and "same_customer" in f
    ]
    if siblings and all(
        f["same_customer"] == "no" or f.get("same_funding_amount") == "no" for f in siblings
    ):
        return "no related transfer has the same customer and the same funding amount"
    return None


def _duplicated_across_ccy(exc: Fields, items: list[Fields]) -> str | None:
    legs = _facts(items, "leg_counts")
    if legs and all(", " not in f["fee_and_vat_leg_currencies"] for f in legs):
        return "the charge and VAT legs are not in more than one currency"
    return None


def _duplicate_posting(exc: Fields, items: list[Fields]) -> str | None:
    entries = [f for f in items if "entry_id" in f]
    keys = [(f["debited"], f["credited"], f["currency"], f["amount"]) for f in entries]
    if entries and len(set(keys)) == len(keys):
        return "no two entries share accounts, currency and amount"
    return None


def _residual(exc: Fields, items: list[Fields]) -> str | None:
    moves = _facts(items, "transient_movement")
    if moves and all(Decimal(f["net"]) == 0 for f in moves):
        return "nothing is left in TRANSIENT"
    return None


def _leg_missing(exc: Fields, items: list[Fields]) -> str | None:
    legs = _facts(items, "leg_counts")
    if legs and all(int(f["fee_legs"]) >= 1 and int(f["vat_legs"]) >= 1 for f in legs):
        return "the transfer has both a charge leg and a VAT leg"
    return None


def _rate_mismatch(exc: Fields, items: list[Fields]) -> str | None:
    comparisons = [
        f
        for f in _facts(items, "amount_comparison")
        if f.get("exception") == exc["_evidence_id"] and "recorded_rate" in f
    ]
    if comparisons and all(
        Decimal(f["recorded_rate"]) == Decimal(f["rate_implied_by_actual"]) for f in comparisons
    ):
        return "the amount implies exactly the recorded rate"
    return None


RULES: dict[RootCause, Rule] = {
    "duplicate_customer_instruction": _duplicate_instruction,
    "fee_or_vat_leg_duplicated_across_ccy": _duplicated_across_ccy,
    "duplicate_posting_same_instruction": _duplicate_posting,
    "transient_residual_unallocated": _residual,
    "fee_or_vat_leg_missing": _leg_missing,
    "fx_rate_applied_mismatch": _rate_mismatch,
}


def ruled_out(
    evidence_id: str,
    exception_fields: Fields,
    items: Iterable[tuple[str, Fields]],
    candidates: Iterable[RootCause],
) -> dict[RootCause, str]:
    """Candidates the evidence about this exception (its transfer's items and its own
    comparison) contradicts, with why. `items` is every (evidence id, fields) in the
    bundle."""
    transfer = exception_fields["transfer_id"]
    about = [
        f for _, f in items if f.get("transfer_id") == transfer or f.get("exception") == evidence_id
    ]
    exc = {**exception_fields, "_evidence_id": evidence_id}
    out: dict[RootCause, str] = {}
    for category in candidates:
        rule = RULES.get(category)
        reason = rule(exc, about) if rule else None
        if reason is not None:
            out[category] = reason
    return out


def concerns(fields: Fields) -> set[str]:
    """The transfers an item is about; empty for reference data (tariff, rates,
    account catalog), which concerns every transfer."""
    out = {fields["transfer_id"]} if "transfer_id" in fields else set()
    out.update(t for t in fields.get("related_transfer_ids", "").split(", ") if t)
    if "related_transfer_id" in fields:
        out.add(fields["related_transfer_id"])
    return out


def unrelated_support(cited: Iterable[Fields], explained: Iterable[Fields]) -> bool:
    """True when a claim cites evidence about transfers, and none of it is about a
    transfer the hypothesis explains. Reference data alone is never unrelated."""
    wanted = set().union(*(concerns(f) for f in explained))
    scoped = [t for t in (concerns(f) for f in cited) if t]
    return bool(scoped) and not any(t & wanted for t in scoped)
