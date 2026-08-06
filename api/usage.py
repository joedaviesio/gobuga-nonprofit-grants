"""Anthropic API usage tracking — centralized token counting and cost calculation."""

import os

# Pricing per million tokens (keep updated)
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
}

DEFAULT_MODEL = os.getenv("GOBUGA_BOT_MODEL", "claude-haiku-4-5-20251001")


def new_usage() -> dict:
    """Create a fresh usage accumulator."""
    return {"input_tokens": 0, "output_tokens": 0, "api_calls": 0, "cost_usd": 0.0}


def track_usage(accumulator: dict, response, model: str | None = None) -> dict:
    """Add token usage from an Anthropic API response to the accumulator.

    Gracefully handles responses that lack a ``usage`` attribute.
    """
    usage_obj = getattr(response, "usage", None)
    if usage_obj is None:
        accumulator["api_calls"] += 1
        return accumulator

    accumulator["input_tokens"] += getattr(usage_obj, "input_tokens", 0)
    accumulator["output_tokens"] += getattr(usage_obj, "output_tokens", 0)
    accumulator["api_calls"] += 1
    return accumulator


def calc_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Calculate USD cost for given token counts."""
    prices = PRICING.get(model, PRICING["claude-haiku-4-5-20251001"])
    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]
    return round(input_cost + output_cost, 6)


def finalize_usage(accumulator: dict, model: str | None = None) -> dict:
    """Add cost calculation and model tag to a usage accumulator.

    This replaces the old ``_calc_cost`` helper — call it once when you are
    done accumulating and want the final dict to log or return.
    """
    m = model or DEFAULT_MODEL
    accumulator["cost_usd"] = calc_cost(
        accumulator["input_tokens"], accumulator["output_tokens"], m
    )
    accumulator["model"] = m
    return accumulator
