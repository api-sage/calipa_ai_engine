"""REPORTING end to end: an analysed run -> facts -> P3 (scripted) -> V3 -> template
fallback -> rendered, hashed report -> PENDING_REVIEW, everything recorded."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import anthropic
import httpx2
import psycopg

from calipa.analyst.step import AnalyseStep
from calipa.audit_trail.artifacts import put_artifact, require_artifact
from calipa.audit_trail.store import create_run, get_run, list_events, verify_chain
from calipa.canonical import hash_value
from calipa.llm.client import ClaudeClient
from calipa.llm.prompts import load_prompt
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import start, transition
from calipa.orchestrator.runner import Orchestrator, StepContext
from calipa.orchestrator.steps import APPROVED_SCOPE, ENGINE_REQUEST, ENGINE_RESULT
from calipa.reference import load_account_catalog, load_control_catalog, load_tariff
from calipa.reporter.step import REPORT, ReportArtifact, ReportStep, markdown_of
from calipa.synthetic import Anomaly, generate
from tests.analyst_helpers import edited, good_answer, real_group, reconcile
from tests.interpreter_helpers import NOW, ScriptedAnthropic
from tests.report_helpers import G1_TEXT, SCOPE, finding_text, siblings_key
from tests.test_analyse_step import analysing_run
from tests.test_llm_client import message

CATALOG, TARIFF, CONTROLS = load_account_catalog(), load_tariff(), load_control_catalog()
P2_GOOD = good_answer(real_group().bundle)


def narrative() -> dict[str, Any]:
    """A V3-clean answer for a run whose only finding is the analysed real group."""
    g1 = G1_TEXT % siblings_key()
    return {
        "executive_summary": (
            "The engine checked {{fact:run.transfers_checked}} transfers and raised "
            "{{fact:run.exceptions_in_scope}} exceptions within the scope."
        ),
        "findings": [finding_text("G1", g1)],
        "cross_finding_patterns": [],
    }


GOOD = narrative()
WITH_DIGIT = edited(
    GOOD, "findings.0", finding_text("G1", GOOD["findings"][0]["narrative"] + " It had 3 legs.")
)


def orchestrator(conn: psycopg.Connection, fake: ScriptedAnthropic, **kw: Any) -> Orchestrator:
    llm = ClaudeClient(conn, fake, model="claude-opus-5-5")  # type: ignore[arg-type]
    analyse = AnalyseStep(
        llm=llm,
        prompt=load_prompt("analyst", 3),
        tariff=TARIFF,
        catalog=CATALOG,
        controls=CONTROLS,
    )
    report = ReportStep(llm=llm, prompt=load_prompt("report", 2), controls=CONTROLS, **kw)
    return Orchestrator(
        conn,
        {**analyse.handlers(), **report.handlers()},
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def run_to_review(
    conn: psycopg.Connection, *p3: dict[str, Any] | Exception, **kw: Any
) -> tuple[UUID, ScriptedAnthropic, ReportArtifact]:
    run_id = analysing_run(conn)
    fake = ScriptedAnthropic(P2_GOOD, *p3)
    assert orchestrator(conn, fake, **kw).advance(run_id) is S.PENDING_REVIEW
    _, artifact = require_artifact(conn, run_id, REPORT, ReportArtifact)
    return run_id, fake, artifact


def test_a_checked_narrative_becomes_the_report(conn: psycopg.Connection) -> None:
    run_id, fake, artifact = run_to_review(conn, GOOD)
    assert artifact.narrative_source == "ai" and artifact.flags == []
    assert artifact.used_attempt == 1 and artifact.narrative.template_sections() == []
    assert artifact.manifest.prompt is not None
    assert artifact.manifest.prompt.sha256 == load_prompt("report", 2).sha256

    sent = fake.requests[1]
    assert sent["output_config"]["effort"] == "medium"
    assert sent["system"][0]["text"] == load_prompt("report", 2).text
    content = fake.user_content(1)
    for tag in ("run_facts", "verified_findings", "finding_keys"):
        assert f"<{tag}>" in content
    assert "2.22" not in content and "5648968500" not in content  # keys only, no values

    text = markdown_of(conn, artifact)
    assert "{{fact:" not in text and "{E" not in text
    assert "a charge or VAT leg that was not posted \\(high confidence\\)" in text
    assert "followed the first by 18 seconds" in text
    assert "GBP 2.22" in text  # P2's verified claim, rendered by code
    assert "The USD legs entry " in text and " E11 " not in text  # ids an auditor can use
    assert "| CUSTOMER\\_1 | 5648968500 |" in text  # labels resolved for the auditor
    assert "AI-written, checked by V3" in text
    assert "Reserved for the reviewing auditor" in text
    assert "Draft for second-person review" in text

    # The hash covers the manifest, which covers the narrative and the rendered bytes.
    assert artifact.report_sha256 == hash_value(artifact.manifest)
    assert artifact.manifest.narrative_sha256 == hash_value(artifact.narrative)
    assert [i.kind for i in artifact.manifest.inputs][:3] == [
        APPROVED_SCOPE,
        ENGINE_REQUEST,
        ENGINE_RESULT,
    ]
    events = list_events(conn, run_id)
    assert [e.event_type for e in events if e.event_type.startswith("report")] == [
        "report_attempt",
        "report_rendered",
    ]
    review = events[-1]
    assert review.state == S.PENDING_REVIEW.value
    assert review.payload["report_sha256"] == artifact.report_sha256
    assert review.payload["flagged"] is False
    assert verify_chain(conn, run_id).ok


def test_one_retry_with_the_errors_listed(conn: psycopg.Connection) -> None:
    _, fake, artifact = run_to_review(conn, WITH_DIGIT, GOOD)
    retry = fake.user_content(2)
    assert "<previous_answer>" in retry and "<errors_to_fix>" in retry and "'3'" in retry
    assert artifact.used_attempt == 2 and artifact.narrative_source == "ai"
    assert artifact.attempts[0].check is not None and not artifact.attempts[0].check.clean


def test_a_section_that_still_fails_is_template_text_and_flagged(
    conn: psycopg.Connection,
) -> None:
    run_id, _, artifact = run_to_review(conn, WITH_DIGIT, WITH_DIGIT)
    assert artifact.narrative_source == "partial"
    assert artifact.narrative.template_sections() == ["G1"]
    assert artifact.narrative.executive_summary.source == "ai"
    assert any("G1" in f and "failed the report checker" in f for f in artifact.flags)
    text = markdown_of(conn, artifact)
    assert "Template text, written by code" in text and "It had 3 legs" not in text
    assert list_events(conn, run_id)[-1].payload["flagged"] is True


def test_a_refusal_gives_a_template_report(conn: psycopg.Connection) -> None:
    refusal = message(
        text=None,
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": None, "explanation": None},
    )
    _, fake, artifact = run_to_review(conn, refusal)
    assert len(fake.requests) == 2  # a refusal is not retried
    assert artifact.narrative_source == "template" and artifact.used_attempt is None
    assert any("P3 gave no usable answer" in f for f in artifact.flags)
    assert artifact.manifest.prompt is not None  # P3 was called, so its prompt is recorded


def test_a_failing_call_still_gives_a_report(conn: psycopg.Connection) -> None:
    down = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    _, _, artifact = run_to_review(conn, down)
    assert artifact.narrative_source == "template"
    assert any("the P3 call failed (connection_error)" in f for f in artifact.flags)


def test_no_ai_narrative_once_the_cost_cap_is_reached(conn: psycopg.Connection) -> None:
    _, fake, artifact = run_to_review(conn, max_cost_usd=Decimal("0.01"))
    assert len(fake.requests) == 1  # P2 only
    assert artifact.manifest.prompt is None
    assert any("cost cap" in f for f in artifact.flags)


def test_a_clean_run_reports_without_ai(conn: psycopg.Connection) -> None:
    data = generate(
        7,
        day=date(2026, 10, 25),
        counts={Anomaly.CLEAN: 3},
        catalog=CATALOG,
        tariff=TARIFF,
        tz=ZoneInfo("Africa/Lagos"),
    )
    request, result = reconcile(data.entries, rates=data.rates)
    assert result.exceptions == []
    run_id = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    ).run_id
    for kind, value in (
        (APPROVED_SCOPE, SCOPE),
        (ENGINE_REQUEST, request),
        (ENGINE_RESULT, result),
    ):
        put_artifact(conn, run_id, kind, value, created_by="test")
    start(conn, run_id, actor="test")
    for state in (S.INTERPRETING, S.EXTRACTING, S.RECONCILING, S.NO_EXCEPTIONS):
        transition(conn, run_id, state, actor="test")

    fake = ScriptedAnthropic()
    assert orchestrator(conn, fake).advance(run_id) is S.PENDING_REVIEW
    assert fake.requests == []
    _, artifact = require_artifact(conn, run_id, REPORT, ReportArtifact)
    assert artifact.flags == [] and artifact.narrative_source == "template"
    text = markdown_of(conn, artifact)
    assert "It found no exceptions within the approved scope" in text
    assert "| Transfers checked | 3 |" in text


def test_a_retried_step_reuses_the_stored_report(conn: psycopg.Connection) -> None:
    run_id, fake, artifact = run_to_review(conn, GOOD)
    llm = ClaudeClient(conn, fake, model="claude-opus-5-5")  # type: ignore[arg-type]
    step = ReportStep(llm=llm, prompt=load_prompt("report", 2), controls=CONTROLS)
    ctx = StepContext(conn, get_run(conn, run_id), S.REPORTING, "test", lambda: NOW)
    again = step.report(ctx)  # no scripted reply left: a new call would fail
    assert again.payload["report_sha256"] == artifact.report_sha256


def test_the_report_records_every_ai_call(conn: psycopg.Connection) -> None:
    _, _, artifact = run_to_review(conn, WITH_DIGIT, GOOD)
    text = markdown_of(conn, artifact)
    section = text.split("## 8. How AI was used", 1)[1].split("## 9.", 1)[0]
    assert section.count("claude-opus-5-5 | claude-opus-5-5 | ok") == 3  # P2, P3 twice
    assert "analyst.v3" in section and "report.v2" in section
