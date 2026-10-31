from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from calipa.audit_trail import db
from calipa.audit_trail.store import (
    Run,
    append_event,
    create_run,
    get_blob,
    list_events,
    put_blob,
    runs_with_state,
    verify_chain,
)
from calipa.canonical import sha256_hex
from calipa.errors import RunNotFound

# Table -> a column to "update", for the append-only checks.
TABLES = {
    "raw_blobs": "content_type",
    "runs": "request_text",
    "run_events": "actor",
    "api_calls": "url",
    "llm_calls": "step",
    "llm_call_results": "error",
    "fx_rate_snapshots": "rate_count",
    "fx_rates": "rate",
}


def new_run(conn: psycopg.Connection) -> Run:
    return create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    )


def test_migrate_is_idempotent(conn: psycopg.Connection) -> None:
    assert db.migrate(conn) == []


def test_changed_migration_is_refused(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = db._migration_files()
    tampered = [(version, sql + "\n-- edited") for version, sql in original]
    monkeypatch.setattr(db, "_migration_files", lambda: tampered)
    with pytest.raises(db.MigrationError, match="changed after it was applied"):
        db.migrate(conn)


@pytest.mark.parametrize(("table", "column"), TABLES.items())
def test_update_delete_truncate_are_blocked(
    conn: psycopg.Connection, table: str, column: str
) -> None:
    _ensure_rows(conn)  # row triggers only fire when a row exists
    for statement in (
        f"update {table} set {column} = {column}",
        f"delete from {table}",
        f"truncate {table} cascade",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            conn.execute(statement)


def _ensure_rows(conn: psycopg.Connection) -> None:
    run = new_run(conn)
    blob = put_blob(conn, f"row-{uuid4()}".encode(), "text/plain")
    append_event(conn, run.run_id, event_type="created", actor="system")
    call_id = uuid4()
    conn.execute(
        """insert into api_calls (call_id, run_id, method, url, started_at, duration_ms)
           values (%s, %s, 'GET', 'https://example.test/x', now(), 1)""",
        (call_id, run.run_id),
    )
    conn.execute(
        """insert into llm_calls (call_id, run_id, step, prompt_name, prompt_version,
             prompt_sha256, schema_name, schema_sha256, model_requested, effort,
             request_blob, started_at)
           values (%s, %s, 's', 'p', '1', %s, 'S', %s, 'm', 'low', %s, now())""",
        (call_id, run.run_id, "a" * 64, "b" * 64, blob),
    )
    conn.execute(
        """insert into llm_call_results (call_id, outcome, finished_at, duration_ms)
           values (%s, 'ok', now(), 1)""",
        (call_id,),
    )
    snapshot_id = uuid4()
    conn.execute(
        """insert into fx_rate_snapshots
             (snapshot_id, api_call_id, response_blob, rate_count, fetched_at)
           values (%s, %s, %s, 1, now())""",
        (snapshot_id, call_id, blob),
    )
    conn.execute(
        """insert into fx_rates (snapshot_id, source_rate_id, from_currency, to_currency,
             rate, rate_date, source_created_at)
           values (%s, 1, 'USD', 'NGN', 1338.380059, '2026-10-26', now())""",
        (snapshot_id,),
    )


def test_api_calls_only_accept_get(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """insert into api_calls (call_id, method, url, started_at, duration_ms)
               values (%s, 'POST', 'https://example.test/transfer-funds', now(), 1)""",
            (uuid4(),),
        )


def test_blobs_are_content_addressed(conn: psycopg.Connection) -> None:
    body = b'{"success":true}'
    digest = put_blob(conn, body, "application/json")
    assert digest == sha256_hex(body)
    assert put_blob(conn, body, "application/json") == digest  # idempotent
    assert get_blob(conn, digest) == body
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into raw_blobs (sha256, content, content_type, size_bytes) "
            "values (%s, %s, 'text/plain', 3)",
            ("0" * 64, b"abc"),
        )


def test_create_run_is_idempotent(conn: psycopg.Connection) -> None:
    key = f"k-{uuid4()}"
    first = create_run(conn, idempotency_key=key, initiated_by="a", request_text="audit Q3")
    again = create_run(conn, idempotency_key=key, initiated_by="a", request_text="audit Q3")
    assert first.created and not again.created
    assert first.run_id == again.run_id
    with pytest.raises(ValueError, match="different request"):
        create_run(conn, idempotency_key=key, initiated_by="a", request_text="audit Q4")


def test_hash_chain_links_and_verifies(conn: psycopg.Connection) -> None:
    run = new_run(conn)
    e1 = append_event(
        conn, run.run_id, event_type="received", actor="auditor@bank", state="RECEIVED"
    )
    e2 = append_event(
        conn,
        run.run_id,
        event_type="scope_saved",
        actor="system",
        payload={"residual": Decimal("2.220"), "accounts": ["TRANSIENT"]},
    )
    e3 = append_event(conn, run.run_id, event_type="state", actor="system", state="EXTRACTING")
    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)
    assert e1.prev_hash is None and e2.prev_hash == e1.row_hash and e3.prev_hash == e2.row_hash
    assert e2.payload == {"residual": "2.22", "accounts": ["TRANSIENT"]}
    assert verify_chain(conn, run.run_id).ok
    assert len(list_events(conn, run.run_id)) == 3


