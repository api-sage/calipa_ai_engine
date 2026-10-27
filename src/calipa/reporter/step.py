"""The REPORTING step: facts, then P3, then V3, then the rendered and hashed report.

P3 gets up to two attempts; the second sees V3's errors. The attempt with the most
passing sections is used, and any section that still fails is replaced by template
text and flagged for the reviewer. If P3 gives no usable answer, or the call fails,
or the run's AI budget is spent, the whole narrative is template text, flagged: a
report always comes out, and every value in it comes from code.

The report is stored once per run. A step that is retried after the report was
saved reuses it, after checking that its inputs have not changed.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from calipa.analyst.step import (
    ANALYSIS,
    DEFAULT_MAX_COST_USD,
    FINDING,
    AnalysisSummary,
    Finding,
    run_calls,
    run_cost,
)
from calipa.audit_trail.artifacts import (
    ArtifactRef,
    latest_artifact,
    list_artifacts,
    put_artifact,
    require_artifact,
)
from calipa.audit_trail.store import get_blob, put_blob
from calipa.canonical import hash_value, sha256_hex
from calipa.engine.contract import ReconciliationRequest, ReconciliationResult
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
from calipa.reference import ControlCatalog
from calipa.reporter.facts import Fact, ReportInputs, build_facts, user_content
from calipa.reporter.render import (
    TEMPLATE_VERSION,
    Disclosure,
    FinalNarrative,
    assemble,
    render_markdown,
)
from calipa.reporter.schema import ReportNarrative
from calipa.reporter.verify import ReportCheck, check_report
from calipa.review.store import register_report

REPORT = "report"
MAX_ATTEMPTS = 2
P3_MAX_TOKENS = 16_000
DEFAULT_MAX_CALLS = 2
_BAD_ANSWER = {Outcome.REFUSAL, Outcome.SCHEMA_INVALID, Outcome.EMPTY, Outcome.MAX_TOKENS}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportAttempt(_Model):
    attempt: int
    llm_call_id: UUID
    outcome: str
    output: ReportNarrative | None
    check: ReportCheck | None


class InputRef(_Model):
    kind: str
    artifact_id: UUID
    blob_sha256: str


class PromptRef(_Model):
    name: str
    version: str
    sha256: str


class ReportManifest(_Model):
    """What the report hash covers."""

    template_version: str
    run_id: UUID
    inputs: list[InputRef]
    snapshot_sha256: str
    prompt: PromptRef | None  # None when P3 was not called
    facts_sha256: str
    narrative_sha256: str
    markdown_sha256: str


class ReportArtifact(_Model):
    manifest: ReportManifest
    report_sha256: str
    narrative_source: Literal["ai", "partial", "template"]
    flags: list[str]
    attempts: list[ReportAttempt]
    used_attempt: int | None
    narrative: FinalNarrative
    facts: list[Fact]


class ReportStep:
    def __init__(
        self,
        *,
        llm: StructuredLLM,
        prompt: Prompt,
        controls: ControlCatalog,
        max_cost_usd: Decimal = DEFAULT_MAX_COST_USD,
        max_calls: int = DEFAULT_MAX_CALLS,
    ) -> None:
        self._llm = llm
        self._prompt = prompt
        self._controls = controls
        self._max_cost = max_cost_usd
        self._max_calls = max_calls

    def handlers(self) -> dict[RunState, StepHandler]:
        return {RunState.NO_EXCEPTIONS: self.no_exceptions, RunState.REPORTING: self.report}

    def no_exceptions(self, ctx: StepContext) -> Next:
        """Nothing to analyse: the report records the checks that passed."""
        return Next(RunState.REPORTING)

    def report(self, ctx: StepContext) -> Next:
        inputs, refs = self._inputs(ctx)
        stored = latest_artifact(ctx.conn, ctx.run_id, REPORT, ReportArtifact)
        if stored is not None:
            ref, artifact = stored
            if artifact.manifest.inputs != refs:
                raise IntegrityViolation("report inputs changed since the report was written")
            return _done(ctx, ref, artifact)

        facts = build_facts(inputs)
        findings = dict(inputs.keyed())
        attempts: list[ReportAttempt] = []
        flags: list[str] = []
        skipped = self._skip_reason(ctx, inputs)
        if skipped:
            if inputs.findings:
                flags.append(f"No AI narrative: {skipped}.")
        else:
            attempts, failure = self._write(ctx, inputs, facts, findings)
            if failure:
                flags.append(f"No AI narrative: {failure}.")

        chosen = _best(attempts)
        narrative = assemble(
            inputs,
            facts,
            chosen.output if chosen else None,
            chosen.check if chosen else None,
            why_template=skipped or ("no usable P3 answer" if not chosen else ""),
        )
        flags += _flags(inputs, narrative, called=bool(attempts))
        source = _source(inputs, narrative)

        markdown = render_markdown(
            inputs,
            narrative,
            run_id=str(ctx.run_id),
            request_text=ctx.run.request_text,
            initiated_by=ctx.run.initiated_by,
            calls=disclosures(
                ctx.conn,
                ctx.run_id,
                reused=[
                    a.llm_call_id
                    for f in inputs.findings
                    if f.reused_from_run is not None
                    for a in f.attempts
                ],
            ),
            flags=flags,
        ).encode("utf-8")
        markdown_sha256 = put_blob(ctx.conn, markdown, "text/markdown; charset=utf-8")
        manifest = ReportManifest(
            template_version=TEMPLATE_VERSION,
            run_id=ctx.run_id,
            inputs=refs,
            snapshot_sha256=inputs.result.snapshot_sha256,
            prompt=(
                PromptRef(
                    name=self._prompt.name,
                    version=self._prompt.version,
                    sha256=self._prompt.sha256,
                )
                if attempts
                else None
            ),
            facts_sha256=hash_value(list(facts.values())),
            narrative_sha256=hash_value(narrative),
            markdown_sha256=markdown_sha256,
        )
        artifact = ReportArtifact(
            manifest=manifest,
            report_sha256=hash_value(manifest),
            narrative_source=source,
            flags=flags,
            attempts=attempts,
            used_attempt=chosen.attempt if chosen else None,
            narrative=narrative,
            facts=list(facts.values()),
        )
        ref = put_artifact(ctx.conn, ctx.run_id, REPORT, artifact, created_by=ctx.actor)
        ctx.record(
            "report_rendered",
            {
                "artifact_id": str(ref.artifact_id),
                "report_sha256": artifact.report_sha256,
                "markdown_sha256": markdown_sha256,
                "narrative_source": source,
                "flags": len(flags),
            },
        )
        return _done(ctx, ref, artifact)

    # --- inputs ---------------------------------------------------------------------------

    def _inputs(self, ctx: StepContext) -> tuple[ReportInputs, list[InputRef]]:
        conn, run_id = ctx.conn, ctx.run_id
        scope_ref, scope = require_artifact(conn, run_id, APPROVED_SCOPE, ApprovedScope)
        request_ref, _ = require_artifact(conn, run_id, ENGINE_REQUEST, ReconciliationRequest)
        result_ref, result = require_artifact(conn, run_id, ENGINE_RESULT, ReconciliationResult)
        snapshot = latest_artifact(conn, run_id, GL_SNAPSHOT, SnapshotArtifact)
        analysis = latest_artifact(conn, run_id, ANALYSIS, AnalysisSummary)
        refs = [_ref(scope_ref), _ref(request_ref), _ref(result_ref)]
        if snapshot is not None:
            refs.append(_ref(snapshot[0]))

        findings: list[Finding] = []
        if analysis is None:
            in_scope = len(in_scope_exceptions(result.exceptions, scope, self._controls))
            if in_scope:
                raise IntegrityViolation("exceptions in scope but no analysis was stored")
            summary = None
        else:
            summary_ref, summary = analysis
            refs.append(_ref(summary_ref))
            stored = {
                r.artifact_id: (r, f) for r, f in list_artifacts(conn, run_id, FINDING, Finding)
            }
            for artifact_id in summary.finding_artifact_ids:
                ref, finding = stored[artifact_id]
                refs.append(_ref(ref))
                findings.append(finding)
            in_scope = summary.exceptions_in_scope
        inputs = ReportInputs(
            scope=scope,
            snapshot=snapshot[1] if snapshot else None,
            result=result,
            summary=summary,
            findings=findings,
            exceptions_in_scope=in_scope,
        )
        return inputs, refs

    def _skip_reason(self, ctx: StepContext, inputs: ReportInputs) -> str:
        if not inputs.findings:
            return "no exceptions within the scope, so there is nothing to explain"
        if run_cost(ctx.conn, ctx.run_id) >= self._max_cost:
            return f"the run's AI cost cap of USD {self._max_cost} was reached"
        if run_calls(ctx.conn, ctx.run_id, step="report") + MAX_ATTEMPTS > self._max_calls:
            return f"the run's limit of {self._max_calls} report calls was reached"
        return ""

    # --- P3 -------------------------------------------------------------------------------

    def _write(
        self,
        ctx: StepContext,
        inputs: ReportInputs,
        facts: dict[str, Fact],
        findings: dict[str, Finding],
    ) -> tuple[list[ReportAttempt], str]:
        """P3's attempts, and why the calls stopped working if they did."""
        base = user_content(inputs, facts)
        content = base
        attempts: list[ReportAttempt] = []
        for n in range(1, MAX_ATTEMPTS + 1):
            try:
                result = self._llm.structured(
                    step="report",
                    prompt=self._prompt,
                    user_content=content,
                    output_model=ReportNarrative,
                    effort="medium",
                    max_tokens=P3_MAX_TOKENS,
                    run_id=ctx.run_id,
                )
            except LLMCallFailed as exc:
                ctx.record(
                    "report_attempt",
                    {"attempt": n, "llm_call_id": str(exc.call_id), "outcome": exc.outcome.value},
                )
                if exc.outcome not in _BAD_ANSWER:
                    return attempts, f"the P3 call failed ({exc.outcome.value})"
                attempts.append(
                    ReportAttempt(
                        attempt=n,
                        llm_call_id=exc.call_id,
                        outcome=exc.outcome.value,
                        output=None,
                        check=None,
                    )
                )
                if exc.outcome is Outcome.REFUSAL:
                    break
                errors = [f"the previous answer was unusable ({exc.outcome.value})"]
                previous = None
            else:
                check = check_report(result.output, facts, findings)
                attempts.append(
                    ReportAttempt(
                        attempt=n,
                        llm_call_id=result.call_id,
                        outcome="ok",
                        output=result.output,
                        check=check,
                    )
                )
                errors = check.errors()
                ctx.record(
                    "report_attempt",
                    {
                        "attempt": n,
                        "llm_call_id": str(result.call_id),
                        "outcome": "ok",
                        "errors": len(errors),
                    },
                )
                if not errors:
                    break
                previous = result.output.model_dump(mode="json")
            content = (
                f"{base}\n<previous_answer>\n{tagged_json(previous)}\n</previous_answer>\n"
                f"<errors_to_fix>\n{tagged_json(errors)}\n</errors_to_fix>"
            )
        failure = "" if any(a.check for a in attempts) else "P3 gave no usable answer"
        return attempts, failure


