"""The HTTP API the UI calls.

Runs are background jobs: POST /runs records the request and returns at once; a
worker (calipa.worker) does the steps, and the UI follows them through the event
stream. Everything a person does (answer a question, review a report) goes through
calipa.review.service, which checks who may do it.

Who is calling comes from `identity` (see Settings.api_identity). A run a person may
not see answers 404, the same as a run that does not exist.
"""

# No `from __future__ import annotations` here: FastAPI reads the endpoint signatures,
# and the dependency aliases (Conn, User) are local to create_app.
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from psycopg_pool import ConnectionPool

from calipa.api.schemas import (
    ClarificationAnswer,
    CreateRun,
    EventsView,
    EventView,
    Me,
    PatternView,
    ReportSummary,
    ReportView,
    ReviewRequest,
    ReviewView,
    RunListItem,
    RunView,
    SectionView,
    VersionView,
)
from calipa.audit_trail.artifacts import require_artifact
from calipa.audit_trail.db import open_pool
from calipa.audit_trail.store import (
    Run,
    RunEvent,
    create_run,
    get_run,
    list_events,
    now_utc,
    verify_chain,
)
from calipa.config import Settings, get_settings
from calipa.metrics import collect as collect_metrics
from calipa.metrics import render as render_metrics
from calipa.orchestrator.lifecycle import (
    AWAITING_HUMAN,
    TERMINAL,
    RunState,
    TransitionNotAllowed,
    current_state,
    start,
)
from calipa.reference import Entitlements, load_entitlements
from calipa.reporter.step import REPORT, ReportArtifact, markdown_of
from calipa.review.service import (
    Conflict,
    Forbidden,
    Invalid,
    answer_clarification,
    can_view,
    review,
    scope_authors,
)
from calipa.review.store import Review, get_review, report_version, versions

USER_HEADER = "X-Calipa-User"


@dataclass(frozen=True, slots=True)
class ApiDeps:
    # An autocommit connection for one unit of work, returned (or closed) on exit.
    connect: Callable[[], AbstractContextManager[psycopg.Connection]]
    entitlements: Entitlements
    identity: Literal["none", "dev_header"]
    cors_origins: list[str]
    stream_poll_seconds: float = 1.0
    stream_max_seconds: float = 600.0  # a client reconnects with Last-Event-ID
    clock: Callable[[], datetime] = now_utc
    pool: ConnectionPool | None = None  # opened and closed with the app
    stall_after: timedelta = timedelta(minutes=15)  # for calipa_runs_stalled

    @classmethod
    def from_settings(cls, settings: Settings) -> "ApiDeps":
        pool = open_pool(settings.database_url, size=settings.api_pool_size, open_now=False)
        return cls(
            connect=pool.connection,
            entitlements=load_entitlements(),
            identity=settings.api_identity,
            cors_origins=settings.api_cors_origins,
            stream_poll_seconds=settings.api_stream_poll_seconds,
            pool=pool,
            stall_after=timedelta(seconds=settings.stall_after_seconds),
        )


def app_from_settings() -> FastAPI:
    """For `uvicorn --factory calipa.api.app:app_from_settings`."""
    return create_app(ApiDeps.from_settings(get_settings()))


