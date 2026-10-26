"""Anchoring the audit trail outside the database (see migration 0005).

`anchor` records the new chain heads as the next anchor and publishes every anchor
not yet published to a write-once sink. `verify_anchors` checks three things against
each other: the anchors in the database, the documents in the sink, and the runs'
event chains as they are now. Any rewrite of history shows up as a mismatch.

The sink is anything that will not overwrite or delete what it was given:
`DirectorySink` writes each anchor as a new read-only file and refuses to replace one,
which is enough for development and for a directory on WORM storage. Object storage
with a retention lock (for example S3 Object Lock in compliance mode) is the
production choice; it fits the same two-method interface.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

from calipa.audit_trail.store import now_utc
from calipa.canonical import canonical_json, hash_value, sha256_hex

_ANCHOR_LOCK = 0x43414C4950410001  # one anchorer at a time, across workers


class SinkError(RuntimeError):
    pass


class AnchorSink(Protocol):
    name: str

    def put(self, key: str, content: bytes) -> str:
        """Store `content` under `key` for good and return a receipt. Storing the same
        bytes again returns a receipt; different bytes under an existing key fail."""
        ...

    def get(self, key: str) -> bytes | None: ...

    def keys(self) -> list[str]: ...


class DirectorySink:
    """One read-only file per anchor, never replaced."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.name = f"dir:{directory}"

    def put(self, key: str, content: bytes) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / key
        try:
            with open(path, "xb") as out:  # fails if the file exists
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            path.chmod(0o444)
        except FileExistsError:
            if path.read_bytes() != content:
                raise SinkError(f"{key} already exists with different content") from None
        return f"{self.name}/{key} sha256:{sha256_hex(content)}"

    def get(self, key: str) -> bytes | None:
        path = self.directory / key
        return path.read_bytes() if path.exists() else None

    def keys(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(p.name for p in self.directory.glob("anchor-*.json"))


@dataclass(frozen=True, slots=True)
class Anchor:
    seq: int
    created_at: datetime
    heads: list[dict[str, Any]]
    prev_hash: str | None
    anchor_hash: str

    @property
    def key(self) -> str:
        return f"anchor-{self.seq:010d}.json"

    def document(self) -> bytes:
        """What is published: everything needed to check the anchor on its own."""
        return canonical_json(
            {
                "seq": self.seq,
                "created_at": self.created_at,
                "heads": self.heads,
                "prev_hash": self.prev_hash,
                "anchor_hash": self.anchor_hash,
            }
        )


def _anchor_hash(
    seq: int, created_at: datetime, heads: list[dict[str, Any]], prev_hash: str | None
) -> str:
    return hash_value(
        {"seq": seq, "created_at": created_at, "heads": heads, "prev_hash": prev_hash}
    )


def _changed_heads(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """The latest event of every run whose chain grew since it was last anchored."""
    rows = conn.execute(
        """
        with anchored as (
            select (h->>'run_id')::uuid as run_id, max((h->>'seq')::integer) as seq
            from chain_anchors, jsonb_array_elements(heads) as h
            group by 1
        ), latest as (
            select distinct on (run_id) run_id, seq, row_hash
            from run_events order by run_id, seq desc
        )
        select l.run_id, l.seq, l.row_hash
        from latest l left join anchored a using (run_id)
        where a.seq is null or l.seq > a.seq
        order by l.run_id
        """
    ).fetchall()
    return [{"run_id": str(r[0]), "seq": r[1], "row_hash": r[2]} for r in rows]


def create_anchor(
    conn: psycopg.Connection, *, clock: Callable[[], datetime] = now_utc
) -> Anchor | None:
    """Record the next anchor, or None when no chain has grown since the last one."""
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (_ANCHOR_LOCK,))
        heads = _changed_heads(conn)
        if not heads:
            return None
        last = conn.execute(
            "select seq, anchor_hash from chain_anchors order by seq desc limit 1"
        ).fetchone()
        seq, prev_hash = (1, None) if last is None else (last[0] + 1, last[1])
        created_at = clock()
        anchor = Anchor(
            seq, created_at, heads, prev_hash, _anchor_hash(seq, created_at, heads, prev_hash)
        )
        conn.execute(
            """
            insert into chain_anchors (seq, created_at, heads, prev_hash, anchor_hash)
            values (%s, %s, %s, %s, %s)
            """,
            (seq, created_at, Jsonb(heads), prev_hash, anchor.anchor_hash),
        )
    return anchor


def _fields(a: Anchor) -> tuple[int, datetime, list[dict[str, Any]], str | None]:
    return a.seq, a.created_at, a.heads, a.prev_hash


def list_anchors(conn: psycopg.Connection) -> list[Anchor]:
    rows = conn.execute(
        "select seq, created_at, heads, prev_hash, anchor_hash from chain_anchors order by seq"
    ).fetchall()
    return [Anchor(*row) for row in rows]


def publish_pending(
    conn: psycopg.Connection, sink: AnchorSink, *, clock: Callable[[], datetime] = now_utc
) -> list[int]:
    """Publish every anchor this sink has no receipt for. Returns their seqs."""
    rows = conn.execute(
        """
        select a.seq from chain_anchors a
        where not exists (select 1 from anchor_receipts r
                          where r.anchor_seq = a.seq and r.sink = %s)
        order by a.seq
        """,
        (sink.name,),
    ).fetchall()
    pending = {r[0] for r in rows}
    published: list[int] = []
    for anchor in list_anchors(conn):
        if anchor.seq not in pending:
            continue
        receipt = sink.put(anchor.key, anchor.document())
        conn.execute(
            """
            insert into anchor_receipts (anchor_seq, sink, receipt, published_at)
            values (%s, %s, %s, %s) on conflict do nothing
            """,
            (anchor.seq, sink.name, receipt, clock()),
        )
        published.append(anchor.seq)
    return published


def anchor(
    conn: psycopg.Connection, sink: AnchorSink, *, clock: Callable[[], datetime] = now_utc
) -> list[int]:
    """One anchoring pass: record new heads, then publish what is unpublished."""
    create_anchor(conn, clock=clock)
    return publish_pending(conn, sink, clock=clock)


@dataclass(frozen=True, slots=True)
class AnchorCheck:
    anchors_checked: int
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_anchors(conn: psycopg.Connection, sink: AnchorSink) -> AnchorCheck:
    anchors = list_anchors(conn)
    problems: list[str] = []
    prev: str | None = None
    for index, a in enumerate(anchors, start=1):
        if a.seq != index:
            problems.append(f"anchor {a.seq}: expected seq {index}")
        if a.prev_hash != prev:
            problems.append(f"anchor {a.seq}: prev_hash does not match anchor {a.seq - 1}")
        if _anchor_hash(*_fields(a)) != a.anchor_hash:
            problems.append(f"anchor {a.seq}: hash does not match its contents")
        prev = a.anchor_hash
        published = sink.get(a.key)
        if published is None:
            problems.append(f"anchor {a.seq}: not in {sink.name}")
        elif published != a.document():
            problems.append(f"anchor {a.seq}: differs from the copy in {sink.name}")
        for head in a.heads:
            row = conn.execute(
                "select row_hash from run_events where run_id = %s and seq = %s",
                (head["run_id"], head["seq"]),
            ).fetchone()
            if row is None or row[0] != head["row_hash"]:
                problems.append(
                    f"anchor {a.seq}: run {head['run_id']} event {head['seq']} has changed"
                )
    known = {a.key for a in anchors}
    for key in sink.keys():
        if key not in known:
            document = json.loads(sink.get(key) or b"{}")
            problems.append(
                f"{key} is in {sink.name} but not in the database "
                f"(anchor_hash {document.get('anchor_hash')})"
            )
    return AnchorCheck(len(anchors), problems)


def main() -> None:
    """uv run python -m calipa.audit_trail.anchor {publish,verify}"""
    import argparse

    from calipa.audit_trail.db import connect
    from calipa.config import get_settings

    parser = argparse.ArgumentParser(description="Anchor the audit trail, or check it.")
    parser.add_argument("action", choices=["publish", "verify"])
    args = parser.parse_args()
    settings = get_settings()
    if settings.anchor_dir is None:
        raise SystemExit("set CALIPA_ANCHOR_DIR to the write-once anchor store")
    sink = DirectorySink(settings.anchor_dir)
    with connect(settings.database_url) as conn:
        if args.action == "publish":
            print(f"published anchors {anchor(conn, sink) or 'none (nothing new)'}")
            return
        check = verify_anchors(conn, sink)
    for problem in check.problems:
        print(problem)
    print(f"{check.anchors_checked} anchors checked: {'ok' if check.ok else 'PROBLEMS'}")
    raise SystemExit(0 if check.ok else 1)


if __name__ == "__main__":
    main()
