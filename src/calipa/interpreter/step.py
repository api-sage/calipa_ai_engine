"""The INTERPRETING step: P1, then V1, then the policy decision.

P1 gets one retry with V1's errors listed. If the second answer still fails V1, the
auditor is asked to restate the request. Questions are capped by the lifecycle; once
the cap is reached the run is escalated instead of asking again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict

from calipa.audit_trail.artifacts import latest_artifact, put_artifact
from calipa.audit_trail.store import Run, list_events
from calipa.interpreter.policy import (
    Approve,
    Clarify,
    Deny,
    PolicyContext,
    Unsupported,
    decide,
)
from calipa.interpreter.schema import InterpretedScope
from calipa.interpreter.validate import check_interpretation
from calipa.llm.client import LLMCallFailed, Outcome, StructuredLLM
from calipa.llm.prompts import Prompt
from calipa.llm.tagging import tagged_json
from calipa.orchestrator.lifecycle import MAX_CLARIFICATION_ROUNDS, RunState, count_entries
from calipa.orchestrator.runner import Next, StepContext, StepHandler
from calipa.orchestrator.steps import APPROVED_SCOPE, ApprovedScope

INTERPRETATION = "interpretation"
MAX_ATTEMPTS = 2
SUPPORTED_CONSTRAINTS = (
    "period",
    "currency (USD, EUR, GBP, NGN)",
    "transaction reference",
    "account role from <account_catalog>",
    "control from <control_catalog>",
)
RESTATE = (
    "I could not interpret the request reliably. Please restate what you want "
    "checked, for which period, and on which accounts."
)
# Failures that mean "this answer was unusable", as opposed to the call failing.
_BAD_ANSWER = {Outcome.SCHEMA_INVALID, Outcome.EMPTY, Outcome.MAX_TOKENS}


class Interpretation(BaseModel):
    """What P1 produced and what code made of it, kept with the run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_ids: list[str]
    attempt_errors: list[list[str]]
    output: InterpretedScope | None
    decision: str
    reason: str


@dataclass(frozen=True, slots=True)
class Conversation:
    payload: dict[str, Any]  # what P1 sees
    auditor_text: list[str]  # what people wrote, for V1's grounding checks


def build_conversation(conn: psycopg.Connection, run: Run) -> Conversation:
    turns: list[dict[str, str]] = [{"from": "auditor", "text": run.request_text}]
    written = [run.request_text]
    reviewer_note = ""
    for event in list_events(conn, run.run_id):
        if event.state == RunState.CLARIFICATION_REQUESTED.value:
            turns.append({"from": "calipa", "text": str(event.payload["question"])})
        elif event.event_type == "clarification_answered":
            answer = str(event.payload["answer"])
            turns.append({"from": "auditor", "text": answer})
            written.append(answer)
        elif event.event_type == "reinvestigation_of":
            reviewer_note = str(event.payload.get("reviewer_note", ""))
    payload: dict[str, Any] = {"turns": turns}
    if run.parent_run_id is not None:
        previous = latest_artifact(conn, run.parent_run_id, APPROVED_SCOPE, ApprovedScope)
        if previous is not None:
            payload["previous_scope"] = previous[1].model_dump(
                mode="json",
                include={
                    "objective",
                    "period_start",
                    "period_end",
                    "currencies",
                    "transaction_references",
                    "account_roles",
                    "control_ids",
                },
            )
        if reviewer_note:
            payload["reviewer_note"] = reviewer_note
            written.append(reviewer_note)
    return Conversation(payload, written)


