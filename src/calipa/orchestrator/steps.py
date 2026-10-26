"""Step handlers for extraction and reconciliation.

INTERPRETING (P1 + policy, in calipa.interpreter) writes the `approved_scope` artifact
these steps start from; tests may also write it directly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from calipa.audit_trail.artifacts import put_artifact, require_artifact
from calipa.engine.contract import (
    EngineException,
    ExceptionType,
    RateQuote,
    ReconciliationEngine,
    ReconciliationRequest,
    ReconciliationResult,
)
from calipa.gateway.fcy import FcyClient, GLEntry
from calipa.gateway.rates import rates_between
from calipa.gateway.snapshot import extract_gl_snapshot
from calipa.money import Currency
from calipa.orchestrator.lifecycle import RunState
from calipa.orchestrator.runner import Next, StepContext, StepHandler
from calipa.reference import (
    AccountCatalog,
    AccountRole,
    ControlCatalog,
    Tariff,
    load_control_catalog,
)

APPROVED_SCOPE = "approved_scope"
GL_SNAPSHOT = "gl_snapshot"
ENGINE_REQUEST = "engine_request"
ENGINE_RESULT = "engine_result"


class _Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApprovedScope(_Artifact):
    """What the run is allowed to audit, after interpretation and the policy check."""

    objective: str
    period_start: date
    period_end: date
    timezone: str
    currencies: list[Currency] = []  # empty: all
    transaction_references: list[str] = []  # empty: all
    account_roles: list[AccountRole] = []
    control_ids: list[str] = []
    period_rule: str = ""  # how the dates were worked out
    assumptions: list[str] = []  # for the auditor to confirm in the report
    account_catalog_sha256: str
    tariff_sha256: str
    control_catalog_sha256: str = ""
    entitlements_sha256: str = ""
    approved_by: str


class PageInfo(_Artifact):
    day: date
    attempt: int
    page: int
    total: int
    blob_sha256: str


class SnapshotArtifact(_Artifact):
    snapshot_sha256: str
    period_start: date
    period_end: date
    timezone: str
    status: Literal["complete", "partial"]
    limitations: list[str]
    pages: list[PageInfo]
    entries_in_period: int
    entries: list[GLEntry]  # after scope filters, whole transfers kept together


class Steps:
    def __init__(
        self,
        *,
        fcy: FcyClient,
        engine: ReconciliationEngine,
        catalog: AccountCatalog,
        tariff: Tariff,
        controls: ControlCatalog | None = None,
        settle_lag: timedelta = timedelta(hours=6),
    ) -> None:
        self._fcy = fcy
        self._engine = engine
        self._catalog = catalog
        self._tariff = tariff
        self._controls = controls or load_control_catalog()
        self._settle_lag = settle_lag

    def handlers(self) -> dict[RunState, StepHandler]:
        return {
            RunState.EXTRACTING: self.extract,
            RunState.EXTRACTION_PARTIAL: self.continue_partial,
            RunState.RECONCILING: self.reconcile,
        }

    def extract(self, ctx: StepContext) -> Next:
        _, scope = require_artifact(ctx.conn, ctx.run_id, APPROVED_SCOPE, ApprovedScope)
        if (scope.account_catalog_sha256, scope.tariff_sha256) != (
            self._catalog.sha256,
            self._tariff.sha256,
        ):
            raise RuntimeError("reference data changed since the scope was approved")
        snap = extract_gl_snapshot(
            self._fcy,
            period_start=scope.period_start,
            period_end=scope.period_end,
            tz=ZoneInfo(scope.timezone),
            now=ctx.now(),
            settle_lag=self._settle_lag,
            run_id=ctx.run_id,
            record=ctx.record,
        )
        entries = _apply_scope(list(snap.entries), scope)
        artifact = SnapshotArtifact(
            snapshot_sha256=snap.sha256,
            period_start=snap.period_start,
            period_end=snap.period_end,
            timezone=snap.timezone,
            status=snap.status,
            limitations=list(snap.limitations),
            pages=[
                PageInfo(
                    day=p.day,
                    attempt=p.attempt,
                    page=p.page,
                    total=p.total,
                    blob_sha256=p.blob_sha256,
                )
                for p in snap.pages
            ],
            entries_in_period=len(snap.entries),
            entries=entries,
        )
        ref = put_artifact(ctx.conn, ctx.run_id, GL_SNAPSHOT, artifact, created_by=ctx.actor)
        payload = {
            "artifact_id": ref.artifact_id,
            "snapshot_sha256": snap.sha256,
            "entries_in_scope": len(entries),
        }
        if snap.status == "partial":
            return Next(
                RunState.EXTRACTION_PARTIAL, {**payload, "limitations": artifact.limitations}
            )
        return Next(RunState.RECONCILING, payload)

    def continue_partial(self, ctx: StepContext) -> Next:
        """Partial data is reconciled with its limitations carried into the report."""
        return Next(RunState.RECONCILING, {"continued_with_limitations": True})

    def reconcile(self, ctx: StepContext) -> Next:
        _, snap = require_artifact(ctx.conn, ctx.run_id, GL_SNAPSHOT, SnapshotArtifact)
        stored = rates_between(
            ctx.conn,
            snap.period_start - timedelta(days=1),
            snap.period_end + timedelta(days=1),
        )
        request = ReconciliationRequest(
            # Same run and snapshot, same request id: a resumed step is recognisable.
            request_id=uuid5(NAMESPACE_URL, f"calipa:{ctx.run_id}:{snap.snapshot_sha256}"),
            snapshot_sha256=snap.snapshot_sha256,
            bank_timezone=snap.timezone,
            entries=snap.entries,
            account_roles={n: r.value for n, r in self._catalog.roles.items()},
            account_catalog_sha256=self._catalog.sha256,
            tariff_sha256=self._tariff.sha256,
            rates=[
                RateQuote(
                    from_currency=r.from_currency,
                    to_currency=r.to_currency,
                    rate_date=r.rate_date,
                    rate=r.rate,
                    source=f"fx_rate_snapshot:{r.snapshot_id}:{r.source_rate_id}",
                )
                for r in stored
            ],
        )
        put_artifact(ctx.conn, ctx.run_id, ENGINE_REQUEST, request, created_by=ctx.actor)
        result = self._engine.reconcile(request)
        if (result.request_id, result.snapshot_sha256) != (
            request.request_id,
            request.snapshot_sha256,
        ):
            raise RuntimeError("engine answered a different request")
        ref = put_artifact(ctx.conn, ctx.run_id, ENGINE_RESULT, result, created_by=ctx.actor)
        _, scope = require_artifact(ctx.conn, ctx.run_id, APPROVED_SCOPE, ApprovedScope)
        in_scope = in_scope_exceptions(result.exceptions, scope, self._controls)
        payload = {
            "artifact_id": ref.artifact_id,
            "engine": f"{result.engine_name} {result.engine_version} / {result.ruleset_version}",
            "transfers_checked": result.transfers_checked,
            "exceptions": len(result.exceptions),
            "exceptions_in_scope": len(in_scope),
        }
        if in_scope:
            return Next(RunState.ANALYSING, payload)
        return Next(RunState.NO_EXCEPTIONS, payload)


def _apply_scope(entries: list[GLEntry], scope: ApprovedScope) -> list[GLEntry]:
    """Keep whole transfers: a transfer is in scope if any of its legs matches."""
    if not scope.currencies and not scope.transaction_references:
        return entries
    references = set(scope.transaction_references)
    currencies = set(scope.currencies)
    matching = {
        e.transfer_id
        for e in entries
        if (not references or e.external_reference in references)
        and (not currencies or e.currency in currencies)
    }
    return [e for e in entries if e.transfer_id in matching]


def in_scope_exceptions(
    exceptions: list[EngineException], scope: ApprovedScope, controls: ControlCatalog
) -> list[EngineException]:
    """The exceptions the approved controls cover. The engine runs every rule; only
    what the auditor's objective asked about is analysed and reported. No control ids
    means every control (scopes written before step 5 have none)."""
    if not scope.control_ids:
        return list(exceptions)
    if scope.control_catalog_sha256 and scope.control_catalog_sha256 != controls.sha256:
        raise RuntimeError("control catalog changed since the scope was approved")
    types = {
        ExceptionType(t) for c in scope.control_ids for t in controls.controls[c].exception_types
    }
    return [e for e in exceptions if e.type in types]


def load_engine_result(ctx: StepContext) -> ReconciliationResult:
    return require_artifact(ctx.conn, ctx.run_id, ENGINE_RESULT, ReconciliationResult)[1]
