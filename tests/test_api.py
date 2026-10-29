"""The HTTP API over a real database: identity, who sees what, starting runs, the
event stream, the report, and review."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from calipa.api.app import ApiDeps, create_app
from calipa.audit_trail.db import connect
from calipa.audit_trail.store import create_run
from calipa.config import Settings
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import start, transition
from calipa.reference import load_entitlements
from tests.test_report_step import GOOD, WITH_DIGIT, run_to_review

OPENAPI = Path(__file__).parent.parent / "ops" / "openapi.json"


def deps(url: str, **kw: object) -> ApiDeps:
    base: dict[str, object] = {
        "connect": lambda: connect(url),
        "entitlements": load_entitlements(),
        "identity": "dev_header",
        "cors_origins": [],
        "stream_poll_seconds": 0.01,
        "stream_max_seconds": 2.0,
    }
    return ApiDeps(**(base | kw))  # type: ignore[arg-type]


def as_(user: str) -> dict[str, str]:
    return {"X-Calipa-User": user}


def test_no_sign_in_means_no_access(migrated_url: str) -> None:
    with TestClient(create_app(deps(migrated_url, identity="none"))) as client:
        assert client.get("/me", headers=as_("auditor@bank")).status_code == 401
        assert client.get("/health").status_code == 200


def test_identity(api: TestClient) -> None:
    assert api.get("/me").status_code == 401
    assert api.get("/me", headers=as_("stranger@bank")).status_code == 403
    me = api.get("/me", headers=as_("lead@bank")).json()
    assert me == {"user": "lead@bank", "can_initiate": True, "can_review": True}


def test_starting_a_run_is_idempotent_and_entitled(api: TestClient) -> None:
    body = {"request_text": "Check fees for last week", "idempotency_key": f"k-{uuid4()}"}
    first = api.post("/runs", json=body, headers=as_("auditor@bank"))
    assert first.status_code == 202
    run = first.json()
    assert (run["state"], run["created"], run["initiated_by"]) == (
        "RECEIVED",
        True,
        "auditor@bank",
    )
    again = api.post("/runs", json=body, headers=as_("auditor@bank")).json()
    assert (again["run_id"], again["created"]) == (run["run_id"], False)

    changed = api.post("/runs", json={**body, "request_text": "other"}, headers=as_("auditor@bank"))
    assert changed.status_code == 409
    assert api.post("/runs", json=body, headers=as_("reviewer@bank")).status_code == 403
    mine = api.get("/runs", headers=as_("auditor@bank")).json()
    assert run["run_id"] in [r["run_id"] for r in mine]


def test_runs_are_visible_only_to_those_entitled(api: TestClient, conn: psycopg.Connection) -> None:
    run_id, _, _ = run_to_review(conn, GOOD)
    assert api.get(f"/runs/{run_id}", headers=as_("auditor@bank")).status_code == 200
    assert api.get(f"/runs/{run_id}", headers=as_("reviewer@bank")).status_code == 200
    # Not the initiator and not a reviewer: indistinguishable from a missing run.
    hidden = api.get(f"/runs/{run_id}", headers=as_("fees@bank"))
    missing = api.get(f"/runs/{uuid4()}", headers=as_("fees@bank"))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_report_and_review_over_http(api: TestClient, conn: psycopg.Connection) -> None:
    run_id, _, artifact = run_to_review(conn, GOOD)
    queue = api.get("/runs?view=review_queue", headers=as_("reviewer@bank")).json()
    assert str(run_id) in [r["run_id"] for r in queue]

    view = api.get(f"/runs/{run_id}", headers=as_("reviewer@bank")).json()
    assert view["state"] == "PENDING_REVIEW" and view["can_review"] is True
    assert view["report"]["report_sha256"] == artifact.report_sha256
    assert view["versions"] == [
        {
            "run_id": str(run_id),
            "version": 1,
            "report_sha256": artifact.report_sha256,
            "state": "PENDING_REVIEW",
        }
    ]
    assert api.get(f"/runs/{run_id}", headers=as_("auditor@bank")).json()["can_review"] is False

    report = api.get(f"/runs/{run_id}/report", headers=as_("reviewer@bank")).json()
    assert report["findings"][0]["source"] == "ai"
    assert "followed the first by 18 seconds" in report["findings"][0]["text"]
    md = api.get(f"/runs/{run_id}/report.md", headers=as_("reviewer@bank"))
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.headers["x-report-sha256"] == artifact.report_sha256
    assert md.text == report["markdown"]

    stale = {"decision": "approved", "report_sha256": "0" * 64}
    assert (
        api.post(f"/runs/{run_id}/review", json=stale, headers=as_("reviewer@bank")).status_code
        == 409
    )
    no_reason = {"decision": "rejected", "report_sha256": artifact.report_sha256}
    assert (
        api.post(f"/runs/{run_id}/review", json=no_reason, headers=as_("reviewer@bank")).status_code
        == 422
    )
    approve = {"decision": "approved", "report_sha256": artifact.report_sha256}
    assert (
        api.post(f"/runs/{run_id}/review", json=approve, headers=as_("auditor@bank")).status_code
        == 403
    )
    done = api.post(f"/runs/{run_id}/review", json=approve, headers=as_("reviewer@bank"))
    assert done.status_code == 200
    assert done.json()["state"] == "APPROVED"
    assert done.json()["review"]["reviewer"] == "reviewer@bank"
    assert (
        api.post(f"/runs/{run_id}/review", json=approve, headers=as_("lead@bank")).status_code
        == 409
    )


def test_a_flagged_report_is_approved_only_with_acknowledgement(
    api: TestClient, conn: psycopg.Connection
) -> None:
    run_id, _, artifact = run_to_review(conn, WITH_DIGIT, WITH_DIGIT)
    body = {"decision": "approved", "report_sha256": artifact.report_sha256}
    url, who = f"/runs/{run_id}/review", as_("reviewer@bank")
    assert api.post(url, json=body, headers=who).status_code == 409
    ok = api.post(url, json={**body, "acknowledge_flags": True}, headers=who)
    assert ok.json()["review"]["flags_acknowledged"] is True


def test_events_and_their_stream(api: TestClient, conn: psycopg.Connection) -> None:
    run_id, _, _ = run_to_review(conn, GOOD)
    listed = api.get(f"/runs/{run_id}/events", headers=as_("auditor@bank")).json()
    assert listed["chain_ok"] is True
    seqs = [e["seq"] for e in listed["events"]]

    with api.stream(
        "GET", f"/runs/{run_id}/events/stream", headers=as_("auditor@bank")
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    frames = [f for f in text.split("\n\n") if f]
    ids = [int(f.split("\n")[0][4:]) for f in frames if f.startswith("id: ")]
    assert ids == seqs
    assert frames[-1] == 'event: end\ndata: {"state": "PENDING_REVIEW"}'

    # Reconnecting resumes after the last event the client saw.
    resumed = api.get(
        f"/runs/{run_id}/events/stream",
        headers={**as_("auditor@bank"), "Last-Event-ID": str(seqs[-2])},
    ).text
    data = [json.loads(line[6:]) for line in resumed.split("\n") if line.startswith('data: {"seq')]
    assert [d["seq"] for d in data] == [seqs[-1]]


def test_answering_a_question(api: TestClient, conn: psycopg.Connection) -> None:
    run_id: UUID = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    ).run_id
    start(conn, run_id, actor="auditor@bank")
    transition(conn, run_id, S.INTERPRETING, actor="test")
    transition(
        conn, run_id, S.CLARIFICATION_REQUESTED, actor="test", payload={"question": "Which period?"}
    )
    view = api.get(f"/runs/{run_id}", headers=as_("auditor@bank")).json()
    assert (view["question"], view["can_answer"]) == ("Which period?", True)

    url = f"/runs/{run_id}/clarification"
    assert api.post(url, json={"answer": "x"}, headers=as_("lead@bank")).status_code == 403
    assert api.post(url, json={"answer": "x"}, headers=as_("fees@bank")).status_code == 404
    answered = api.post(url, json={"answer": "Last week."}, headers=as_("auditor@bank"))
    assert answered.json()["state"] == "INTERPRETING"
    assert api.post(url, json={"answer": "again"}, headers=as_("auditor@bank")).status_code == 409


def test_the_published_contract_is_current(migrated_url: str) -> None:
    """The UI's TypeScript types are generated from ops/openapi.json; regenerate it
    with `uv run python ops/export_openapi.py` when the API changes."""
    schema = create_app(deps(migrated_url)).openapi()
    assert json.loads(OPENAPI.read_text()) == schema


def test_the_deployed_app_uses_a_bounded_pool(migrated_url: str) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=migrated_url,
        api_identity="dev_header",
        api_pool_size=2,
    )
    pooled = ApiDeps.from_settings(settings)
    assert pooled.pool is not None
    with TestClient(create_app(pooled)) as client:
        for _ in range(5):
            assert client.get("/runs", headers=as_("auditor@bank")).status_code == 200
        assert pooled.pool.get_stats()["pool_size"] <= 2
    assert pooled.pool.closed  # closed with the app
