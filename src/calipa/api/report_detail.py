"""The report's data as JSON, for the UI.

Built from the same inputs as the Markdown (reporter.step.load_inputs), and only
after checking those inputs are the ones the report's manifest names: the UI must
never show data the reviewed report was not built from.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from calipa.analyst.step import Finding
from calipa.api.schemas import (
    AccountLabel,
    AICallView,
    ClaimView,
    ControlTotalView,
    DataView,
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
from calipa.errors import IntegrityViolation
from calipa.reference import ControlCatalog
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
