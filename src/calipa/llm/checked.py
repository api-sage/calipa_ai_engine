"""A model answer checked by code, retried with the errors listed.

P1, P2 and P3 all work this way: ask the model, check the answer with code (V1, V2,
V3), and if the check finds errors, ask again with the previous answer and the
errors. What happens to each kind of answer is decided here, once, for all three:

- an answer that fails its check, or is unusable (cut off, empty, not matching the
  schema), is retried while attempts remain;
- a refusal is recorded and not retried: asking the same question again is not a
  fix, and the step decides what a refusal means for the run;
- a call that fails (API or connection error) ends the attempts. It is returned,
  not raised, with the attempts made before it, so the step can either raise it (the
  orchestrator then retries the step) or carry on without AI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from calipa.llm.client import Effort, LLMCallFailed, Outcome, StructuredLLM
from calipa.llm.prompts import Prompt
from calipa.llm.tagging import tagged_json

# Outcomes that mean the answer was unusable, as opposed to the call failing.
UNUSABLE = frozenset({Outcome.SCHEMA_INVALID, Outcome.EMPTY, Outcome.MAX_TOKENS})


class Verdict(Protocol):
    def errors(self) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class Attempt[T: BaseModel, C: Verdict]:
    number: int
    call_id: UUID
    outcome: Outcome  # OK, REFUSAL or one of UNUSABLE
    output: T | None
    check: C | None  # None when there was no answer to check
    errors: list[str]


@dataclass(frozen=True, slots=True)
class Checked[T: BaseModel, C: Verdict]:
    attempts: list[Attempt[T, C]]
    failure: LLMCallFailed | None  # the call that failed, if one did

    @property
    def refused(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].outcome is Outcome.REFUSAL

    @property
    def passed(self) -> Attempt[T, C] | None:
        """The attempt whose answer passed its check, if one did."""
        return next((a for a in self.attempts if a.check is not None and not a.errors), None)


def call_checked[T: BaseModel, C: Verdict](
    llm: StructuredLLM,
    *,
    step: str,
    prompt: Prompt,
    content: str,
    output_model: type[T],
    check: Callable[[T], C],
    effort: Effort,
    max_attempts: int,
    max_tokens: int = 16_000,
    run_id: UUID | None = None,
    on_attempt: Callable[[Attempt[T, C]], None] | None = None,
) -> Checked[T, C]:
    """Up to `max_attempts` answers; each retry sees the previous answer and its errors.
    `on_attempt` is called after each answer is checked, before any retry."""
    attempts: list[Attempt[T, C]] = []
    sent = content
    for number in range(1, max_attempts + 1):
        try:
            result = llm.structured(
                step=step,
                prompt=prompt,
                user_content=sent,
                output_model=output_model,
                effort=effort,
                max_tokens=max_tokens,
                run_id=run_id,
            )
        except LLMCallFailed as exc:
            if exc.outcome is not Outcome.REFUSAL and exc.outcome not in UNUSABLE:
                return Checked(attempts, exc)
            attempt: Attempt[T, C] = Attempt(
                number,
                exc.call_id,
                exc.outcome,
                None,
                None,
                [f"the previous answer was unusable ({exc.outcome.value})"],
            )
        else:
            verdict = check(result.output)
            attempt = Attempt(
                number, result.call_id, Outcome.OK, result.output, verdict, verdict.errors()
            )
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)
        if attempt.outcome is Outcome.REFUSAL or not attempt.errors:
            break
        previous = attempt.output.model_dump(mode="json") if attempt.output else None
        sent = _retry_content(content, previous, attempt.errors)
    return Checked(attempts, None)


def _retry_content(content: str, previous: Any, errors: list[str]) -> str:
    return (
        f"{content}\n<previous_answer>\n{tagged_json(previous)}\n</previous_answer>\n"
        f"<errors_to_fix>\n{tagged_json(errors)}\n</errors_to_fix>"
    )
