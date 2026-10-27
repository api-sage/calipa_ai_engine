from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from calipa.audit_trail.store import create_run, get_run, list_events, now_utc, verify_chain
from calipa.errors import RunNotFound
from calipa.orchestrator.lifecycle import (
    RunState as S,
)
from calipa.orchestrator.lifecycle import (
    TransitionNotAllowed,
    current_state,
    start,
    transition,
)
from calipa.reference import load_entitlements
from calipa.review.service import (
    Forbidden,
    Invalid,
    ReviewOutcome,
    answer_clarification,
    review,
)
from calipa.review.store import Decision, report_version
from tests.test_review import stand_in_report

# The initiator can also review, so these tests reach the segregation-of-duties check.
AUDITOR, REVIEWER = "lead@bank", "reviewer@bank"
ENTITLEMENTS = load_entitlements()


def new_run(conn: psycopg.Connection, *, to: S | None = None) -> UUID:
    run = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by=AUDITOR, request_text="audit fees"
    )
    start(conn, run.run_id, actor="system")
    path = {
        S.INTERPRETING: [S.INTERPRETING],
        S.CLARIFICATION_REQUESTED: [S.INTERPRETING, S.CLARIFICATION_REQUESTED],
        S.PENDING_REVIEW: [
            S.INTERPRETING,
            S.EXTRACTING,
            S.RECONCILING,
            S.ANALYSING,
            S.REPORTING,
            S.PENDING_REVIEW,
        ],
    }
    for state in path.get(to, []) if to else []:
        transition(conn, run.run_id, state, actor="system")
    return run.run_id


def test_happy_path_and_state_is_derived_from_events(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.PENDING_REVIEW)
    assert current_state(conn, run_id) is S.PENDING_REVIEW
    transitions = [e for e in list_events(conn, run_id) if e.event_type == "transition"]
    assert [e.state for e in transitions] == [
        "RECEIVED",
        "INTERPRETING",
        "EXTRACTING",
        "RECONCILING",
        "ANALYSING",
        "REPORTING",
        "PENDING_REVIEW",
    ]
    assert transitions[2].payload == {"from": "INTERPRETING", "to": "EXTRACTING"}
    assert verify_chain(conn, run_id).ok


@pytest.mark.parametrize(
    ("start_at", "target"),
    [
        (S.INTERPRETING, S.RECONCILING),  # skipping extraction
        (S.INTERPRETING, S.APPROVED),  # approving without a report
        (S.PENDING_REVIEW, S.EXTRACTING),
    ],
)
def test_illegal_transitions_are_refused(conn: psycopg.Connection, start_at: S, target: S) -> None:
    run_id = new_run(conn, to=start_at)
    with pytest.raises(TransitionNotAllowed):
        transition(conn, run_id, target, actor="system")
    assert current_state(conn, run_id) is start_at


def test_terminal_states_are_final(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.INTERPRETING)
    transition(conn, run_id, S.FAILED, actor="system")
    for target in S:
        with pytest.raises(TransitionNotAllowed):
            transition(conn, run_id, target, actor="system")


def test_stale_reads_are_refused(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.INTERPRETING)
    transition(conn, run_id, S.EXTRACTING, actor="worker-a")
    with pytest.raises(TransitionNotAllowed, match="expected INTERPRETING"):
        transition(conn, run_id, S.DENIED, actor="worker-b", expected_from=S.INTERPRETING)


def test_start_twice_and_unknown_run(conn: psycopg.Connection) -> None:
    run_id = new_run(conn)
    with pytest.raises(TransitionNotAllowed):
        start(conn, run_id, actor="system")
    with pytest.raises(RunNotFound):
        transition(conn, uuid4(), S.INTERPRETING, actor="system")


def test_clarification_rounds_are_capped(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.CLARIFICATION_REQUESTED)
    answer_clarification(conn, run_id, user=AUDITOR, answer="the GBP fee account")
    transition(conn, run_id, S.CLARIFICATION_REQUESTED, actor="system")
    answer_clarification(conn, run_id, user=AUDITOR, answer="calendar Q3")
    with pytest.raises(TransitionNotAllowed, match="clarification limit"):
        transition(conn, run_id, S.CLARIFICATION_REQUESTED, actor="system")
    transition(conn, run_id, S.ESCALATED, actor="system")


def test_only_the_initiator_answers_questions(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.CLARIFICATION_REQUESTED)
    with pytest.raises(Forbidden):
        answer_clarification(conn, run_id, user="someone-else", answer="Q3")
    answer_clarification(conn, run_id, user=AUDITOR, answer="Q3")
    assert current_state(conn, run_id) is S.INTERPRETING


def decide(
    conn: psycopg.Connection, run_id: UUID, actor: str, decision: Decision, note: str = ""
) -> ReviewOutcome:
    report = report_version(conn, run_id)
    sha = report.report_sha256 if report else stand_in_report(conn, run_id)
    return review(
        conn,
        run_id,
        reviewer=actor,
        decision=decision,
        note=note,
        report_sha256=sha,
        acknowledge_flags=False,
        entitlements=ENTITLEMENTS,
        now=now_utc(),
    )


def test_initiator_cannot_approve_their_own_audit(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.PENDING_REVIEW)
    with pytest.raises(Forbidden, match="started the run"):
        decide(conn, run_id, AUDITOR, "approved")
    assert current_state(conn, run_id) is S.PENDING_REVIEW


def test_whoever_shaped_the_scope_cannot_approve(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.CLARIFICATION_REQUESTED)
    answer_clarification(conn, run_id, user=AUDITOR, answer="the fee account")
    for state in (S.EXTRACTING, S.RECONCILING, S.ANALYSING, S.REPORTING, S.PENDING_REVIEW):
        transition(conn, run_id, state, actor="system")
    with pytest.raises(Forbidden):
        decide(conn, run_id, AUDITOR, "approved")
    outcome = decide(conn, run_id, REVIEWER, "approved")
    assert outcome.state is S.APPROVED and outcome.child_run_id is None


def test_reinvestigation_creates_a_linked_run(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.PENDING_REVIEW)
    with pytest.raises(Invalid, match="say why"):
        decide(conn, run_id, REVIEWER, "reinvestigate")
    child_id = decide(
        conn, run_id, REVIEWER, "reinvestigate", note="Look at the duplicate pair on 25 Oct"
    ).child_run_id
    assert child_id is not None
    assert current_state(conn, run_id) is S.REINVESTIGATION
    assert current_state(conn, child_id) is S.RECEIVED
    child_run = get_run(conn, child_id)
    assert child_run.parent_run_id == run_id and child_run.initiated_by == REVIEWER
    # The reviewer who asked for the re-investigation now owns it and cannot approve it.
    for state in (
        S.INTERPRETING,
        S.EXTRACTING,
        S.RECONCILING,
        S.ANALYSING,
        S.REPORTING,
        S.PENDING_REVIEW,
    ):
        transition(conn, child_id, state, actor="system")
    with pytest.raises(Forbidden):
        decide(conn, child_id, REVIEWER, "approved")


def test_only_entitled_reviewers_can_review(conn: psycopg.Connection) -> None:
    run_id = new_run(conn, to=S.PENDING_REVIEW)
    for actor in ("fees@bank", "stranger@bank"):
        with pytest.raises(Forbidden, match="not entitled to review"):
            decide(conn, run_id, actor, "approved")
    assert current_state(conn, run_id) is S.PENDING_REVIEW
