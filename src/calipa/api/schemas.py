"""What the API accepts and returns. These models are the contract with the UI: the
OpenAPI document generated from them is what its TypeScript types come from."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from calipa.orchestrator.lifecycle import RunState


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- requests --------------------------------------------------------------------------


class CreateRun(_Model):
    request_text: str = Field(min_length=1, max_length=4_000)
    # Chosen by the client, once per submission: sending it again returns the same run.
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class ClarificationAnswer(_Model):
    answer: str = Field(min_length=1, max_length=4_000)


class ReviewRequest(_Model):
    decision: Literal["approved", "rejected", "reinvestigate"]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # the version the reviewer read
    note: str = Field(default="", max_length=4_000)
    acknowledge_flags: bool = False


# --- responses -------------------------------------------------------------------------


class ReportSummary(_Model):
    version: int
    report_sha256: str
    markdown_sha256: str
    narrative_source: Literal["ai", "partial", "template"]
    flags: list[str]
    created_at: datetime


class ReviewView(_Model):
    review_id: UUID
    decision: Literal["approved", "rejected", "reinvestigate"]
    reviewer: str
    note: str
    report_sha256: str
    flags_acknowledged: bool
    child_run_id: UUID | None
    created_at: datetime


class VersionView(_Model):
    run_id: UUID
    version: int
    report_sha256: str
    state: RunState


class RunView(_Model):
    run_id: UUID
    state: RunState | None
    initiated_by: str
    request_text: str
    created_at: datetime
    parent_run_id: UUID | None
    created: bool  # False when the idempotency key returned an existing run
    question: str | None  # set while the run waits for the auditor's answer
    reason: str | None  # why the run ended, for runs that did not reach a report
    report: ReportSummary | None
    review: ReviewView | None
    versions: list[VersionView]  # every report version of this audit
    can_answer: bool
    can_review: bool


class RunListItem(_Model):
    run_id: UUID
    state: RunState | None
    initiated_by: str
    request_text: str
    created_at: datetime
    parent_run_id: UUID | None


class EventView(_Model):
    seq: int
    event_type: str
    state: str | None
    actor: str
    created_at: datetime
    payload: dict[str, Any]


class EventsView(_Model):
    chain_ok: bool  # every event's hash recomputed and linked
    events: list[EventView]


class SectionView(_Model):
    ref: str
    source: Literal["ai", "template"]
    text: str  # plain text; show as text, never as HTML


class PatternView(_Model):
    finding_keys: list[str]
    text: str


class MoneyView(_Model):
    amount: str  # exact decimal, as stored; never a float
    currency: str


class ScopeView(_Model):
    objective: str
    period_start: date
    period_end: date
    timezone: str
    period_rule: str
    currencies: list[str]  # empty: all
    transaction_references: list[str]  # empty: all
    control_ids: list[str]  # empty: all
    assumptions: list[str]
    approved_by: str


class DataView(_Model):
    status: Literal["complete", "partial"]
    snapshot_sha256: str
    entries_in_period: int
    entries_in_scope: int
    pages: int


class RuleCount(_Model):
    rule_id: str
    passed: int
    failed: int


class ControlTotalView(_Model):
    account_role: str
    currency: str
    credits_to_transient: str
    debits_from_transient: str
    entries: int


class ReconciliationView(_Model):
    engine: str
    snapshot_sha256: str
    transfers_checked: int
    exceptions_in_scope: int
    exceptions_outside_scope: int
    rules: list[RuleCount]
    control_totals: list[ControlTotalView]


class ExceptionView(_Model):
    exception_id: str
    label: str
    severity: Literal["critical", "high", "medium", "low"]
    rule_id: str
    transfer_id: str
    expected: MoneyView | None
    actual: MoneyView | None
    difference: MoneyView | None


class ClaimView(_Model):
    claim_id: str
    kind: Literal["observation", "inference"]
    text: str  # values filled in by code; checked by V2
    based_on: list[str]


class HypothesisView(_Model):
    rank: int
    category: str
    label: str
    confidence: Literal["high", "medium", "low"]
    summary: str
    supporting_claims: list[str]
    contradicting_claims: list[str]
    would_confirm: list[str]
    would_refute: list[str]


class AccountLabel(_Model):
    label: str
    account: str


class FindingDetail(_Model):
    key: str  # G1, G2, ...
    severity: Literal["critical", "high", "medium", "low"]
    status: Literal["analysed", "analysis_unavailable", "not_analysed"]
    reason: str
    narrative: SectionView
    exceptions: list[ExceptionView]
    hypotheses: list[HypothesisView]
    claims: list[ClaimView]
    next_steps: list[str]
    data_limitations: list[str]
    dropped_hypotheses: int
    uncovered_exceptions: list[str]
    reused_from_run: UUID | None
    accounts: list[AccountLabel]


class AICallView(_Model):
    step: str
    prompt: str
    model_requested: str
    model_served: str | None
    outcome: str | None
    reused: bool


class ReportDetail(_Model):
    """The data the report was built from, for the UI to lay out. Same inputs as the
    Markdown, read back from the run's artifacts and checked against its manifest."""

    scope: ScopeView
    data: DataView | None
    reconciliation: ReconciliationView
    findings: list[FindingDetail]
    limitations: list[str]
    ai_calls: list[AICallView]


class ReportView(_Model):
    summary: ReportSummary
    executive_summary: SectionView
    findings: list[SectionView]
    patterns: list[PatternView]
    detail: ReportDetail
    markdown: str


class Me(_Model):
    user: str
    can_initiate: bool
    can_review: bool
    account_roles: list[str]  # sorted
    objectives: list[str]  # sorted
    max_period_days: int


class ControlView(_Model):
    control_id: str
    name: str
    exception_types: list[str]
    objectives: list[str]  # the objectives that run this control


class AccountView(_Model):
    account_number: str
    role: str
    description: str


class TariffView(_Model):
    flat_charge: MoneyView
    vat_rate: str  # exact decimal, e.g. "0.075"
    rounding: str


class ReferenceView(_Model):
    """What Calipa checks against: the control catalogue, the accounts and the tariff."""

    controls: list[ControlView]
    objectives: dict[str, list[str]]  # objective -> control ids
    accounts: list[AccountView]
    tariff: TariffView
    versions: dict[str, str]  # file -> version
