"""Per-token prices for hosted models, used to log a computed cost per request."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_usd_per_million: float
    output_usd_per_million: float
    source: str


# Prices checked 2026-09-23 against the OpenRouter model list
# (https://openrouter.ai/api/v1/models, pricing.prompt and pricing.completion per token)
# and the model pages:
#   https://openrouter.ai/openai/gpt-6-luna
#   https://openrouter.ai/openai/gpt-6-sol
#   https://openrouter.ai/typesafe/jev-1.13
# TypeSafe direct uses the same Jev input rate the eval scripts already assume
# (services/agent/eval/jev_metrics.py INPUT_USD_PER_MILLION).
PRICES: dict[str, Price] = {
    "openai/gpt-6-luna": Price(0.10, 0.50, "https://openrouter.ai/openai/gpt-6-luna"),
    "openai/gpt-6-sol": Price(2.00, 10.00, "https://openrouter.ai/openai/gpt-6-sol"),
    "jev": Price(0.042, 0.0, "https://openrouter.ai/typesafe/jev-1.13"),
}


def price_for(model: str | None) -> Price | None:
    name = (model or "").strip().lower()
    if name in PRICES:
        return PRICES[name]
    # Jev ids vary by backend: jev-latest, jev-1.13, typesafe/jev-1.13-20260917.
    if "jev" in name:
        return PRICES["jev"]
    return None


def cost_usd(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """Computed cost for one request, or None when the model has no known price."""
    price = price_for(model)
    if price is None:
        return None
    return (
        (input_tokens or 0) * price.input_usd_per_million
        + (output_tokens or 0) * price.output_usd_per_million
    ) / 1_000_000
