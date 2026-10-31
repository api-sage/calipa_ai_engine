"""The ANALYSING step end to end: engine result -> bundles -> P2 (scripted) -> V2 ->
findings, with retries, drops, caps and resume, everything recorded."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import anthropic
import httpx2
import psycopg

from calipa.analyst.step import ANALYSIS, FINDING, AnalyseStep, AnalysisSummary, Finding
from calipa.audit_trail.artifacts import list_artifacts, put_artifact, require_artifact
from calipa.audit_trail.store import create_run, list_events, verify_chain
from calipa.llm.client import ClaudeClient, StructuredLLM
from calipa.llm.prompts import ACTIVE, active_prompt, load_prompt
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import start, transition
from calipa.orchestrator.runner import Orchestrator
from calipa.orchestrator.steps import (
    APPROVED_SCOPE,
    ENGINE_REQUEST,
    ENGINE_RESULT,
    GL_SNAPSHOT,
    ApprovedScope,
    SnapshotArtifact,
)
from calipa.reference import load_account_catalog, load_control_catalog, load_tariff
from calipa.synthetic import Anomaly, generate
from tests.analyst_helpers import (
    edited,
    good_answer,
    groups,
    real_group,
    reconcile,
    unexplained_answer,
)
from tests.interpreter_helpers import NOW, ScriptedAnthropic
from tests.test_llm_client import message

CATALOG, TARIFF, CONTROLS = load_account_catalog(), load_tariff(), load_control_catalog()


def analysing_run(
    conn: psycopg.Connection,
    raw: list[dict[str, Any]] | None = None,
    *,
    parent: UUID | None = None,
    **kw: Any,
) -> UUID:
    """A run that has reconciled and is waiting in ANALYSING."""
    request, result = reconcile(raw, **kw)
    run_id = create_run(
        conn,
        idempotency_key=f"k-{uuid4()}",
        initiated_by="auditor@bank",
        request_text="audit",
        parent_run_id=parent,
    ).run_id
    scope = ApprovedScope(
        objective="transaction_trace",
        period_start=date(2026, 10, 25),
        period_end=date(2026, 10, 25),
        timezone="Africa/Lagos",
        account_catalog_sha256=CATALOG.sha256,
        tariff_sha256=TARIFF.sha256,
        approved_by="test",
    )
    snapshot = SnapshotArtifact(
        snapshot_sha256=request.snapshot_sha256,
        period_start=scope.period_start,
        period_end=scope.period_end,
        timezone=scope.timezone,
        status="complete",
        limitations=[],
        pages=[],
        entries_in_period=len(request.entries),
        entries=request.entries,
    )
    for kind, value in (
        (APPROVED_SCOPE, scope),
        (GL_SNAPSHOT, snapshot),
        (ENGINE_REQUEST, request),
        (ENGINE_RESULT, result),
    ):
        put_artifact(conn, run_id, kind, value, created_by="test")
    start(conn, run_id, actor="test")
    for state in (S.INTERPRETING, S.EXTRACTING, S.RECONCILING, S.ANALYSING):
        transition(conn, run_id, state, actor="test")
    return run_id


def advance(
    conn: psycopg.Connection,
    fake: ScriptedAnthropic,
    run_id: UUID,
    *,
    max_attempts: int = 3,
    **step_kw: Any,
) -> S:
    llm = ClaudeClient(conn, fake, model="claude-opus-5-5")  # type: ignore[arg-type]
    return orchestrate(conn, llm, run_id, max_attempts=max_attempts, **step_kw)


def orchestrate(
    conn: psycopg.Connection,
    llm: StructuredLLM,
    run_id: UUID,
    *,
    max_attempts: int = 3,
    **step_kw: Any,
) -> S:
    step = AnalyseStep(
        llm=llm,
        prompt=step_kw.pop("prompt", active_prompt("analyst")),
        tariff=TARIFF,
        catalog=CATALOG,
        controls=CONTROLS,
        **step_kw,
    )
    orchestrator = Orchestrator(
        conn, step.handlers(), clock=lambda: NOW, sleep=lambda _: None, max_attempts=max_attempts
    )
    return orchestrator.advance(run_id)


def findings(conn: psycopg.Connection, run_id: UUID) -> list[Finding]:
    return [f for _, f in list_artifacts(conn, run_id, FINDING, Finding)]


BUNDLE = real_group().bundle
GOOD = good_answer(BUNDLE)


def test_a_verified_analysis_becomes_a_finding(conn: psycopg.Connection) -> None:
    run_id = analysing_run(conn)
    fake = ScriptedAnthropic(GOOD)
    assert advance(conn, fake, run_id) is S.REPORTING

    [finding] = findings(conn, run_id)
    assert finding.status == "analysed" and finding.used_attempt == 1
    assert finding.analysis is not None
    assert [h.category for h in finding.analysis.hypotheses] == [
        "fee_or_vat_leg_missing",
        "duplicate_customer_instruction",
        "fee_or_vat_leg_duplicated_across_ccy",
    ]
    assert finding.dropped == [] and finding.uncovered_exceptions == []
    assert finding.labels["CUSTOMER_1"] == "5648968500"  # kept for the report, never sent

    sent = fake.requests[0]
    assert sent["output_config"]["effort"] == "high"
    assert sent["max_tokens"] == 20_000
    assert sent["system"][0]["text"] == active_prompt("analyst").text
    content = fake.user_content(0)
    for tag in ("exception_group", "evidence", "available_categories", "known_data_gaps"):
        assert f"<{tag}>" in content
    assert "5648968500" not in content and "0123456890" not in content

    _, summary = require_artifact(conn, run_id, ANALYSIS, AnalysisSummary)
    assert (summary.groups, summary.analysed, summary.limitations) == (1, 1, [])
    assert summary.cost_usd > 0
    events = list_events(conn, run_id)
    assert [
        e.event_type for e in events if e.event_type in ("analysis_attempt", "finding_recorded")
    ] == [
        "analysis_attempt",
        "finding_recorded",
    ]
    assert verify_chain(conn, run_id).ok


def test_one_retry_with_the_errors_listed(conn: psycopg.Connection) -> None:
    hidden = edited(GOOD, "claims.4.text", "Transfer 828972ad repeats ca650882 19 seconds later.")
    fake = ScriptedAnthropic(hidden, GOOD)
    run_id = analysing_run(conn)
    assert advance(conn, fake, run_id) is S.REPORTING
    retry = fake.user_content(1)
    assert "<previous_answer>" in retry and "<errors_to_fix>" in retry and "'19'" in retry
    [finding] = findings(conn, run_id)
    assert finding.used_attempt == 2 and finding.status == "analysed"
    assert len(finding.attempts) == 2 and not finding.attempts[0].report.clean  # type: ignore[union-attr]


def test_hypotheses_that_still_fail_are_dropped_and_recorded(conn: psycopg.Connection) -> None:
    broken = edited(GOOD, "claims.0.text", "Transfer ca650882 has 2 fee legs.")  # C1 fails
    fake = ScriptedAnthropic(broken, broken)
    run_id = analysing_run(conn)
    assert advance(conn, fake, run_id) is S.REPORTING
    [finding] = findings(conn, run_id)
    assert finding.status == "analysed" and finding.used_attempt == 2
    assert finding.analysis is not None
    assert [h.rank for h in finding.analysis.hypotheses] == [1, 2]  # re-ranked
    assert [(d.rank, d.category) for d in finding.dropped] == [(1, "fee_or_vat_leg_missing")]
    assert any("relies on claim C1" in e for e in finding.dropped[0].errors)
    assert len(finding.uncovered_exceptions) == 3  # fee, VAT and residual
    assert "C1" not in {c.claim_id for c in finding.analysis.claims}


def test_the_attempt_that_explains_more_is_kept(conn: psycopg.Connection) -> None:
    # Attempt 1 loses only hypothesis 2; attempt 2 loses hypotheses 1 and 2.
    first = edited(GOOD, "claims.4.text", "Transfer 828972ad repeats it 19 seconds later.")
    second = edited(first, "claims.0.text", "Transfer ca650882 has 2 fee legs.")
    run_id = analysing_run(conn)
    assert advance(conn, ScriptedAnthropic(first, second), run_id) is S.REPORTING
    [finding] = findings(conn, run_id)
    assert finding.used_attempt == 1
    assert finding.analysis is not None
    assert [h.category for h in finding.analysis.hypotheses] == [
        "fee_or_vat_leg_missing",
        "fee_or_vat_leg_duplicated_across_ccy",
    ]
    assert [(d.attempt, d.category) for d in finding.dropped] == [
        (1, "duplicate_customer_instruction")
    ]


def test_nothing_survives_means_analysis_unavailable(conn: psycopg.Connection) -> None:
    unusable = message(text='{"claims": []')
    fake = ScriptedAnthropic(unusable, edited(GOOD, "hypotheses", []))
    run_id = analysing_run(conn)
    assert advance(conn, fake, run_id) is S.REPORTING  # the report still goes ahead
    [finding] = findings(conn, run_id)
    assert finding.status == "analysis_unavailable"
    assert finding.reason == "no hypothesis passed the claim checker after 2 attempts"
    assert [a.outcome for a in finding.attempts] == ["schema_invalid", "ok"]
    assert len(finding.uncovered_exceptions) == 6
    _, summary = require_artifact(conn, run_id, ANALYSIS, AnalysisSummary)
    assert summary.limitations == [
        "1 exception group(s) without AI analysis: no hypothesis passed the claim checker "
        "after 2 attempts."
    ]


def test_a_refusal_is_not_retried(conn: psycopg.Connection) -> None:
    refusal = message(
        text=None,
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": None, "explanation": None},
    )
    fake = ScriptedAnthropic(refusal)
    run_id = analysing_run(conn)
    assert advance(conn, fake, run_id) is S.REPORTING
    [finding] = findings(conn, run_id)
    assert (finding.status, finding.reason) == (
        "analysis_unavailable",
        "the model declined to analyse this group",
    )
    assert len(fake.requests) == 1


def test_a_failed_call_fails_the_step(conn: psycopg.Connection) -> None:
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    run_id = analysing_run(conn)
    assert advance(conn, ScriptedAnthropic(error), run_id, max_attempts=1) is S.FAILED
    assert findings(conn, run_id) == []


def _two_groups() -> tuple[list[dict[str, Any]], list[Any]]:
    data = generate(
        8,
        day=date(2026, 10, 25),
        counts={Anomaly.FEE_VAT_LEGS_MISSING: 1, Anomaly.FX_RATE_WRONG: 1},
        catalog=CATALOG,
        tariff=TARIFF,
        tz=ZoneInfo("Africa/Lagos"),
    )
    return data.entries, data.rates


def test_a_resumed_step_does_not_pay_twice(conn: psycopg.Connection) -> None:
    entries, rates = _two_groups()
    first, second = groups(entries, rates=rates)
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake = ScriptedAnthropic(
        unexplained_answer(first.bundle), error, unexplained_answer(second.bundle)
    )
    run_id = analysing_run(conn, entries, rates=rates)
    assert advance(conn, fake, run_id, max_attempts=2) is S.REPORTING
    assert len(fake.requests) == 3  # the first group was not analysed again
    done = findings(conn, run_id)
    assert [f.group_id for f in done] == [first.bundle.group_id, second.bundle.group_id]
    failures = [e for e in list_events(conn, run_id) if e.event_type == "step_failed"]
    assert len(failures) == 1


def test_groups_beyond_the_limit_are_listed_not_analysed(conn: psycopg.Connection) -> None:
    entries, rates = _two_groups()
    first, _ = groups(entries, rates=rates)
    fake = ScriptedAnthropic(unexplained_answer(first.bundle))
    run_id = analysing_run(conn, entries, rates=rates)
    assert advance(conn, fake, run_id, max_groups=1) is S.REPORTING
    assert [f.status for f in findings(conn, run_id)] == ["analysed", "not_analysed"]
    assert findings(conn, run_id)[1].reason == "only the 1 most severe groups are analysed per run"


def test_the_cost_cap_stops_new_calls(conn: psycopg.Connection) -> None:
    entries, rates = _two_groups()
    fake = ScriptedAnthropic()
    run_id = analysing_run(conn, entries, rates=rates)
    assert advance(conn, fake, run_id, max_cost_usd=Decimal(0)) is S.REPORTING
    assert fake.requests == []
    assert {f.status for f in findings(conn, run_id)} == {"not_analysed"}


def test_the_call_cap_holds_when_cost_is_unknown(conn: psycopg.Connection) -> None:
    entries, rates = _two_groups()
    first, _ = groups(entries, rates=rates)
    fake = ScriptedAnthropic(unexplained_answer(first.bundle))
    run_id = analysing_run(conn, entries, rates=rates)
    # Room for one group's two attempts, not a second group's.
    assert advance(conn, fake, run_id, max_calls=3) is S.REPORTING
    assert [f.status for f in findings(conn, run_id)] == ["analysed", "not_analysed"]
    assert findings(conn, run_id)[1].reason == "the run's limit of 3 analysis calls was reached"


def test_checks_that_could_not_run_are_not_sent_to_claude(conn: psycopg.Connection) -> None:
    data = generate(
        2,
        day=date(2026, 10, 25),
        counts={Anomaly.CLEAN: 1},
        catalog=CATALOG,
        tariff=TARIFF,
        tz=ZoneInfo("Africa/Lagos"),
    )
    fake = ScriptedAnthropic()
    run_id = analysing_run(conn, data.entries, rates=[])
    assert advance(conn, fake, run_id) is S.REPORTING
    [finding] = findings(conn, run_id)
    assert finding.status == "not_analysed" and fake.requests == []


def test_unchanged_groups_are_carried_over_to_a_reinvestigation(
    conn: psycopg.Connection,
) -> None:
    parent = analysing_run(conn)
    assert advance(conn, ScriptedAnthropic(GOOD), parent) is S.REPORTING
    child = analysing_run(conn, parent=parent)
    fake = ScriptedAnthropic()
    assert advance(conn, fake, child) is S.REPORTING
    assert fake.requests == []
    [finding] = findings(conn, child)
    assert finding.reused_from_run == parent and finding.status == "analysed"
    recorded = [e for e in list_events(conn, child) if e.event_type == "finding_recorded"]
    assert recorded[0].payload["reused_from_run"] == str(parent)

    # A grandchild still points at the run that actually made the analysis.
    grandchild = analysing_run(conn, parent=child)
    assert advance(conn, ScriptedAnthropic(), grandchild) is S.REPORTING
    assert findings(conn, grandchild)[0].reused_from_run == parent


def test_what_is_not_carried_over(conn: psycopg.Connection, tmp_path: Any) -> None:
    entries, rates = _two_groups()
    first, second = groups(entries, rates=rates)
    parent = analysing_run(conn, entries, rates=rates)
    unusable = {"stop_reason": "end_turn", **message(text="not json")}
    fake = ScriptedAnthropic(unexplained_answer(first.bundle), unusable, unusable)
    assert advance(conn, fake, parent) is S.REPORTING
    assert [f.status for f in findings(conn, parent)] == ["analysed", "analysis_unavailable"]

    # Only the analysed group is carried over; the other gets a fresh attempt.
    child = analysing_run(conn, entries, rates=rates, parent=parent)
    fake = ScriptedAnthropic(unexplained_answer(second.bundle))
    assert advance(conn, fake, child) is S.REPORTING
    assert len(fake.requests) == 1 and second.bundle.group_id in fake.user_content(0)

    # A different prompt, or reuse switched off, means asking again.
    version = ACTIVE["analyst"]
    (tmp_path / f"analyst.v{version}.md").write_text(active_prompt("analyst").text + "\n")
    changed = load_prompt("analyst", version, directory=tmp_path)
    for kw in ({"prompt": changed}, {"reuse_unchanged": False}):
        again = analysing_run(conn, entries, rates=rates, parent=parent)
        fake = ScriptedAnthropic(
            unexplained_answer(first.bundle), unexplained_answer(second.bundle)
        )
        assert advance(conn, fake, again, **kw) is S.REPORTING
        assert len(fake.requests) == 2