def test_tampering_is_detected(conn: psycopg.Connection) -> None:
    run = new_run(conn)
    for i in range(3):
        append_event(conn, run.run_id, event_type="step", actor="system", payload={"i": i})
    # A superuser can bypass the trigger; the hash chain still exposes the edit.
    with conn.transaction():
        conn.execute("alter table run_events disable trigger run_events_append_only")
        conn.execute(
            "update run_events set payload = %s where run_id = %s and seq = 2",
            ('{"i": 99}', run.run_id),
        )
        conn.execute("alter table run_events enable trigger run_events_append_only")
    check = verify_chain(conn, run.run_id)
    assert not check.ok
    assert check.first_bad_seq == 2
    assert check.reason == "row_hash does not match contents"


def test_event_for_unknown_run_is_rejected(conn: psycopg.Connection) -> None:
    with pytest.raises(RunNotFound):
        append_event(conn, uuid4(), event_type="x", actor="system")


def test_events_after_a_seq(conn: psycopg.Connection) -> None:
    run = new_run(conn)
    for n in range(3):
        append_event(conn, run.run_id, event_type="note", actor="test", payload={"n": n})
    events = list_events(conn, run.run_id)
    later = list_events(conn, run.run_id, after=events[0].seq)
    assert [e.seq for e in later] == [e.seq for e in events[1:]]


def test_runs_with_their_current_state(conn: psycopg.Connection) -> None:
    by = f"lister-{uuid4()}@bank"
    runs = [
        create_run(conn, idempotency_key=f"k-{uuid4()}", initiated_by=by, request_text="audit")
        for _ in range(3)
    ]
    append_event(conn, runs[0].run_id, event_type="state", actor="t", payload={}, state="RECEIVED")
    append_event(conn, runs[0].run_id, event_type="state", actor="t", payload={}, state="DENIED")
    append_event(conn, runs[0].run_id, event_type="note", actor="t", payload={})

    found = runs_with_state(conn, initiated_by=by)
    assert {r.run_id: state for r, state in found} == {
        runs[0].run_id: "DENIED",
        runs[1].run_id: None,
        runs[2].run_id: None,
    }
    assert [r.run_id for r, _ in runs_with_state(conn, initiated_by=by, state="DENIED")] == [
        runs[0].run_id
    ]
    oldest = runs_with_state(conn, initiated_by=by, newest_first=False, limit=2)
    newest = runs_with_state(conn, initiated_by=by)
    assert len(oldest) == 2
    assert [r.run_id for r, _ in oldest] == [r.run_id for r, _ in reversed(newest)][:2]
