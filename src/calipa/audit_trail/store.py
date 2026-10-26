"""Writes to the audit trail. Inserts only; nothing in here updates or deletes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from calipa.canonical import canonical_json, normalize, sha256_hex


def now_utc() -> datetime:
    return datetime.now(UTC)


def put_blob(conn: psycopg.Connection, content: bytes, content_type: str) -> str:
    """Store bytes by their SHA-256 and return the hash. Storing the same bytes twice
    is a no-op."""
    digest = sha256_hex(content)
    conn.execute(
        """
        insert into raw_blobs (sha256, content, content_type, size_bytes)
        values (%s, %s, %s, %s)
        on conflict (sha256) do nothing
        """,
        (digest, content, content_type, len(content)),
    )
    return digest


def get_blob(conn: psycopg.Connection, digest: str) -> bytes:
    row = conn.execute("select content from raw_blobs where sha256 = %s", (digest,)).fetchone()
    if row is None:
        raise KeyError(digest)
    return bytes(row[0])


@dataclass(frozen=True, slots=True)
class Run:
    run_id: UUID
    idempotency_key: str
    initiated_by: str
    request_text: str
    created_at: datetime
    parent_run_id: UUID | None
    created: bool  # False when an existing run was returned for the same key


_RUN_COLUMNS = "run_id, idempotency_key, initiated_by, request_text, created_at, parent_run_id"


def create_run(
    conn: psycopg.Connection,
    *,
    idempotency_key: str,
    initiated_by: str,
    request_text: str,
    parent_run_id: UUID | None = None,
) -> Run:
    """Create a run, or return the existing one for the same idempotency key (a UI
    double-submit must not start a second audit)."""
    with conn.transaction():
        row = conn.execute(
            f"""
            insert into runs ({_RUN_COLUMNS})
            values (%s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do nothing
            returning {_RUN_COLUMNS}
            """,  # noqa: S608 - constant column list
            (uuid4(), idempotency_key, initiated_by, request_text, now_utc(), parent_run_id),
        ).fetchone()
        if row is not None:
            return _run(row, created=True)
        existing = conn.execute(
            f"select {_RUN_COLUMNS} from runs where idempotency_key = %s",  # noqa: S608
            (idempotency_key,),
        ).fetchone()
    assert existing is not None
    if (existing[2], existing[3], existing[5]) != (initiated_by, request_text, parent_run_id):
        raise ValueError("idempotency key reused for a different request")
    return _run(existing, created=False)


def get_run(conn: psycopg.Connection, run_id: UUID) -> Run:
    row = conn.execute(
        f"select {_RUN_COLUMNS} from runs where run_id = %s",  # noqa: S608
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"run {run_id} not found")
    return _run(row, created=False)


def _run(row: tuple[Any, ...], *, created: bool) -> Run:
    run_id, idempotency_key, initiated_by, request_text, created_at, parent = row
    return Run(run_id, idempotency_key, initiated_by, request_text, created_at, parent, created)


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: UUID
    seq: int
    event_type: str
    state: str | None
    payload: dict[str, Any]
    actor: str
    created_at: datetime
    prev_hash: str | None
    row_hash: str


def _event_hash(
    *,
    run_id: UUID,
    seq: int,
    event_type: str,
    state: str | None,
    payload: dict[str, Any],
    actor: str,
    created_at: datetime,
    prev_hash: str | None,
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "run_id": run_id,
                "seq": seq,
                "event_type": event_type,
                "state": state,
                "payload": payload,
                "actor": actor,
                "created_at": created_at,
                "prev_hash": prev_hash,
            }
        )
    )


def append_event(
    conn: psycopg.Connection,
    run_id: UUID,
    *,
    event_type: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    state: str | None = None,
) -> RunEvent:
    """Append an event to the run's hash chain."""
    # Round-trip through canonical JSON so the stored jsonb holds only JSON-native
    # values (Decimals become strings). Reading it back then rehashes identically.
    clean_payload: dict[str, Any] = json.loads(canonical_json(normalize(payload or {})))
    created_at = now_utc()
    with conn.transaction():
        locked = conn.execute(
            "select 1 from runs where run_id = %s for update", (run_id,)
        ).fetchone()
        if locked is None:
            raise KeyError(f"run {run_id} not found")
        last = conn.execute(
            "select seq, row_hash from run_events where run_id = %s order by seq desc limit 1",
            (run_id,),
        ).fetchone()
        seq, prev_hash = (1, None) if last is None else (last[0] + 1, last[1])
        row_hash = _event_hash(
            run_id=run_id,
            seq=seq,
            event_type=event_type,
            state=state,
            payload=clean_payload,
            actor=actor,
            created_at=created_at,
            prev_hash=prev_hash,
        )
        conn.execute(
            """
            insert into run_events
                (run_id, seq, event_type, state, payload, actor, created_at, prev_hash, row_hash)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                seq,
                event_type,
                state,
                Jsonb(clean_payload),
                actor,
                created_at,
                prev_hash,
                row_hash,
            ),
        )
    return RunEvent(
        run_id, seq, event_type, state, clean_payload, actor, created_at, prev_hash, row_hash
    )


def list_events(conn: psycopg.Connection, run_id: UUID) -> list[RunEvent]:
    rows = conn.execute(
        """
        select run_id, seq, event_type, state, payload, actor, created_at, prev_hash, row_hash
        from run_events where run_id = %s order by seq
        """,
        (run_id,),
    ).fetchall()
    return [RunEvent(*row) for row in rows]


@dataclass(frozen=True, slots=True)
class ChainCheck:
    ok: bool
    events_checked: int
    first_bad_seq: int | None = None
    reason: str | None = None


def verify_chain(conn: psycopg.Connection, run_id: UUID) -> ChainCheck:
    """Recompute every hash in the run's chain from the stored rows."""
    events = list_events(conn, run_id)
    expected_prev: str | None = None
    for index, event in enumerate(events, start=1):
        if event.seq != index:
            return ChainCheck(False, index - 1, event.seq, f"expected seq {index}")
        if event.prev_hash != expected_prev:
            return ChainCheck(False, index - 1, event.seq, "prev_hash does not match")
        recomputed = _event_hash(
            run_id=event.run_id,
            seq=event.seq,
            event_type=event.event_type,
            state=event.state,
            payload=event.payload,
            actor=event.actor,
            created_at=event.created_at,
            prev_hash=event.prev_hash,
        )
        if recomputed != event.row_hash:
            return ChainCheck(False, index - 1, event.seq, "row_hash does not match contents")
        expected_prev = event.row_hash
    return ChainCheck(True, len(events))
