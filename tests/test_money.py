from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest

from calipa.money import (
    Currency,
    CurrencyMismatchError,
    Money,
    MoneyError,
    canonical_decimal,
    loads_decimal,
    to_decimal,
)


def test_floats_are_rejected_everywhere() -> None:
    with pytest.raises(MoneyError):
        to_decimal(13.58)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        Money(13.58, Currency.USD)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        Money.of(True, "USD")  # type: ignore[arg-type]


def test_non_finite_is_rejected() -> None:
    for bad in ("NaN", "Infinity", "-inf"):
        with pytest.raises(MoneyError):
            Money.of(bad, "USD")
    with pytest.raises(MoneyError):
        loads_decimal('{"amount": NaN}')


def test_api_json_keeps_every_digit() -> None:
    # Real get-charges payload: more digits than a binary double can hold.
    data = loads_decimal(
        '{"amount":10,"currency":"GBP","charge":1.4730266,'
        '"vat":0.7500000038011815,"sumTotal":12.2230266038011815}'
    )
    assert data["vat"] == Decimal("0.7500000038011815")
    assert data["sumTotal"] == Decimal("12.2230266038011815")
    assert data["amount"] == 10 and isinstance(data["amount"], int)
    assert Decimal(float("12.2230266038011815")) != data["sumTotal"]


def test_arithmetic_requires_same_currency() -> None:
    gbp = Money.of("12.22", "GBP")
    assert gbp - Money.of("10", "GBP") == Money.of("2.22", "GBP")
    with pytest.raises(CurrencyMismatchError):
        _ = gbp + Money.of("13.58", "USD")


def test_quantize_needs_an_explicit_rounding_mode() -> None:
    vat = Money.of("1.0185", "USD")
    assert vat.quantize(ROUND_HALF_UP).amount == Decimal("1.02")
    assert Money.of("0.125", "USD").quantize(ROUND_HALF_EVEN).amount == Decimal("0.12")
    assert Money.of("0.125", "USD").quantize(ROUND_HALF_UP).amount == Decimal("0.13")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", "2"),
        ("2.00", "2"),
        ("13.580", "13.58"),
        ("1E+2", "100"),
        ("0.000", "0"),
        ("-0", "0"),
        ("1338.380059", "1338.380059"),
        ("0.00074717", "0.00074717"),
    ],
)
def test_canonical_decimal(value: str, expected: str) -> None:
    assert canonical_decimal(Decimal(value)) == expected


def test_money_canonical_form() -> None:
    assert Money.of("2.00", "USD").canonical() == {"amount": "2", "currency": "USD"}
    assert str(Money.of("12.220", "GBP")) == "GBP 12.22"
