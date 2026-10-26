"""Per-token prices in USD per million tokens. Used for cost tracking and per-run
caps, so a model with no entry has unknown cost (None), never zero.

Claude's prices are published and listed here. Meta does not publish fixed Llama API
prices for every account, so Llama's come from configuration (the price on the
bank's contract); until they are set, Llama cost stays unknown and steps rely on
their call caps."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Price:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5m: Decimal


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(
        input=Decimal("4.00"),
        output=Decimal("20.00"),
        cache_read=Decimal("0.20"),
        cache_write_5m=Decimal("5.00"),
    ),
}


def flat_price(input_per_mtok: Decimal, output_per_mtok: Decimal) -> Price:
    """A provider without prompt caching (Llama): cached tokens cost as input."""
    return Price(
        input=input_per_mtok,
        output=output_per_mtok,
        cache_read=input_per_mtok,
        cache_write_5m=input_per_mtok,
    )


def cost_usd(
    model: str | None,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
    price: Price | None = None,
) -> Decimal | None:
    """`price` overrides the table, for providers whose price is configured per
    deployment rather than published (see Settings.llama_price_*)."""
    price = price or PRICES.get(model or "")
    if price is None:
        return None
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_input_tokens * price.cache_read
        + cache_creation_input_tokens * price.cache_write_5m
    )
    return (total / _MILLION).quantize(Decimal("0.000001"))
