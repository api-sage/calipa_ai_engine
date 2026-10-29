"""Operational metrics, read from the audit trail itself, in Prometheus text format.

Aggregates only: counts, ages and costs, never run ids, users, accounts or text, so
the endpoint that serves them can be scraped without signing in. Alert rules on these
are in ops/alerts.yml.

A step runs inside the worker and is bounded by its clients' timeouts (model calls,
the bank API), not by a separate clock. What can still go wrong (a worker that hangs
or died holding nothing) shows up here as a stalled run: one that needs work and has
had no event for longer than `stall_after`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg

from calipa.orchestrator.lifecycle import AWAITING_HUMAN, TERMINAL, RunState


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    help: str
    value: float
    labels: tuple[tuple[str, str], ...] = ()


def collect(conn: psycopg.Connection, *, now: datetime, stall_after: timedelta) -> list[Metric]:
    day_ago = now - timedelta(days=1)
    out: list[Metric] = []

    latest = conn.execute(
        """
        with latest as (
            select distinct on (run_id) run_id, state
            from run_events where state is not null
            order by run_id, seq desc
        ), last_event as (
            select run_id, max(created_at) as at from run_events group by run_id
        )
        select coalesce(l.state, 'RECEIVED'), count(*),
               count(*) filter (where coalesce(e.at, r.created_at) < %s)
        from runs r left join latest l using (run_id) left join last_event e using (run_id)
        group by 1
        """,
        (now - stall_after,),
    ).fetchall()
    idle = {s.value for s in TERMINAL | AWAITING_HUMAN}
    by_state = {state: (int(n), int(old)) for state, n, old in latest}
    for state in RunState:
        out.append(
            Metric(
                "calipa_runs",
                "Runs by current state.",
                by_state.get(state.value, (0, 0))[0],
                (("state", state.value),),
            )
        )
    out.append(
        Metric(
            "calipa_runs_stalled",
            f"Runs needing work with no event for {int(stall_after.total_seconds())}s.",
            sum(old for state, (_, old) in by_state.items() if state not in idle),
        )
    )

    failed = _one(
        conn,
        "select count(*) from run_events where state = 'FAILED' and created_at >= %s",
        day_ago,
    )
    out.append(Metric("calipa_runs_failed_24h", "Runs that failed in the last 24h.", failed))
    step_failures = _one(
        conn,
        "select count(*) from run_events where event_type = 'step_failed' and created_at >= %s",
        day_ago,
    )
    out.append(
        Metric(
            "calipa_step_failures_24h", "Step attempts that failed in the last 24h.", step_failures
        )
    )

    calls = conn.execute(
        """
        select coalesce(r.outcome, 'unfinished'), count(*),
               coalesce(sum(r.cost_usd), 0),
               count(*) filter (where r.outcome is not null and r.cost_usd is null)
        from llm_calls c left join llm_call_results r using (call_id)
        where c.started_at >= %s
        group by 1
        """,
        (day_ago,),
    ).fetchall()
    for outcome, n, _, _ in calls:
        out.append(
            Metric(
                "calipa_llm_calls_24h",
                "Model calls in the last 24h by outcome.",
                int(n),
                (("outcome", outcome),),
            )
        )
    out.append(
        Metric(
            "calipa_llm_cost_usd_24h",
            "Known model cost in USD in the last 24h (calls without a price add nothing).",
            float(sum((Decimal(c) for _, _, c, _ in calls), Decimal(0))),
        )
    )
    out.append(
        Metric(
            "calipa_llm_calls_unpriced_24h",
            "Model calls in the last 24h whose cost is unknown.",
            sum(int(u) for *_, u in calls),
        )
    )
    out.append(
        Metric(
            "calipa_llm_calls_unfinished",
            "Model calls started over an hour ago with no result (the process died mid-call).",
            _one(
                conn,
                """
                select count(*) from llm_calls c
                where c.started_at < %s
                  and not exists (select 1 from llm_call_results r where r.call_id = c.call_id)
                """,
                now - timedelta(hours=1),
            ),
        )
    )

    bank = conn.execute(
        """
        select count(*), count(*) filter (where error is not null or status_code >= 400)
        from api_calls where started_at >= %s
        """,
        (day_ago,),
    ).fetchone()
    assert bank is not None
    out.append(Metric("calipa_bank_calls_24h", "Bank API calls in the last 24h.", int(bank[0])))
    out.append(
        Metric(
            "calipa_bank_call_errors_24h",
            "Bank API calls in the last 24h that failed or returned an error status.",
            int(bank[1]),
        )
    )

    anchors = conn.execute(
        """
        select count(*), max(created_at),
               count(*) filter (where not exists
                   (select 1 from anchor_receipts r where r.anchor_seq = a.seq))
        from chain_anchors a
        """
    ).fetchone()
    assert anchors is not None
    out.append(Metric("calipa_anchors", "Audit-trail anchors recorded.", int(anchors[0])))
    out.append(
        Metric(
            "calipa_anchors_unpublished",
            "Anchors recorded but not yet in the write-once store.",
            int(anchors[2]),
        )
    )
    unanchored = conn.execute(
        """
        select min(e.created_at) from run_events e
        where e.created_at > coalesce((select max(created_at) from chain_anchors),
                                      '-infinity'::timestamptz)
        """
    ).fetchone()
    oldest = unanchored[0] if unanchored else None
    out.append(
        Metric(
            "calipa_unanchored_seconds",
            "Age of the oldest event not yet covered by an anchor (0: all covered).",
            (now - oldest).total_seconds() if oldest else 0.0,
        )
    )
    return out


def _one(conn: psycopg.Connection, sql: str, *params: object) -> int:
    row = conn.execute(sql.encode(), params).fetchone()  # constant SQL from this module
    return int(row[0]) if row else 0


def render(metrics: list[Metric]) -> str:
    lines: list[str] = []
    described: set[str] = set()
    for m in metrics:
        if m.name not in described:
            described.add(m.name)
            lines += [f"# HELP {m.name} {m.help}", f"# TYPE {m.name} gauge"]
        labels = ",".join(f'{k}="{_escape(v)}"' for k, v in m.labels)
        lines.append(
            f"{m.name}{{{labels}}} {_number(m.value)}" if labels else f"{m.name} {_number(m.value)}"
        )
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
