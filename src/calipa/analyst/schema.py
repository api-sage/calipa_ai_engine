"""What P2 (the Evidence Analyst) returns.

The answer is split into claims so that code can check each one: an observation
states what evidence shows, an inference builds only on observations. Values never
appear as text; they are placeholders such as {E23.expected} that code fills in.
Hypotheses name a root-cause category from a fixed list and say which exceptions
they explain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceType = Literal[
    "engine_exception",
    "gl_entry",
    "derived_fact",
    "rate_snapshot",
    "tariff_version",
    "account_catalog",
    # Not available to Calipa yet; may only appear in would_confirm / would_refute.
    "reversal_record",
    "switch_log",
    "settlement_record",
    "transaction_status",
    "ops_confirmation",
]

RootCause = Literal[
    "fee_or_vat_leg_missing",
    "fee_or_vat_leg_duplicated_across_ccy",
    "tariff_applied_mismatch",
    "transient_residual_unallocated",
    "fx_rate_applied_mismatch",
    "rounding_difference",
    "duplicate_customer_instruction",
    "duplicate_posting_same_instruction",
    "entry_direction_inconsistency",
    "timing_cutoff_difference",
    "wrong_account_mapping",
    "missing_reversal",
    "partial_reversal",
    "orphan_reversal",
    "switch_timeout_unposted",
    "posted_not_settled",
    "manual_adjustment",
    "unexplained",
]

# Calipa can only read. None of these changes data.
NextStep = Literal[
    "request_reversal_data",
    "request_switch_logs",
    "request_settlement_report",
    "confirm_tariff_with_finance",
    "confirm_entry_semantics_with_core_banking",
    "check_customer_instruction_with_ops",
    "escalate_to_auditor",
]

MAX_HYPOTHESES = 5
MAX_CLAIMS = 40


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Claim(_Model):
    claim_id: str = Field(max_length=8)  # C1..Cn
    kind: Literal["observation", "inference"]
    text: str = Field(max_length=600)  # values only as {E<n>.<field>} placeholders
    evidence_ids: list[str]  # observation: exactly the items its placeholders cite
    based_on: list[str]  # inference: observation claim ids; observation: none


class Hypothesis(_Model):
    rank: int
    category: RootCause
    explains_exceptions: list[str]  # evidence ids of engine_exception items
    summary: str = Field(max_length=600)
    supporting_claims: list[str]
    contradicting_claims: list[str]
    confidence: Literal["high", "medium", "low"]
    would_confirm: list[EvidenceType]
    would_refute: list[EvidenceType]


class ExceptionAnalysis(_Model):
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    hypotheses: list[Hypothesis]  # 1..MAX_HYPOTHESES, checked by code
    next_steps: list[NextStep]
    data_limitations: list[str]
