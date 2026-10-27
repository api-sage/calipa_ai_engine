"""The ANALYSING step: evidence bundles, then P2, then V2, one finding per group.

Each group gets up to two P2 attempts; the second sees V2's errors. Hypotheses that
still fail are dropped and the drop is recorded. If none survives, the finding says
"AI analysis unavailable" and the engine's exceptions still go into the report.

Each finding is saved as soon as it is made, so a step that fails part-way resumes
without paying for the groups it already analysed. Groups are analysed most severe
first, and only up to a per-run limit and a per-run cost cap; the rest are recorded
as not analysed, which the report states as a limitation.

With `parallelism` above 1, up to that many groups are analysed at once, each on its
own lane (a database connection and model client, from `lanes`). Only the step's own
thread writes the run's events and artifacts, so the hash chain is appended in one
order. The cost cap is checked as each group starts, so it can be overshot by the
groups already running; the call cap counts the calls those groups may still make.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from calipa.analyst.categories import CATEGORIES, NOT_ANALYSED
from calipa.analyst.evidence import EvidenceBundle, ExceptionGroup, build_groups
from calipa.analyst.schema import ExceptionAnalysis, RootCause
from calipa.analyst.verify import (
    CheckReport,
    VerifiedAnalysis,
    check_analysis,
    explained_exceptions,
    surviving,
)
from calipa.audit_trail.artifacts import (
    latest_artifact,
    list_artifacts,
    put_artifact,
    require_artifact,
)
from calipa.engine.contract import ReconciliationRequest, ReconciliationResult, Severity
from calipa.errors import IntegrityViolation
from calipa.llm.client import LLMCallFailed, Outcome, StructuredLLM
from calipa.llm.prompts import Prompt
from calipa.llm.tagging import tagged_json
from calipa.orchestrator.lifecycle import RunState
from calipa.orchestrator.runner import Next, StepContext, StepHandler
from calipa.orchestrator.steps import (
    APPROVED_SCOPE,
    ENGINE_REQUEST,
    ENGINE_RESULT,
    GL_SNAPSHOT,
    ApprovedScope,
    SnapshotArtifact,
    in_scope_exceptions,
)
from calipa.reference import AccountCatalog, ControlCatalog, Tariff
from calipa.review.store import lineage

FINDING = "finding"
ANALYSIS = "analysis"
MAX_ATTEMPTS = 2
DEFAULT_MAX_GROUPS = 25
DEFAULT_MAX_COST_USD = Decimal("10")
# Cost is unknown for providers without a price table (Llama), so the number of P2
# calls per run is capped as well.
DEFAULT_MAX_CALLS = 60
# Stays under the SDK's limit for non-streaming requests.
P2_MAX_TOKENS = 20_000
_BAD_ANSWER = {Outcome.SCHEMA_INVALID, Outcome.EMPTY, Outcome.MAX_TOKENS}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Attempt(_Model):
    attempt: int
    llm_call_id: UUID
    outcome: str  # "ok", or why the call gave no usable answer
    output: ExceptionAnalysis | None
    report: CheckReport | None


class DroppedHypothesis(_Model):
    attempt: int
    rank: int
    category: RootCause
    errors: list[str]


class Finding(_Model):
    group_id: str
    bundle_sha256: str
    bundle: EvidenceBundle
    labels: dict[str, str]  # role label -> account number, for the report; never sent
    severity: Severity
    status: Literal["analysed", "analysis_unavailable", "not_analysed"]
    reason: str
    attempts: list[Attempt]
    used_attempt: int | None
    analysis: VerifiedAnalysis | None
    dropped: list[DroppedHypothesis]
    uncovered_exceptions: list[str]  # engine exception ids no surviving hypothesis explains
    # Set when this finding was carried over from an earlier version of the audit
    # because the group's evidence and the prompt were identical (see _reusable).
    reused_from_run: UUID | None = None


@dataclass(frozen=True, slots=True)
class Analysed:
    finding: Finding
    attempt_events: list[dict[str, Any]]  # recorded by the step's thread, in order


class AnalysisSummary(_Model):
    exceptions_in_scope: int
    exceptions_out_of_scope: int
    groups: int
    analysed: int
    analysis_unavailable: int
    not_analysed: int
    finding_artifact_ids: list[UUID]  # most severe group first
    limitations: list[str]
    cost_usd: Decimal


class AnalyseStep:
    def __init__(
        self,
        *,
        llm: StructuredLLM,
        prompt: Prompt,
        tariff: Tariff,
        catalog: AccountCatalog,
        controls: ControlCatalog,
        max_groups: int = DEFAULT_MAX_GROUPS,
        max_cost_usd: Decimal = DEFAULT_MAX_COST_USD,
        max_calls: int = DEFAULT_MAX_CALLS,
        parallelism: int = 1,
        lanes: Callable[[], AbstractContextManager[StructuredLLM]] | None = None,
        reuse_unchanged: bool = True,
    ) -> None:
        if parallelism < 1 or (parallelism > 1 and lanes is None):
            raise ValueError("parallel analysis needs lanes, one per concurrent group")
        self._llm = llm
        self._prompt = prompt
        self._tariff = tariff
        self._catalog = catalog
        self._controls = controls
        self._max_groups = max_groups
        self._max_cost = max_cost_usd
        self._max_calls = max_calls
        self._parallelism = parallelism
        self._lanes = lanes
        self._reuse = reuse_unchanged

    def handlers(self) -> dict[RunState, StepHandler]:
        return {RunState.ANALYSING: self.analyse}

    def analyse(self, ctx: StepContext) -> Next:
        _, scope = require_artifact(ctx.conn, ctx.run_id, APPROVED_SCOPE, ApprovedScope)
        _, request = require_artifact(ctx.conn, ctx.run_id, ENGINE_REQUEST, ReconciliationRequest)
        _, result = require_artifact(ctx.conn, ctx.run_id, ENGINE_RESULT, ReconciliationResult)
        if (request.tariff_sha256, request.account_catalog_sha256) != (
            self._tariff.sha256,
            self._catalog.sha256,
        ):
            raise IntegrityViolation("reference data changed since reconciliation")
        snapshot = latest_artifact(ctx.conn, ctx.run_id, GL_SNAPSHOT, SnapshotArtifact)
        gaps = [*(snapshot[1].limitations if snapshot else []), *result.limitations]
        in_scope = in_scope_exceptions(result.exceptions, scope, self._controls)
        groups = build_groups(
            in_scope, request, tariff=self._tariff, catalog=self._catalog, data_gaps=gaps
        )

        done = {
            f.group_id: (ref.artifact_id, f)
            for ref, f in list_artifacts(ctx.conn, ctx.run_id, FINDING, Finding)
        }
        for group in groups:
            previous = done.get(group.bundle.group_id)
            if previous is not None and previous[1].bundle_sha256 != group.sha256:
                raise IntegrityViolation(f"evidence for {group.bundle.group_id} changed on resume")
        todo = [
            (position, group)
            for position, group in enumerate(groups)
            if group.bundle.group_id not in done
        ]
        if self._reuse and todo:
            earlier = self._earlier_findings(ctx)
            for position, group in list(todo):
                if group.sha256 in earlier:
                    done[group.bundle.group_id] = self._save(ctx, earlier[group.sha256])
                    todo.remove((position, group))
        if self._parallelism == 1:
            for position, group in todo:
                skip = self._skip(ctx, group, position, running=0)
                outcome = skip or self._analyse_group(self._llm, ctx.run_id, group)
                done[group.bundle.group_id] = self._save(ctx, outcome)
        else:
            self._analyse_in_parallel(ctx, todo, done)

        artifact_ids = [done[g.bundle.group_id][0] for g in groups]
        findings = [done[g.bundle.group_id][1] for g in groups]
        summary = _summary(
            findings,
            artifact_ids,
            result,
            in_scope_count=len(in_scope),
            cost_usd=run_cost(ctx.conn, ctx.run_id),
        )
        ref = put_artifact(ctx.conn, ctx.run_id, ANALYSIS, summary, created_by=ctx.actor)
        return Next(
            RunState.REPORTING,
            {
                "analysis_artifact_id": ref.artifact_id,
                "groups": summary.groups,
                "analysed": summary.analysed,
                "analysis_unavailable": summary.analysis_unavailable,
                "not_analysed": summary.not_analysed,
                "cost_usd": str(summary.cost_usd),
            },
        )

    def _analyse_in_parallel(
        self,
        ctx: StepContext,
        todo: list[tuple[int, ExceptionGroup]],
        done: dict[str, tuple[UUID, Finding]],
    ) -> None:
        """Keep up to `parallelism` groups running; save each as it finishes. If a group
        fails, the others already running are still saved before the error is raised,
        so a retry does not pay for them again."""
        assert self._lanes is not None
        lanes = self._lanes

        def work(group: ExceptionGroup) -> Analysed:
            with lanes() as llm:
                return self._analyse_group(llm, ctx.run_id, group)

        failure: BaseException | None = None
        queue = list(todo)
        running: set[Future[Analysed]] = set()
        with ThreadPoolExecutor(self._parallelism, thread_name_prefix="p2") as pool:
            while queue or running:
                while queue and failure is None and len(running) < self._parallelism:
                    position, group = queue.pop(0)
                    skip = self._skip(ctx, group, position, running=len(running))
                    if skip is not None:
                        done[group.bundle.group_id] = self._save(ctx, skip)
                    else:
                        running.add(pool.submit(work, group))
                if not running:
                    break
                finished, running = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    try:
                        outcome = future.result()
                    except Exception as exc:  # noqa: BLE001 - raised once the rest are saved
                        failure = failure or exc
                        continue
                    done[outcome.finding.group_id] = self._save(ctx, outcome)
        if failure is not None:
            raise failure

    def _save(self, ctx: StepContext, outcome: Analysed) -> tuple[UUID, Finding]:
        finding = outcome.finding
        for attempt in outcome.attempt_events:
            ctx.record("analysis_attempt", attempt)
        ref = put_artifact(ctx.conn, ctx.run_id, FINDING, finding, created_by=ctx.actor)
        payload: dict[str, Any] = {
            "group_id": finding.group_id,
            "status": finding.status,
            "artifact_id": str(ref.artifact_id),
            "blob_sha256": ref.blob_sha256,
            "bundle_sha256": finding.bundle_sha256,
        }
        if finding.reused_from_run is not None:
            payload["reused_from_run"] = str(finding.reused_from_run)
        ctx.record("finding_recorded", payload)
        return ref.artifact_id, finding

    def _earlier_findings(self, ctx: StepContext) -> dict[str, Analysed]:
        """Findings from earlier versions of this audit that can be carried over, by
        bundle hash. A re-investigation usually changes the scope of some groups and
        leaves others exactly as they were; asking P2 again about an identical group
        with an identical prompt would only add cost and variation. Only analysed
        findings are carried over: a group the model could not explain, or that a cap
        skipped, gets a fresh attempt. The latest version wins."""
        out: dict[str, Analysed] = {}
        for run_id in lineage(ctx.conn, ctx.run_id)[:-1]:
            for _, f in list_artifacts(ctx.conn, run_id, FINDING, Finding):
                if f.status != "analysed" or f.used_attempt is None:
                    continue
                call_id = f.attempts[f.used_attempt - 1].llm_call_id
                row = ctx.conn.execute(
                    "select prompt_sha256 from llm_calls where call_id = %s", (call_id,)
                ).fetchone()
                if row is None or row[0] != self._prompt.sha256:
                    continue
                origin = f.reused_from_run or run_id
                out[f.bundle_sha256] = Analysed(
                    f.model_copy(update={"reused_from_run": origin}), []
                )
        return out

    # --- one group ----------------------------------------------------------------------

    def _skip(
        self, ctx: StepContext, group: ExceptionGroup, position: int, *, running: int
    ) -> Analysed | None:
        """Why this group is not analysed, if it is not. `running`: groups already
        under way, each of which may still make MAX_ATTEMPTS calls."""
        reason = None
        if not group.bundle.analysable:
            reason = "the engine could not run these checks (rate missing)"
        elif position >= self._max_groups:
            reason = f"only the {self._max_groups} most severe groups are analysed per run"
        elif run_cost(ctx.conn, ctx.run_id) >= self._max_cost:
            reason = f"the run's AI cost cap of USD {self._max_cost} was reached"
        elif (
            run_calls(ctx.conn, ctx.run_id, step="analyse") + MAX_ATTEMPTS * (running + 1)
            > self._max_calls
        ):
            reason = f"the run's limit of {self._max_calls} analysis calls was reached"
        return None if reason is None else Analysed(_not_analysed(group, reason), [])

    def _analyse_group(self, llm: StructuredLLM, run_id: UUID, group: ExceptionGroup) -> Analysed:
        """P2 and V2 for one group. Touches the database only through `llm`, so it can
        run on another thread; the events it would record are returned instead."""
        bundle = group.bundle
        base = user_content(bundle)
        content = base
        attempts: list[Attempt] = []
        events: list[dict[str, Any]] = []
        refused = False
        for n in range(1, MAX_ATTEMPTS + 1):
            try:
                result = llm.structured(
                    step="analyse",
                    prompt=self._prompt,
                    user_content=content,
                    output_model=ExceptionAnalysis,
                    effort="high",
                    max_tokens=P2_MAX_TOKENS,
                    run_id=run_id,
                )
            except LLMCallFailed as exc:
                if exc.outcome is not Outcome.REFUSAL and exc.outcome not in _BAD_ANSWER:
                    raise  # the call itself failed; the orchestrator retries the step
                attempt = Attempt(
                    attempt=n,
                    llm_call_id=exc.call_id,
                    outcome=exc.outcome.value,
                    output=None,
                    report=None,
                )
                errors = [f"the previous answer was unusable ({exc.outcome.value})"]
                refused = exc.outcome is Outcome.REFUSAL
            else:
                report = check_analysis(result.output, bundle)
                attempt = Attempt(
                    attempt=n,
                    llm_call_id=result.call_id,
                    outcome="ok",
                    output=result.output,
                    report=report,
                )
                errors = report.errors()
            attempts.append(attempt)
            events.append(
                {
                    "group_id": bundle.group_id,
                    "attempt": n,
                    "llm_call_id": str(attempt.llm_call_id),
                    "outcome": attempt.outcome,
                    "errors": len(errors),
                    "passing_hypotheses": (
                        len(attempt.report.passing_hypotheses()) if attempt.report else 0
                    ),
                },
            )
            if refused or not errors:
                break
            previous = attempt.output.model_dump(mode="json") if attempt.output else None
            content = (
                f"{base}\n<previous_answer>\n{tagged_json(previous)}\n</previous_answer>\n"
                f"<errors_to_fix>\n{tagged_json(errors)}\n</errors_to_fix>"
            )
        return Analysed(_decide(group, attempts, refused=refused), events)


def user_content(bundle: EvidenceBundle) -> str:
    """What P2 sees for one group: the bundle, the categories and the data gaps."""
    group = {
        "group_id": bundle.group_id,
        "exceptions": [
            {
                "evidence_id": e.evidence_id,
                "type": e.type.value,
                "severity": e.severity.value,
                "allowed_categories": e.allowed_categories,
                "ruled_out_by_evidence": e.ruled_out,
            }
            for e in bundle.exceptions
        ],
    }
    evidence = [{"id": i.evidence_id, "type": i.type, "fields": i.fields} for i in bundle.items]
    categories = [
        {
            "category": name,
            "description": CATEGORIES[name].description,
            "required_evidence": list(CATEGORIES[name].required),
        }
        for name in bundle.available_categories
    ]
    return (
        f"<exception_group>\n{tagged_json(group)}\n</exception_group>\n"
        f"<evidence>\n{tagged_json(evidence)}\n</evidence>\n"
        f"<available_categories>\n{tagged_json(categories)}\n</available_categories>\n"
        f"<known_data_gaps>\n{tagged_json(bundle.known_data_gaps)}\n</known_data_gaps>"
    )


def _decide(group: ExceptionGroup, attempts: list[Attempt], *, refused: bool) -> Finding:
    """Use the attempt that explains the most exceptions (then has the most passing
    hypotheses, then is the later one); record what was dropped from it."""

    def score(a: Attempt) -> tuple[int, int, int]:
        assert a.output is not None and a.report is not None
        explained = explained_exceptions(a.output, a.report)
        return (len(explained), len(a.report.passing_hypotheses()), a.attempt)

    judged_attempts = [a for a in attempts if a.output is not None and a.report is not None]
    passing = [a for a in judged_attempts if a.report and a.report.passing_hypotheses()]
    chosen = max(passing, key=score, default=None)
    judged = chosen or (judged_attempts[-1] if judged_attempts else None)
    dropped: list[DroppedHypothesis] = []
    if judged is not None and judged.output is not None and judged.report is not None:
        whole = judged.report.global_errors
        for h, check in zip(judged.output.hypotheses, judged.report.hypotheses, strict=True):
            if whole or not check.ok:
                dropped.append(
                    DroppedHypothesis(
                        attempt=judged.attempt,
                        rank=h.rank,
                        category=h.category,
                        errors=[*whole, *check.errors],
                    )
                )
    bundle = group.bundle
    analysable = [e for e in bundle.exceptions if e.type not in NOT_ANALYSED]
    if chosen is None or chosen.output is None or chosen.report is None:
        reason = (
            "the model declined to analyse this group"
            if refused
            else f"no hypothesis passed the claim checker after {len(attempts)} attempts"
        )
        return Finding(
            group_id=bundle.group_id,
            bundle_sha256=group.sha256,
            bundle=bundle,
            labels=group.labels,
            severity=bundle.severity,
            status="analysis_unavailable",
            reason=reason,
            attempts=attempts,
            used_attempt=None,
            analysis=None,
            dropped=dropped,
            uncovered_exceptions=[e.exception_id for e in analysable],
        )
    verified = surviving(chosen.output, chosen.report, bundle)
    assert verified is not None  # chosen has a passing hypothesis
    explained = {e for h in verified.hypotheses for e in h.explains_exceptions}
    return Finding(
        group_id=bundle.group_id,
        bundle_sha256=group.sha256,
        bundle=bundle,
        labels=group.labels,
        severity=bundle.severity,
        status="analysed",
        reason="verified" if not dropped else f"{len(dropped)} hypotheses dropped by V2",
        attempts=attempts,
        used_attempt=chosen.attempt,
        analysis=verified,
        dropped=dropped,
        uncovered_exceptions=[e.exception_id for e in analysable if e.evidence_id not in explained],
    )


def _not_analysed(group: ExceptionGroup, reason: str) -> Finding:
    bundle = group.bundle
    return Finding(
        group_id=bundle.group_id,
        bundle_sha256=group.sha256,
        bundle=bundle,
        labels=group.labels,
        severity=bundle.severity,
        status="not_analysed",
        reason=reason,
        attempts=[],
        used_attempt=None,
        analysis=None,
        dropped=[],
        uncovered_exceptions=[e.exception_id for e in bundle.exceptions],
    )


def _summary(
    findings: list[Finding],
    artifact_ids: list[UUID],
    result: ReconciliationResult,
    *,
    in_scope_count: int,
    cost_usd: Decimal,
) -> AnalysisSummary:
    counts = {
        s: sum(f.status == s for f in findings)
        for s in ("analysed", "analysis_unavailable", "not_analysed")
    }
    limitations: list[str] = []
    reasons: dict[str, int] = {}
    for f in findings:
        if f.status != "analysed":
            reasons[f.reason] = reasons.get(f.reason, 0) + 1
    for reason, count in sorted(reasons.items()):
        limitations.append(f"{count} exception group(s) without AI analysis: {reason}.")
    return AnalysisSummary(
        exceptions_in_scope=in_scope_count,
        exceptions_out_of_scope=len(result.exceptions) - in_scope_count,
        groups=len(findings),
        analysed=counts["analysed"],
        analysis_unavailable=counts["analysis_unavailable"],
        not_analysed=counts["not_analysed"],
        finding_artifact_ids=artifact_ids,
        limitations=limitations,
        cost_usd=cost_usd,
    )


def run_calls(conn: psycopg.Connection, run_id: UUID, *, step: str) -> int:
    row = conn.execute(
        "select count(*) from llm_calls where run_id = %s and step = %s", (run_id, step)
    ).fetchone()
    return int(row[0]) if row else 0


def run_cost(conn: psycopg.Connection, run_id: UUID) -> Decimal:
    """Known cost only: calls with no price (cost None) add nothing; see run_calls."""
    row = conn.execute(
        """
        select coalesce(sum(r.cost_usd), 0)
        from llm_calls c join llm_call_results r using (call_id)
        where c.run_id = %s
        """,
        (run_id,),
    ).fetchone()
    value: Any = row[0] if row else 0
    return Decimal(value)
