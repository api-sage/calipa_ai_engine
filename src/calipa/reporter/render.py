"""The report as code renders it.

Code writes the scope, the data, the engine's totals and tables, every value, the
limitations word for word, the AI-use disclosure and the place for the auditor's
opinion. P3's sections that passed V3 go in with their values filled in by code; any
section that failed is replaced by template text written here from the facts alone.

The output is Markdown with every piece of data escaped, so text from the bank or
from a model cannot add structure, links or HTML. The same inputs give the same
bytes: nothing here reads a clock.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from calipa.analyst.evidence import STANDING_DATA_GAPS
from calipa.analyst.step import Finding
from calipa.analyst.tokens import REF
from calipa.reporter.facts import (
    CATEGORY_LABELS,
    EXCEPTION_LABELS,
    STATUS_LABELS,
    Fact,
    ReportInputs,
    hypothesis_text,
    join_words,
)
from calipa.reporter.schema import ReportNarrative
from calipa.reporter.verify import FACT, ReportCheck

TEMPLATE_VERSION = "report-template.v1"

NEXT_STEP_LABELS = {
    "request_reversal_data": "Request reversal data from the bank.",
    "request_switch_logs": "Request payment switch logs.",
    "request_settlement_report": "Request the settlement report.",
    "confirm_tariff_with_finance": "Confirm the tariff with Finance.",
    "confirm_entry_semantics_with_core_banking": (
        "Confirm the meaning of the entries with the core banking team."
    ),
    "check_customer_instruction_with_ops": "Check the customer's instruction with Operations.",
    "escalate_to_auditor": "Escalate to the auditor.",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Section(_Model):
    ref: str  # "executive_summary" or a finding key
    source: Literal["ai", "template"]
    text: str  # values filled in
    written: str | None  # P3's text with placeholders, when source is "ai"
    errors: list[str]  # why P3's text was not used


class PatternText(_Model):
    finding_keys: list[str]
    text: str
    written: str


class FinalNarrative(_Model):
    executive_summary: Section
    findings: list[Section]  # in finding order
    patterns: list[PatternText]
    rejected_patterns: list[str]  # V3 errors for patterns left out

    def template_sections(self) -> list[str]:
        return [s.ref for s in (self.executive_summary, *self.findings) if s.source == "template"]


# --- choosing text ---------------------------------------------------------------------


def fill(text: str, facts: dict[str, Fact]) -> str:
    return FACT.sub(lambda m: facts[m.group(1)].value, text)


def assemble(
    inputs: ReportInputs,
    facts: dict[str, Fact],
    narrative: ReportNarrative | None,
    check: ReportCheck | None,
    *,
    why_template: str = "",
) -> FinalNarrative:
    """P3's passing sections, template text for the rest. With no narrative (no P3
    call, or no usable answer), everything is template text."""
    if narrative is None or check is None:
        reason = [why_template] if why_template else []
        return FinalNarrative(
            executive_summary=Section(
                ref="executive_summary",
                source="template",
                text=template_summary(inputs),
                written=None,
                errors=reason,
            ),
            findings=[
                Section(
                    ref=key, source="template", text=template_finding(f), written=None, errors=[]
                )
                for key, f in inputs.keyed()
            ],
            patterns=[],
            rejected_patterns=[],
        )

    def section(ref: str, written: str | None, errors: list[str], template: str) -> Section:
        if written is not None and not errors:
            return Section(
                ref=ref, source="ai", text=fill(written, facts), written=written, errors=[]
            )
        return Section(
            ref=ref,
            source="template",
            text=template,
            written=None,
            errors=errors or ["no narrative was written for this finding"],
        )

    by_key = {
        f.finding_key: (f, c)
        for f, c in zip(narrative.findings, check.findings, strict=True)
        if c.ok
    }
    failed = {c.ref: c.errors for c in check.findings if not c.ok}
    findings = []
    for key, finding in inputs.keyed():
        found = by_key.get(key)
        findings.append(
            section(
                key,
                found[0].narrative if found else None,
                [] if found else failed.get(key, []),
                template_finding(finding),
            )
        )
    patterns = [
        PatternText(
            finding_keys=list(dict.fromkeys(p.finding_keys)),
            text=fill(p.text, facts),
            written=p.text,
        )
        for p, c in zip(narrative.cross_finding_patterns, check.patterns, strict=True)
        if c.ok
    ]
    return FinalNarrative(
        executive_summary=section(
            "executive_summary",
            narrative.executive_summary,
            check.summary.errors,
            template_summary(inputs),
        ),
        findings=findings,
        patterns=patterns,
        rejected_patterns=[f"{c.ref}: {e}" for c in check.patterns for e in c.errors],
    )


def template_summary(inputs: ReportInputs) -> str:
    scope, result = inputs.scope, inputs.result
    period = f"{scope.period_start.isoformat()} to {scope.period_end.isoformat()}"
    text = (
        f"The reconciliation engine checked {result.transfers_checked} transfers for the "
        f"period {period} ({scope.timezone})."
    )
    if not inputs.findings:
        return text + " It found no exceptions within the approved scope."
    counts = Counter(f.status for f in inputs.findings)
    return text + (
        f" It raised {inputs.exceptions_in_scope} exceptions within the approved scope, "
        f"in {len(inputs.findings)} findings: {counts['analysed']} analysed, "
        f"{counts['analysis_unavailable']} without a usable AI analysis and "
        f"{counts['not_analysed']} not analysed."
    )


def template_finding(finding: Finding) -> str:
    kinds = join_words(
        list(dict.fromkeys(EXCEPTION_LABELS[e.type] for e in finding.bundle.exceptions))
    )
    text = (
        f"The engine raised {len(finding.bundle.exceptions)} exceptions (highest severity "
        f"{finding.severity.value}) across {len(finding.bundle.transfer_ids)} transfers: "
        f"{kinds}."
    )
    if finding.analysis is None:
        return text + f" There is no AI analysis for this finding: {finding.reason}."
    ranked = "; ".join(
        f"{h.rank}. {hypothesis_text(h.category, h.confidence)}"
        for h in finding.analysis.hypotheses
    )
    text += f" The verified hypotheses, most likely first, are: {ranked}."
    if finding.uncovered_exceptions:
        text += (
            f" {len(finding.uncovered_exceptions)} exceptions are not explained by any "
            "verified hypothesis."
        )
    return text


# --- markdown --------------------------------------------------------------------------

_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+!|~@&])")
_LEADING = re.compile(r"^([0-9]+)([.)])|^([-=])")
_AUTOLINK = re.compile(r"\b(www)\.|\b(https?|ftp|file|mailto|javascript|data|vbscript):", re.I)


def md(value: object) -> str:
    """Plain text, safe anywhere in the Markdown: no structure, links, autolinks, HTML
    or entities, and no invisible characters that could reorder what is shown."""
    text = "".join(c for c in str(value) if unicodedata.category(c) not in ("Cf", "Co"))
    text = _SPECIAL.sub(r"\\\1", " ".join(text.split()))
    text = _LEADING.sub(lambda m: f"{m[1]}\\{m[2]}" if m[1] else f"\\{m[3]}", text)
    return _AUTOLINK.sub(lambda m: f"{m[1]}\\." if m[1] else f"{m[2]}\\:", text)


def _table(headers: list[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(md(h) for h in headers) + " |", "|" + " --- |" * len(headers)]
    out += ["| " + " | ".join(md(c) for c in row) + " |" for row in rows]
    return [*out, ""]


def _marked(section: Section | PatternText) -> str:
    if isinstance(section, PatternText) or section.source == "ai":
        return f"{md(section.text)}\n\n*AI-written, checked by V3; values inserted by code.*"
    return f"{md(section.text)}\n\n*Template text, written by code.*"


class Disclosure(_Model):
    step: str
    prompt: str
    model_requested: str
    model_served: str | None
    outcome: str | None
    reused: bool = False  # made for an earlier version; its finding was carried over


def render_markdown(
    inputs: ReportInputs,
    narrative: FinalNarrative,
    *,
    run_id: str,
    request_text: str,
    initiated_by: str,
    calls: list[Disclosure],
    flags: list[str],
) -> str:
    scope, result = inputs.scope, inputs.result
    lines = [
        "# Calipa GL audit report",
        "",
        "**Draft for second-person review. Not approved.**",
        "",
    ]
    if flags:
        lines += ["**Flagged for the reviewer:**", ""]
        lines += [f"- {md(f)}" for f in flags]
        lines.append("")

    lines += ["## 1. Scope", ""]
    lines += _table(
        ["Item", "Value"],
        [
            ["Run", run_id],
            ["Requested by", initiated_by],
            ["Request", request_text],
            ["Objective", scope.objective.replace("_", " ")],
            ["Period", f"{scope.period_start.isoformat()} to {scope.period_end.isoformat()}"],
            ["Timezone", scope.timezone],
            ["How the period was worked out", scope.period_rule or "not recorded"],
            ["Controls", ", ".join(scope.control_ids) or "all"],
            ["Currencies", ", ".join(c.value for c in scope.currencies) or "all"],
            ["Transaction references", ", ".join(scope.transaction_references) or "all"],
            ["Scope approved by", scope.approved_by],
            ["Account catalog", scope.account_catalog_sha256],
            ["Tariff", scope.tariff_sha256],
            ["Control catalog", scope.control_catalog_sha256 or "not recorded"],
        ],
    )

    lines += ["## 2. Data", ""]
    snapshot = inputs.snapshot
    if snapshot is None:
        lines += ["No extraction record is stored with this run.", ""]
    else:
        lines += _table(
            ["Item", "Value"],
            [
                ["Extraction", snapshot.status],
                ["Snapshot SHA-256", snapshot.snapshot_sha256],
                ["Entries in the period", snapshot.entries_in_period],
                ["Entries in scope", len(snapshot.entries)],
                ["Pages fetched", len(snapshot.pages)],
            ],
        )

    lines += ["## 3. Reconciliation", ""]
    lines += _table(
        ["Item", "Value"],
        [
            [
                "Engine",
                f"{result.engine_name} {result.engine_version}, rules {result.ruleset_version}",
            ],
            ["Snapshot SHA-256", result.snapshot_sha256],
            ["Transfers checked", result.transfers_checked],
            ["Exceptions within scope", inputs.exceptions_in_scope],
            [
                "Exceptions outside the approved controls",
                len(result.exceptions) - inputs.exceptions_in_scope,
            ],
        ],
    )
    by_rule: dict[str, list[int]] = {}
    for c in result.checks:
        counts = by_rule.setdefault(c.rule_id, [0, 0])
        counts[0 if c.passed else 1] += 1
    if by_rule:
        lines += _table(
            ["Rule", "Passed", "Failed"], [[r, p, f] for r, (p, f) in sorted(by_rule.items())]
        )
    if result.control_totals:
        lines += _table(
            ["Account", "Currency", "Credits to transient", "Debits from transient", "Entries"],
            [
                [
                    t.account_role,
                    t.currency.value,
                    t.credits_to_transient.amount,
                    t.debits_from_transient.amount,
                    t.entry_count,
                ]
                for t in result.control_totals
            ],
        )

    lines += ["## 4. Summary", "", _marked(narrative.executive_summary), ""]

    lines += ["## 5. Findings", ""]
    if not inputs.findings:
        lines += ["No exceptions were found within the approved scope.", ""]
    for (key, finding), section in zip(inputs.keyed(), narrative.findings, strict=True):
        lines += _finding(key, finding, section)

    lines += ["## 6. Patterns across findings", ""]
    if narrative.patterns:
        for p in narrative.patterns:
            lines += [f"**{md(', '.join(p.finding_keys))}.** {_marked(p)}", ""]
    else:
        lines += ["None reported.", ""]

    lines += ["## 7. Limitations", ""]
    lines += [f"- {md(text)}" for text in limitations(inputs)]
    lines.append("")

    lines += ["## 8. How AI was used", ""]
    lines += [
        "AI wrote only the text marked as AI-written. It never saw account numbers and "
        "never wrote a value: every number, date, amount and id in this report was "
        "inserted by code from the evidence. Every AI answer was checked by code "
        "before use, and every call is kept in the audit trail.",
        "",
    ]
    if not calls:
        lines += ["No AI calls were made for this run.", ""]
    lines += (
        []
        if not calls
        else _table(
            ["Step", "Prompt", "Model requested", "Model that answered", "Outcome"],
            [
                [
                    c.step,
                    c.prompt,
                    c.model_requested,
                    c.model_served or "unknown",
                    (c.outcome or "none")
                    + (" (earlier version, finding carried over)" if c.reused else ""),
                ]
                for c in calls
            ],
        )
    )

    lines += [
        "## 9. Auditor's opinion",
        "",
        "Reserved for the reviewing auditor. Calipa does not express an audit opinion "
        "or a conclusion on control effectiveness, materiality, compliance or intent.",
        "",
    ]
    return "\n".join(lines)


def _finding(key: str, finding: Finding, section: Section) -> list[str]:
    lines = [
        f"### {md(key)}: {md(finding.severity.value)} severity, "
        f"{md(STATUS_LABELS[finding.status])}",
        "",
    ]
    items = {i.evidence_id: i.fields for i in finding.bundle.items}
    rows = []
    for e in finding.bundle.exceptions:
        f = items[e.evidence_id]
        rows.append(
            [
                EXCEPTION_LABELS[e.type],
                e.severity.value,
                f.get("rule_id", ""),
                f.get("transfer_id", ""),
                _amount(f, "expected"),
                _amount(f, "actual"),
                _amount(f, "difference"),
            ]
        )
    lines += _table(
        ["Exception", "Severity", "Rule", "Transfer", "Expected", "Actual", "Difference"], rows
    )
    lines += [_marked(section), ""]

    analysis = finding.analysis
    if analysis is not None:
        lines += _table(
            ["Rank", "Hypothesis", "Confidence", "Analyst's summary", "Would confirm"],
            [
                [
                    h.rank,
                    CATEGORY_LABELS[h.category],
                    h.confidence,
                    analysis.rendered[f"hypothesis {h.rank}"],
                    ", ".join(h.would_confirm) or "none",
                ]
                for h in analysis.hypotheses
            ],
        )
        lines += ["Evidence the analyst relied on (AI-written claims checked by V2):", ""]
        lines += [
            f"- {md(_entry_ids(analysis.rendered[c.claim_id], items))}" for c in analysis.claims
        ]
        lines.append("")
        if analysis.next_steps:
            lines += ["Suggested next steps:", ""]
            lines += [f"- {md(NEXT_STEP_LABELS[s])}" for s in analysis.next_steps]
            lines.append("")
        if analysis.data_limitations:
            lines += ["Limitations the analyst noted:", ""]
            lines += [f"- {md(t)}" for t in analysis.data_limitations]
            lines.append("")
    if finding.dropped:
        lines += [
            f"{len(finding.dropped)} hypotheses failed the claim checker and were left out; "
            "they are kept in the audit trail.",
            "",
        ]
    used = sorted(
        label
        for label in finding.labels
        if any(label in fields.values() for fields in items.values())
    )
    if used:
        lines += _table(["Label", "Account"], [[label, finding.labels[label]] for label in used])
    return lines


def _entry_ids(text: str, items: dict[str, dict[str, str]]) -> str:
    """Evidence ids mean nothing outside the bundle: name the ledger entry instead."""

    def entry(match: re.Match[str]) -> str:
        fields = items.get(match.group(1), {})
        return f"entry {fields['entry_id']}" if "entry_id" in fields else match.group()

    return REF.sub(entry, text)


def _amount(fields: dict[str, str], name: str) -> str:
    amount = fields.get(f"{name}_amount")
    return f"{fields.get(f'{name}_currency', '')} {amount}" if amount is not None else ""


def limitations(inputs: ReportInputs) -> list[str]:
    """Every limitation, word for word, each once, in a fixed order."""
    out: list[str] = []
    if inputs.snapshot is not None:
        out += inputs.snapshot.limitations
    out += inputs.result.limitations
    if inputs.summary is not None:
        out += inputs.summary.limitations
    out += STANDING_DATA_GAPS
    out += [f"Assumption to confirm: {a}" for a in inputs.scope.assumptions]
    return list(dict.fromkeys(out))
