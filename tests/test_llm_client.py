"""ClaudeClient against a fake Anthropic transport: every outcome must leave a
request row, a result row and the raw bytes in the audit trail."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import anthropic
import httpx2
import psycopg
import pytest
from anthropic.types import Message
from pydantic import BaseModel, ConfigDict

from calipa.audit_trail.store import get_blob
from calipa.llm.client import ClaudeClient, LLMCallFailed, Outcome
from calipa.llm.prompts import Prompt

PROMPT = Prompt(name="interpreter", version="2", text="You convert requests into scopes.")


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "needs_clarification"]
    accounts: list[str]


def message(
    *,
    text: str | None = '{"status": "ok", "accounts": ["TRANSIENT"]}',
    stop_reason: str = "end_turn",
    stop_details: dict[str, Any] | None = None,
    model: str = "claude-opus-5-5",
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "summary of reasoning", "signature": "sig"}
    ]
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_details": stop_details,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 800,
            "cache_read_input_tokens": 3000,
            "cache_creation_input_tokens": 0,
        },
    }


class FakeRaw:
    def __init__(self, body: dict[str, Any]) -> None:
        self._bytes = json.dumps(body).encode()
        self._body = body
        self.request_id = "req_test_123"
        self.status_code = 200

    def read(self) -> bytes:
        return self._bytes

    def parse(self) -> Message:
        return Message.model_validate(self._body)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: records the request, returns a canned reply."""

    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []
        self.messages = self
        self.with_raw_response = self

    def create(self, **request: Any) -> FakeRaw:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return FakeRaw(self.reply)


def call(conn: psycopg.Connection, fake: FakeAnthropic) -> Any:
    client = ClaudeClient(conn, fake, model="claude-opus-5-5")  # type: ignore[arg-type]
    return client.structured(
        step="interpret",
        prompt=PROMPT,
        user_content="<conversation>audit fees for last month</conversation>",
        output_model=Scope,
        effort="low",
    )


def rows(conn: psycopg.Connection, call_id: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    conn.row_factory = psycopg.rows.dict_row
    try:
        request = conn.execute("select * from llm_calls where call_id = %s", (call_id,)).fetchone()
        result = conn.execute(
            "select * from llm_call_results where call_id = %s", (call_id,)
        ).fetchone()
    finally:
        conn.row_factory = psycopg.rows.tuple_row
    assert request is not None and result is not None
    return request, result


def test_success_is_logged_with_raw_bytes_and_cost(conn: psycopg.Connection) -> None:
    fake = FakeAnthropic(message())
    result = call(conn, fake)

    assert result.output == Scope(status="ok", accounts=["TRANSIENT"])
    assert result.model_served == "claude-opus-5-5"
    # 1200*4 + 800*20 + 3000*0.20 = 21,400 per million
    assert result.cost_usd == Decimal("0.021400")

    request_row, result_row = rows(conn, result.call_id)
    assert request_row["prompt_sha256"] == PROMPT.sha256
    assert request_row["schema_name"] == "Scope"
    assert request_row["effort"] == "low"
    assert result_row["outcome"] == "ok"
    assert result_row["request_id"] == "req_test_123"
    assert result_row["model_served"] == "claude-opus-5-5"
    stored = json.loads(get_blob(conn, result_row["response_blob"]))
    assert stored["content"][0]["thinking"] == "summary of reasoning"
    sent = json.loads(get_blob(conn, request_row["request_blob"]))
    assert sent == json.loads(json.dumps(fake.requests[0]))


def test_request_shape(conn: psycopg.Connection) -> None:
    fake = FakeAnthropic(message())
    call(conn, fake)
    sent = fake.requests[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    for forbidden in ("temperature", "top_p", "top_k", "fallbacks", "tool_choice"):
        assert forbidden not in sent


@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        (
            message(
                text=None,
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": "cyber", "explanation": "no"},
            ),
            Outcome.REFUSAL,
        ),
        (message(text='{"status": "ok", "acc', stop_reason="max_tokens"), Outcome.MAX_TOKENS),
        (message(text=None), Outcome.EMPTY),
        (message(text='{"status": "ok", "accounts": [], "extra": 1}'), Outcome.SCHEMA_INVALID),
        (message(text='{"status": "maybe", "accounts": []}'), Outcome.SCHEMA_INVALID),
    ],
)
def test_bad_responses_are_logged_then_raised(
    conn: psycopg.Connection, reply: dict[str, Any], outcome: Outcome
) -> None:
    with pytest.raises(LLMCallFailed) as failure:
        call(conn, FakeAnthropic(reply))
    assert failure.value.outcome is outcome
    _, result_row = rows(conn, failure.value.call_id)
    assert result_row["outcome"] == outcome.value
    assert result_row["response_blob"] is not None  # the raw response is kept
    if outcome is Outcome.REFUSAL:
        assert result_row["refusal_category"] == "cyber"


def test_served_model_is_recorded_even_if_it_differs(conn: psycopg.Connection) -> None:
    result = call(conn, FakeAnthropic(message(model="claude-opus-4-8")))
    _, result_row = rows(conn, result.call_id)
    assert result_row["model_served"] == "claude-opus-4-8"
    assert result.cost_usd is None  # unknown price is never reported as zero


def _status_error(status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(
        status,
        request=request,
        headers={"request-id": "req_err"},
        json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
    )
    return anthropic.RateLimitError("slow down", response=response, body=None)


def test_api_errors_are_logged(conn: psycopg.Connection) -> None:
    with pytest.raises(LLMCallFailed) as failure:
        call(conn, FakeAnthropic(_status_error(429)))
    assert failure.value.outcome is Outcome.API_ERROR
    _, result_row = rows(conn, failure.value.call_id)
    assert result_row["http_status"] == 429
    assert result_row["request_id"] == "req_err"


def test_connection_errors_are_logged(conn: psycopg.Connection) -> None:
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    with pytest.raises(LLMCallFailed) as failure:
        call(conn, FakeAnthropic(error))
    assert failure.value.outcome is Outcome.CONNECTION_ERROR
    request_row, result_row = rows(conn, failure.value.call_id)
    assert result_row["outcome"] == "connection_error"
    assert request_row["request_blob"] is not None


def test_requires_autocommit_connection(migrated_url: str) -> None:
    with psycopg.connect(migrated_url) as plain:
        with pytest.raises(ValueError, match="autocommit"):
            ClaudeClient(plain, FakeAnthropic(message()), model="claude-opus-5-5")  # type: ignore[arg-type]