def create_app(deps: ApiDeps) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if deps.pool is not None:
            deps.pool.open(wait=True)
        try:
            yield
        finally:
            if deps.pool is not None:
                deps.pool.close()

    app = FastAPI(
        title="Calipa Intelligence Layer",
        version="0.1.0",
        description="Read-only GL audit runs: request, progress, report and review.",
        lifespan=lifespan,
    )
    if deps.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=deps.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", USER_HEADER, "Last-Event-ID"],
        )

    for error, status in ((Forbidden, 403), (Conflict, 409), (Invalid, 422)):
        app.add_exception_handler(error, _handler(status))

    def get_conn() -> Iterator[psycopg.Connection]:
        with deps.connect() as conn:
            yield conn

    def current_user(
        x_calipa_user: Annotated[str | None, Header(alias=USER_HEADER)] = None,
    ) -> str:
        if deps.identity == "none":
            raise HTTPException(401, "sign-in is not configured for this deployment")
        if not x_calipa_user:
            raise HTTPException(401, f"missing {USER_HEADER}")
        if x_calipa_user not in deps.entitlements.users:
            raise HTTPException(403, "unknown user")
        return x_calipa_user

    Conn = Annotated[psycopg.Connection, Depends(get_conn)]
    User = Annotated[str, Depends(current_user)]

    def visible_run(conn: psycopg.Connection, run_id: UUID, user: str) -> Run:
        try:
            run = get_run(conn, run_id)
        except KeyError:
            raise HTTPException(404, "run not found") from None
        if not can_view(conn, run, user, deps.entitlements):
            raise HTTPException(404, "run not found")
        return run

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    def metrics(conn: Conn) -> PlainTextResponse:
        """Prometheus metrics. Aggregates only (no ids, users or text), so no sign-in;
        restrict it to the monitoring network at the load balancer all the same."""
        body = render_metrics(collect_metrics(conn, now=deps.clock(), stall_after=deps.stall_after))
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    @app.get("/me")
    def me(user: User) -> Me:
        grant = deps.entitlements.grant_for(user)
        return Me(user=user, can_initiate=grant.can_initiate, can_review=grant.can_review)

    @app.post("/runs", status_code=202)
    def create(body: CreateRun, conn: Conn, user: User) -> RunView:
        if not deps.entitlements.grant_for(user).can_initiate:
            raise Forbidden(f"{user} is not entitled to start audits")
        try:
            run = create_run(
                conn,
                idempotency_key=f"api:{user}:{body.idempotency_key}",
                initiated_by=user,
                request_text=body.request_text,
            )
        except ValueError as exc:
            raise Conflict(str(exc)) from None
        if run.created:
            try:
                start(conn, run.run_id, actor=user)
            except TransitionNotAllowed:
                pass  # a concurrent identical request started it
        return _run_view(conn, run, user, deps.entitlements)

    @app.get("/runs")
    def list_runs(
        conn: Conn,
        user: User,
        view: Literal["mine", "review_queue"] = "mine",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[RunListItem]:
        if view == "mine":
            rows = conn.execute(
                """
                select run_id from runs where initiated_by = %s
                order by created_at desc, run_id limit %s
                """,
                (user, limit),
            ).fetchall()
            runs = [get_run(conn, r[0]) for r in rows]
        else:
            rows = conn.execute(
                """
                with latest as (
                    select distinct on (run_id) run_id, state from run_events
                    where state is not null order by run_id, seq desc
                )
                select r.run_id from runs r join latest l using (run_id)
                where l.state = %s order by r.created_at, r.run_id
                """,
                (RunState.PENDING_REVIEW.value,),
            ).fetchall()
            runs = [
                run
                for run in (get_run(conn, r[0]) for r in rows)
                if user not in scope_authors(conn, run)
                and can_view(conn, run, user, deps.entitlements)
            ][:limit]
        return [
            RunListItem(
                run_id=r.run_id,
                state=current_state(conn, r.run_id),
                initiated_by=r.initiated_by,
                request_text=r.request_text,
                created_at=r.created_at,
                parent_run_id=r.parent_run_id,
            )
            for r in runs
        ]

    @app.get("/runs/{run_id}")
    def get(run_id: UUID, conn: Conn, user: User) -> RunView:
        return _run_view(conn, visible_run(conn, run_id, user), user, deps.entitlements)

    @app.get("/runs/{run_id}/events")
    def events(run_id: UUID, conn: Conn, user: User) -> EventsView:
        visible_run(conn, run_id, user)
        return EventsView(
            chain_ok=verify_chain(conn, run_id).ok,
            events=[_event(e) for e in list_events(conn, run_id)],
        )

    @app.get(
        "/runs/{run_id}/events/stream",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    def stream(
        run_id: UUID,
        conn: Conn,
        user: User,
        last_event_id: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0,
    ) -> StreamingResponse:
        """Server-sent events: each run event once, in order, as `id: <seq>`. The
        stream ends when the run finishes or waits for a person; reconnect with
        Last-Event-ID to continue."""
        visible_run(conn, run_id, user)
        return StreamingResponse(
            _sse(deps, run_id, after=last_event_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/runs/{run_id}/clarification")
    def clarify(run_id: UUID, body: ClarificationAnswer, conn: Conn, user: User) -> RunView:
        run = visible_run(conn, run_id, user)
        answer_clarification(conn, run_id, user=user, answer=body.answer)
        return _run_view(conn, run, user, deps.entitlements)

    @app.get("/runs/{run_id}/report")
    def report(run_id: UUID, conn: Conn, user: User) -> ReportView:
        visible_run(conn, run_id, user)
        summary, artifact = _report(conn, run_id)
        n = artifact.narrative
        return ReportView(
            summary=summary,
            executive_summary=SectionView(
                ref=n.executive_summary.ref,
                source=n.executive_summary.source,
                text=n.executive_summary.text,
            ),
            findings=[SectionView(ref=s.ref, source=s.source, text=s.text) for s in n.findings],
            patterns=[PatternView(finding_keys=p.finding_keys, text=p.text) for p in n.patterns],
            markdown=markdown_of(conn, artifact),
        )

    @app.get(
        "/runs/{run_id}/report.md",
        response_class=Response,
        responses={200: {"content": {"text/markdown": {}}}},
    )
    def report_markdown(run_id: UUID, conn: Conn, user: User) -> Response:
        visible_run(conn, run_id, user)
        summary, artifact = _report(conn, run_id)
        return Response(
            markdown_of(conn, artifact),
            media_type="text/markdown; charset=utf-8",
            headers={
                "X-Report-SHA256": summary.report_sha256,
                "Content-Disposition": (
                    f'attachment; filename="calipa-{run_id}-v{summary.version}.md"'
                ),
            },
        )

    @app.post("/runs/{run_id}/review")
    def decide(run_id: UUID, body: ReviewRequest, conn: Conn, user: User) -> RunView:
        run = visible_run(conn, run_id, user)
        review(
            conn,
            run_id,
            reviewer=user,
            decision=body.decision,
            note=body.note,
            report_sha256=body.report_sha256,
            acknowledge_flags=body.acknowledge_flags,
            entitlements=deps.entitlements,
            now=deps.clock(),
        )
        return _run_view(conn, run, user, deps.entitlements)

    return app


def _handler(status: int) -> Callable[[Request, Exception], Response]:
    def handle(request: Request, exc: Exception) -> Response:
        return JSONResponse({"detail": str(exc)}, status_code=status)

    return handle


def _report(conn: psycopg.Connection, run_id: UUID) -> tuple[ReportSummary, ReportArtifact]:
    version = report_version(conn, run_id)
    if version is None:
        raise HTTPException(404, "this run has no report yet")
    _, artifact = require_artifact(conn, run_id, REPORT, ReportArtifact)
    if artifact.report_sha256 != version.report_sha256:
        raise RuntimeError("stored report does not match its recorded version")
    return (
        ReportSummary(
            version=version.version,
            report_sha256=version.report_sha256,
            markdown_sha256=version.markdown_sha256,
            narrative_source=artifact.narrative_source,
            flags=artifact.flags,
            created_at=version.created_at,
        ),
        artifact,
    )


def _run_view(conn: psycopg.Connection, run: Run, user: str, entitlements: Entitlements) -> RunView:
    state = current_state(conn, run.run_id)
    events = list_events(conn, run.run_id)
    last = next((e for e in reversed(events) if e.state is not None), None)
    question = (
        str(last.payload.get("question", ""))
        if last is not None and state is RunState.CLARIFICATION_REQUESTED
        else None
    )
    reason = (
        str(last.payload.get("reason", ""))
        if last is not None and state in TERMINAL and state is not RunState.APPROVED
        else None
    )
    summary = _report(conn, run.run_id)[0] if report_version(conn, run.run_id) else None
    decision = get_review(conn, run.run_id)
    authors = scope_authors(conn, run)
    grant = entitlements.grant_for(user)
    return RunView(
        run_id=run.run_id,
        state=state,
        initiated_by=run.initiated_by,
        request_text=run.request_text,
        created_at=run.created_at,
        parent_run_id=run.parent_run_id,
        created=run.created,
        question=question,
        reason=reason or None,
        report=summary,
        review=_review(decision) if decision else None,
        versions=[
            VersionView(
                run_id=v.run_id,
                version=v.version,
                report_sha256=v.report_sha256,
                state=current_state(conn, v.run_id) or RunState.RECEIVED,
            )
            for v in versions(conn, run.run_id)
        ],
        can_answer=state is RunState.CLARIFICATION_REQUESTED and user == run.initiated_by,
        can_review=(state is RunState.PENDING_REVIEW and grant.can_review and user not in authors),
    )


def _review(r: Review) -> ReviewView:
    return ReviewView(
        review_id=r.review_id,
        decision=r.decision,
        reviewer=r.reviewer,
        note=r.note,
        report_sha256=r.report_sha256,
        flags_acknowledged=r.flags_acknowledged,
        child_run_id=r.child_run_id,
        created_at=r.created_at,
    )


def _event(e: RunEvent) -> EventView:
    return EventView(
        seq=e.seq,
        event_type=e.event_type,
        state=e.state,
        actor=e.actor,
        created_at=e.created_at,
        payload=e.payload,
    )


def _sse(deps: ApiDeps, run_id: UUID, *, after: int) -> Iterator[str]:
    """Poll the run's events and send each new one. Each poll borrows a connection
    and returns it before waiting, so an open stream does not hold one for minutes."""
    deadline = time.monotonic() + deps.stream_max_seconds
    seen = after
    while time.monotonic() < deadline:
        with deps.connect() as conn:
            new = [e for e in list_events(conn, run_id) if e.seq > seen]
            state = current_state(conn, run_id)
        for e in new:
            data = _event(e).model_dump_json()
            yield f"id: {e.seq}\nevent: {e.event_type}\ndata: {data}\n\n"
            seen = e.seq
        if state in TERMINAL or state in AWAITING_HUMAN:
            yield f"event: end\ndata: {json.dumps({'state': state.value})}\n\n"
            return
        if not new:
            yield ": keep-alive\n\n"
        time.sleep(deps.stream_poll_seconds)
