"""Root-cause categories: what each needs as evidence, whether Calipa can see that
evidence today, and which categories may explain each engine exception type.

A category whose evidence Calipa cannot see is never offered to P2. Its missing
evidence is listed as a known data gap instead, so P2 can name it in would_confirm.
"""

from __future__ import annotations

from dataclasses import dataclass

from calipa.analyst.schema import EvidenceType, RootCause
from calipa.engine.contract import ExceptionType

# Evidence types Calipa can put in a bundle today.
AVAILABLE_EVIDENCE: frozenset[EvidenceType] = frozenset(
    {
        "engine_exception",
        "gl_entry",
        "derived_fact",
        "rate_snapshot",
        "tariff_version",
        "account_catalog",
    }
)


@dataclass(frozen=True, slots=True)
class Category:
    name: RootCause
    description: str
    required: tuple[EvidenceType, ...]  # all needed for high confidence

    @property
    def available(self) -> bool:
        return set(self.required) <= AVAILABLE_EVIDENCE


_CATEGORIES = (
    Category(
        "fee_or_vat_leg_missing",
        "A charge or VAT entry the tariff requires was not posted.",
        ("gl_entry", "tariff_version"),
    ),
    Category(
        "fee_or_vat_leg_duplicated_across_ccy",
        "Charge or VAT entries were posted in both the source and destination currency.",
        ("gl_entry", "tariff_version"),
    ),
    Category(
        "tariff_applied_mismatch",
        "A charge or VAT entry was posted at an amount other than the tariff gives.",
        ("gl_entry", "tariff_version"),
    ),
    Category(
        "transient_residual_unallocated",
        "Money stayed in the transient account and was not allocated to any leg.",
        ("gl_entry", "derived_fact"),
    ),
    Category(
        "fx_rate_applied_mismatch",
        "The conversion used a rate other than the day's recorded rate.",
        ("gl_entry", "rate_snapshot"),
    ),
    Category(
        "rounding_difference",
        "The amounts differ by rounding only.",
        ("gl_entry", "derived_fact"),
    ),
    Category(
        "duplicate_customer_instruction",
        "The customer's instruction was processed twice as separate transfers.",
        ("gl_entry", "derived_fact"),
    ),
    Category(
        "duplicate_posting_same_instruction",
        "The same entry was posted more than once within one transfer.",
        ("gl_entry",),
    ),
    Category(
        "entry_direction_inconsistency",
        "The entry type does not match the direction the money moved.",
        ("gl_entry",),
    ),
    Category(
        "timing_cutoff_difference",
        "Legs of the transfer fall on different days or outside the audited period.",
        ("gl_entry",),
    ),
    Category(
        "wrong_account_mapping",
        "An entry was posted to an account with a different role than expected.",
        ("gl_entry", "account_catalog"),
    ),
    Category(
        "missing_reversal", "A reversal that should exist was not posted.", ("reversal_record",)
    ),
    Category("partial_reversal", "Only part of the transfer was reversed.", ("reversal_record",)),
    Category(
        "orphan_reversal",
        "A reversal was posted without the original entry.",
        ("reversal_record",),
    ),
    Category(
        "switch_timeout_unposted",
        "The payment switch timed out and some legs were never posted.",
        ("switch_log",),
    ),
    Category(
        "posted_not_settled",
        "Entries were posted but the transfer did not settle.",
        ("settlement_record",),
    ),
    Category(
        "manual_adjustment",
        "Someone adjusted the entries by hand.",
        ("ops_confirmation",),
    ),
    Category(
        "unexplained",
        "Nothing on this list fits the evidence.",
        (),
    ),
)

CATEGORIES: dict[RootCause, Category] = {c.name: c for c in _CATEGORIES}

E = ExceptionType
_CANDIDATES: dict[ExceptionType, tuple[RootCause, ...]] = {
    E.FEE_LEG_MISSING: (
        "fee_or_vat_leg_missing",
        "timing_cutoff_difference",
        "wrong_account_mapping",
        "switch_timeout_unposted",
        "manual_adjustment",
    ),
    E.FEE_AMOUNT_MISMATCH: (
        "tariff_applied_mismatch",
        "duplicate_posting_same_instruction",
        "fx_rate_applied_mismatch",
        "partial_reversal",
        "manual_adjustment",
    ),
    E.FX_CONVERSION_MISMATCH: (
        "fx_rate_applied_mismatch",
        "timing_cutoff_difference",
        "manual_adjustment",
    ),
    E.ROUNDING_DIFFERENCE: ("rounding_difference", "fx_rate_applied_mismatch"),
    E.TRANSIENT_RESIDUAL: (
        "transient_residual_unallocated",
        "fee_or_vat_leg_missing",
        "tariff_applied_mismatch",
        "duplicate_posting_same_instruction",
        "timing_cutoff_difference",
        "missing_reversal",
        "partial_reversal",
    ),
    E.FEE_VAT_IN_BOTH_CURRENCIES: (
        "fee_or_vat_leg_duplicated_across_ccy",
        "wrong_account_mapping",
        "orphan_reversal",
    ),
    E.ENTRY_DIRECTION_INCONSISTENT: (
        "entry_direction_inconsistency",
        "fee_or_vat_leg_duplicated_across_ccy",
        "orphan_reversal",
    ),
    E.DUPLICATE_ENTRY: (
        "duplicate_posting_same_instruction",
        "partial_reversal",
        "missing_reversal",
    ),
    E.DUPLICATE_INSTRUCTION_CANDIDATE: (
        "duplicate_customer_instruction",
        "switch_timeout_unposted",
    ),
    E.STRUCTURE_INCOMPLETE: (
        "timing_cutoff_difference",
        "wrong_account_mapping",
        "switch_timeout_unposted",
        "posted_not_settled",
        "missing_reversal",
    ),
}
_CANDIDATES[E.VAT_LEG_MISSING] = _CANDIDATES[E.FEE_LEG_MISSING]
_CANDIDATES[E.VAT_AMOUNT_MISMATCH] = _CANDIDATES[E.FEE_AMOUNT_MISMATCH]

# The engine could not run the check, so there is nothing to explain yet: these are
# reported as checks that did not run, not analysed.
NOT_ANALYSED: frozenset[ExceptionType] = frozenset({E.RATE_UNAVAILABLE, E.RATE_AMBIGUOUS})

assert set(_CANDIDATES) | NOT_ANALYSED == set(ExceptionType)


def allowed_categories(exception_type: ExceptionType) -> tuple[RootCause, ...]:
    """Categories P2 may use for this exception type: the available candidates plus
    "unexplained"."""
    candidates = _CANDIDATES.get(exception_type, ())
    return (*(c for c in candidates if CATEGORIES[c].available), "unexplained")


def unavailable_categories(exception_type: ExceptionType) -> tuple[RootCause, ...]:
    return tuple(c for c in _CANDIDATES.get(exception_type, ()) if not CATEGORIES[c].available)
