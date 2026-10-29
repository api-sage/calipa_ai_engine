"""Running steps and workers.

`Orchestrator.advance` moves a run forward one step at a time until it ends, waits
for a person, or reaches a state with no handler yet. Each step reads its inputs
from persisted artifacts, so a run interrupted at any point resumes from its last
state on the next `advance`, by any worker.

`Worker` picks up runs that need work. Ownership of a run is a Postgres session
advisory lock: it is released automatically if the worker's connection dies, so a
crashed worker never leaves a run stuck.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from calipa.audit_trail.store import Run, append_event, get_run, now_utc
from calipa.errors import IntegrityViolation
from calipa.orchestrator.lifecycle import (
    AWAITING_HUMAN,
    TERMINAL,
    RunState,
    current_state,
    start,
    transition,
)

MAX_ERROR_TEXT = 2_000


@dataclass(frozen=True, slots=True)
class Next:
    to: RunState
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StepContext:
    conn: psycopg.Connection
    run: Run
    state: RunState
    actor: str
    clock: Callable[[], datetime]

    @property
    def run_id(self) -> UUID:
        return self.run.run_id

    def now(self) -> datetime:
        return self.clock()

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        append_event(
            self.conn, self.run_id, event_type=event_type, actor=self.actor, payload=payload
        )


StepHandler = Callable[[StepContext], Next]


class Orchestrator:
    def __init__(
        self,
        conn: psycopg.Connection,
        handlers: Mapping[RunState, StepHandler],
        *,
        actor: str = "orchestrator",
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        clock: Callable[[], datetime] = now_utc,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not conn.autocommit:
            raise ValueError("Orchestrator needs an autocommit connection")
        self._conn = conn
        self._handlers = dict(handlers)
        self._actor = actor
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay_seconds
        self._clock = clock
        self._sleep = sleep

    def advance(self, run_id: UUID) -> RunState:
        run = get_run(self._conn, run_id)
        while True:
            state = current_state(self._conn, run_id)
            if state is None:
                start(self._conn, run_id, actor=self._actor)
                continue
            if state in TERMINAL or state in AWAITING_HUMAN:
                return state
            if state is RunState.RECEIVED:
                transition(
                    self._conn,
                    run_id,
                    RunState.INTERPRETING,
                    actor=self._actor,
                    expected_from=state,
                )
                continue
            handler = self._handlers.get(state)
            if handler is None:
                return state
            context = StepContext(self._conn, run, state, self._actor, self._clock)
            try:
                nxt = handler(context)
            except Exception as exc:  # noqa: BLE001 - every step failure is recorded
                attempt = self._failures_in_current_state(run_id) + 1
                append_event(
                    self._conn,
                    run_id,
                    event_type="step_failed",
                    actor=self._actor,
                    payload={
                        "state": state.value,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:MAX_ERROR_TEXT],
                        "traceback": traceback.format_exc()[-MAX_ERROR_TEXT:],
                    },
                )
                # Retrying cannot fix data that no longer matches its record.
                integrity = isinstance(exc, IntegrityViolation)
                if integrity or attempt >= self._max_attempts:
                    why = "integrity check failed" if integrity else f"failed {attempt} times"
                    transition(
                        self._conn,
                        run_id,
                        RunState.FAILED,
                        actor=self._actor,
                        expected_from=state,
                        payload={
                            "reason": f"{state.value} {why}: {type(exc).__name__}: {str(exc)[:500]}"
                        },
                    )
                    return RunState.FAILED
                self._sleep(self._retry_delay * 2 ** (attempt - 1))
                continue
            transition(
                self._conn,
                run_id,
                nxt.to,
                actor=self._actor,
                expected_from=state,
                payload=nxt.payload,
            )

    def _failures_in_current_state(self, run_id: UUID) -> int:
        row = self._conn.execute(
            """
            select count(*) from run_events
            where run_id = %s and event_type = 'step_failed'
              and seq > (select max(seq) from run_events
                         where run_id = %s and state is not null)
            """,
            (run_id, run_id),
        ).fetchone()
        return int(row[0]) if row else 0


def runnable_runs(conn: psycopg.Connection, *, limit: int = 20) -> list[UUID]:
    """Runs that are neither finished nor waiting for a person, oldest first."""
    idle = sorted(s.value for s in TERMINAL | AWAITING_HUMAN)
    rows = conn.execute(
        """
        with latest as (
            select distinct on (run_id) run_id, state
            from run_events where state is not null
            order by run_id, seq desc
        )
        select r.run_id from runs r left join latest l using (run_id)
        where l.state is null or l.state <> all(%s)
        order by r.created_at, r.run_id
        limit %s
        """,
        (idle, limit),
    ).fetchall()
    return [row[0] for row in rows]


class Worker:
    def __init__(self, conn: psycopg.Connection, orchestrator: Orchestrator, *, worker_id: str):
        self._conn = conn
        self._orchestrator = orchestrator
        self._worker_id = worker_id

    def run_once(self, *, limit: int = 20) -> dict[UUID, RunState]:
        """Advance every runnable run this worker can claim. Returns where each ended."""
        results: dict[UUID, RunState] = {}
        for run_id in runnable_runs(self._conn, limit=limit):
            if not self._try_claim(run_id):
                continue
            try:
                append_event(
                    self._conn,
                    run_id,
                    event_type="worker_claimed",
                    actor=self._worker_id,
                    payload={"worker_id": self._worker_id},
                )
                results[run_id] = self._orchestrator.advance(run_id)
            finally:
                self._release(run_id)
        return results

    def _try_claim(self, run_id: UUID) -> bool:
        row = self._conn.execute(
            "select pg_try_advisory_lock(hashtextextended(%s::text, 0))", (str(run_id),)
        ).fetchone()
        return bool(row and row[0])

    def _release(self, run_id: UUID) -> None:
        self._conn.execute(
            "select pg_advisory_unlock(hashtextextended(%s::text, 0))", (str(run_id),)
        )
