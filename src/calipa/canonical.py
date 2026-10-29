"""Canonical JSON and hashing.

Everything Calipa hashes goes through `canonical_json`, so the same logical value
always produces the same bytes: sorted keys, no whitespace, UTF-8, Decimals as
canonical strings, timezone-aware datetimes in UTC. Floats are rejected so that a
hash can never depend on binary floating-point formatting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from calipa.money import Money, canonical_decimal


class CanonicalizationError(TypeError):
    pass


def normalize(value: Any) -> Any:
    """Reduce a value to JSON-native types (str, int, bool, None, list, dict)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise CanonicalizationError(f"float {value!r} cannot be canonicalized; use Decimal")
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, Money):
        return value.canonical()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise CanonicalizationError(f"naive datetime {value!r}; timezone required")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return normalize(value.value)
    if isinstance(value, BaseModel):
        return normalize(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return normalize(asdict(value))
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"dict key {key!r} must be str")
            out[key] = normalize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    raise CanonicalizationError(f"cannot canonicalize {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_value(value: Any) -> str:
    return sha256_hex(canonical_json(value))
