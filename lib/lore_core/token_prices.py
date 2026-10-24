"""Estimated cost of token usage at Anthropic's API list prices.

The figures are an estimate, not a bill. A Claude subscription does not
charge per token; for one, the cost reads as "what this would cost on the
API", a way to compare models and tiers rather than an invoice.

Simplifications, stated so nobody reads more precision into the number:

- One rate per model. Long-prompt surcharges (Haiku 5.5 above 100K input
  tokens), fast mode and batch discounts are not applied.
- A cache write bills at a multiple of the model's input price: 1.25x for
  the 5-minute cache, 2x for the 1-hour cache.

``PRICES_AS_OF`` dates the table. A person's ``tokens.prices`` config block
overrides any row, or adds one for a model the table lacks.
"""

from __future__ import annotations

from typing import Any

PRICES_AS_OF = "2026-10-06"

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0

#: USD per million tokens: input, output, cache read. Where no cache-read
#: price was published alongside the list price, it is the usual 0.1x input.
PRICES: dict[str, dict[str, float]] = {
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25},
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10},
}


def _normalise(model: str) -> str:
    """``us.anthropic.claude-x[1m]`` -> ``claude-x``: drop a provider prefix and a suffix."""
    model = model.strip().lower()
    start = model.find("claude-")
    if start > 0:
        model = model[start:]
    return model.split("[", 1)[0]


def price_for(model: str, overrides: dict[str, Any] | None = None) -> dict[str, float] | None:
    """The price row for ``model``, or None when neither table knows it.

    Matches the exact id, or the id plus a date (``claude-haiku-4-5-20251001``).
    A newer model of a family (``claude-sonnet-5-7``) is unpriced, not
    priced as its predecessor. ``overrides`` (``tokens.prices``) wins over
    the shipped table.
    """
    from lore_core.token_trace import is_variant_of

    table = {**PRICES, **{_normalise(k): v for k, v in (overrides or {}).items()}}
    key = _normalise(model)
    if key not in table:
        key = next((k for k in table if is_variant_of(key, k)), "")
        if not key:
            return None
    row = table[key]
    try:
        return {k: float(row[k]) for k in ("input", "output", "cache_read")}
    except (KeyError, TypeError, ValueError):
        return None


def estimate_cost(
    model: str, counts: dict, overrides: dict[str, Any] | None = None
) -> float | None:
    """US dollars for one model's token ``counts`` (``PhaseTokens.counts()``), or None."""
    row = price_for(model, overrides)
    if row is None:
        return None
    write_1h = counts.get("cache_write_1h", 0)
    write_5m = counts.get("cache_write", 0) - write_1h
    dollars = (
        counts.get("input", 0) * row["input"]
        + counts.get("output", 0) * row["output"]
        + counts.get("cache_read", 0) * row["cache_read"]
        + write_5m * row["input"] * CACHE_WRITE_5M
        + write_1h * row["input"] * CACHE_WRITE_1H
    )
    return dollars / 1_000_000