def _best(attempts: list[ReportAttempt]) -> ReportAttempt | None:
    """The attempt with the most sections usable as written; the later one on a tie."""
    judged = [a for a in attempts if a.output is not None and a.check is not None]
    return max(judged, key=lambda a: (a.check.passing() if a.check else 0, a.attempt), default=None)


def _flags(inputs: ReportInputs, narrative: FinalNarrative, *, called: bool) -> list[str]:
    flags: list[str] = []
    replaced = narrative.template_sections()
    if called and replaced:
        flags.append(
            "Template text replaced AI text that failed the report checker in: "
            + ", ".join(replaced)
            + "."
        )
    if narrative.rejected_patterns:
        flags.append(
            f"{len(narrative.rejected_patterns)} cross-finding patterns failed the report "
            "checker and were left out."
        )
    without = [k for k, f in inputs.keyed() if f.status != "analysed"]
    if without:
        flags.append("Findings without a verified AI analysis: " + ", ".join(without) + ".")
    if inputs.snapshot is not None and inputs.snapshot.status == "partial":
        flags.append("Extraction was partial; see the limitations.")
    return flags


def _source(
    inputs: ReportInputs, narrative: FinalNarrative
) -> Literal["ai", "partial", "template"]:
    replaced = narrative.template_sections()
    if not replaced:
        return "ai"
    return "template" if len(replaced) == 1 + len(inputs.findings) else "partial"


