"""Facts for the report: every value P3 may mention, under a key it can cite.

P3 sees a fact's key and description, never its value, and writes {{fact:KEY}} where
the value belongs; code fills it in. Run facts (run.*) may appear anywhere. A
finding's facts (G1.*) are its severity, status, exception types, its verified
hypotheses' categories and confidences, and the evidence values its engine
exceptions and surviving claims use (G1.E3.expected_amount).

Findings are keyed G1, G2, ... in the order the analysis stored them, most severe
first, so the same run always gives the same keys.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from calipa.analyst.schema import RootCause
from calipa.analyst.step import AnalysisSummary, Finding
from calipa.analyst.tokens import PLACEHOLDER, scan
from calipa.engine.contract import ExceptionType, ReconciliationResult
from calipa.llm.tagging import tagged_json
from calipa.orchestrator.steps import ApprovedScope, SnapshotArtifact

FACT_KEY = re.compile(r"^(run|G[1-9][0-9]*)\.([A-Za-z0-9_.]+)$")

EXCEPTION_LABELS: dict[ExceptionType, str] = {
    ExceptionType.STRUCTURE_INCOMPLETE: "incomplete transfer structure",
    ExceptionType.FEE_LEG_MISSING: "missing charge leg",
    ExceptionType.VAT_LEG_MISSING: "missing VAT leg",
    ExceptionType.FEE_AMOUNT_MISMATCH: "charge amount mismatch",
    ExceptionType.VAT_AMOUNT_MISMATCH: "VAT amount mismatch",
    ExceptionType.FX_CONVERSION_MISMATCH: "conversion amount mismatch",
    ExceptionType.ROUNDING_DIFFERENCE: "rounding difference",
    ExceptionType.TRANSIENT_RESIDUAL: "balance left in the transient account",
    ExceptionType.FEE_VAT_IN_BOTH_CURRENCIES: "charge and VAT posted in both currencies",
    ExceptionType.ENTRY_DIRECTION_INCONSISTENT: "inconsistent entry direction",
    ExceptionType.DUPLICATE_ENTRY: "duplicate entry",
    ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE: "possible duplicate instruction",
    ExceptionType.RATE_UNAVAILABLE: "no recorded rate",
    ExceptionType.RATE_AMBIGUOUS: "more than one recorded rate",
}

CATEGORY_LABELS: dict[RootCause, str] = {
    "fee_or_vat_leg_missing": "a charge or VAT leg that was not posted",
    "fee_or_vat_leg_duplicated_across_ccy": "charge or VAT legs posted in both currencies",
    "tariff_applied_mismatch": "a charge or VAT amount that differs from the tariff",
    "transient_residual_unallocated": "money left unallocated in the transient account",
    "fx_rate_applied_mismatch": "a conversion at a rate other than the recorded rate",
    "rounding_difference": "a rounding difference",
    "duplicate_customer_instruction": "a customer instruction processed twice",
    "duplicate_posting_same_instruction": "the same entry posted more than once",
    "entry_direction_inconsistency": "entries recorded with inconsistent direction",
    "timing_cutoff_difference": "a timing or cut-off difference",
    "wrong_account_mapping": "an entry posted to the wrong account",
    "missing_reversal": "a reversal that was not posted",
    "partial_reversal": "a partial reversal",
    "orphan_reversal": "a reversal with no original transfer",
    "switch_timeout_unposted": "a switch timeout that left legs unposted",
    "posted_not_settled": "a transfer posted but not settled",
    "manual_adjustment": "a manual adjustment",
    "unexplained": "no explanation the evidence supports",
}

STATUS_LABELS = {
    "analysed": "analysed",
    "analysis_unavailable": "AI analysis unavailable",
    "not_analysed": "not analysed",
}


_Add = Callable[[str, str, object], None]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(_Model):
    key: str
    description: str
    value: str


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """Everything the report is built from, all read back from persisted artifacts."""

    scope: ApprovedScope
    snapshot: SnapshotArtifact | None
    result: ReconciliationResult
    summary: AnalysisSummary | None  # None when no exception was in scope
    findings: list[Finding]  # most severe first
    exceptions_in_scope: int

    def finding_keys(self) -> list[str]:
        return [f"G{i}" for i in range(1, len(self.findings) + 1)]

    def keyed(self) -> list[tuple[str, Finding]]:
        return list(zip(self.finding_keys(), self.findings, strict=True))


def build_facts(inputs: ReportInputs) -> dict[str, Fact]:
    facts: dict[str, Fact] = {}

    def add(key: str, description: str, value: object) -> None:
        assert FACT_KEY.match(key), key
        facts[key] = Fact(key=key, description=description, value=str(value))

    scope, result, summary = inputs.scope, inputs.result, inputs.summary
    add("run.objective", "the audit objective", scope.objective.replace("_", " "))
    add("run.period_start", "first day of the audited period", scope.period_start.isoformat())
    add("run.period_end", "last day of the audited period", scope.period_end.isoformat())
    add("run.timezone", "the bank timezone the period is in", scope.timezone)
    if inputs.snapshot is not None:
        add("run.entries_in_scope", "ledger entries in scope", len(inputs.snapshot.entries))
        add("run.snapshot_status", "whether extraction was complete", inputs.snapshot.status)
    add("run.transfers_checked", "transfers the engine checked", result.transfers_checked)
    add("run.checks_passed", "engine checks passed", sum(c.passed for c in result.checks))
    add("run.checks_failed", "engine checks failed", sum(not c.passed for c in result.checks))
    add("run.exceptions_in_scope", "exceptions within the scope", inputs.exceptions_in_scope)
    add(
        "run.exceptions_out_of_scope",
        "exceptions outside the approved controls, not reported",
        len(result.exceptions) - inputs.exceptions_in_scope,
    )
    add("run.groups", "exception groups (findings)", len(inputs.findings))
    for status, label in STATUS_LABELS.items():
        add(
            f"run.groups_{status}",
            f"findings {label}",
            sum(f.status == status for f in inputs.findings),
        )
    if summary is not None:
        add("run.groups_not_analysed_reasons", "why findings were not analysed", _reasons(summary))

    for key, finding in inputs.keyed():
        _finding_facts(key, finding, add)
    return facts


def _reasons(summary: AnalysisSummary) -> str:
    return "; ".join(summary.limitations) or "none"


def _finding_facts(key: str, finding: Finding, add: _Add) -> None:
    exceptions = finding.bundle.exceptions
    add(f"{key}.severity", "the finding's highest exception severity", finding.severity.value)
    add(f"{key}.status", "whether the finding was analysed", STATUS_LABELS[finding.status])
    add(
        f"{key}.exceptions",
        "the kinds of exception the engine raised",
        join_words(list(dict.fromkeys(EXCEPTION_LABELS[e.type] for e in exceptions))),
    )
    add(f"{key}.exception_count", "exceptions in the finding", len(exceptions))
    add(f"{key}.transfer_count", "transfers in the finding", len(finding.bundle.transfer_ids))
    if finding.status != "analysed" or finding.analysis is None:
        add(f"{key}.reason", "why there is no AI analysis", finding.reason)
        cited: set[tuple[str, str]] = set()
    else:
        analysis = finding.analysis
        for h in analysis.hypotheses:
            add(
                f"{key}.h{h.rank}",
                f"hypothesis {h.rank}: its root cause and confidence",
                hypothesis_text(h.category, h.confidence),
            )
        add(f"{key}.hypotheses", "verified hypotheses", len(analysis.hypotheses))
        add(
            f"{key}.unexplained_exceptions",
            "exceptions no verified hypothesis explains",
            len(finding.uncovered_exceptions),
        )
        add(f"{key}.dropped_hypotheses", "hypotheses V2 rejected", len(finding.dropped))
        cited = {
            (p.evidence_id, p.field)
            for text in (
                *(c.text for c in analysis.claims),
                *(h.summary for h in analysis.hypotheses),
            )
            for p in scan(text).placeholders
        }
    for item in finding.bundle.items:
        kind = item.fields.get("fact", item.type)
        for field, value in item.fields.items():
            if item.type == "engine_exception" or (item.evidence_id, field) in cited:
                description = f"{item.type} {item.evidence_id}"
                if kind != item.type:
                    description += f" ({kind})"
                add(f"{key}.{item.evidence_id}.{field}", f"{description}: {field}", value)


def hypothesis_text(category: RootCause, confidence: str) -> str:
    """A hypothesis as one value, so its root cause and confidence cannot be parted."""
    return f"{CATEGORY_LABELS[category]} ({confidence} confidence)"


def join_words(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def as_fact_placeholders(text: str, key: str) -> str:
    """P2's {E3.field} placeholders as this finding's {{fact:G1.E3.field}}."""
    return PLACEHOLDER.sub(lambda m: f"{{{{fact:{key}.{m.group(1)}.{m.group(2)}}}}}", text)


def user_content(inputs: ReportInputs, facts: dict[str, Fact]) -> str:
    """What P3 sees: fact keys and descriptions (no values), and the verified findings
    with their claims written as fact placeholders."""
    run_facts = [
        {"key": f.key, "description": f.description}
        for f in facts.values()
        if f.key.startswith("run.")
    ]
    findings = []
    for key, finding in inputs.keyed():
        entry: dict[str, object] = {
            "finding_key": key,
            "status": finding.status,
            "facts": [
                {"key": f.key, "description": f.description}
                for f in facts.values()
                if f.key.startswith(f"{key}.")
            ],
        }
        if finding.analysis is not None:
            claims = {c.claim_id: c for c in finding.analysis.claims}
            entry["hypotheses"] = [
                {
                    "rank": h.rank,
                    "fact": f"{key}.h{h.rank}",
                    "summary": as_fact_placeholders(h.summary, key),
                    "supporting_claims": [
                        as_fact_placeholders(claims[c].text, key) for c in h.supporting_claims
                    ],
                    "contradicting_claims": [
                        as_fact_placeholders(claims[c].text, key) for c in h.contradicting_claims
                    ],
                    "would_confirm": h.would_confirm,
                }
                for h in finding.analysis.hypotheses
            ]
        findings.append(entry)
    return (
        f"<run_facts>\n{tagged_json(run_facts)}\n</run_facts>\n"
        f"<verified_findings>\n{tagged_json(findings)}\n</verified_findings>\n"
        f"<finding_keys>\n{tagged_json(inputs.finding_keys())}\n</finding_keys>"
    )
