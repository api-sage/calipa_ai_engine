"""Run artifacts: step outputs stored as canonical JSON and read back by later steps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel

from calipa.audit_trail.store import get_blob, now_utc, put_blob
from calipa.canonical import canonical_json
from calipa.money import loads_decimal


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: UUID
    run_id: UUID
    kind: str
    blob_sha256: str
    created_by: str
    created_at: datetime


def put_artifact(
    conn: psycopg.Connection, run_id: UUID, kind: str, value: BaseModel, *, created_by: str
) -> ArtifactRef:
    blob = put_blob(conn, canonical_json(value), "application/json")
    ref = ArtifactRef(uuid4(), run_id, kind, blob, created_by, now_utc())
    conn.execute(
        """
        insert into run_artifacts
            (artifact_id, run_id, kind, blob_sha256, created_by, created_at)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (ref.artifact_id, ref.run_id, ref.kind, ref.blob_sha256, ref.created_by, ref.created_at),
    )
    return ref


def latest_artifact[M: BaseModel](
    conn: psycopg.Connection, run_id: UUID, kind: str, model: type[M]
) -> tuple[ArtifactRef, M] | None:
    row = conn.execute(
        """
        select artifact_id, run_id, kind, blob_sha256, created_by, created_at
        from run_artifacts where run_id = %s and kind = %s
        order by created_at desc, artifact_id desc limit 1
        """,
        (run_id, kind),
    ).fetchone()
    if row is None:
        return None
    ref = ArtifactRef(*row)
    return ref, model.model_validate(loads_decimal(get_blob(conn, ref.blob_sha256)))


def require_artifact[M: BaseModel](
    conn: psycopg.Connection, run_id: UUID, kind: str, model: type[M]
) -> tuple[ArtifactRef, M]:
    found = latest_artifact(conn, run_id, kind, model)
    if found is None:
        raise LookupError(f"run {run_id} has no {kind!r} artifact")
    return found


def list_artifacts[M: BaseModel](
    conn: psycopg.Connection, run_id: UUID, kind: str, model: type[M]
) -> list[tuple[ArtifactRef, M]]:
    """Every artifact of one kind for a run, oldest first."""
    rows = conn.execute(
        """
        select artifact_id, run_id, kind, blob_sha256, created_by, created_at
        from run_artifacts where run_id = %s and kind = %s
        order by created_at, artifact_id
        """,
        (run_id, kind),
    ).fetchall()
    out: list[tuple[ArtifactRef, M]] = []
    for row in rows:
        ref = ArtifactRef(*row)
        out.append((ref, model.model_validate(loads_decimal(get_blob(conn, ref.blob_sha256)))))
    return out
