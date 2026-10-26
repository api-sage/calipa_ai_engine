"""The background worker: advances every run that needs work, one step at a time.

    uv run python -m calipa.worker [--once]

Run as many as needed; each run is advanced by one worker at a time (see
orchestrator.runner.Worker). The API only records requests and decisions, so a
worker must be running for runs to progress.
"""

from __future__ import annotations

import argparse
import logging
import socket
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager

import psycopg

from calipa.analyst.step import AnalyseStep
from calipa.audit_trail.anchor import AnchorSink, DirectorySink, anchor
from calipa.audit_trail.db import (
    MigrationError,
    connect,
    migrate,
    open_pool,
    require_current_schema,
)
from calipa.config import Settings, get_settings
from calipa.engine.standin import StandInEngine
from calipa.gateway.fcy import FcyClient
from calipa.gateway.http import ReadOnlyClient
from calipa.interpreter.policy import PolicyContext
from calipa.interpreter.step import InterpretStep
from calipa.llm import llm_factory
from calipa.llm.client import StructuredLLM
from calipa.llm.prompts import load_prompt
from calipa.orchestrator.lifecycle import RunState
from calipa.orchestrator.runner import Orchestrator, StepHandler, Worker
from calipa.orchestrator.steps import Steps
from calipa.reference import (
    load_account_catalog,
    load_control_catalog,
    load_entitlements,
    load_tariff,
)
from calipa.reporter.step import ReportStep

log = logging.getLogger("calipa.worker")


def build_handlers(
    *,
    fcy: FcyClient,
    llm: StructuredLLM,
    settings: Settings,
    lanes: Callable[[], AbstractContextManager[StructuredLLM]] | None = None,
) -> Mapping[RunState, StepHandler]:
    """Every step, with the versioned reference data and prompts it runs on. Without
    `lanes`, exception groups are analysed one at a time."""
    catalog, tariff, controls = load_account_catalog(), load_tariff(), load_control_catalog()
    policy = PolicyContext(
        entitlements=load_entitlements(),
        accounts=catalog,
        controls=controls,
        tariff=tariff,
        timezone=settings.bank_timezone,
    )
    steps = (
        InterpretStep(llm=llm, prompt=load_prompt("interpreter", 2), policy=policy),
        Steps(
            fcy=fcy,
            engine=StandInEngine(tariff),
            catalog=catalog,
            tariff=tariff,
            controls=controls,
        ),
        AnalyseStep(
            llm=llm,
            prompt=load_prompt("analyst", 3),
            tariff=tariff,
            catalog=catalog,
            controls=controls,
            parallelism=settings.analysis_parallelism if lanes else 1,
            lanes=lanes,
        ),
        ReportStep(llm=llm, prompt=load_prompt("report", 2), controls=controls),
    )
    handlers: dict[RunState, StepHandler] = {}
    for step in steps:
        overlap = handlers.keys() & step.handlers().keys()
        if overlap:
            raise ValueError(f"two steps handle {sorted(overlap)}")
        handlers.update(step.handlers())
    return handlers


class Anchoring:
    """Anchors the audit trail every `interval` seconds from the worker loop. A failed
    pass is logged and retried next time; runs carry on meanwhile, and the anchor
    covers everything that happened in between."""

    def __init__(
        self,
        conn: psycopg.Connection,
        sink: AnchorSink,
        *,
        interval: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._conn, self._sink, self._interval, self._clock = conn, sink, interval, clock
        self._last: float | None = None

    def maybe_run(self) -> list[int]:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return []
        self._last = now
        try:
            published = anchor(self._conn, self._sink)
        except Exception:
            log.exception("anchoring the audit trail failed; retrying in %ss", self._interval)
            return []
        if published:
            log.info("published audit-trail anchors %s to %s", published, self._sink.name)
        return published


def run(settings: Settings, *, once: bool) -> None:
    if settings.fcy_username is None or settings.fcy_password is None:
        raise SystemExit("set CALIPA_FCY_USERNAME and CALIPA_FCY_PASSWORD")
    worker_id = f"worker:{socket.gethostname()}"
    with (
        connect(settings.database_url) as conn,
        open_pool(settings.database_url, size=settings.analysis_parallelism) as pool,
    ):
        if settings.worker_auto_migrate:
            migrate(conn)
        require_current_schema(conn)

        llm_for = llm_factory(settings)

        @contextmanager
        def lane() -> Iterator[StructuredLLM]:
            with pool.connection() as lane_conn:
                yield llm_for(lane_conn)

        with ReadOnlyClient(
            conn,
            base_url=settings.fcy_base_url,
            username=settings.fcy_username,
            password=settings.fcy_password,
        ) as http:
            handlers = build_handlers(
                fcy=FcyClient(http),
                llm=llm_for(conn),
                settings=settings,
                lanes=lane if settings.analysis_parallelism > 1 else None,
            )
            worker = Worker(
                conn, Orchestrator(conn, handlers, actor=worker_id), worker_id=worker_id
            )
            anchoring = None
            if settings.anchor_dir is None:
                log.warning("CALIPA_ANCHOR_DIR is not set: the audit trail is not anchored")
            else:
                anchoring = Anchoring(
                    conn,
                    DirectorySink(settings.anchor_dir),
                    interval=settings.anchor_interval_seconds,
                )
            while True:
                for run_id, state in worker.run_once().items():
                    log.info("run %s is %s", run_id, state.value)
                if anchoring is not None:
                    anchoring.maybe_run()
                if once:
                    return
                time.sleep(settings.worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        run(get_settings(), once=args.once)
    except MigrationError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
