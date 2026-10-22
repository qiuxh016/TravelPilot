"""Opt-in LLM usage cost estimates with no baked-in provider price claims."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def estimate_cost_microusd(
    *,
    input_tokens: Any,
    output_tokens: Any,
    input_rate_per_million: Decimal | None,
    output_rate_per_million: Decimal | None,
) -> int | None:
    """Return an estimated USD cost in millionths, or None without both rates.

    Rates are operator-configured USD per million tokens and must be sourced
    from the configured provider/model's current pricing. Missing usage or rate
    data is intentionally not treated as zero cost.
    """
    if input_rate_per_million is None or output_rate_per_million is None:
        return None
    counts = (input_tokens, output_tokens)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts
    ):
        return None
    cost_usd = (
        Decimal(input_tokens) * input_rate_per_million
        + Decimal(output_tokens) * output_rate_per_million
    ) / Decimal(1_000_000)
    return int((cost_usd * Decimal(1_000_000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
