"""Meta's Model API (api.meta.ai) over plain HTTPS: OpenAI-compatible chat
completions for the Muse models, with no vendor SDK in between.

Differences from Claude that matter here, all recorded rather than hidden:
- Structured output is requested with `response_format` json_schema; the answer is
  still validated against the Pydantic model after it is stored.
- There is no effort setting or extended thinking. The step's effort is recorded with
  the call but not sent.
- The served model comes from the response; when it does not name one, the requested
  model is recorded as served.
- Token counts come from the response's `usage` when present. Cost uses the
  configured price (Settings.muse_price_*); without one it is unknown (None), never
  zero, and steps rely on their call caps.
- Retries (429, 5xx, transport errors) are a small loop here instead of SDK
  behaviour, honouring Settings.muse_max_retries with exponential backoff.
- Temperature 0, for answers that are as repeatable as the API allows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx2
import psycopg
from pydantic import BaseModel, ValidationError

from calipa.audit_trail.store import put_blob
from calipa.llm.client import CallResult, Effort, Outcome, RecordingClient
from calipa.llm.pricing import Price, cost_usd
from calipa.llm.prompts import Prompt

BASE_URL = "https://api.meta.ai/v1"

RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class MuseClient(RecordingClient):
    def __init__(
        self,
        conn: psycopg.Connection,
        http: httpx2.Client,
        *,
        api_key: str,
        model: str,
        price: Price | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(conn, model=model)
        self._http = http
        self._api_key = api_key
        self._price = price
        self._max_retries = max_retries
        self._sleep = sleep

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
            "max_tokens": max_tokens,
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

    def _post(self, request: dict[str, Any]) -> tuple[httpx2.Response | None, str, int | None]:
        """One POST with retries on transport errors and retryable statuses: the
        response, or the last error and its HTTP status (None after a transport
        error). Intermediate attempts are not stored, like SDK retries."""
        last_error = "no attempt made"
        last_status: int | None = None
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post("/chat/completions", headers=headers, json=request)
            except httpx2.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
            else:
                if response.status_code in RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}"
                    last_status = response.status_code
                else:
                    return response, "", None
            if attempt < self._max_retries:
                self._sleep(0.5 * 2**attempt)
        attempts = self._max_retries + 1
        return None, f"failed after {attempts} attempts: {last_error}", last_status

    def _send[T: BaseModel](
        self, request: dict[str, Any], output_model: type[T]
    ) -> tuple[CallResult, T | None]:
        response, last_error, last_status = self._post(request)
        if response is None:
            if last_status is None:
                return CallResult(Outcome.CONNECTION_ERROR, error=last_error), None
            return CallResult(Outcome.API_ERROR, http_status=last_status, error=last_error), None

        status, content = response.status_code, response.content
        content_type = response.headers.get("content-type", "application/json")
        header_request_id = response.headers.get("x-request-id")

        def api_error(error: str) -> tuple[CallResult, None]:
            return CallResult(
                Outcome.API_ERROR,
                request_id=header_request_id,
                http_status=status,
                response_blob=put_blob(self._conn, content, content_type),
                error=error,
            ), None

        if status != 200:
            return api_error(f"HTTP {status}")
        try:
            body = response.json()
        except ValueError as exc:
            return api_error(f"invalid JSON: {exc}")
        if not isinstance(body, dict):
            return api_error("expected a JSON object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return api_error("no choices in the response")
        choice = choices[0]
        if not isinstance(choice, dict):
            return api_error("the first choice is not an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            return api_error("the first choice has no message")
        usage = body.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        finish_reason = choice.get("finish_reason")
        result = CallResult(
            Outcome.OK,
            model_served=_str(body.get("model")) or self._model,
            request_id=_str(body.get("id")) or header_request_id,
            http_status=status,
            stop_reason=_str(finish_reason),
            response_blob=put_blob(self._conn, content, content_type),
            input_tokens=_count(usage.get("prompt_tokens")),
            output_tokens=_count(usage.get("completion_tokens")),
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        if result.input_tokens is not None and result.output_tokens is not None:
            result.cost_usd = cost_usd(
                result.model_served,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                price=self._price,
            )

        refusal = _str(message.get("refusal"))
        if refusal:
            result.outcome = Outcome.REFUSAL
            result.error = refusal
            return result, None
        if finish_reason == "content_filter":
            result.outcome = Outcome.REFUSAL
            result.refusal_category = "content_filter"
            result.error = "response filtered (content_filter)"
            return result, None
        if finish_reason == "length":
            result.outcome = Outcome.MAX_TOKENS
            result.error = "response cut off at max_tokens"
            return result, None
        text = _text(message.get("content"))
        if finish_reason == "tool_calls" or not text:
            result.outcome = Outcome.EMPTY
            result.error = f"no text answer (finish_reason {finish_reason!r})"
            return result, None
        try:
            output = output_model.model_validate_json(text)
        except ValidationError as exc:
            result.outcome = Outcome.SCHEMA_INVALID
            result.error = str(exc)
            return result, None
        return result, output


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _count(value: Any) -> int | None:
    """A reported token count: anything else is missing, never zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(content: Any) -> str | None:
    """Assistant content as plain text, whether a string or content blocks."""
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "".join(parts) or None
    return None
