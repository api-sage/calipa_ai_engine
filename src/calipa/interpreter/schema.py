"""P1's output schema. Claude fills it; V1 (`validate.py`) checks it against the
catalogs and the conversation before code acts on it."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from calipa.money import Currency
from calipa.reference import AccountRole, Objective

MAX_LIST = 20
MAX_TEXT = 1_000


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PeriodSpec(_Out):
    """What the auditor said about the period. Code turns it into dates."""

    kind: Literal["absolute", "relative"]
    start: date | None  # absolute only, as stated
    end: date | None
    relative_unit: Literal["day", "week", "month", "quarter", "year"] | None
    relative_offset: int | None  # 0 = current, -1 = previous
    relative_count: int | None  # how many units, ending at the offset; None = 1
    calendar: Literal["calendar", "fiscal", "unspecified"]
    source_text: str = Field(max_length=MAX_TEXT)


class Assumption(_Out):
    field: str = Field(max_length=100)
    text: str = Field(max_length=MAX_TEXT)


class Ambiguity(_Out):
    field: Literal["accounts", "period", "objective", "controls", "currency", "other"]
    candidates: list[str] = Field(max_length=MAX_LIST)
    note: str = Field(max_length=MAX_TEXT)


class InterpretedScope(_Out):
    status: Literal["ok", "needs_clarification", "out_of_scope", "unsupported"]
    objective: Objective | None
    account_ids: list[AccountRole] = Field(max_length=MAX_LIST)
    all_entitled_accounts: bool
    period: PeriodSpec | None
    currencies: list[Currency] = Field(max_length=MAX_LIST)  # empty = all
    transaction_references: list[str] = Field(max_length=MAX_LIST)
    control_ids: list[str] = Field(max_length=MAX_LIST)
    unsupported_constraints: list[str] = Field(max_length=MAX_LIST)
    assumptions: list[Assumption] = Field(max_length=MAX_LIST)
    ambiguities: list[Ambiguity] = Field(max_length=MAX_LIST)
    clarifying_question: str | None = Field(max_length=MAX_TEXT)
    status_reason: str | None = Field(max_length=MAX_TEXT)
