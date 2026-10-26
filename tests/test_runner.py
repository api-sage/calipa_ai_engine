from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pydantic import SecretStr

from calipa.audit_trail.artifacts import put_artifact, require_artifact
from calipa.audit_trail.db import connect
from calipa.audit_trail.store import create_run, list_events, verify_chain
from calipa.engine.contract import ReconciliationResult
from calipa.engine.standin import StandInEngine
from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.gateway.rates import snapshot_rates
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import current_state
from calipa.orchestrator.runner import Next, Orchestrator, StepContext, Worker
from calipa.orchestrator.steps import (
    APPROVED_SCOPE,
    ENGINE_RESULT,
    GL_SNAPSHOT,
    ApprovedScope,
    SnapshotArtifact,
    Steps,
)
from calipa.reference import load_account_catalog, load_tariff
from calipa.synthetic import Anomaly, Dataset, generate
from tests.fake_fcy import PASSWORD, USER, FakeFcy

LATER = datetime(2026, 10, 28, tzinfo=UTC)


def new_run(conn: psycopg.Connection) -> UUID:
    return create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    ).run_id


class Crash(BaseException):
    """Simulates the process dying: not an Exception, so it is not caught as a step
    failure."""


def scripted(calls: Counter[S], script: dict[S, list[object]]):  # type: ignore[no-untyped-def]
    """Handlers that follow a script: each call pops the next outcome for its state."""

    def make(state: S):  # type: ignore[no-untyped-def]
        def handler(ctx: StepContext) -> Next:
            calls[state] += 1
            outcome = script[state].pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            assert isinstance(outcome, S)
            return Next(outcome)

        return handler

    return {state: make(state) for state in script}


def orchestrator(conn: psycopg.Connection, handlers, **kw) -> Orchestrator:  # type: ignore[no-untyped-def]
    return Orchestrator(conn, handlers, sleep=lambda _: None, clock=lambda: LATER, **kw)


def test_runs_until_a_person_is_needed(conn: psycopg.Connection) -> None:
    calls: Counter[S] = Counter()
    handlers = scripted(
        calls,
        {
            S.INTERPRETING: [S.EXTRACTING],
            S.EXTRACTING: [S.RECONCILING],
            S.RECONCILING: [S.ANALYSING],
            S.ANALYSING: [S.REPORTING],
            S.REPORTING: [S.PENDING_REVIEW],
        },
    )
    run_id = new_run(conn)
    assert orchestrator(conn, handlers).advance(run_id) is S.PENDING_REVIEW
    assert set(calls.values()) == {1}
    assert verify_chain(conn, run_id).ok


def test_unbuilt_steps_leave_the_run_waiting(conn: psycopg.Connection) -> None:
    run_id = new_run(conn)
    assert orchestrator(conn, {}).advance(run_id) is S.INTERPRETING


def test_failures_are_retried_then_recorded(conn: psycopg.Connection) -> None:
    calls: Counter[S] = Counter()
    handlers = scripted(
        calls,
        {
            S.INTERPRETING: [S.EXTRACTING],
            S.EXTRACTING: [TimeoutError("bank API slow"), ConnectionError("reset"), S.RECONCILING],
        },
    )
    run_id = new_run(conn)
    assert orchestrator(conn, handlers).advance(run_id) is S.RECONCILING
    failures = [e for e in list_events(conn, run_id) if e.event_type == "step_failed"]
    assert [(f.payload["attempt"], f.payload["error_type"]) for f in failures] == [
        (1, "TimeoutError"),
        (2, "ConnectionError"),
    ]


def test_persistent_failure_fails_the_run(conn: psycopg.Connection) -> None:
    calls: Counter[S] = Counter()
    handlers = scripted(
        calls,
        {
            S.INTERPRETING: [S.EXTRACTING],
            S.EXTRACTING: [RuntimeError("down")] * 3,
        },
    )
    run_id = new_run(conn)
    assert orchestrator(conn, handlers, max_attempts=3).advance(run_id) is S.FAILED
    final = next(e for e in list_events(conn, run_id) if e.state == "FAILED")
    assert "EXTRACTING failed 3 times" in final.payload["reason"]


def test_crash_resumes_without_redoing_finished_steps(conn: psycopg.Connection) -> None:
    calls: Counter[S] = Counter()
    handlers = scripted(
        calls,
        {
            S.INTERPRETING: [S.EXTRACTING],
            S.EXTRACTING: [S.RECONCILING],
            S.RECONCILING: [Crash(), S.NO_EXCEPTIONS],
            S.NO_EXCEPTIONS: [S.REPORTING],
        },
    )
    run_id = new_run(conn)
    with pytest.raises(Crash):
        orchestrator(conn, handlers).advance(run_id)
    assert current_state(conn, run_id) is S.RECONCILING
    # A fresh orchestrator (another process) picks up where the first one stopped.
    assert orchestrator(conn, handlers).advance(run_id) is S.REPORTING
    assert calls == Counter(
        {S.INTERPRETING: 1, S.EXTRACTING: 1, S.RECONCILING: 2, S.NO_EXCEPTIONS: 1}
    )


def test_a_claimed_run_is_skipped_by_other_workers(migrated_url: str) -> None:
    with connect(migrated_url) as a, connect(migrated_url) as b:
        run_id = new_run(a)
        a.execute("select pg_advisory_lock(hashtextextended(%s::text, 0))", (str(run_id),))
        try:
            worker_b = Worker(b, orchestrator(b, {}), worker_id="worker-b")
            assert run_id not in worker_b.run_once(limit=1000)
        finally:
            a.execute("select pg_advisory_unlock(hashtextextended(%s::text, 0))", (str(run_id),))
        assert worker_b.run_once(limit=1000)[run_id] is S.INTERPRETING
        claims = [e for e in list_events(b, run_id) if e.event_type == "worker_claimed"]
        assert claims and claims[-1].actor == "worker-b"


