"""Draft contract with the Reconciliation Engine team (v0). To be agreed with them;
until then this is what the Intelligence Layer builds against.

The engine is authoritative for classification and every number in an exception.
P2 explains exceptions; it never re-derives them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from calipa.gateway.fcy import GLEntry
from calipa.money import Currency, Money, to_decimal

CONTRACT_VERSION = "0.1-draft"

Dec = Annotated[Decimal, BeforeValidator(to_decimal)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Amount(_Model):
    amount: Dec
    currency: Currency

    @classmethod
    def of(cls, money: Money) -> Amount:
        return cls(amount=money.amount, currency=money.currency)

    def money(self) -> Money:
        return Money(self.amount, self.currency)


class RateQuote(_Model):
    from_currency: Currency
    to_currency: Currency
    rate_date: date
    rate: Dec = Field(gt=0)
    source: str  # e.g. "fx_rate_snapshot:<uuid>"


class ReconciliationRequest(_Model):
    contract_version: str = CONTRACT_VERSION
    request_id: UUID
    snapshot_sha256: str
    bank_timezone: str
    entries: list[GLEntry]
    account_roles: dict[str, str]  # account number -> role
    account_catalog_sha256: str
    tariff_sha256: str
    rates: list[RateQuote]


class ExceptionType(StrEnum):
    STRUCTURE_INCOMPLETE = "STRUCTURE_INCOMPLETE"
    FEE_LEG_MISSING = "FEE_LEG_MISSING"
    VAT_LEG_MISSING = "VAT_LEG_MISSING"
    FEE_AMOUNT_MISMATCH = "FEE_AMOUNT_MISMATCH"
    VAT_AMOUNT_MISMATCH = "VAT_AMOUNT_MISMATCH"
    FX_CONVERSION_MISMATCH = "FX_CONVERSION_MISMATCH"
    ROUNDING_DIFFERENCE = "ROUNDING_DIFFERENCE"
    TRANSIENT_RESIDUAL = "TRANSIENT_RESIDUAL"
    FEE_VAT_IN_BOTH_CURRENCIES = "FEE_VAT_IN_BOTH_CURRENCIES"
    ENTRY_DIRECTION_INCONSISTENT = "ENTRY_DIRECTION_INCONSISTENT"
    DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
    DUPLICATE_INSTRUCTION_CANDIDATE = "DUPLICATE_INSTRUCTION_CANDIDATE"
    RATE_UNAVAILABLE = "RATE_UNAVAILABLE"
    RATE_AMBIGUOUS = "RATE_AMBIGUOUS"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EngineException(_Model):
    exception_id: str  # deterministic: same input, same id
    type: ExceptionType
    severity: Severity
    rule_id: str
    transfer_id: UUID
    related_transfer_ids: list[UUID] = []
    record_ids: list[UUID]
    expected: Amount | None = None
    actual: Amount | None = None
    difference: Amount | None = None
    details: dict[str, str] = {}  # machine-readable extras, values as strings


class CheckResult(_Model):
    rule_id: str
    transfer_id: UUID
    passed: bool


class ControlTotal(_Model):
    account_role: str  # role, or "OTHER" for customer/beneficiary accounts
    currency: Currency
    debits_from_transient: Amount
    credits_to_transient: Amount
    entry_count: int


class ReconciliationResult(_Model):
    contract_version: str
    engine_name: str
    engine_version: str
    ruleset_version: str
    request_id: UUID
    snapshot_sha256: str
    transfers_checked: int
    control_totals: list[ControlTotal]
    checks: list[CheckResult]
    exceptions: list[EngineException]
    limitations: list[str]


class ReconciliationEngine(Protocol):
    def reconcile(self, request: ReconciliationRequest) -> ReconciliationResult: ...
