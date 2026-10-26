"""LlamaClient through Meta's real SDK over a mocked HTTP transport: every outcome
must leave a request row, a result row and the raw bytes, like ClaudeClient."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import llama_api_client
import psycopg
import pytest
from pydantic import SecretStr

from calipa.audit_trail.store import get_blob
from calipa.config import Settings
from calipa.llm import llama_price, make_llm
from calipa.llm.client import ClaudeClient, LLMCallFailed, LLMResult, Outcome
from calipa.llm.llama import LlamaClient
from calipa.llm.pricing import Price, flat_price
from calipa.orchestrator.lifecycle import RunState as S
from tests.test_analyse_step import GOOD, analysing_run, findings, orchestrate
from tests.test_llm_client import PROMPT, Scope, rows

MODEL = "test-llama-model"

Handler = Callable[[httpx.Request], httpx.Response]


def reply(
    text: Any = '{"status": "ok", "accounts": ["TRANSIENT"]}',
    *,
    stop_reason: str = "stop",
    metrics: list[dict[str, Any]] | None = None,
    plain: bool = False,
) -> Handler:
    content = {"type": "text", "text": text} if isinstance(text, str) and not plain else text
    body = {
        "id": "chatcmpl-test",
        "completion_message": {"role": "assistant", "content": content, "stop_reason": stop_reason},
        "metrics": metrics
        if metrics is not None
        else [
            {"metric": "num_prompt_tokens", "value": 1200, "unit": "tokens"},
            {"metric": "num_completion_tokens", "value": 300, "unit": "tokens"},
        ],
    }
    return lambda request: httpx.Response(200, json=body)


class Recorder:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def body(self, index: int = 0) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[index].content)
        return parsed


def client(
    conn: psycopg.Connection, handler: Handler, price: Price | None = None
) -> tuple[LlamaClient, Recorder]:
    recorder = Recorder(handler)
    sdk = llama_api_client.LlamaAPIClient(
        api_key="test-key",
        base_url="https://llama.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(recorder)),
    )
    return LlamaClient(conn, sdk, model=MODEL, price=price), recorder


def call(llm: LlamaClient) -> LLMResult[Scope]:
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
    assert result.model_served == MODEL
    assert result.cost_usd is None  # no Llama price table: unknown, not zero

    sent = recorder.body()
    assert recorder.requests[0].url.path == "/v1/chat/completions"
    assert recorder.requests[0].headers["authorization"] == "Bearer test-key"
    assert sent["model"] == MODEL and sent["max_completion_tokens"] == 4000
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
    assert stored["completion_message"]["stop_reason"] == "stop"


def test_a_configured_price_gives_a_cost(conn: psycopg.Connection) -> None:
    llm, _ = client(conn, reply(), price=flat_price(Decimal("0.5"), Decimal("2")))
    result = call(llm)
    # 1200 input at 0.5 and 300 output at 2 per million tokens
    assert result.cost_usd == Decimal("0.0012")
    _, result_row = rows(conn, result.call_id)
    assert result_row["cost_usd"] == Decimal("0.0012")


def test_a_price_needs_both_halves(conn: psycopg.Connection) -> None:
    base = {"_env_file": None, "llama_model": MODEL}
    half = Settings(llama_price_input_per_mtok=Decimal("1"), **base)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="set both"):
        llama_price(half)
    both = Settings(
        llama_price_input_per_mtok=Decimal("1"),
        llama_price_output_per_mtok=Decimal("3"),
        **base,  # type: ignore[arg-type]
    )
    assert llama_price(both) == flat_price(Decimal("1"), Decimal("3"))
    assert llama_price(Settings(**base)) is None  # type: ignore[arg-type]


def test_empty_settings_in_the_environment_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALIPA_LLAMA_PRICE_INPUT_PER_MTOK", "")
    monkeypatch.setenv("CALIPA_FCY_USERNAME", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.llama_price_input_per_mtok is None and settings.fcy_username is None


def test_plain_string_content_and_missing_metrics(conn: psycopg.Connection) -> None:
    llm, _ = client(
        conn, reply({"type": "text", "text": '{"status": "ok", "accounts": []}'}, metrics=[])
    )
    result = call(llm)
    _, result_row = rows(conn, result.call_id)
    assert result.output.accounts == []
    assert result_row["input_tokens"] is None  # not reported, so not invented


@pytest.mark.parametrize(
    ("handler", "outcome"),
    [
        (reply(stop_reason="length"), Outcome.MAX_TOKENS),
        # Llama has no refusal stop reason: a refusal is text that fails the schema.
        (reply("I can't help with that."), Outcome.SCHEMA_INVALID),
        (reply('{"status": "maybe", "accounts": []}'), Outcome.SCHEMA_INVALID),
        (reply(None), Outcome.EMPTY),
        (reply(stop_reason="tool_calls"), Outcome.EMPTY),
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
        lambda request: httpx.Response(
            429, json={"error": "slow down"}, headers={"x-request-id": "req-429"}
        ),
    )
    assert error.outcome is Outcome.API_ERROR
    assert (result_row["http_status"], result_row["request_id"]) == (429, "req-429")
    assert json.loads(get_blob(conn, result_row["response_blob"])) == {"error": "slow down"}


def test_connection_errors_are_recorded(conn: psycopg.Connection) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    error, result_row = failure(conn, down)
    assert error.outcome is Outcome.CONNECTION_ERROR
    assert result_row["response_blob"] is None


def test_the_factory_builds_the_configured_provider(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLAMA_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    base = {"fcy_username": SecretStr("u"), "_env_file": None}
    with pytest.raises(ValueError, match="CALIPA_LLAMA_MODEL"):
        make_llm(conn, Settings(llm_provider="meta", **base))  # type: ignore[arg-type]
    assert isinstance(
        make_llm(conn, Settings(llm_provider="meta", llama_model=MODEL, **base)),  # type: ignore[arg-type]
        LlamaClient,
    )
    assert isinstance(
        make_llm(conn, Settings(llm_provider="anthropic", **base)),  # type: ignore[arg-type]
        ClaudeClient,
    )


def test_the_analysis_step_runs_on_llama(conn: psycopg.Connection) -> None:
    llm, recorder = client(conn, reply(json.dumps(GOOD)))
    run_id = analysing_run(conn)
    assert orchestrate(conn, llm, run_id) is S.REPORTING
    [finding] = findings(conn, run_id)
    assert finding.status == "analysed" and finding.analysis is not None
    assert len(finding.analysis.hypotheses) == 3
    assert recorder.body()["response_format"]["json_schema"]["name"] == "ExceptionAnalysis"
