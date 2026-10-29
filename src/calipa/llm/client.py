"""The only path to a language model. Each call is recorded in two append-only rows:
the full request before it is sent, and the outcome after, with the raw response
bytes. Steps depend on `StructuredLLM`; `ClaudeClient` and `LlamaClient` (in
calipa.llm.llama) are the two providers, chosen by configuration.

SDK helpers that parse and validate inside the SDK would lose a response that fails
validation before it could be stored. Instead each client sends the JSON schema
itself, stores the raw response, and only then validates.

No fallback model is configured: a failed or refused call is recorded and raised,
never silently answered by an unevaluated model. Claude gets no temperature (Opus 5.5
rejects it).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import anthropic
import psycopg
from anthropic.types import Message
from pydantic import BaseModel, ValidationError

from calipa.audit_trail.store import now_utc, put_blob
from calipa.canonical import canonical_json, hash_value
from calipa.llm.pricing import cost_usd
from calipa.llm.prompts import Prompt

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Outcome(StrEnum):
    OK = "ok"
    REFUSAL = "refusal"
    MAX_TOKENS = "max_tokens"
    EMPTY = "empty"
    SCHEMA_INVALID = "schema_invalid"
    API_ERROR = "api_error"
    CONNECTION_ERROR = "connection_error"


class LLMCallFailed(Exception):
    def __init__(self, outcome: Outcome, call_id: UUID, detail: str) -> None:
        super().__init__(f"{outcome}: {detail} (llm call {call_id})")
        self.outcome = outcome
        self.call_id = call_id
        self.detail = detail


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    call_id: UUID
    output: T
    model_served: str
    request_id: str | None
    cost_usd: Decimal | None


@dataclass(slots=True)
class CallResult:  # what a provider reports back about one call
    outcome: Outcome
    model_served: str | None = None
    request_id: str | None = None
    http_status: int | None = None
    stop_reason: str | None = None
    refusal_category: str | None = None
    response_blob: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cost_usd: Decimal | None = None
    error: str | None = None


class StructuredLLM(Protocol):
    """What a step needs from a model: one JSON answer matching `output_model`."""

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
    ) -> LLMResult[T]: ...


class RecordingClient(ABC):
    """Records each call before and after sending it. Providers say how to build the
    request and how to send it and read the answer."""

    def __init__(self, conn: psycopg.Connection, *, model: str) -> None:
        if not conn.autocommit:
            raise ValueError("LLM clients need an autocommit connection (audit_trail.db.connect)")
        self._conn = conn
        self._model = model

    @abstractmethod
    def _schema(self, output_model: type[BaseModel]) -> dict[str, Any]: ...

    @abstractmethod
    def _request(
        self,
        prompt: Prompt,
        user_content: str,
        schema: dict[str, Any],
        output_model: type[BaseModel],
        effort: Effort,
        max_tokens: int,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def _send[T: BaseModel](
        self, request: dict[str, Any], output_model: type[T]
    ) -> tuple[CallResult, T | None]: ...

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
        schema = self._schema(output_model)
        request = self._request(prompt, user_content, schema, output_model, effort, max_tokens)
        call_id = uuid4()
        started_at = now_utc()
        with self._conn.transaction():
            request_blob = put_blob(self._conn, canonical_json(request), "application/json")
            self._conn.execute(
                """
                insert into llm_calls
                    (call_id, run_id, step, prompt_name, prompt_version, prompt_sha256,
                     schema_name, schema_sha256, model_requested, effort, request_blob,
                     started_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    call_id,
                    run_id,
                    step,
                    prompt.name,
                    prompt.version,
                    prompt.sha256,
                    output_model.__name__,
                    hash_value(schema),
                    self._model,
                    effort,
                    request_blob,
                    started_at,
                ),
            )

        clock = time.monotonic()
        result, output = self._send(request, output_model)
        self._record(call_id, result, duration_ms=int((time.monotonic() - clock) * 1000))

        if result.outcome is not Outcome.OK or output is None:
            raise LLMCallFailed(result.outcome, call_id, result.error or result.outcome.value)
        assert result.model_served is not None
        return LLMResult(
            call_id=call_id,
            output=output,
            model_served=result.model_served,
            request_id=result.request_id,
            cost_usd=result.cost_usd,
        )

    def _record(self, call_id: UUID, result: CallResult, *, duration_ms: int) -> None:
        with self._conn.transaction():
            self._conn.execute(
                """
                insert into llm_call_results
                    (call_id, outcome, model_served, request_id, http_status, stop_reason,
                     refusal_category, response_blob, input_tokens, output_tokens,
                     cache_read_input_tokens, cache_creation_input_tokens, cost_usd, error,
                     finished_at, duration_ms)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    call_id,
                    result.outcome.value,
                    result.model_served,
                    result.request_id,
                    result.http_status,
                    result.stop_reason,
                    result.refusal_category,
                    result.response_blob,
                    result.input_tokens,
                    result.output_tokens,
                    result.cache_read_input_tokens,
                    result.cache_creation_input_tokens,
                    result.cost_usd,
                    result.error,
                    now_utc(),
                    duration_ms,
                ),
            )


class ClaudeClient(RecordingClient):
    def __init__(
        self, conn: psycopg.Connection, anthropic_client: anthropic.Anthropic, *, model: str
    ) -> None:
        super().__init__(conn, model=model)
        self._anthropic = anthropic_client

    def _schema(self, output_model: type[BaseModel]) -> dict[str, Any]:
        return anthropic.transform_schema(output_model)

    def _request(
        self,
        prompt: Prompt,
        user_content: str,
        schema: dict[str, Any],
        output_model: type[BaseModel],
        effort: Effort,
        max_tokens: int,
    ) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": [
                {"type": "text", "text": prompt.text, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": user_content}],
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }

    def _send[T: BaseModel](
        self, request: dict[str, Any], output_model: type[T]
    ) -> tuple[CallResult, T | None]:
        try:
            raw = self._anthropic.messages.with_raw_response.create(**request)
        except anthropic.APIStatusError as exc:
            blob = put_blob(self._conn, exc.response.content, "application/json")
            return CallResult(
                Outcome.API_ERROR,
                request_id=exc.request_id,
                http_status=exc.status_code,
                response_blob=blob,
                error=f"{type(exc).__name__}: {exc.message}",
            ), None
        except anthropic.APIConnectionError as exc:
            return CallResult(Outcome.CONNECTION_ERROR, error=f"{type(exc).__name__}: {exc}"), None

        body = raw.read()
        message: Message = raw.parse()
        usage = message.usage
        result = CallResult(
            Outcome.OK,
            model_served=message.model,
            request_id=raw.request_id,
            http_status=raw.status_code,
            stop_reason=message.stop_reason,
            response_blob=put_blob(self._conn, body, "application/json"),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        )
        result.cost_usd = cost_usd(
            message.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens or 0,
            cache_creation_input_tokens=result.cache_creation_input_tokens or 0,
        )

        # Check why the model stopped before reading any content.
        if message.stop_reason == "refusal":
            details = message.stop_details
            result.outcome = Outcome.REFUSAL
            result.refusal_category = details.category if details else None
            result.error = (details.explanation if details else None) or "model refused"
            return result, None
        if message.stop_reason == "max_tokens":
            result.outcome = Outcome.MAX_TOKENS
            result.error = "response cut off at max_tokens"
            return result, None

        texts = [block.text for block in message.content if block.type == "text"]
        if len(texts) != 1:
            result.outcome = Outcome.EMPTY
            result.error = f"expected exactly one text block, got {len(texts)}"
            return result, None
        try:
            output = output_model.model_validate_json(texts[0])
        except ValidationError as exc:
            result.outcome = Outcome.SCHEMA_INVALID
            result.error = str(exc)
            return result, None
        return result, output
