"""Model access. Every call goes through a `StructuredLLM` (Claude or Llama) so that it
is logged; `make_llm` builds the configured one."""

from __future__ import annotations

from collections.abc import Callable

import anthropic
import llama_api_client
import psycopg

from calipa.config import Settings
from calipa.llm.client import ClaudeClient, StructuredLLM
from calipa.llm.llama import LlamaClient
from calipa.llm.pricing import Price, flat_price


def make_llm(conn: psycopg.Connection, settings: Settings) -> StructuredLLM:
    """The configured provider. API keys come from ANTHROPIC_API_KEY / LLAMA_API_KEY."""
    return llm_factory(settings)(conn)


def llm_factory(settings: Settings) -> Callable[[psycopg.Connection], StructuredLLM]:
    """Clients of the configured provider, one per connection, all sharing one SDK
    client (and so one HTTP connection pool): for steps that call the model from
    several threads, each recording through its own database connection."""
    if settings.llm_provider == "anthropic":
        claude = anthropic.Anthropic(
            timeout=settings.claude_timeout_seconds, max_retries=settings.claude_max_retries
        )
        return lambda conn: ClaudeClient(conn, claude, model=settings.claude_model)
    model = settings.llama_model
    if not model:
        raise ValueError("set CALIPA_LLAMA_MODEL to the Llama API model id to use")
    price = llama_price(settings)
    llama = llama_api_client.LlamaAPIClient(
        timeout=settings.llama_timeout_seconds, max_retries=settings.llama_max_retries
    )
    return lambda conn: LlamaClient(conn, llama, model=model, price=price)


def llama_price(settings: Settings) -> Price | None:
    """Both prices or neither: half a price would undercount every call."""
    inp, out = settings.llama_price_input_per_mtok, settings.llama_price_output_per_mtok
    if (inp is None) != (out is None):
        raise ValueError("set both CALIPA_LLAMA_PRICE_INPUT_PER_MTOK and _OUTPUT_PER_MTOK")
    return None if inp is None or out is None else flat_price(inp, out)
