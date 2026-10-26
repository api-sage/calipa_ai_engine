"""Operations: metrics read from the audit trail, the endpoint that serves them, and
the start-up check that the schema is current."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from calipa.api.app import create_app
from calipa.audit_trail.anchor import DirectorySink, anchor
from calipa.audit_trail.db import (
    MigrationError,
    connect,
    migrate,
    pending_migrations,
    require_current_schema,
)
from calipa.audit_trail.store import create_run, now_utc, put_blob
from calipa.llm.client import ClaudeClient
from calipa.metrics import Metric, collect, render
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import start, transition
from tests.interpreter_helpers import ScriptedAnthropic
from tests.test_api import deps
from tests.test_llm_client import PROMPT, Scope


def _value(metrics: list[Metric], name: str, **labels: str) -> float:
    [m] = [m for m in metrics if m.name == name and dict(m.labels) == labels]
    return m.value


def _run(conn: psycopg.Connection, *states: S) -> None:
    run_id = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    ).run_id
    start(conn, run_id, actor="test")
    for state in states:
        transition(conn, run_id, state, actor="test")


def test_metrics_count_what_the_trail_records(own: psycopg.Connection, tmp_path: Path) -> None:
    _run(own, S.INTERPRETING)
    _run(own, S.INTERPRETING, S.FAILED)
    _run(own, S.INTERPRETING, S.CLARIFICATION_REQUESTED)
    fake = ScriptedAnthropic({"status": "ok", "accounts": []})
    llm = ClaudeClient(own, fake, model="claude-opus-5-5")  # type: ignore[arg-type]
    llm.structured(step="t", prompt=PROMPT, user_content="x", output_model=Scope, effort="low")
    # A call whose process died before the answer was recorded.
    own.execute(
        """
        insert into llm_calls (call_id, step, prompt_name, prompt_version, prompt_sha256,
            schema_name, schema_sha256, model_requested, effort, request_blob, started_at)
        values (%s, 't', 'p', '1', %s, 's', %s, 'm', 'low', %s, %s)
        """,
        (
            uuid4(),
            "0" * 64,
            "0" * 64,
            put_blob(own, b"{}", "application/json"),
            now_utc() - timedelta(hours=2),
        ),
    )

    now = now_utc()
    fresh = collect(own, now=now, stall_after=timedelta(minutes=15))
    assert _value(fresh, "calipa_runs", state="INTERPRETING") == 1
    assert _value(fresh, "calipa_runs", state="FAILED") == 1
    assert _value(fresh, "calipa_runs", state="APPROVED") == 0
    assert _value(fresh, "calipa_runs_stalled") == 0
    assert _value(fresh, "calipa_runs_failed_24h") == 1
    assert _value(fresh, "calipa_llm_calls_24h", outcome="ok") == 1
    assert _value(fresh, "calipa_llm_cost_usd_24h") > 0
    assert _value(fresh, "calipa_llm_calls_unfinished") == 1
    assert _value(fresh, "calipa_anchors") == 0
    assert _value(fresh, "calipa_unanchored_seconds") > 0

    # An hour on, the run still INTERPRETING is stalled; the one waiting for a person
    # and the failed one are not.
    later = collect(own, now=now + timedelta(hours=1), stall_after=timedelta(minutes=15))
    assert _value(later, "calipa_runs_stalled") == 1

    anchor(own, DirectorySink(tmp_path))
    anchored = collect(own, now=now_utc(), stall_after=timedelta(minutes=15))
    assert _value(anchored, "calipa_anchors") == 1
    assert _value(anchored, "calipa_anchors_unpublished") == 0
    assert _value(anchored, "calipa_unanchored_seconds") == 0


def test_the_exposition_format() -> None:
    text = render(
        [
            Metric("calipa_runs", "Runs by current state.", 2, (("state", "FAILED"),)),
            Metric("calipa_runs", "Runs by current state.", 0, (("state", 'odd"one'),)),
            Metric("calipa_llm_cost_usd_24h", "Known cost.", 0.25),
        ]
    )
    assert text.splitlines() == [
        "# HELP calipa_runs Runs by current state.",
        "# TYPE calipa_runs gauge",
        'calipa_runs{state="FAILED"} 2',
        'calipa_runs{state="odd\\"one"} 0',
        "# HELP calipa_llm_cost_usd_24h Known cost.",
        "# TYPE calipa_llm_cost_usd_24h gauge",
        "calipa_llm_cost_usd_24h 0.25",
    ]


def test_metrics_need_no_sign_in_and_name_no_one(migrated_url: str) -> None:
    with TestClient(create_app(deps(migrated_url, identity="none"))) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "calipa_runs_stalled" in response.text
        assert "@bank" not in response.text
        assert client.get("/runs").status_code == 401  # everything else still refuses


def test_a_service_refuses_to_start_on_an_old_schema(empty_url: str) -> None:
    with connect(empty_url) as conn:
        everything = pending_migrations(conn)
        assert everything[0] == "0001_foundation" and "0005_anchors" in everything
        with pytest.raises(MigrationError, match="missing migrations"):
            require_current_schema(conn)
        migrate(conn)
        assert pending_migrations(conn) == []
        require_current_schema(conn)
