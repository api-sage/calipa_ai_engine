"""What the API accepts and returns. These models are the contract with the UI: the
OpenAPI document generated from them is what its TypeScript types come from."""

from __future__ import annotations

from datetime import datetime
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


class ReportView(_Model):
    summary: ReportSummary
    executive_summary: SectionView
    findings: list[SectionView]
    patterns: list[PatternView]
    markdown: str


class Me(_Model):
    user: str
    can_initiate: bool
    can_review: bool
