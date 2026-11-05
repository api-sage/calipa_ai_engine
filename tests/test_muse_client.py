"""MuseClient against a fake Meta Model API transport: every outcome must leave a
request row, a result row and the raw bytes, like the other providers."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx2
import psycopg
import pytest

from calipa.audit_trail.store import get_blob
from calipa.config import Settings
from calipa.llm import make_llm, muse_price
from calipa.llm.client import LLMCallFailed, LLMResult, Outcome
from calipa.llm.muse import MuseClient
from calipa.llm.pricing import Price, flat_price
from calipa.orchestrator.lifecycle import RunState as S
from tests.test_analyse_step import GOOD, analysing_run, findings, orchestrate
from tests.test_llm_client import PROMPT, Scope, rows

MODEL = "muse-spark-1.3"
SERVED = "muse-spark-1.3-2026-10-01"

Handler = Callable[[httpx2.Request], httpx2.Response]


def reply(
    text: Any = '{"status": "ok", "accounts": ["TRANSIENT"]}',
    *,
    finish_reason: str | None = "stop",
    usage: dict[str, Any] | None = None,
    served: str | None = SERVED,
    refusal: str | None = None,
) -> Handler:
    resolved_usage: dict[str, Any] = {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
    }
    if usage is not None:
        resolved_usage = usage
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if refusal is not None:
        message["refusal"] = refusal
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": resolved_usage,
    }
    if served is not None:
        body["model"] = served
    return lambda request: httpx2.Response(200, json=body)


class Recorder:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handler(request)

    def body(self, index: int = 0) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[index].content)
        return parsed


def _no_sleep(_: float) -> None:
    pass


def client(
    conn: psycopg.Connection,
    handler: Handler,
    price: Price | None = None,
    *,
    max_retries: int = 0,
    sleep: Callable[[float], None] = _no_sleep,
) -> tuple[MuseClient, Recorder]:
    recorder = Recorder(handler)
    http = httpx2.Client(
        base_url="https://muse.test/v1",
        transport=httpx2.MockTransport(recorder),
    )
    llm = MuseClient(
        conn,
        http,
        api_key="test-key",
        model=MODEL,
        price=price,
        max_retries=max_retries,
        sleep=sleep,
    )
    return llm, recorder


def call(llm: MuseClient) -> LLMResult[Scope]:
    return llm.structured(
        step="interpret",
        prompt=PROMPT,
        user_content="<conversation>audit fees</conversation>",
        output_model=Scope,
        effort="low",
        max_tokens=4000,
    )


def failure(conn: psycopg.Connection, handler: Handler) -> tuple[LLMCallFailed, dict[str, Any]]:
    llm, _ = client(conn, handler)
    with pytest.raises(LLMCallFailed) as raised:
        call(llm)
    _, result_row = rows(conn, raised.value.call_id)
    return raised.value, result_row


def test_success_is_logged_with_raw_bytes(conn: psycopg.Connection) -> None:
    llm, recorder = client(conn, reply())
    result = call(llm)
    assert result.output == Scope(status="ok", accounts=["TRANSIENT"])
    assert result.model_served == SERVED
    assert result.request_id == "chatcmpl-test"
    assert result.cost_usd is None  # no Muse price table: unknown, not zero

    sent = recorder.body()
    assert recorder.requests[0].url.path == "/v1/chat/completions"
    assert recorder.requests[0].headers["authorization"] == "Bearer test-key"
    assert sent["model"] == MODEL and sent["max_tokens"] == 4000
    assert sent["temperature"] == 0
    assert sent["messages"] == [
        {"role": "system", "content": PROMPT.text},
        {"role": "user", "content": "<conversation>audit fees</conversation>"},
    ]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["name"] == "Scope"
    assert sent["response_format"]["json_schema"]["schema"] == Scope.model_json_schema()

    request_row, result_row = rows(conn, result.call_id)
    assert (request_row["model_requested"], request_row["effort"]) == (MODEL, "low")
    assert json.loads(get_blob(conn, request_row["request_blob"])) == sent
    assert (result_row["outcome"], result_row["request_id"]) == ("ok", "chatcmpl-test")
    assert (result_row["input_tokens"], result_row["output_tokens"]) == (1200, 300)
    assert result_row["cost_usd"] is None
    stored = json.loads(get_blob(conn, result_row["response_blob"]))
    assert stored["choices"][0]["finish_reason"] == "stop"


def test_a_configured_price_gives_a_cost(conn: psycopg.Connection) -> None:
    llm, _ = client(conn, reply(), price=flat_price(Decimal("0.5"), Decimal("2")))
    result = call(llm)
    # 1200 input at 0.5 and 300 output at 2 per million tokens
    assert result.cost_usd == Decimal("0.0012")
    _, result_row = rows(conn, result.call_id)
    assert result_row["cost_usd"] == Decimal("0.0012")


def test_a_price_needs_both_halves() -> None:
    base = {"_env_file": None, "muse_model": MODEL}
    half = Settings(muse_price_input_per_mtok=Decimal("1"), **base)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="set both"):
        muse_price(half)
    both = Settings(
        muse_price_input_per_mtok=Decimal("1"),
        muse_price_output_per_mtok=Decimal("3"),
        **base,  # type: ignore[arg-type]
    )
    assert muse_price(both) == flat_price(Decimal("1"), Decimal("3"))
    assert muse_price(Settings(**base)) is None  # type: ignore[arg-type]


def test_missing_usage_and_model_fall_back(conn: psycopg.Connection) -> None:
    llm, _ = client(conn, reply(usage={}, served=None))
    result = call(llm)
    _, result_row = rows(conn, result.call_id)
    assert result.model_served == MODEL  # not reported, so the requested model
    assert result_row["input_tokens"] is None  # not reported, so not invented


def test_content_blocks_are_joined(conn: psycopg.Connection) -> None:
    text = '{"status": "ok", "accounts": []}'
    blocks = [{"type": "text", "text": text[:20]}, {"type": "text", "text": text[20:]}]
    llm, _ = client(conn, reply(blocks))
    result = call(llm)
    assert result.output.accounts == []


@pytest.mark.parametrize(
    ("handler", "outcome"),
    [
        (reply(finish_reason="length"), Outcome.MAX_TOKENS),
        (reply("I can't help with that."), Outcome.SCHEMA_INVALID),
        (reply('{"status": "maybe", "accounts": []}'), Outcome.SCHEMA_INVALID),
        (reply(None), Outcome.EMPTY),
        (reply(finish_reason="tool_calls"), Outcome.EMPTY),
        (reply(finish_reason="content_filter", text=None), Outcome.REFUSAL),
        (reply(refusal="I can't help with that.", text=None), Outcome.REFUSAL),
    ],
)
def test_unusable_answers_are_recorded(
    conn: psycopg.Connection, handler: Handler, outcome: Outcome
) -> None:
    error, result_row = failure(conn, handler)
    assert error.outcome is outcome
    assert result_row["outcome"] == outcome.value and result_row["response_blob"]


def test_api_errors_keep_the_error_body(conn: psycopg.Connection) -> None:
    error, result_row = failure(
        conn,
        lambda request: httpx2.Response(
            401, json={"error": "bad key"}, headers={"x-request-id": "req-401"}
        ),
    )
    assert error.outcome is Outcome.API_ERROR
    assert (result_row["http_status"], result_row["request_id"]) == (401, "req-401")
    assert json.loads(get_blob(conn, result_row["response_blob"])) == {"error": "bad key"}


def test_connection_errors_are_recorded(conn: psycopg.Connection) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    error, result_row = failure(conn, down)
    assert error.outcome is Outcome.CONNECTION_ERROR
    assert result_row["response_blob"] is None


def test_retryable_statuses_are_retried_then_succeed(conn: psycopg.Connection) -> None:
    attempts = 0

    def flaky(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(503, json={"error": "busy"})
        return reply()(request)

    sleeps: list[float] = []
    llm, recorder = client(conn, flaky, max_retries=2, sleep=sleeps.append)
    result = call(llm)
    assert result.output == Scope(status="ok", accounts=["TRANSIENT"])
    assert attempts == 2 and len(recorder.requests) == 2
    assert sleeps == [0.5]


def test_retries_used_up_on_errors_are_recorded(conn: psycopg.Connection) -> None:
    sleeps: list[float] = []
    llm, recorder = client(
        conn,
        lambda request: httpx2.Response(503, json={"error": "busy"}),
        max_retries=1,
        sleep=sleeps.append,
    )
    with pytest.raises(LLMCallFailed) as raised:
        call(llm)
    assert raised.value.outcome is Outcome.API_ERROR
    assert len(recorder.requests) == 2 and sleeps == [0.5]
    _, result_row = rows(conn, raised.value.call_id)
    assert result_row["outcome"] == "api_error" and result_row["http_status"] == 503
    assert result_row["response_blob"] is None


def test_retries_used_up_on_transport_errors_are_recorded(conn: psycopg.Connection) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    llm, recorder = client(conn, down, max_retries=1)
    with pytest.raises(LLMCallFailed) as raised:
        call(llm)
    assert raised.value.outcome is Outcome.CONNECTION_ERROR
    assert len(recorder.requests) == 2


def test_the_factory_builds_muse_from_configuration(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    base = {"_env_file": None}
    with pytest.raises(ValueError, match="CALIPA_MUSE_MODEL"):
        make_llm(conn, Settings(llm_provider="muse", **base))  # type: ignore[arg-type]
    with_model = {"_env_file": None, "muse_model": MODEL}
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        make_llm(conn, Settings(llm_provider="muse", **with_model))  # type: ignore[arg-type]
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    llm = make_llm(conn, Settings(llm_provider="muse", **with_model))  # type: ignore[arg-type]
    assert isinstance(llm, MuseClient)


def test_the_analysis_step_runs_on_muse(conn: psycopg.Connection) -> None:
    llm, recorder = client(conn, reply(json.dumps(GOOD)))
    run_id = analysing_run(conn)
    assert orchestrate(conn, llm, run_id) is S.REPORTING
    [finding] = findings(conn, run_id)
    assert finding.status == "analysed" and finding.analysis is not None
    assert len(finding.analysis.hypotheses) == 3
    assert recorder.body()["response_format"]["json_schema"]["name"] == "ExceptionAnalysis"
