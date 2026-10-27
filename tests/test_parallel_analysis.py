"""P2 on several groups at once: each group on its own lane (connection and client),
the run's events and findings written by the step's thread only, in a valid chain."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import anthropic
import httpx2
import psycopg
import pytest

from calipa.analyst.step import ANALYSIS, FINDING, AnalyseStep, AnalysisSummary, Finding
from calipa.audit_trail.artifacts import list_artifacts, require_artifact
from calipa.audit_trail.db import connect
from calipa.audit_trail.store import list_events, verify_chain
from calipa.llm.client import ClaudeClient, StructuredLLM
from calipa.llm.prompts import active_prompt
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.runner import Orchestrator
from tests.analyst_helpers import groups, unexplained_answer
from tests.interpreter_helpers import NOW
from tests.test_analyse_step import CATALOG, CONTROLS, TARIFF, _two_groups, analysing_run, findings
from tests.test_llm_client import FakeRaw, message

_GROUP = re.compile(r'"group_id": "(G-[0-9a-f]+)"')


class ByGroup:
    """Answers by the group named in the request. Every call waits at a barrier until
    `together` calls are in flight, so a test fails unless they really overlap."""

    def __init__(self, replies: dict[str, dict[str, Any] | Exception], *, together: int) -> None:
        self.replies = replies
        self.barrier = threading.Barrier(together, timeout=10)
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.messages = self
        self.with_raw_response = self

    def create(self, **request: Any) -> FakeRaw:
        with self.lock:
            self.requests.append(request)
        self.barrier.wait()
        match = _GROUP.search(request["messages"][0]["content"])
        assert match is not None
        reply = self.replies[match.group(1)]
        if isinstance(reply, Exception):
            raise reply
        return FakeRaw(message(text=json.dumps(reply)))


def advance(url: str, conn: psycopg.Connection, fake: ByGroup, run_id: UUID, **kw: Any) -> S:
    lanes_opened: list[int] = []

    @contextmanager
    def lane() -> Iterator[StructuredLLM]:
        with connect(url) as lane_conn:
            lanes_opened.append(1)
            yield ClaudeClient(lane_conn, fake, model="claude-opus-5-5")  # type: ignore[arg-type]

    step = AnalyseStep(
        llm=ClaudeClient(conn, fake, model="claude-opus-5-5"),  # type: ignore[arg-type]
        prompt=active_prompt("analyst"),
        tariff=TARIFF,
        catalog=CATALOG,
        controls=CONTROLS,
        parallelism=4,
        lanes=lane,
    )
    orchestrator = Orchestrator(
        conn,
        step.handlers(),
        clock=lambda: NOW,
        sleep=lambda _: None,
        max_attempts=kw.get("max_attempts", 3),
    )
    state = orchestrator.advance(run_id)
    assert lanes_opened  # the step's own client was not used
    return state


def test_groups_are_analysed_at_the_same_time(migrated_url: str, conn: psycopg.Connection) -> None:
    entries, rates = _two_groups()
    first, second = groups(entries, rates=rates)
    fake = ByGroup(
        {
            first.bundle.group_id: unexplained_answer(first.bundle),
            second.bundle.group_id: unexplained_answer(second.bundle),
        },
        together=2,
    )
    run_id = analysing_run(conn, entries, rates=rates)
    assert advance(migrated_url, conn, fake, run_id) is S.REPORTING

    assert {f.status for f in findings(conn, run_id)} == {"analysed"}
    _, summary = require_artifact(conn, run_id, ANALYSIS, AnalysisSummary)
    group_of = {
        ref.artifact_id: f.group_id for ref, f in list_artifacts(conn, run_id, FINDING, Finding)
    }
    # The summary lists findings most severe first, whichever finished first.
    assert [group_of[a] for a in summary.finding_artifact_ids] == [
        first.bundle.group_id,
        second.bundle.group_id,
    ]
    events = list_events(conn, run_id)
    assert sum(e.event_type == "analysis_attempt" for e in events) == 2
    assert verify_chain(conn, run_id).ok


def test_a_failing_group_does_not_lose_the_others(
    migrated_url: str, conn: psycopg.Connection
) -> None:
    entries, rates = _two_groups()
    first, second = groups(entries, rates=rates)
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake = ByGroup(
        {first.bundle.group_id: unexplained_answer(first.bundle), second.bundle.group_id: error},
        together=2,
    )
    run_id = analysing_run(conn, entries, rates=rates)
    assert advance(migrated_url, conn, fake, run_id, max_attempts=1) is S.FAILED
    # The group that finished was saved, so a retry would not pay for it again.
    assert [f.group_id for f in findings(conn, run_id)] == [first.bundle.group_id]
    assert verify_chain(conn, run_id).ok


def test_parallel_analysis_needs_lanes() -> None:
    with pytest.raises(ValueError, match="needs lanes"):
        AnalyseStep(
            llm=None,  # type: ignore[arg-type]
            prompt=active_prompt("analyst"),
            tariff=TARIFF,
            catalog=CATALOG,
            controls=CONTROLS,
            parallelism=2,
        )