def _ref(ref: ArtifactRef) -> InputRef:
    return InputRef(kind=ref.kind, artifact_id=ref.artifact_id, blob_sha256=ref.blob_sha256)


def _done(ctx: StepContext, ref: ArtifactRef, artifact: ReportArtifact) -> Next:
    """Record the report as its audit's next version, then wait for review."""
    version = register_report(
        ctx.conn,
        ctx.run_id,
        artifact_id=ref.artifact_id,
        report_sha256=artifact.report_sha256,
        markdown_sha256=artifact.manifest.markdown_sha256,
        flagged=bool(artifact.flags),
    )
    return Next(
        RunState.PENDING_REVIEW,
        {
            "version": version.version,
            "report_artifact_id": ref.artifact_id,
            "report_sha256": artifact.report_sha256,
            "markdown_sha256": artifact.manifest.markdown_sha256,
            "narrative_source": artifact.narrative_source,
            "flagged": bool(artifact.flags),
        },
    )


def disclosures(
    conn: psycopg.Connection, run_id: UUID, *, reused: Sequence[UUID] = ()
) -> list[Disclosure]:
    """Every AI call this run made, and the earlier calls behind findings it carried
    over from a previous version."""
    rows = conn.execute(
        """
        select c.step, c.prompt_name || '.v' || c.prompt_version, c.model_requested,
               r.model_served, r.outcome, c.run_id <> %s
        from llm_calls c left join llm_call_results r using (call_id)
        where c.run_id = %s or c.call_id = any(%s)
        order by c.started_at, c.call_id
        """,
        (run_id, run_id, list(reused)),
    ).fetchall()
    return [
        Disclosure(
            step=s, prompt=p, model_requested=m, model_served=served, outcome=o, reused=earlier
        )
        for s, p, m, served, o, earlier in rows
    ]


def markdown_of(conn: psycopg.Connection, artifact: ReportArtifact) -> str:
    data = get_blob(conn, artifact.manifest.markdown_sha256)
    if sha256_hex(data) != artifact.manifest.markdown_sha256:
        raise IntegrityViolation("stored report does not match its hash")
    return data.decode("utf-8")