class InterpretStep:
    def __init__(self, *, llm: StructuredLLM, prompt: Prompt, policy: PolicyContext) -> None:
        self._llm = llm
        self._prompt = prompt
        self._policy = policy

    def handlers(self) -> dict[RunState, StepHandler]:
        return {RunState.INTERPRETING: self.interpret}

    def user_content(self, conversation: Conversation) -> str:
        catalog = [
            {"id": role.value, "description": text}
            for role, text in sorted(self._policy.accounts.descriptions.items())
        ]
        controls = {
            "controls": [
                {"id": c.control_id, "name": c.name}
                for c in self._policy.controls.controls.values()
            ],
            "objectives": {
                o.value: list(ids) for o, ids in self._policy.controls.objectives.items()
            },
        }
        return (
            f"<account_catalog>\n{tagged_json(catalog)}\n</account_catalog>\n"
            f"<control_catalog>\n{tagged_json(controls)}\n</control_catalog>\n"
            f"<supported_constraints>\n{tagged_json(list(SUPPORTED_CONSTRAINTS))}\n"
            "</supported_constraints>\n"
            f"<conversation>\n{tagged_json(conversation.payload)}\n</conversation>"
        )

    def interpret(self, ctx: StepContext) -> Next:
        conversation = build_conversation(ctx.conn, ctx.run)
        base = self.user_content(conversation)
        content = base
        call_ids: list[str] = []
        attempt_errors: list[list[str]] = []
        output: InterpretedScope | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = self._llm.structured(
                    step="interpret",
                    prompt=self._prompt,
                    user_content=content,
                    output_model=InterpretedScope,
                    effort="low",
                    run_id=ctx.run_id,
                )
            except LLMCallFailed as exc:
                call_ids.append(str(exc.call_id))
                if exc.outcome is Outcome.REFUSAL:
                    reason = "the model declined to interpret the request"
                    return self._finish(
                        ctx, call_ids, attempt_errors, None, RunState.ESCALATED, reason
                    )
                if exc.outcome not in _BAD_ANSWER:
                    raise  # the call itself failed; the orchestrator retries the step
                errors = [f"the previous answer was unusable ({exc.outcome.value})"]
                candidate = None
            else:
                call_ids.append(str(result.call_id))
                candidate = result.output
                errors = check_interpretation(
                    candidate,
                    controls=self._policy.controls,
                    auditor_text=conversation.auditor_text,
                )
            attempt_errors.append(errors)
            ctx.record(
                "interpretation_attempt",
                {"attempt": attempt, "llm_call_id": call_ids[-1], "errors": errors},
            )
            if not errors:
                output = candidate
                break
            previous = candidate.model_dump(mode="json") if candidate else None
            content = (
                f"{base}\n<previous_answer>\n{tagged_json(previous)}\n</previous_answer>\n"
                f"<errors_to_fix>\n{tagged_json(errors)}\n</errors_to_fix>"
            )

        if output is None:
            return self._ask(ctx, call_ids, attempt_errors, None, RESTATE, "interpretation failed")

        decision = decide(output, initiator=ctx.run.initiated_by, now=ctx.now(), ctx=self._policy)
        match decision:
            case Approve(scope=scope):
                ref = put_artifact(
                    ctx.conn, ctx.run_id, APPROVED_SCOPE, scope, created_by=ctx.actor
                )
                nxt = self._finish(
                    ctx, call_ids, attempt_errors, output, RunState.EXTRACTING, "approved"
                )
                return Next(
                    nxt.to,
                    {
                        **nxt.payload,
                        "approved_scope_artifact_id": ref.artifact_id,
                        "objective": scope.objective,
                        "period": f"{scope.period_start} to {scope.period_end}",
                        "period_rule": scope.period_rule,
                    },
                )
            case Clarify(question=question, reason=reason):
                return self._ask(ctx, call_ids, attempt_errors, output, question, reason)
            case Deny(reason=reason):
                return self._finish(ctx, call_ids, attempt_errors, output, RunState.DENIED, reason)
            case Unsupported(reason=reason):
                return self._finish(
                    ctx, call_ids, attempt_errors, output, RunState.UNSUPPORTED, reason
                )

    def _ask(
        self,
        ctx: StepContext,
        call_ids: list[str],
        attempt_errors: list[list[str]],
        output: InterpretedScope | None,
        question: str,
        reason: str,
    ) -> Next:
        if count_entries(ctx.conn, ctx.run_id, RunState.CLARIFICATION_REQUESTED) >= (
            MAX_CLARIFICATION_ROUNDS
        ):
            nxt = self._finish(
                ctx,
                call_ids,
                attempt_errors,
                output,
                RunState.ESCALATED,
                f"still unclear after {MAX_CLARIFICATION_ROUNDS} questions: {reason}",
            )
            return Next(nxt.to, {**nxt.payload, "open_question": question})
        nxt = self._finish(
            ctx, call_ids, attempt_errors, output, RunState.CLARIFICATION_REQUESTED, reason
        )
        return Next(nxt.to, {**nxt.payload, "question": question})

    def _finish(
        self,
        ctx: StepContext,
        call_ids: list[str],
        attempt_errors: list[list[str]],
        output: InterpretedScope | None,
        to: RunState,
        reason: str,
    ) -> Next:
        record = Interpretation(
            call_ids=call_ids,
            attempt_errors=attempt_errors,
            output=output,
            decision=to.value,
            reason=reason,
        )
        ref = put_artifact(ctx.conn, ctx.run_id, INTERPRETATION, record, created_by=ctx.actor)
        return Next(to, {"interpretation_artifact_id": ref.artifact_id, "reason": reason})
