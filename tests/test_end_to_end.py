"""The whole product path: an auditor's request over HTTP, the worker's handlers
running every real step (P1, policy, extraction from the fake bank, reconciliation, report), and a
second person approving the report over HTTP."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr

from calipa.config import Settings
from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.gateway.rates import snapshot_rates
from calipa.llm.client import ClaudeClient
from calipa.orchestrator.lifecycle import TERMINAL, RunState
from calipa.orchestrator.runner import Orchestrator
from calipa.reference import load_account_catalog, load_tariff
from calipa.synthetic import Anomaly, generate
from calipa.worker import build_handlers
from tests.fake_fcy import PASSWORD, USER, FakeFcy
from tests.interpreter_helpers import NOW, ScriptedAnthropic, period, scope_dict
from tests.test_api import as_

SETTINGS = Settings(_env_file=None, bank_timezone="Africa/Lagos")  # type: ignore[call-arg]
P1 = scope_dict(
    period=period(
        kind="absolute",
        start="2026-10-25",
        end="2026-10-25",
        relative_unit=None,
        relative_offset=None,
        source_text="25 October 2026",
    )
)


def test_every_step_needing_work_has_a_handler() -> None:
    handlers = build_handlers(fcy=None, llm=None, settings=SETTINGS)  # type: ignore[arg-type]
    waiting = {RunState.RECEIVED, RunState.CLARIFICATION_REQUESTED, RunState.PENDING_REVIEW}
    assert set(handlers) == set(RunState) - TERMINAL - waiting


def test_request_to_approved_report(api: TestClient, conn: psycopg.Connection) -> None:
    data = generate(
        3,
        day=date(2026, 10, 25),
        counts={Anomaly.CLEAN: 4},
        catalog=load_account_catalog(),
        tariff=load_tariff(),
        tz=ZoneInfo("Africa/Lagos"),
    )
    created = api.post(
        "/runs",
        json={
            "request_text": "Check the fee and VAT legs for 25 October 2026",
            "idempotency_key": f"k-{uuid4()}",
        },
        headers=as_("auditor@bank"),
    ).json()
    run_id = created["run_id"]

    fake = ScriptedAnthropic(P1)  # P1 only: clean data means no P2, and no P3 call
    with ReadOnlyClient(
        conn,
        base_url="https://fcy.test",
        username=SecretStr(USER),
        password=SecretStr(PASSWORD),
        transport=FakeFcy(data.entries).transport(),
        sleep=lambda _: None,
    ) as http:
        fcy = FcyClient(http)
        snapshot_rates(conn, fcy)
        llm = ClaudeClient(conn, fake, model="claude-opus-5-5")  # type: ignore[arg-type]
        handlers = build_handlers(fcy=fcy, llm=llm, settings=SETTINGS)
        orchestrator = Orchestrator(conn, handlers, clock=lambda: NOW, sleep=lambda _: None)
        # Only this run: the test database holds other tests' runs too.
        assert orchestrator.advance(UUID(run_id)) is RunState.PENDING_REVIEW
    assert len(fake.requests) == 1

    view = api.get(f"/runs/{run_id}", headers=as_("reviewer@bank")).json()
    assert view["state"] == "PENDING_REVIEW" and view["report"]["flags"] == []
    report = api.get(f"/runs/{run_id}/report", headers=as_("reviewer@bank")).json()
    assert "It found no exceptions within the approved scope" in report["markdown"]
    assert "| Transfers checked | 4 |" in report["markdown"]

    decided = api.post(
        f"/runs/{run_id}/review",
        json={"decision": "approved", "report_sha256": view["report"]["report_sha256"]},
        headers=as_("reviewer@bank"),
    ).json()
    assert decided["state"] == "APPROVED"
    events = api.get(f"/runs/{run_id}/events", headers=as_("auditor@bank")).json()
    assert events["chain_ok"] is True
    states = [e["state"] for e in events["events"] if e["state"]]
    assert states == [
        "RECEIVED",
        "INTERPRETING",
        "EXTRACTING",
        "RECONCILING",
        "NO_EXCEPTIONS",
        "REPORTING",
        "PENDING_REVIEW",
        "APPROVED",
    ]
