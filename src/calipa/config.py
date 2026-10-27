"""Settings, read from the environment or a local `.env` (never committed)."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALIPA_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql:///calipa"

    # Which model provider runs P1/P2/P3: "meta" (Llama API) or "anthropic" (Claude).
    llm_provider: Literal["meta", "anthropic"] = "meta"
    # The Llama API model id. Required for "meta"; no default is guessed.
    llama_model: str | None = None
    # Provider keys keep the names the SDKs use. Read here too, so a key in `.env`
    # reaches the SDK (which only looks at the process environment itself).
    llama_api_key: SecretStr | None = Field(default=None, validation_alias="LLAMA_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    # USD per million tokens on the bank's Llama API contract. Unset: cost unknown.
    llama_price_input_per_mtok: Decimal | None = None
    llama_price_output_per_mtok: Decimal | None = None
    llama_timeout_seconds: float = 600.0
    llama_max_retries: int = 2

    claude_model: str = "claude-opus-5-5"
    claude_timeout_seconds: float = 600.0
    claude_max_retries: int = 2

    fcy_base_url: str = "https://fcy-payment-processor-yga8.vercel.app"
    fcy_username: SecretStr | None = None
    fcy_password: SecretStr | None = None

    # Assumed until the bank confirms the timezone of its day filters.
    bank_timezone: str = "Africa/Lagos"

    # How the API knows who is calling. "none" refuses every request until SSO is
    # wired in; "dev_header" trusts an X-Calipa-User header and is for development
    # only. There is deliberately no default that lets anyone in.
    api_identity: Literal["none", "dev_header"] = "none"
    # Browser origins allowed to call the API (the Next.js app), e.g.
    # ["http://localhost:3000"]. Empty: no cross-origin access.
    api_cors_origins: list[str] = []
    # Database connections the API may hold at once.
    api_pool_size: int = 10
    # Exception groups P2 analyses at the same time in one run (each uses its own
    # database connection and model client). 1 analyses them one after another.
    analysis_parallelism: int = 4
    # How often the event stream checks for new events.
    api_stream_poll_seconds: float = 1.0
    # Write-once store for audit-trail anchors (see audit_trail.anchor). Unset: the
    # worker does not anchor, and says so at start-up.
    anchor_dir: Path | None = None
    anchor_interval_seconds: float = 300.0
    # A run needing work with no event for this long counts as stalled (metrics).
    stall_after_seconds: float = 900.0
    # Development convenience: the worker applies migrations itself. In production
    # migrations run as a separate step as calipa_migrator, and the worker (as
    # calipa_app) only checks that none are pending.
    worker_auto_migrate: bool = False
    # How often the worker looks for runs to advance.
    worker_poll_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
