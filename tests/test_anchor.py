"""Anchors: chain heads published to write-once storage, and the checks that catch a
history rewritten by someone with full database access. These tests tamper with the
database, so they use a database of their own."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from calipa.audit_trail.anchor import (
    DirectorySink,
    SinkError,
    anchor,
    create_anchor,
    list_anchors,
    publish_pending,
    verify_anchors,
)
from calipa.audit_trail.store import append_event, create_run


def _run(conn: psycopg.Connection, events: int = 2) -> UUID:
    run_id = create_run(
        conn, idempotency_key=f"k-{uuid4()}", initiated_by="auditor@bank", request_text="audit"
    ).run_id
    for n in range(events):
        append_event(conn, run_id, event_type="note", actor="test", payload={"n": n})
    return run_id


def test_anchors_record_only_chains_that_grew(own: psycopg.Connection, tmp_path: Path) -> None:
    sink = DirectorySink(tmp_path)
    a, b = _run(own), _run(own, 3)
    assert anchor(own, sink) == [1]
    [first] = list_anchors(own)
    assert {(h["run_id"], h["seq"]) for h in first.heads} == {(str(a), 2), (str(b), 3)}
    assert first.prev_hash is None

    assert anchor(own, sink) == []  # nothing grew: no new anchor
    append_event(own, a, event_type="note", actor="test", payload={})
    assert anchor(own, sink) == [2]
    second = list_anchors(own)[1]
    assert [(h["run_id"], h["seq"]) for h in second.heads] == [(str(a), 3)]
    assert second.prev_hash == first.anchor_hash

    published = tmp_path / first.key
    assert published.read_bytes() == first.document()
    assert published.stat().st_mode & 0o222 == 0  # read-only
    assert verify_anchors(own, sink).ok


def test_an_anchor_that_failed_to_publish_is_published_next_time(
    own: psycopg.Connection, tmp_path: Path
) -> None:
    class Down(DirectorySink):
        def put(self, key: str, content: bytes) -> str:
            raise SinkError("store unavailable")

    _run(own)
    with pytest.raises(SinkError):
        anchor(own, Down(tmp_path))
    assert len(list_anchors(own)) == 1  # recorded, not yet published
    sink = DirectorySink(tmp_path)
    assert publish_pending(own, sink) == [1]
    assert publish_pending(own, sink) == []
    assert verify_anchors(own, sink).ok


def _rewrite(conn: psycopg.Connection, run_id: UUID) -> None:
    """What only someone with owner access could do: switch off the append-only
    trigger and change an event."""
    conn.execute("alter table run_events disable trigger run_events_append_only")
    conn.execute(
        "update run_events set payload = '{\"n\": 99}', row_hash = repeat('0', 64) "
        "where run_id = %s and seq = 2",
        (run_id,),
    )
    conn.execute("alter table run_events enable trigger run_events_append_only")


def test_a_rewritten_event_no_longer_matches_its_anchor(
    own: psycopg.Connection, tmp_path: Path
) -> None:
    sink = DirectorySink(tmp_path)
    run_id = _run(own)
    anchor(own, sink)
    _rewrite(own, run_id)
    check = verify_anchors(own, sink)
    assert not check.ok
    assert check.problems == [f"anchor 1: run {run_id} event 2 has changed"]


def test_a_rewritten_anchor_no_longer_matches_the_published_copy(
    own: psycopg.Connection, tmp_path: Path
) -> None:
    sink = DirectorySink(tmp_path)
    _run(own)
    anchor(own, sink)
    own.execute("alter table chain_anchors disable trigger chain_anchors_append_only")
    own.execute("update chain_anchors set created_at = created_at + interval '1 day'")
    own.execute("alter table chain_anchors enable trigger chain_anchors_append_only")
    problems = verify_anchors(own, sink).problems
    assert "anchor 1: hash does not match its contents" in problems
    assert "anchor 1: differs from the copy in " + sink.name in problems


def test_published_anchors_missing_from_the_database_are_reported(
    own: psycopg.Connection, tmp_path: Path
) -> None:
    sink = DirectorySink(tmp_path)
    (tmp_path / "anchor-0000000007.json").write_text('{"anchor_hash": "abc"}')
    problems = verify_anchors(own, sink).problems
    assert problems == [
        f"anchor-0000000007.json is in {sink.name} but not in the database (anchor_hash abc)"
    ]


def test_the_sink_never_replaces_what_it_holds(tmp_path: Path) -> None:
    sink = DirectorySink(tmp_path)
    receipt = sink.put("anchor-0000000001.json", b"one")
    assert receipt.endswith("anchor-0000000001.json sha256:" + hashlib.sha256(b"one").hexdigest())
    assert sink.put("anchor-0000000001.json", b"one") == receipt  # a retry is fine
    with pytest.raises(SinkError, match="different content"):
        sink.put("anchor-0000000001.json", b"two")
    assert sink.get("anchor-0000000001.json") == b"one"


def test_anchors_are_append_only(own: psycopg.Connection, tmp_path: Path) -> None:
    _run(own)
    assert create_anchor(own) is not None
    with pytest.raises(psycopg.Error, match="append-only"):
        own.execute("delete from chain_anchors")


def test_the_worker_anchors_on_a_schedule_and_survives_failures(
    own: psycopg.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from calipa.worker import Anchoring

    now = [0.0]
    sink = DirectorySink(tmp_path)
    anchoring = Anchoring(own, sink, interval=60, clock=lambda: now[0])
    run_id = _run(own)
    assert anchoring.maybe_run() == [1]
    append_event(own, run_id, event_type="note", actor="test", payload={})
    now[0] = 30
    assert anchoring.maybe_run() == []  # not due yet
    now[0] = 61
    assert anchoring.maybe_run() == [2]

    class Down(DirectorySink):
        def put(self, key: str, content: bytes) -> str:
            raise SinkError("store unavailable")

    append_event(own, run_id, event_type="note", actor="test", payload={})
    failing = Anchoring(own, Down(tmp_path), interval=60, clock=lambda: now[0])
    assert failing.maybe_run() == []
    assert "anchoring the audit trail failed" in caplog.text
