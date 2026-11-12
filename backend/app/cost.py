"""What a Call cost: tokens, seconds and characters times configured prices.

Each component says how well it is known:
- Claude tokens are `measured`: the provider's own usage report for each request.
- Cartesia characters are `counted`: the characters of what the agent said, counted by us. The
  provider's invoice may differ (speech cut off by the Caller may bill fewer or all characters).
- Deepgram seconds are `estimated`: the Call's length stands in for the audio streamed to the
  recogniser, which is the billing unit but is not reported back to us.
Prices come from `BackendSettings`; a price nobody configured is a placeholder and is flagged."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.config import BackendSettings

MTOK = 1_000_000


@dataclass(frozen=True)
class Prices:
    llm_per_mtok: dict[str, tuple[float, float]]
    deepgram_per_minute: float
    cartesia_per_1k_characters: float
    llm_placeholder: bool
    deepgram_placeholder: bool
    cartesia_placeholder: bool

    @staticmethod
    def from_settings(settings: BackendSettings) -> "Prices":
        configured = settings.model_fields_set
        return Prices(
            dict(settings.llm_prices_per_mtok),
            settings.deepgram_usd_per_minute,
            settings.cartesia_usd_per_1k_characters,
            "llm_prices_per_mtok" not in configured,
            "deepgram_usd_per_minute" not in configured,
            "cartesia_usd_per_1k_characters" not in configured,
        )

    def llm_cost(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        """None when the model has no configured price: never a guess."""
        if model not in self.llm_per_mtok:
            return None
        price_in, price_out = self.llm_per_mtok[model]
        return (input_tokens * price_in + output_tokens * price_out) / MTOK

    def cartesia_cost(self, characters: int) -> float:
        return characters / 1000 * self.cartesia_per_1k_characters


def call_cost(
    prices: Prices,
    mode: str,
    started_at: datetime,
    ended_at: datetime,
    llm_calls: list[tuple[str, int, int]],  # (model, input tokens, output tokens)
    agent_characters: int,
) -> dict[str, Any]:
    by_model: dict[str, tuple[int, int]] = {}
    for model, tokens_in, tokens_out in llm_calls:
        i, o = by_model.get(model, (0, 0))
        by_model[model] = (i + tokens_in, o + tokens_out)
    unpriced = sorted(m for m in by_model if m not in prices.llm_per_mtok)
    claude_usd = (
        None if unpriced else sum(prices.llm_cost(m, i, o) or 0.0 for m, (i, o) in by_model.items())
    )
    claude: dict[str, Any] = {
        "basis": "measured",
        "requests": len(llm_calls),
        "input_tokens": sum(t[0] for t in by_model.values()),
        "output_tokens": sum(t[1] for t in by_model.values()),
        "usd": claude_usd,
        "unpriced_models": unpriced,
        "placeholder_price": prices.llm_placeholder,
    }
    deepgram: dict[str, Any] | None = None
    cartesia: dict[str, Any] | None = None
    if mode == "voice":  # a text Call has no audio, so nothing to pay for
        seconds = round((ended_at - started_at).total_seconds())
        deepgram = {
            "basis": "estimated",
            "seconds": seconds,
            "usd": seconds / 60 * prices.deepgram_per_minute,
            "placeholder_price": prices.deepgram_placeholder,
        }
        cartesia = {
            "basis": "counted",
            "characters": agent_characters,
            "usd": prices.cartesia_cost(agent_characters),
            "placeholder_price": prices.cartesia_placeholder,
        }
    components = {"claude": claude, "deepgram": deepgram, "cartesia": cartesia}
    present = [c for c in components.values() if c is not None]
    return {
        "currency": "USD",
        "total_usd": sum(c["usd"] for c in present if c["usd"] is not None),
        # The total leaves out a component that has no price, so say so.
        "total_complete": all(c["usd"] is not None for c in present),
        "placeholder_prices": any(c["placeholder_price"] for c in present),
        "components": components,
    }
