"""Second-person review: who may decide, on which report version, and what each
decision does. Rules are checked in code and, for the most important ones, again by
the database."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from calipa.analyst.step import FINDING, Finding
from calipa.audit_trail.artifacts import list_artifacts, put_artifact, require_artifact
from calipa.audit_trail.store import (
    create_run,
    get_run,
    list_events,
    now_utc,
    put_blob,
    verify_chain,
)
from calipa.interpreter.step import build_conversation
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import current_state, start, transition
from calipa.orchestrator.steps import APPROVED_SCOPE, ENGINE_REQUEST, ENGINE_RESULT
from calipa.reference import load_entitlements
from calipa.reporter.render import md
from calipa.reporter.step import REPORT, ReportArtifact, markdown_of
from calipa.review.service import (
    Conflict,
    Forbidden,
    Invalid,
    answer_clarification,
    review,
)
from calipa.review.store import (
    Review,
    get_review,
    insert_review,
    register_report,
    report_version,
    versions,
)
from tests.analyst_helpers import reconcile
from tests.interpreter_helpers import ScriptedAnthropic
from tests.report_helpers import SCOPE
from tests.test_report_step import GOOD, P2_GOOD, WITH_DIGIT, orchestrator, run_to_review

ENTITLEMENTS = load_entitlements()


def decide(conn: psycopg.Connection, run_id: UUID, reviewer: str, **kw: Any) -> Any:
    report = report_version(conn, run_id)
    assert report is not None
    args: dict[str, Any] = {
        "decision": "approved",
        "note": "",
        "report_sha256": report.report_sha256,
        "acknowledge_flags": False,
    } | kw
    return review(conn, run_id, reviewer=reviewer, entitlements=ENTITLEMENTS, now=now_utc(), **args)


def to_review(conn: psycopg.Connection, run_id: UUID, *p3: Any, p2: bool = True) -> Any:
    """Take a started run (e.g. a re-investigation) through to PENDING_REVIEW. With
    p2=False the group is expected to be carried over, so P2 is not scripted."""
    request, result = reconcile()
    for kind, value in (
        (APPROVED_SCOPE, SCOPE),
        (ENGINE_REQUEST, request),
        (ENGINE_RESULT, result),
    ):
        put_artifact(conn, run_id, kind, value, created_by="test")
    for state in (S.INTERPRETING, S.EXTRACTING, S.RECONCILING, S.ANALYSING):
        transition(conn, run_id, state, actor="test")
    fake = ScriptedAnthropic(*((P2_GOOD,) if p2 else ()), *(p3 or (GOOD,)))
    assert orchestrator(conn, fake).advance(run_id) is S.PENDING_REVIEW
    assert fake.replies == []  # every scripted reply was used, by the step it was for
    return fake


def test_a_second_person_approves_and_the_version_is_final(conn: psycopg.Connection) -> None:
    run_id, _, artifact = run_to_review(conn, GOOD)
    report = report_version(conn, run_id)
    assert report is not None and report.version == 1 and report.root_run_id == run_id
    assert report.report_sha256 == artifact.report_sha256 and not report.flagged

    outcome = decide(conn, run_id, "reviewer@bank")
    assert outcome.state is S.APPROVED and current_state(conn, run_id) is S.APPROVED
    stored = get_review(conn, run_id)
    assert stored is not None and stored.reviewer == "reviewer@bank"
    last = list_events(conn, run_id)[-1]
    assert (last.actor, last.payload["report_sha256"]) == ("reviewer@bank", artifact.report_sha256)
    assert verify_chain(conn, run_id).ok

    with pytest.raises(Conflict, match="APPROVED"):
        decide(conn, run_id, "lead@bank", decision="rejected", note="changed my mind")
    with pytest.raises(psycopg.Error, match="append-only"):
        conn.execute("update reviews set decision = 'rejected' where run_id = %s", (run_id,))


@pytest.mark.parametrize(
    ("reviewer", "error"),
    [
        ("auditor@bank", "not entitled to review"),  # the initiator, and not a reviewer
        ("fees@bank", "not entitled to review"),
        ("stranger@bank", "not entitled to review"),
    ],
)
def test_only_entitled_reviewers_decide(
    conn: psycopg.Connection, reviewer: str, error: str
) -> None:
    run_id, _, _ = run_to_review(conn, GOOD)
    with pytest.raises(Forbidden, match=error):
        decide(conn, run_id, reviewer)
    assert current_state(conn, run_id) is S.PENDING_REVIEW


def test_the_initiator_never_reviews_even_with_the_reviewer_role(
    conn: psycopg.Connection,
) -> None:
    # lead@bank may both start and review audits, but not review their own.
    run_id = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="lead@bank", request_text="audit"
    ).run_id
    start(conn, run_id, actor="lead@bank")
    to_review(conn, run_id)
    with pytest.raises(Forbidden, match="started the run"):
        decide(conn, run_id, "lead@bank")
    # The database refuses it too, whatever the code does.
    report = report_version(conn, run_id)
    assert report is not None
    with pytest.raises(psycopg.errors.CheckViolation, match="cannot be the person"):
        insert_review(conn, _row(run_id, "lead@bank", report.report_sha256))


def test_a_decision_names_the_version_the_reviewer_read(conn: psycopg.Connection) -> None:
    run_id, _, _ = run_to_review(conn, GOOD)
    with pytest.raises(Conflict, match="changed since you opened it"):
        decide(conn, run_id, "reviewer@bank", report_sha256="0" * 64)
    with pytest.raises(psycopg.errors.CheckViolation, match="report hash"):
        insert_review(conn, _row(run_id, "reviewer@bank", "0" * 64))


def test_a_flagged_report_needs_its_flags_acknowledged(conn: psycopg.Connection) -> None:
    run_id, _, artifact = run_to_review(conn, WITH_DIGIT, WITH_DIGIT)
    assert artifact.flags
    with pytest.raises(Conflict, match="acknowledge its flags"):
        decide(conn, run_id, "reviewer@bank")
    assert decide(conn, run_id, "reviewer@bank", acknowledge_flags=True).state is S.APPROVED


def test_rejecting_needs_a_reason(conn: psycopg.Connection) -> None:
    run_id, _, _ = run_to_review(conn, GOOD)
    with pytest.raises(Invalid, match="say why"):
        decide(conn, run_id, "reviewer@bank", decision="rejected", note="  ")
    outcome = decide(conn, run_id, "reviewer@bank", decision="rejected", note="Wrong period.")
    assert outcome.state is S.REJECTED and outcome.child_run_id is None


def test_reinvestigation_starts_the_next_version(conn: psycopg.Connection) -> None:
    run_id, _, first = run_to_review(conn, GOOD)
    outcome = decide(
        conn,
        run_id,
        "reviewer@bank",
        decision="reinvestigate",
        note="Include the EUR transfers as well.",
    )
    assert outcome.state is S.REINVESTIGATION
    child_id = outcome.child_run_id
    assert child_id is not None
    child = get_run(conn, child_id)
    assert (child.parent_run_id, child.initiated_by) == (run_id, "reviewer@bank")
    assert current_state(conn, child_id) is S.RECEIVED

    # P1 sees the reviewer's note and the previous scope.
    conversation = build_conversation(conn, child)
    assert conversation.payload["reviewer_note"] == "Include the EUR transfers as well."
    assert "previous_scope" in conversation.payload

    # The evidence did not change, so the analysis is carried over, not paid for again.
    fake = to_review(conn, child_id, p2=False)
    assert [r["output_config"]["effort"] for r in fake.requests] == ["medium"]  # P3 only
    [carried] = [f for _, f in list_artifacts(conn, child_id, FINDING, Finding)]
    [original] = [f for _, f in list_artifacts(conn, run_id, FINDING, Finding)]
    assert carried.reused_from_run == run_id
    assert carried.model_copy(update={"reused_from_run": None}) == original
    _, child_report = require_artifact(conn, child_id, REPORT, ReportArtifact)
    carried_row = md("ok (earlier version, finding carried over)")
    assert carried_row in markdown_of(conn, child_report)
    second = report_version(conn, child_id)
    assert second is not None and (second.version, second.root_run_id) == (2, run_id)
    assert [v.version for v in versions(conn, child_id)] == [1, 2]
    assert versions(conn, run_id)[0].report_sha256 == first.report_sha256  # unchanged

    # The reviewer who sent it back started the new run, so someone else decides.
    with pytest.raises(Forbidden, match="started the run"):
        decide(conn, child_id, "reviewer@bank")
    assert decide(conn, child_id, "lead@bank").state is S.APPROVED


def test_only_the_initiator_answers_a_question(conn: psycopg.Connection) -> None:
    run_id = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    ).run_id
    start(conn, run_id, actor="auditor@bank")
    transition(conn, run_id, S.INTERPRETING, actor="test")
    with pytest.raises(Conflict, match="CLARIFICATION_REQUESTED"):
        answer_clarification(conn, run_id, user="auditor@bank", answer="Last week.")
    transition(
        conn, run_id, S.CLARIFICATION_REQUESTED, actor="test", payload={"question": "Which period?"}
    )
    with pytest.raises(Forbidden):
        answer_clarification(conn, run_id, user="lead@bank", answer="Last week.")
    with pytest.raises(Invalid):
        answer_clarification(conn, run_id, user="auditor@bank", answer=" ")
    assert answer_clarification(conn, run_id, user="auditor@bank", answer="Last week.") is (
        S.INTERPRETING
    )
    assert build_conversation(conn, get_run(conn, run_id)).payload["turns"][-1] == {
        "from": "auditor",
        "text": "Last week.",
    }


def _row(run_id: UUID, reviewer: str, report_sha256: str) -> Review:
    return Review(
        review_id=uuid4(),
        run_id=run_id,
        report_sha256=report_sha256,
        decision="approved",
        reviewer=reviewer,
        note="",
        flags_acknowledged=False,
        child_run_id=None,
        created_at=now_utc(),
    )


def stand_in_report(conn: psycopg.Connection, run_id: UUID) -> str:
    """Register a placeholder report for a run moved to PENDING_REVIEW by hand."""
    ref = put_artifact(conn, run_id, "report", SCOPE, created_by="test")
    markdown = put_blob(conn, f"report for {run_id}".encode(), "text/markdown")
    sha = "f" * 64
    register_report(
        conn,
        run_id,
        artifact_id=ref.artifact_id,
        report_sha256=sha,
        markdown_sha256=markdown,
        flagged=False,
    )
    return sha
