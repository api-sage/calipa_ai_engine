"""Report versions and review decisions as stored. Inserts only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg

from calipa.audit_trail.store import now_utc
from calipa.errors import IntegrityViolation, RunNotFound

Decision = Literal["approved", "rejected", "reinvestigate"]


@dataclass(frozen=True, slots=True)
class ReportVersion:
    run_id: UUID
    root_run_id: UUID
    version: int
    artifact_id: UUID
    report_sha256: str
    markdown_sha256: str
    flagged: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Review:
    review_id: UUID
    run_id: UUID
    report_sha256: str
    decision: Decision
    reviewer: str
    note: str
    flags_acknowledged: bool
    child_run_id: UUID | None
    created_at: datetime


_REPORT_COLUMNS = (
    "run_id, root_run_id, version, artifact_id, report_sha256, markdown_sha256, flagged, created_at"
)
_REVIEW_COLUMNS = (
    "review_id, run_id, report_sha256, decision, reviewer, note, flags_acknowledged, "
    "child_run_id, created_at"
)


def lineage(conn: psycopg.Connection, run_id: UUID) -> list[UUID]:
    """The run's chain of re-investigations, oldest first, ending with this run."""
    rows = conn.execute(
        """
        with recursive chain(run_id, parent_run_id, depth) as (
            select run_id, parent_run_id, 0 from runs where run_id = %s
            union all
            select r.run_id, r.parent_run_id, c.depth + 1
            from runs r join chain c on r.run_id = c.parent_run_id
        )
        select run_id from chain order by depth desc
        """,
        (run_id,),
    ).fetchall()
    if not rows:
        raise RunNotFound(run_id)
    return [row[0] for row in rows]


def register_report(
    conn: psycopg.Connection,
    run_id: UUID,
    *,
    artifact_id: UUID,
    report_sha256: str,
    markdown_sha256: str,
    flagged: bool,
) -> ReportVersion:
    """Record the run's report as the next version of its audit. Recording the same
    report again returns the stored row; a different one for the same run is an error."""
    chain = lineage(conn, run_id)
    conn.execute(
        f"""
        insert into reports ({_REPORT_COLUMNS})
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (run_id) do nothing
        """,  # noqa: S608 - constant column list
        (
            run_id,
            chain[0],
            len(chain),
            artifact_id,
            report_sha256,
            markdown_sha256,
            flagged,
            now_utc(),
        ),
    )
    stored = report_version(conn, run_id)
    assert stored is not None
    if stored.report_sha256 != report_sha256:
        raise IntegrityViolation(f"run {run_id} already has a different report")
    return stored


def report_version(conn: psycopg.Connection, run_id: UUID) -> ReportVersion | None:
    row = conn.execute(
        f"select {_REPORT_COLUMNS} from reports where run_id = %s",  # noqa: S608
        (run_id,),
    ).fetchone()
    return None if row is None else ReportVersion(*row)


def versions(conn: psycopg.Connection, run_id: UUID) -> list[ReportVersion]:
    """Every report version of the audit this run belongs to, oldest first."""
    root = lineage(conn, run_id)[0]
    rows = conn.execute(
        f"select {_REPORT_COLUMNS} from reports where root_run_id = %s order by version",  # noqa: S608
        (root,),
    ).fetchall()
    return [ReportVersion(*row) for row in rows]


def insert_review(conn: psycopg.Connection, review: Review) -> None:
    conn.execute(
        f"insert into reviews ({_REVIEW_COLUMNS}) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",  # noqa: S608
        (
            review.review_id,
            review.run_id,
            review.report_sha256,
            review.decision,
            review.reviewer,
            review.note,
            review.flags_acknowledged,
            review.child_run_id,
            review.created_at,
        ),
    )


def get_review(conn: psycopg.Connection, run_id: UUID) -> Review | None:
    row = conn.execute(
        f"select {_REVIEW_COLUMNS} from reviews where run_id = %s",  # noqa: S608
        (run_id,),
    ).fetchone()
    return None if row is None else Review(*row)
