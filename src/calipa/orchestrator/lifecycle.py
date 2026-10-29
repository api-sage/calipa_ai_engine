"""The run state machine.

A run's state is the `state` of its latest state-bearing event in `run_events`; there
is no mutable status column. Every transition is checked against the table below
inside a lock on the run, so two workers or a worker and a reviewer can never both
move the same run.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

import psycopg

from calipa.audit_trail.store import RunEvent, append_event, get_run

MAX_CLARIFICATION_ROUNDS = 2


class RunState(StrEnum):
    RECEIVED = "RECEIVED"
    INTERPRETING = "INTERPRETING"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    EXTRACTING = "EXTRACTING"
    EXTRACTION_PARTIAL = "EXTRACTION_PARTIAL"
    RECONCILING = "RECONCILING"
    NO_EXCEPTIONS = "NO_EXCEPTIONS"
    ANALYSING = "ANALYSING"
    REPORTING = "REPORTING"
    PENDING_REVIEW = "PENDING_REVIEW"
    # Terminal
    DENIED = "DENIED"
    UNSUPPORTED = "UNSUPPORTED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REINVESTIGATION = "REINVESTIGATION"


S = RunState

TERMINAL: frozenset[RunState] = frozenset(
    {S.DENIED, S.UNSUPPORTED, S.ESCALATED, S.FAILED, S.APPROVED, S.REJECTED, S.REINVESTIGATION}
)
# States that wait for a person; workers leave them alone.
AWAITING_HUMAN: frozenset[RunState] = frozenset({S.CLARIFICATION_REQUESTED, S.PENDING_REVIEW})

TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    S.RECEIVED: frozenset({S.INTERPRETING}),
    S.INTERPRETING: frozenset(
        {S.CLARIFICATION_REQUESTED, S.DENIED, S.UNSUPPORTED, S.ESCALATED, S.EXTRACTING}
    ),
    S.CLARIFICATION_REQUESTED: frozenset({S.INTERPRETING}),
    S.EXTRACTING: frozenset({S.RECONCILING, S.EXTRACTION_PARTIAL}),
    S.EXTRACTION_PARTIAL: frozenset({S.RECONCILING}),
    S.RECONCILING: frozenset({S.NO_EXCEPTIONS, S.ANALYSING}),
    S.NO_EXCEPTIONS: frozenset({S.REPORTING}),
    S.ANALYSING: frozenset({S.REPORTING}),
    S.REPORTING: frozenset({S.PENDING_REVIEW}),
    S.PENDING_REVIEW: frozenset({S.APPROVED, S.REJECTED, S.REINVESTIGATION}),
}
# Any non-terminal state may fail.
for _state in RunState:
    if _state not in TERMINAL:
        TRANSITIONS[_state] = TRANSITIONS.get(_state, frozenset()) | {S.FAILED}
for _state in TERMINAL:
    TRANSITIONS[_state] = frozenset()


class TransitionNotAllowed(RuntimeError):
    pass


def current_state(conn: psycopg.Connection, run_id: UUID) -> RunState | None:
    """None means the run has no state yet (it was just created)."""
    row = conn.execute(
        """
        select state from run_events
        where run_id = %s and state is not null
        order by seq desc limit 1
        """,
        (run_id,),
    ).fetchone()
    return None if row is None else RunState(row[0])


def count_entries(conn: psycopg.Connection, run_id: UUID, state: RunState) -> int:
    row = conn.execute(
        "select count(*) from run_events where run_id = %s and state = %s",
        (run_id, state.value),
    ).fetchone()
    return int(row[0]) if row else 0


def start(conn: psycopg.Connection, run_id: UUID, *, actor: str) -> RunEvent:
    """Give a new run its first state. Calling it again on a started run is an error."""
    with conn.transaction():
        _lock(conn, run_id)
        if current_state(conn, run_id) is not None:
            raise TransitionNotAllowed(f"run {run_id} already started")
        return append_event(
            conn,
            run_id,
            event_type="transition",
            actor=actor,
            state=S.RECEIVED.value,
            payload={"from": None, "to": S.RECEIVED.value},
        )


def transition(
    conn: psycopg.Connection,
    run_id: UUID,
    to: RunState,
    *,
    actor: str,
    expected_from: RunState | None = None,
    payload: dict[str, Any] | None = None,
) -> RunEvent:
    """Move the run to `to`. If `expected_from` is given, the run must still be in that
    state, which guards against acting on a stale read."""
    with conn.transaction():
        _lock(conn, run_id)
        state = current_state(conn, run_id)
        if state is None:
            raise TransitionNotAllowed(f"run {run_id} has not been started")
        if expected_from is not None and state is not expected_from:
            raise TransitionNotAllowed(f"run is {state}, expected {expected_from}")
        if to not in TRANSITIONS[state]:
            raise TransitionNotAllowed(f"{state} -> {to} is not allowed")
        if (
            to is S.CLARIFICATION_REQUESTED
            and count_entries(conn, run_id, S.CLARIFICATION_REQUESTED) >= MAX_CLARIFICATION_ROUNDS
        ):
            raise TransitionNotAllowed(
                f"clarification limit ({MAX_CLARIFICATION_ROUNDS}) reached; escalate instead"
            )
        return append_event(
            conn,
            run_id,
            event_type="transition",
            actor=actor,
            state=to.value,
            payload={"from": state.value, "to": to.value, **(payload or {})},
        )


def _lock(conn: psycopg.Connection, run_id: UUID) -> None:
    get_run(conn, run_id)  # raises RunNotFound for an unknown run
    conn.execute("select 1 from runs where run_id = %s for update", (run_id,))
