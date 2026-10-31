"""The report's data as JSON, for the UI.

Built from the same inputs as the Markdown (reporter.step.load_inputs), and only
after checking those inputs are the ones the report's manifest names: the UI must
never show data the reviewed report was not built from.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from calipa.analyst.step import Finding
from calipa.api.schemas import (
    AccountLabel,
    AICallView,
    ClaimView,
    ControlTotalView,
    DataView,
    DayMoneyView,
    DayView,
    ExceptionView,
    FindingDetail,
    HypothesisView,
    MoneyView,
    ReconciliationView,
    ReportDetail,
    RuleCount,
    ScopeView,
    SectionView,
)
from calipa.audit_trail.artifacts import require_artifact
from calipa.engine.contract import EngineException, ReconciliationRequest
from calipa.errors import IntegrityViolation
from calipa.gateway.fcy import GLEntry
from calipa.orchestrator.steps import ENGINE_REQUEST, in_scope_exceptions
from calipa.reference import AccountRole, ControlCatalog
from calipa.reporter.facts import CATEGORY_LABELS, EXCEPTION_LABELS
from calipa.reporter.render import NEXT_STEP_LABELS, Section, limitations, name_entries
from calipa.reporter.step import ReportArtifact, disclosures, load_inputs, reused_call_ids


def report_detail(
    conn: psycopg.Connection, run_id: UUID, artifact: ReportArtifact, controls: ControlCatalog
) -> ReportDetail:
    inputs, refs = load_inputs(conn, run_id, controls)
    if refs != artifact.manifest.inputs:
        raise IntegrityViolation("report inputs changed since the report was written")
    scope, result, snapshot = inputs.scope, inputs.result, inputs.snapshot

    by_rule: dict[str, list[int]] = {}
    for c in result.checks:
        counts = by_rule.setdefault(c.rule_id, [0, 0])
        counts[0 if c.passed else 1] += 1

    return ReportDetail(
        scope=ScopeView(
            objective=scope.objective,
            period_start=scope.period_start,
            period_end=scope.period_end,
            timezone=scope.timezone,
            period_rule=scope.period_rule,
            currencies=[c.value for c in scope.currencies],
            transaction_references=scope.transaction_references,
            control_ids=scope.control_ids,
            assumptions=scope.assumptions,
            approved_by=scope.approved_by,
        ),
        data=None
        if snapshot is None
        else DataView(
            status=snapshot.status,
            snapshot_sha256=snapshot.snapshot_sha256,
            entries_in_period=snapshot.entries_in_period,
            entries_in_scope=len(snapshot.entries),
            pages=len(snapshot.pages),
        ),
        reconciliation=ReconciliationView(
            engine=f"{result.engine_name} {result.engine_version}, rules {result.ruleset_version}",
            snapshot_sha256=result.snapshot_sha256,
            transfers_checked=result.transfers_checked,
            exceptions_in_scope=inputs.exceptions_in_scope,
            exceptions_outside_scope=len(result.exceptions) - inputs.exceptions_in_scope,
            rules=[
                RuleCount(rule_id=r, passed=p, failed=f) for r, (p, f) in sorted(by_rule.items())
            ],
            control_totals=[
                ControlTotalView(
                    account_role=t.account_role,
                    currency=t.currency.value,
                    credits_to_transient=str(t.credits_to_transient.amount),
                    debits_from_transient=str(t.debits_from_transient.amount),
                    entries=t.entry_count,
                )
                for t in result.control_totals
            ],
        ),
        days=[]
        if snapshot is None
        else _days(
            snapshot.entries,
            in_scope_exceptions(result.exceptions, scope, controls),
            require_artifact(conn, run_id, ENGINE_REQUEST, ReconciliationRequest)[1],
            scope.period_start,
            scope.period_end,
        ),
        findings=[
            _finding(key, finding, section)
            for (key, finding), section in zip(
                inputs.keyed(), artifact.narrative.findings, strict=True
            )
        ],
        limitations=limitations(inputs),
        ai_calls=[
            AICallView(
                step=c.step,
                prompt=c.prompt,
                model_requested=c.model_requested,
                model_served=c.model_served,
                outcome=c.outcome,
                reused=c.reused,
            )
            for c in disclosures(conn, run_id, reused=reused_call_ids(inputs))
        ],
    )


def _days(
    entries: list[GLEntry],
    exceptions: list[EngineException],
    request: ReconciliationRequest,
    start: date,
    end: date,
) -> list[DayView]:
    """Each day of the period: entries, exceptions, and money into and out of the
    transient account per currency, the same direction rule as the control totals."""
    tz = ZoneInfo(request.bank_timezone)
    transient = next(
        (n for n, r in request.account_roles.items() if r == AccountRole.TRANSIENT), None
    )
    entries_on: dict[date, int] = defaultdict(int)
    started: dict[UUID, date] = {}
    money: dict[date, dict[str, list[Decimal]]] = defaultdict(dict)
    for e in entries:
        day = e.created_at.astimezone(tz).date()
        entries_on[day] += 1
        started[e.transfer_id] = min(day, started.get(e.transfer_id, day))
        if transient not in (e.debited_account, e.credited_account):
            continue
        into_out = money[day].setdefault(e.currency.value, [Decimal(0), Decimal(0)])
        into_out[0 if e.credited_account == transient else 1] += e.amount
    exceptions_on: dict[date, int] = defaultdict(int)
    for x in exceptions:
        if x.transfer_id in started:
            exceptions_on[started[x.transfer_id]] += 1
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [
        DayView(
            day=d,
            entries=entries_on[d],
            exceptions=exceptions_on[d],
            money=[
                DayMoneyView(currency=c, into_transient=str(i), out_of_transient=str(o))
                for c, (i, o) in sorted(money[d].items())
            ],
        )
        for d in days
    ]


def _money(fields: dict[str, str], name: str) -> MoneyView | None:
    amount = fields.get(f"{name}_amount")
    if amount is None:
        return None
    return MoneyView(amount=amount, currency=fields.get(f"{name}_currency", ""))


def _finding(key: str, finding: Finding, section: Section) -> FindingDetail:
    items = {i.evidence_id: i.fields for i in finding.bundle.items}
    analysis = finding.analysis
    used = sorted(
        label
        for label in finding.labels
        if any(label in fields.values() for fields in items.values())
    )
    return FindingDetail(
        key=key,
        severity=finding.severity.value,
        status=finding.status,
        reason=finding.reason,
        narrative=SectionView(ref=section.ref, source=section.source, text=section.text),
        exceptions=[
            ExceptionView(
                exception_id=e.exception_id,
                label=EXCEPTION_LABELS[e.type],
                severity=e.severity.value,
                rule_id=items[e.evidence_id].get("rule_id", ""),
                transfer_id=items[e.evidence_id].get("transfer_id", ""),
                expected=_money(items[e.evidence_id], "expected"),
                actual=_money(items[e.evidence_id], "actual"),
                difference=_money(items[e.evidence_id], "difference"),
            )
            for e in finding.bundle.exceptions
        ],
        hypotheses=[]
        if analysis is None
        else [
            HypothesisView(
                rank=h.rank,
                category=h.category,
                label=CATEGORY_LABELS[h.category],
                confidence=h.confidence,
                summary=analysis.rendered[f"hypothesis {h.rank}"],
                supporting_claims=h.supporting_claims,
                contradicting_claims=h.contradicting_claims,
                would_confirm=list(h.would_confirm),
                would_refute=list(h.would_refute),
            )
            for h in analysis.hypotheses
        ],
        claims=[]
        if analysis is None
        else [
            ClaimView(
                claim_id=c.claim_id,
                kind=c.kind,
                text=name_entries(analysis.rendered[c.claim_id], items),
                based_on=c.based_on,
            )
            for c in analysis.claims
        ],
        next_steps=[] if analysis is None else [NEXT_STEP_LABELS[s] for s in analysis.next_steps],
        data_limitations=[] if analysis is None else analysis.data_limitations,
        dropped_hypotheses=len(finding.dropped),
        uncovered_exceptions=finding.uncovered_exceptions,
        reused_from_run=finding.reused_from_run,
        accounts=[AccountLabel(label=label, account=finding.labels[label]) for label in used],
    )
