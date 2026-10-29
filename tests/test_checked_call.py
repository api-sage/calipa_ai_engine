"""The retry rules P1, P2 and P3 share (calipa.llm.checked)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from calipa.llm.checked import Attempt, Checked, call_checked
from calipa.llm.client import Effort, LLMCallFailed, LLMResult, Outcome
from calipa.llm.prompts import Prompt

PROMPT = Prompt(name="test", version="1", text="Answer.")


class Answer(BaseModel):
    value: int


@dataclass(frozen=True, slots=True)
class Even:
    value: int

    def errors(self) -> list[str]:
        return [] if self.value % 2 == 0 else [f"{self.value} is odd"]


@dataclass
class FakeLLM:
    """Answers in order: an int is an answer, an Outcome is a failed call."""

    script: list[int | Outcome]
    sent: list[str] = field(default_factory=list)

    def structured[T: BaseModel](
        self,
        *,
        step: str,
        prompt: Prompt,
        user_content: str,
        output_model: type[T],
        effort: Effort,
        max_tokens: int = 16_000,
        run_id: UUID | None = None,
    ) -> LLMResult[T]:
        self.sent.append(user_content)
        reply = self.script.pop(0)
        if isinstance(reply, Outcome):
            raise LLMCallFailed(reply, uuid4(), reply.value)
        output = output_model.model_validate({"value": reply})
        return LLMResult(uuid4(), output, "fake", None, None)


def run(llm: FakeLLM, *, max_attempts: int = 2, **kwargs: Any) -> Checked[Answer, Even]:
    return call_checked(
        llm,
        step="test",
        prompt=PROMPT,
        content="<question>pick an even number</question>",
        output_model=Answer,
        check=lambda a: Even(a.value),
        effort="low",
        max_attempts=max_attempts,
        **kwargs,
    )


def test_a_passing_answer_is_not_retried() -> None:
    llm = FakeLLM([4])
    checked = run(llm)
    assert len(llm.sent) == 1
    assert checked.passed is not None and checked.passed.output == Answer(value=4)
    assert checked.failure is None and not checked.refused


def test_the_retry_sees_the_previous_answer_and_its_errors() -> None:
    llm = FakeLLM([3, 6])
    checked = run(llm)
    retry = llm.sent[1]
    assert retry.startswith("<question>pick an even number</question>")
    assert "<previous_answer>" in retry and '"value": 3' in retry
    assert "<errors_to_fix>" in retry and "3 is odd" in retry
    assert [a.errors for a in checked.attempts] == [["3 is odd"], []]
    assert checked.passed is checked.attempts[1]


def test_attempts_stop_at_the_limit() -> None:
    llm = FakeLLM([1, 3, 5])
    checked = run(llm)
    assert len(llm.sent) == 2 and checked.passed is None
    assert all(a.check is not None for a in checked.attempts)


@pytest.mark.parametrize("unusable", [Outcome.SCHEMA_INVALID, Outcome.EMPTY, Outcome.MAX_TOKENS])
def test_an_unusable_answer_is_retried(unusable: Outcome) -> None:
    llm = FakeLLM([unusable, 2])
    checked = run(llm)
    first = checked.attempts[0]
    assert first.outcome is unusable and first.output is None and first.check is None
    assert first.errors == [f"the previous answer was unusable ({unusable.value})"]
    assert "<previous_answer>\nnull\n</previous_answer>" in llm.sent[1]
    assert checked.passed is checked.attempts[1]


def test_a_refusal_is_recorded_and_not_retried() -> None:
    llm = FakeLLM([3, Outcome.REFUSAL, 2])
    checked = run(llm, max_attempts=3)
    assert len(llm.sent) == 2
    assert checked.refused and checked.passed is None and checked.failure is None
    assert [a.outcome for a in checked.attempts] == [Outcome.OK, Outcome.REFUSAL]


@pytest.mark.parametrize("failed", [Outcome.API_ERROR, Outcome.CONNECTION_ERROR])
def test_a_failed_call_ends_the_attempts_and_keeps_the_earlier_ones(failed: Outcome) -> None:
    llm = FakeLLM([3, failed])
    checked = run(llm)
    assert checked.failure is not None and checked.failure.outcome is failed
    assert len(checked.attempts) == 1 and not checked.refused


def test_each_attempt_is_reported_before_any_retry() -> None:
    llm = FakeLLM([Outcome.EMPTY, 3])
    seen: list[tuple[int, int]] = []

    def on_attempt(attempt: Attempt[Answer, Even]) -> None:
        seen.append((attempt.number, len(llm.sent)))

    run(llm, on_attempt=on_attempt)
    assert seen == [(1, 1), (2, 2)]