# --- real extraction and reconciliation over the fake bank --------------------------


@pytest.fixture
def bank(conn: psycopg.Connection) -> Iterator[tuple[Steps, Dataset]]:
    data = generate(
        5,
        day=date(2026, 10, 25),
        counts={
            Anomaly.CLEAN: 10,
            Anomaly.FEE_VAT_LEGS_MISSING: 2,
            Anomaly.DUPLICATE_INSTRUCTION: 1,
            Anomaly.FX_RATE_WRONG: 1,
        },
        catalog=load_account_catalog(),
        tariff=load_tariff(),
        tz=ZoneInfo("Africa/Lagos"),
    )
    with ReadOnlyClient(
        conn,
        base_url="https://fcy.test",
        username=SecretStr(USER),
        password=SecretStr(PASSWORD),
        transport=FakeFcy(data.entries).transport(),
        sleep=lambda _: None,
    ) as http:
        fcy = FcyClient(http)
        snapshot_rates(conn, fcy)  # the daily rate copy the reconciliation relies on
        catalog, tariff = load_account_catalog(), load_tariff()
        yield Steps(fcy=fcy, engine=StandInEngine(tariff), catalog=catalog, tariff=tariff), data


def approve_scope(conn: psycopg.Connection, run_id: UUID, **overrides: object) -> None:
    catalog, tariff = load_account_catalog(), load_tariff()
    scope = ApprovedScope.model_validate(
        {
            "objective": "fee_vat_leg_completeness",
            "period_start": date(2026, 10, 25),
            "period_end": date(2026, 10, 25),
            "timezone": "Africa/Lagos",
            "account_catalog_sha256": catalog.sha256,
            "tariff_sha256": tariff.sha256,
            "approved_by": "test",
            **overrides,
        }
    )
    put_artifact(conn, run_id, APPROVED_SCOPE, scope, created_by="test")


def interpret_from_artifact(ctx: StepContext) -> Next:
    require_artifact(ctx.conn, ctx.run_id, APPROVED_SCOPE, ApprovedScope)
    return Next(S.EXTRACTING)


def test_extraction_and_reconciliation_end_to_end(
    conn: psycopg.Connection, bank: tuple[Steps, Dataset]
) -> None:
    steps, data = bank
    run_id = new_run(conn)
    approve_scope(conn, run_id)
    handlers = {S.INTERPRETING: interpret_from_artifact, **steps.handlers()}
    assert orchestrator(conn, handlers).advance(run_id) is S.ANALYSING  # no P2 handler here

    _, snap = require_artifact(conn, run_id, GL_SNAPSHOT, SnapshotArtifact)
    assert snap.status == "complete" and len(snap.entries) == len(data.entries)
    _, result = require_artifact(conn, run_id, ENGINE_RESULT, ReconciliationResult)
    found: dict[UUID, set[object]] = {}
    for e in result.exceptions:
        found.setdefault(e.transfer_id, set()).add(e.type)
    for label in data.labels:
        assert found.get(label.transfer_id, set()) == label.expected, label.anomaly

    events = list_events(conn, run_id)
    assert sum(e.event_type == "extraction_checkpoint" for e in events) == 3
    reconciled = next(e for e in events if e.state == "ANALYSING")
    assert reconciled.payload["exceptions"] == len(result.exceptions)
    assert verify_chain(conn, run_id).ok


def test_clean_scope_goes_to_no_exceptions(
    conn: psycopg.Connection, bank: tuple[Steps, Dataset]
) -> None:
    steps, data = bank
    clean = next(label for label in data.labels if label.anomaly is Anomaly.CLEAN)
    run_id = new_run(conn)
    approve_scope(conn, run_id, transaction_references=[clean.reference])
    handlers = {S.INTERPRETING: interpret_from_artifact, **steps.handlers()}
    assert orchestrator(conn, handlers).advance(run_id) is S.NO_EXCEPTIONS
    _, snap = require_artifact(conn, run_id, GL_SNAPSHOT, SnapshotArtifact)
    assert {e.transfer_id for e in snap.entries} == {clean.transfer_id}


def test_exceptions_outside_the_approved_controls_do_not_count(
    conn: psycopg.Connection, bank: tuple[Steps, Dataset]
) -> None:
    steps, data = bank
    fx = next(label for label in data.labels if label.anomaly is Anomaly.FX_RATE_WRONG)
    run_id = new_run(conn)
    approve_scope(
        conn,
        run_id,
        objective="duplicate_detection",
        control_ids=["C-DUPLICATE"],
        transaction_references=[fx.reference],
    )
    handlers = {S.INTERPRETING: interpret_from_artifact, **steps.handlers()}
    assert orchestrator(conn, handlers).advance(run_id) is S.NO_EXCEPTIONS
    reconciled = next(e for e in list_events(conn, run_id) if e.state == "NO_EXCEPTIONS")
    assert (reconciled.payload["exceptions"], reconciled.payload["exceptions_in_scope"]) == (1, 0)


def test_reference_data_change_blocks_extraction(
    conn: psycopg.Connection, bank: tuple[Steps, Dataset]
) -> None:
    steps, _ = bank
    run_id = new_run(conn)
    approve_scope(conn, run_id, tariff_sha256="f" * 64)
    handlers = {S.INTERPRETING: interpret_from_artifact, **steps.handlers()}
    assert orchestrator(conn, handlers).advance(run_id) is S.FAILED
