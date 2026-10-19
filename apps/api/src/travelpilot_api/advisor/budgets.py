"""Shared per-turn output-token accounting across Advisor stages."""

from typing import Any


def remaining_turn_output_tokens(
    total_budget: int,
    extraction_output_tokens: int,
    *runtimes: Any,
) -> int:
    """Return nonnegative turn budget after every completed model stage."""
    total = max(0, total_budget) if isinstance(total_budget, int) and not isinstance(total_budget, bool) else 0
    extraction = (
        max(0, extraction_output_tokens)
        if isinstance(extraction_output_tokens, int) and not isinstance(extraction_output_tokens, bool)
        else 0
    )
    consumed = extraction
    for runtime in runtimes:
        output_tokens = getattr(runtime, "output_tokens_used", 0)
        if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
            consumed += max(0, output_tokens)
    return max(0, total - consumed)


def aggregate_turn_token_usage(
    extraction_total_tokens: int,
    extraction_output_tokens: int,
    reply_total_tokens: int,
    reply_output_tokens: int,
    *runtimes: Any,
) -> dict[str, int]:
    """Aggregate usage from extraction, every Agent loop, and composition."""
    def nonnegative(value: Any) -> int:
        return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0

    input_tokens = max(
        0, nonnegative(extraction_total_tokens) - nonnegative(extraction_output_tokens),
    ) + max(0, nonnegative(reply_total_tokens) - nonnegative(reply_output_tokens))
    output_tokens = nonnegative(extraction_output_tokens) + nonnegative(reply_output_tokens)
    for runtime in runtimes:
        metrics = getattr(runtime, "execution_metrics", None)
        if not isinstance(metrics, dict):
            continue
        input_tokens += nonnegative(metrics.get("input_tokens"))
        output_tokens += nonnegative(metrics.get("output_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
