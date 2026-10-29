"""What people do to a run: answer a clarifying question, and review its report.

Every rule is decided by code from the entitlements file and the audit trail, never
by a model:

- Only the person who started a run answers its questions.
- A reviewer must be entitled to review and to the run's accounts, and may not be
  anyone who shaped the run's scope: its initiator, whoever answered its questions,
  or the reviewer who sent it back for re-investigation.
- A decision names the report hash the reviewer saw; if the report is not that one,
  nothing happens. A flagged report is approved only with its flags acknowledged.
- A decision is final. Re-investigation starts a new linked run, started by the
  reviewer, whose report is the next version; the reviewed version stays as it is.

Each action runs in one transaction under the run's lock, so two reviewers cannot
both decide, and a decision and its state change are never half-written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import psycopg

from calipa.audit_trail.artifacts import latest_artifact
from calipa.audit_trail.store import Run, append_event, create_run, get_run, list_events
from calipa.orchestrator.lifecycle import RunState, current_state, start, transition
from calipa.orchestrator.steps import APPROVED_SCOPE, ApprovedScope
from calipa.reference import Entitlements
from calipa.review.store import Decision, Review, insert_review, report_version

MAX_TEXT = 4_000
_OUTCOME: dict[Decision, RunState] = {
    "approved": RunState.APPROVED,
    "rejected": RunState.REJECTED,
    "reinvestigate": RunState.REINVESTIGATION,
}


class Forbidden(Exception):
    """The person may not do this."""


class Conflict(Exception):
    """The run is not in a state, or the report not the version, the action assumes."""


class Invalid(ValueError):
    """The request itself is malformed."""


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    review: Review
    state: RunState
    child_run_id: UUID | None


def scope_authors(conn: psycopg.Connection, run: Run) -> set[str]:
    """Everyone who shaped what this run audits."""
    authors = {run.initiated_by}
    for event in list_events(conn, run.run_id):
        if event.event_type in ("clarification_answered", "reinvestigation_of"):
            authors.add(event.actor)
    return authors


def can_view(conn: psycopg.Connection, run: Run, user: str, entitlements: Entitlements) -> bool:
    if user in scope_authors(conn, run):
        return True
    grant = entitlements.grant_for(user)
    if not grant.can_review:
        return False
    scope = latest_artifact(conn, run.run_id, APPROVED_SCOPE, ApprovedScope)
    return scope is None or set(scope[1].account_roles) <= grant.account_roles


def answer_clarification(
    conn: psycopg.Connection, run_id: UUID, *, user: str, answer: str
) -> RunState:
    answer = answer.strip()
    if not answer or len(answer) > MAX_TEXT:
        raise Invalid(f"the answer must be 1 to {MAX_TEXT} characters")
    with conn.transaction():
        run = _locked(conn, run_id)
        if user != run.initiated_by:
            raise Forbidden("only the person who started the run answers its questions")
        _expect(conn, run_id, RunState.CLARIFICATION_REQUESTED)
        append_event(
            conn,
            run_id,
            event_type="clarification_answered",
            actor=user,
            payload={"answer": answer},
        )
        transition(
            conn,
            run_id,
            RunState.INTERPRETING,
            actor=user,
            expected_from=RunState.CLARIFICATION_REQUESTED,
        )
    return RunState.INTERPRETING


def review(
    conn: psycopg.Connection,
    run_id: UUID,
    *,
    reviewer: str,
    decision: Decision,
    note: str,
    report_sha256: str,
    acknowledge_flags: bool,
    entitlements: Entitlements,
    now: datetime,
) -> ReviewOutcome:
    note = note.strip()
    if len(note) > MAX_TEXT:
        raise Invalid(f"the note must be at most {MAX_TEXT} characters")
    if decision != "approved" and not note:
        raise Invalid("say why when rejecting or sending back for re-investigation")

    with conn.transaction():
        run = _locked(conn, run_id)
        _expect(conn, run_id, RunState.PENDING_REVIEW)
        report = report_version(conn, run_id)
        if report is None:
            raise Conflict("the run has no report to review")
        if report_sha256 != report.report_sha256:
            raise Conflict("the report has changed since you opened it; reload it")

        grant = entitlements.grant_for(reviewer)
        if not grant.can_review:
            raise Forbidden(f"{reviewer} is not entitled to review reports")
        if reviewer in scope_authors(conn, run):
            raise Forbidden(
                "a reviewer cannot be the person who started the run or shaped its scope"
            )
        scope = latest_artifact(conn, run_id, APPROVED_SCOPE, ApprovedScope)
        if scope is not None:
            outside = sorted(set(scope[1].account_roles) - grant.account_roles)
            if outside:
                raise Forbidden(f"{reviewer} is not entitled to the accounts {', '.join(outside)}")
        if decision == "approved" and report.flagged and not acknowledge_flags:
            raise Conflict("this report is flagged; acknowledge its flags to approve it")

        child: UUID | None = None
        if decision == "reinvestigate":
            child = _reinvestigation(conn, run, reviewer=reviewer, note=note, report=report_sha256)
        record = Review(
            review_id=uuid4(),
            run_id=run_id,
            report_sha256=report_sha256,
            decision=decision,
            reviewer=reviewer,
            note=note,
            flags_acknowledged=acknowledge_flags,
            child_run_id=child,
            created_at=now,
        )
        insert_review(conn, record)
        state = _OUTCOME[decision]
        transition(
            conn,
            run_id,
            state,
            actor=reviewer,
            expected_from=RunState.PENDING_REVIEW,
            payload={
                "review_id": str(record.review_id),
                "decision": decision,
                "report_sha256": report_sha256,
                "version": report.version,
                "child_run_id": str(child) if child else None,
            },
        )
    return ReviewOutcome(record, state, child)


def _reinvestigation(
    conn: psycopg.Connection, run: Run, *, reviewer: str, note: str, report: str
) -> UUID:
    """A new run for the same request, linked to this one, started by the reviewer:
    their note goes to P1 with the previous scope (interpreter.step.build_conversation),
    the policy checks their entitlements, and as its initiator they cannot approve it.
    A reviewer who may not start audits therefore gets a denied re-investigation; who
    may re-investigate is still an open decision for the bank."""
    child = create_run(
        conn,
        idempotency_key=f"reinvestigation:{run.run_id}",
        initiated_by=reviewer,
        request_text=run.request_text,
        parent_run_id=run.run_id,
    )
    start(conn, child.run_id, actor=reviewer)
    append_event(
        conn,
        child.run_id,
        event_type="reinvestigation_of",
        actor=reviewer,
        payload={
            "parent_run_id": str(run.run_id),
            "parent_report_sha256": report,
            "reviewer_note": note,
        },
    )
    return child.run_id


def _locked(conn: psycopg.Connection, run_id: UUID) -> Run:
    run = get_run(conn, run_id)  # RunNotFound for an unknown run
    conn.execute("select 1 from runs where run_id = %s for update", (run_id,))
    return run


def _expect(conn: psycopg.Connection, run_id: UUID, state: RunState) -> None:
    current = current_state(conn, run_id)
    if current is not state:
        raise Conflict(f"the run is {current}, not {state}")
