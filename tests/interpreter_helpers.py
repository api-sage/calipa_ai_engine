"""Builders for P1 outputs and a scripted stand-in for the Anthropic client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from calipa.interpreter.policy import PolicyContext
from calipa.interpreter.schema import InterpretedScope
from calipa.reference import (
    load_account_catalog,
    load_control_catalog,
    load_entitlements,
    load_tariff,
)
from tests.test_llm_client import FakeRaw, message

# 11:00 in Lagos on Monday 26 October 2026: the 25th has closed, the 26th has not.
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)


def policy_context(**overrides: Any) -> PolicyContext:
    values: dict[str, Any] = {
        "entitlements": load_entitlements(),
        "accounts": load_account_catalog(),
        "controls": load_control_catalog(),
        "tariff": load_tariff(),
        "timezone": "Africa/Lagos",
        "settle_lag": timedelta(hours=6),
    }
    return PolicyContext(**(values | overrides))


def period(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "relative",
        "start": None,
        "end": None,
        "relative_unit": "month",
        "relative_offset": -1,
        "relative_count": None,
        "calendar": "unspecified",
        "source_text": "last month",
    } | overrides


def scope_dict(**overrides: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "objective": "fee_vat_leg_completeness",
        "account_ids": [],
        "all_entitled_accounts": True,
        "period": period(),
        "currencies": [],
        "transaction_references": [],
        "control_ids": [],
        "unsupported_constraints": [],
        "assumptions": [],
        "ambiguities": [],
        "clarifying_question": None,
        "status_reason": None,
    } | overrides


def scope(**overrides: Any) -> InterpretedScope:
    return InterpretedScope.model_validate(scope_dict(**overrides))


def asking(question: str) -> dict[str, Any]:
    return scope_dict(
        status="needs_clarification",
        period=None,
        clarifying_question=question,
        ambiguities=[{"field": "period", "candidates": ["calendar", "fiscal"], "note": "Q3"}],
    )


class ScriptedAnthropic:
    """Answers each call with the next scripted reply: a P1 output dict, a full
    message dict (key "stop_reason" present), or an exception to raise."""

    def __init__(self, *replies: dict[str, Any] | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.messages = self
        self.with_raw_response = self

    def create(self, **request: Any) -> FakeRaw:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if "stop_reason" in reply:
            return FakeRaw(reply)
        return FakeRaw(message(text=json.dumps(reply)))

    def user_content(self, index: int) -> str:
        content = self.requests[index]["messages"][0]["content"]
        assert isinstance(content, str)
        return content
