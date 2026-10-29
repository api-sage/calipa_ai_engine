"""The policy step: code decides whether an interpretation becomes an approved scope.

Claude takes no part here. The decision uses the initiator's entitlements, the
resolved dates, the closed-period rule and the size limits. Anything that could be
fixed by the auditor becomes a question; anything they may not do is denied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calipa.gateway.snapshot import MAX_PERIOD_DAYS, PeriodNotClosed, assert_period_closed
from calipa.interpreter.dates import PeriodUnresolved, resolve_period
from calipa.interpreter.schema import InterpretedScope
from calipa.orchestrator.steps import ApprovedScope
from calipa.reference import AccountCatalog, ControlCatalog, Entitlements, Tariff

APPROVER = "policy"


@dataclass(frozen=True, slots=True)
class Approve:
    scope: ApprovedScope


@dataclass(frozen=True, slots=True)
class Clarify:
    question: str
    reason: str


@dataclass(frozen=True, slots=True)
class Deny:
    reason: str


@dataclass(frozen=True, slots=True)
class Unsupported:
    reason: str


Decision = Approve | Clarify | Deny | Unsupported


@dataclass(frozen=True, slots=True)
class PolicyContext:
    entitlements: Entitlements
    accounts: AccountCatalog
    controls: ControlCatalog
    tariff: Tariff
    timezone: str
    settle_lag: timedelta = timedelta(hours=6)
    fiscal_year_start_month: int | None = None  # unknown until Finance confirms


def decide(
    scope: InterpretedScope, *, initiator: str, now: datetime, ctx: PolicyContext
) -> Decision:
    grant = ctx.entitlements.grant_for(initiator)
    if not grant.can_initiate:
        return Deny(f"{initiator} is not entitled to start audits")

    if scope.status == "out_of_scope":
        return Deny(scope.status_reason or "the request is outside what Calipa audits")
    if scope.status == "unsupported":
        return Unsupported(scope.status_reason or "Calipa does not have the data for this")
    if scope.status == "needs_clarification":
        assert scope.clarifying_question  # V1 guarantees it
        return Clarify(scope.clarifying_question, "interpretation incomplete")
    if scope.unsupported_constraints:
        listed = ", ".join(scope.unsupported_constraints)
        return Clarify(
            f"Calipa cannot filter by: {listed}. Should the audit run without those filters?",
            "unsupported constraints",
        )

    assert scope.objective is not None and scope.period is not None  # V1 guarantees both
    if scope.objective not in grant.objectives:
        return Deny(f"{initiator} is not entitled to the objective {scope.objective.value}")
    roles = grant.account_roles if scope.all_entitled_accounts else frozenset(scope.account_ids)
    outside = sorted(roles - grant.account_roles)
    if outside:
        return Deny(f"{initiator} is not entitled to the accounts {', '.join(outside)}")
    if not roles:
        return Deny(f"{initiator} is not entitled to any accounts")

    tz = ZoneInfo(ctx.timezone)
    today = now.astimezone(tz).date()
    try:
        period = resolve_period(
            scope.period, today=today, fiscal_year_start_month=ctx.fiscal_year_start_month
        )
    except PeriodUnresolved as exc:
        return Clarify(str(exc), "period unresolved")
    try:
        assert_period_closed(period.end, tz=tz, now=now, settle_lag=ctx.settle_lag)
    except PeriodNotClosed:
        last_closed = (now - ctx.settle_lag).astimezone(tz).date() - timedelta(days=1)
        return Clarify(
            f"The period {period.start.isoformat()} to {period.end.isoformat()} has not "
            f"closed yet. Should the audit stop at {last_closed.isoformat()}, the last "
            "closed day?",
            "period not closed",
        )
    limit = min(MAX_PERIOD_DAYS, grant.max_period_days)
    if period.days > limit:
        return Clarify(
            f"The period {period.start.isoformat()} to {period.end.isoformat()} is "
            f"{period.days} days; you can audit at most {limit} days per run. Which part "
            "should this run cover?",
            "period too long",
        )

    controls = scope.control_ids or list(ctx.controls.objectives[scope.objective])
    return Approve(
        ApprovedScope(
            objective=scope.objective.value,
            period_start=period.start,
            period_end=period.end,
            timezone=ctx.timezone,
            currencies=sorted(scope.currencies),
            transaction_references=sorted(scope.transaction_references),
            account_roles=sorted(roles),
            control_ids=controls,
            period_rule=period.rule,
            assumptions=[f"{a.field}: {a.text}" for a in scope.assumptions],
            account_catalog_sha256=ctx.accounts.sha256,
            tariff_sha256=ctx.tariff.sha256,
            control_catalog_sha256=ctx.controls.sha256,
            entitlements_sha256=ctx.entitlements.sha256,
            approved_by=APPROVER,
        )
    )
