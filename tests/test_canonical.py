from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from calipa.canonical import CanonicalizationError, canonical_json, hash_value
from calipa.money import Money


def test_key_order_and_whitespace_do_not_change_bytes() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})
    assert canonical_json({"a": 1}) == b'{"a":1}'


def test_equal_decimals_hash_equal() -> None:
    assert hash_value({"x": Decimal("2.00")}) == hash_value({"x": Decimal("2")})


def test_datetimes_are_utc_and_must_be_aware() -> None:
    wat = timezone(timedelta(hours=1))
    a = datetime(2026, 10, 25, 21, 36, 31, tzinfo=wat)
    b = datetime(2026, 10, 25, 20, 36, 31, tzinfo=UTC)
    assert canonical_json(a) == canonical_json(b) == b'"2026-10-25T20:36:31Z"'
    with pytest.raises(CanonicalizationError):
        canonical_json(datetime(2026, 10, 25))


def test_floats_and_odd_keys_are_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({"amount": 13.58})
    with pytest.raises(CanonicalizationError):
        canonical_json({1: "x"})


def test_rich_types() -> None:
    value = {
        "id": UUID("295acb22-8b4b-4b33-85de-02103c494399"),
        "amount": Money.of("0.75", "GBP"),
    }
    assert canonical_json(value) == (
        b'{"amount":{"amount":"0.75","currency":"GBP"},"id":"295acb22-8b4b-4b33-85de-02103c494399"}'
    )


def test_non_ascii_is_kept_as_utf8() -> None:
    assert canonical_json("₦") == '"₦"'.encode()
