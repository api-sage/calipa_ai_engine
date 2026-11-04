"""Model access. Every call goes through a `StructuredLLM` (Claude, Llama or Muse)
so that it is logged; `make_llm` builds the configured one."""

from __future__ import annotations

from collections.abc import Callable

import anthropic
import httpx2
import llama_api_client
import psycopg
from pydantic import SecretStr

from calipa.config import Settings
from calipa.llm.client import ClaudeClient, StructuredLLM
from calipa.llm.llama import LlamaClient
from calipa.llm.muse import BASE_URL as MUSE_BASE_URL, MuseClient
from calipa.llm.pricing import Price, flat_price


def make_llm(conn: psycopg.Connection, settings: Settings) -> StructuredLLM:
    """The configured provider. API keys come from ANTHROPIC_API_KEY / LLAMA_API_KEY /
    MODEL_API_KEY, in the environment or `.env`."""
    return llm_factory(settings)(conn)


def llm_factory(settings: Settings) -> Callable[[psycopg.Connection], StructuredLLM]:
    """Clients of the configured provider, one per connection, all sharing one
    underlying client (and so one HTTP connection pool): for steps that call the
    model from several threads, each recording through its own database connection."""
    if settings.llm_provider == "anthropic":
        claude = anthropic.Anthropic(
            api_key=_secret(settings.anthropic_api_key),
            timeout=settings.claude_timeout_seconds,
            max_retries=settings.claude_max_retries,
        )
        return lambda conn: ClaudeClient(conn, claude, model=settings.claude_model)
    if settings.llm_provider == "muse":
        model = settings.muse_model
        if not model:
            raise ValueError("set CALIPA_MUSE_MODEL to the Muse model id to use")
        api_key = _secret(settings.muse_api_key)
        if not api_key:
            raise ValueError("set MODEL_API_KEY to a Meta Model API key")
        price = muse_price(settings)
        http = httpx2.Client(base_url=MUSE_BASE_URL, timeout=settings.muse_timeout_seconds)

        def build(conn: psycopg.Connection) -> MuseClient:
            return MuseClient(
                conn,
                http,
                api_key=api_key,
                model=model,
                price=price,
                max_retries=settings.muse_max_retries,
            )

        return build
    model = settings.llama_model
    if not model:
        raise ValueError("set CALIPA_LLAMA_MODEL to the Llama API model id to use")
    price = llama_price(settings)
    llama = llama_api_client.LlamaAPIClient(
        api_key=_secret(settings.llama_api_key),
        timeout=settings.llama_timeout_seconds,
        max_retries=settings.llama_max_retries,
    )
    return lambda conn: LlamaClient(conn, llama, model=model, price=price)


def llama_price(settings: Settings) -> Price | None:
    """Both prices or neither: half a price would undercount every call."""
    inp, out = settings.llama_price_input_per_mtok, settings.llama_price_output_per_mtok
    if (inp is None) != (out is None):
        raise ValueError("set both CALIPA_LLAMA_PRICE_INPUT_PER_MTOK and _OUTPUT_PER_MTOK")
    return None if inp is None or out is None else flat_price(inp, out)


def muse_price(settings: Settings) -> Price | None:
    """Both prices or neither: half a price would undercount every call."""
    inp, out = settings.muse_price_input_per_mtok, settings.muse_price_output_per_mtok
    if (inp is None) != (out is None):
        raise ValueError("set both CALIPA_MUSE_PRICE_INPUT_PER_MTOK and _OUTPUT_PER_MTOK")
    return None if inp is None or out is None else flat_price(inp, out)


def _secret(value: SecretStr | None) -> str | None:
    """None lets the SDK fall back to its own environment variable."""
    return None if value is None else value.get_secret_value()
