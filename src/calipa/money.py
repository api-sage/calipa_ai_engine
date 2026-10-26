"""Money handling. Floats are never accepted anywhere money is represented.

The FCY Payment Processor API returns amounts as JSON floats (and some fields with
more digits than a double can hold), so all JSON from it must be parsed with
`loads_decimal`, which turns every JSON number with a fraction into a Decimal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    NGN = "NGN"

    @property
    def minor_units(self) -> int:
        return _MINOR_UNITS[self]


# ISO 4217 decimal places.
_MINOR_UNITS: dict[Currency, int] = {
    Currency.USD: 2,
    Currency.EUR: 2,
    Currency.GBP: 2,
    Currency.NGN: 2,
}


class MoneyError(ValueError):
    pass


class CurrencyMismatchError(MoneyError):
    pass


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Convert an exact value to Decimal. Floats are rejected, not converted."""
    if isinstance(value, bool) or isinstance(value, float):
        raise MoneyError(f"refusing to build a Decimal from {type(value).__name__}: {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal number: {value!r}") from exc
    else:
        raise MoneyError(f"unsupported amount type {type(value).__name__}")
    if not result.is_finite():
        raise MoneyError(f"amount must be finite, got {value!r}")
    return result


def canonical_decimal(value: Decimal) -> str:
    """One string per numeric value: no exponent, no trailing zeros ("2.00" -> "2")."""
    if not value.is_finite():
        raise MoneyError(f"amount must be finite, got {value!r}")
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise MoneyError(f"Money.amount must be Decimal, got {type(self.amount).__name__}")
        if not self.amount.is_finite():
            raise MoneyError(f"Money.amount must be finite, got {self.amount!r}")
        if not isinstance(self.currency, Currency):
            raise MoneyError(f"Money.currency must be Currency, got {self.currency!r}")

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: Currency | str) -> Money:
        return cls(to_decimal(amount), Currency(currency))

    def _check_same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise CurrencyMismatchError(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def quantize(self, rounding: str) -> Money:
        """Round to the currency's minor unit. The rounding mode is always explicit,
        because the bank's rounding rule is not documented yet."""
        exponent = Decimal(1).scaleb(-self.currency.minor_units)
        return Money(self.amount.quantize(exponent, rounding=rounding), self.currency)

    def canonical(self) -> dict[str, str]:
        return {"amount": canonical_decimal(self.amount), "currency": self.currency.value}

    def __str__(self) -> str:
        return f"{self.currency.value} {canonical_decimal(self.amount)}"


def loads_decimal(data: str | bytes) -> Any:
    """json.loads with every fractional number parsed as Decimal and no float anywhere."""
    return json.loads(data, parse_float=Decimal, parse_constant=_reject_constant)


def _reject_constant(name: str) -> Any:
    raise MoneyError(f"non-finite JSON number {name} is not allowed")
