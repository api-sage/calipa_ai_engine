"""Report inputs for the real group, analysed (G1) and not analysed (G2), and a P3
answer for them that passes V3. Tests break that answer one way at a time."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from calipa.analyst.schema import ExceptionAnalysis
from calipa.analyst.step import Attempt, Finding, _decide, _not_analysed
from calipa.analyst.verify import check_analysis
from calipa.orchestrator.steps import ApprovedScope
from calipa.reference import load_account_catalog, load_tariff
from calipa.reporter.facts import Fact, ReportInputs, build_facts
from calipa.reporter.verify import FACT
from tests.analyst_helpers import find, good_answer, real_group, reconcile

SCOPE = ApprovedScope(
    objective="transaction_trace",
    period_start=date(2026, 10, 25),
    period_end=date(2026, 10, 25),
    timezone="Africa/Lagos",
    account_catalog_sha256=load_account_catalog().sha256,
    tariff_sha256=load_tariff().sha256,
    approved_by="test",
)


def analysed_finding(answer: dict[str, Any] | None = None) -> Finding:
    group = real_group()
    analysis = ExceptionAnalysis.model_validate(answer or good_answer(group.bundle))
    attempt = Attempt(
        attempt=1,
        llm_call_id=uuid4(),
        outcome="ok",
        output=analysis,
        report=check_analysis(analysis, group.bundle),
    )
    return _decide(group, [attempt], refused=False)


def report_inputs(*findings: Finding) -> ReportInputs:
    _, result = reconcile()
    found = list(findings) or [
        analysed_finding(),
        _not_analysed(real_group(), "the run's limit of analysis calls was reached"),
    ]
    return ReportInputs(
        scope=SCOPE,
        snapshot=None,
        result=result,
        summary=None,
        findings=found,
        exceptions_in_scope=len(result.exceptions),
    )


def siblings_key() -> str:
    """The G1 fact for how far apart the two sibling transfers were posted."""
    bundle = real_group().bundle
    return f"G1.{find(bundle, 'derived_fact', fact='sibling_transfers')}.seconds_apart"


def finding_text(key: str, narrative: str) -> dict[str, Any]:
    """A finding narrative listing exactly the facts it cites."""
    return {
        "finding_key": key,
        "narrative": narrative,
        "fact_keys_used": list(dict.fromkeys(FACT.findall(narrative))),
    }


G1_TEXT = (
    "The engine found {{fact:G1.exceptions}}. The evidence is consistent with "
    "{{fact:G1.h1}}. Another possible explanation is {{fact:G1.h2}}, as the second "
    "transfer followed the first by {{fact:%s}} seconds. The evidence is also "
    "consistent with {{fact:G1.h3}}."
)
G2_TEXT = (
    "The engine found {{fact:G2.exceptions}}. This finding was {{fact:G2.status}} "
    "because {{fact:G2.reason}}."
)


def good_narrative() -> dict[str, Any]:
    return {
        "executive_summary": (
            "The engine checked {{fact:run.transfers_checked}} transfers in the period from "
            "{{fact:run.period_start}} to {{fact:run.period_end}} and raised "
            "{{fact:run.exceptions_in_scope}} exceptions within the scope, in "
            "{{fact:run.groups}} findings."
        ),
        "findings": [
            finding_text("G1", G1_TEXT % siblings_key()),
            finding_text("G2", G2_TEXT),
        ],
        "cross_finding_patterns": [],
    }


def facts_for(inputs: ReportInputs) -> dict[str, Fact]:
    return build_facts(inputs)
