"""Meta's Llama API (api.llama.com) through Meta's official SDK, `llama-api-client`.

Differences from Claude that matter here, all recorded rather than hidden:
- Structured output is requested with `response_format` json_schema; the answer is
  still validated against the Pydantic model after it is stored.
- There is no effort setting or extended thinking. The step's effort is recorded with
  the call but not sent.
- There is no refusal stop reason. A refusal arrives as text that fails the schema,
  so it is handled like any unusable answer (retry, then no AI analysis).
- The response does not name the model that served it; the requested model is
  recorded as served.
- Token counts come from the response's `metrics` when present. Cost uses the
  configured price (Settings.llama_price_*); without one it is unknown (None), never
  zero, and steps rely on their call caps.
- Temperature 0, for answers that are as repeatable as the API allows.
"""

from __future__ import annotations

from typing import Any

import llama_api_client
import psycopg
from pydantic import BaseModel, ValidationError

from calipa.audit_trail.store import put_blob
from calipa.llm.client import CallResult, Effort, Outcome, RecordingClient
from calipa.llm.pricing import Price, cost_usd
from calipa.llm.prompts import Prompt


class LlamaClient(RecordingClient):
    def __init__(
        self,
        conn: psycopg.Connection,
        llama: llama_api_client.LlamaAPIClient,
        *,
        model: str,
        price: Price | None = None,
    ) -> None:
        super().__init__(conn, model=model)
        self._llama = llama
        self._price = price

    def _schema(self, output_model: type[BaseModel]) -> dict[str, Any]:
        return output_model.model_json_schema()

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
            "max_completion_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt.text},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": output_model.__name__, "schema": schema},
            },
        }

    def _send[T: BaseModel](
        self, request: dict[str, Any], output_model: type[T]
    ) -> tuple[CallResult, T | None]:
        try:
            raw = self._llama.chat.completions.with_raw_response.create(**request)
        except llama_api_client.APIStatusError as exc:
            blob = put_blob(self._conn, exc.response.content, "application/json")
            return CallResult(
                Outcome.API_ERROR,
                request_id=exc.response.headers.get("x-request-id"),
                http_status=exc.status_code,
                response_blob=blob,
                error=f"{type(exc).__name__}: {exc.message}",
            ), None
        except llama_api_client.APIConnectionError as exc:
            return CallResult(Outcome.CONNECTION_ERROR, error=f"{type(exc).__name__}: {exc}"), None

        body = raw.read()
        response = raw.parse()
        metrics = {m.metric: m.value for m in response.metrics or []}
        input_tokens = _tokens(metrics, "prompt")
        output_tokens = _tokens(metrics, "completion")
        message = response.completion_message
        result = CallResult(
            Outcome.OK,
            model_served=self._model,  # the response does not say
            request_id=response.id or raw.headers.get("x-request-id"),
            http_status=raw.status_code,
            stop_reason=message.stop_reason,
            response_blob=put_blob(self._conn, body, "application/json"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        if input_tokens is not None and output_tokens is not None:
            result.cost_usd = cost_usd(
                self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                price=self._price,
            )

        if message.stop_reason == "length":
            result.outcome = Outcome.MAX_TOKENS
            result.error = "response cut off at max_completion_tokens"
            return result, None
        content = message.content
        text = content if isinstance(content, str) else (content.text if content else None)
        if message.stop_reason == "tool_calls" or not text:
            result.outcome = Outcome.EMPTY
            result.error = f"no text answer (stop_reason {message.stop_reason!r})"
            return result, None
        try:
            output = output_model.model_validate_json(text)
        except ValidationError as exc:
            result.outcome = Outcome.SCHEMA_INVALID
            result.error = str(exc)
            return result, None
        return result, output


def _tokens(metrics: dict[str, float], kind: str) -> int | None:
    """The token count for "prompt" or "completion", if the response reports exactly one."""
    found = [v for name, v in metrics.items() if kind in name and "token" in name]
    return int(found[0]) if len(found) == 1 else None
