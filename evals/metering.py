"""Cost metering and hard caps for real-API eval runs.

`MeteredClient` wraps an Anthropic client (the same wrapped client serves the agent's LLM and the
simulated Caller). It counts tokens from each response's `usage`, refuses to send a request once a
request or cost cap is reached, and refuses a model that has no configured price.

Cost = input tokens x input price + output tokens x output price, per model, prices from
`evals.config.EvalSettings`."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evals.config import EvalSettings

MTOK = 1_000_000


class BudgetExceeded(Exception):
    """A request or cost cap was reached; the run stops and the report is marked aborted."""


@dataclass(frozen=True)
class PriceBook:
    per_mtok: dict[str, tuple[float, float]]  # model -> (input, output) USD per million tokens
    placeholder: bool  # True when the numbers are the built-in placeholders, not configured

    def price(self, model: str) -> tuple[float, float]:
        if model not in self.per_mtok:
            raise KeyError(f"no price configured for model {model!r}")
        return self.per_mtok[model]


@dataclass
class Ledger:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, tuple[int, int]] = field(default_factory=lambda: {})

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.requests += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        i, o = self.by_model.get(model, (0, 0))
        self.by_model[model] = (i + input_tokens, o + output_tokens)

    def cost_usd(self, prices: PriceBook) -> float:
        total = 0.0
        for model, (tokens_in, tokens_out) in self.by_model.items():
            price_in, price_out = prices.price(model)
            total += (tokens_in * price_in + tokens_out * price_out) / MTOK
        return total


class MeteredClient:
    def __init__(
        self,
        inner: Any,
        ledger: Ledger,
        prices: PriceBook,
        max_requests: int,
        max_cost_usd: float,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._prices = prices
        self._max_requests = max_requests
        self._max_cost = max_cost_usd
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        model: str = kwargs["model"]
        self._prices.price(model)  # unpriced model: refuse before spending anything
        if self._ledger.requests >= self._max_requests:
            raise BudgetExceeded(f"request cap of {self._max_requests} requests reached")
        spent = self._ledger.cost_usd(self._prices)
        if spent >= self._max_cost:
            raise BudgetExceeded(
                f"cost cap of {self._max_cost:.2f} USD reached ({spent:.4f} spent)"
            )
        response = self._inner.messages.create(**kwargs)
        self._ledger.add(model, response.usage.input_tokens, response.usage.output_tokens)
        return response


@dataclass(frozen=True)
class Estimate:
    requests: int
    cost_usd: float
    capped: bool
    placeholder_prices: bool
    max_requests: int
    max_cost_usd: float

    def describe(self) -> str:
        basis = (
            "PLACEHOLDER prices (set EVAL_PRICE_* for real ones)"
            if self.placeholder_prices
            else "configured prices"
        )
        lines = [
            f"Dry run: would send up to {self.requests} requests, worst case about "
            f"{self.cost_usd:.2f} USD ({basis}).",
            f"Hard caps for the run: {self.max_requests} requests, {self.max_cost_usd:.2f} USD.",
        ]
        if self.capped:
            lines.append("The worst case is above a cap, so the run would stop early at the cap.")
        return "\n".join(lines)


def estimate_run(
    scenarios: int,
    turns_per_scenario: int,
    requests_per_turn: int,
    settings: "EvalSettings",
    prices: PriceBook,
    price_model: str,
    max_requests: int,
    max_cost_usd: float,
) -> Estimate:
    """Worst case for a real run: every scenario uses every allowed turn. Uses the priciest model
    given in `price_model`, so it is an upper bound. Calls nothing."""
    wanted = scenarios * turns_per_scenario * requests_per_turn
    requests = min(wanted, max_requests)
    price_in, price_out = prices.price(price_model)
    per_request = (
        settings.est_input_tokens_per_request * price_in
        + settings.est_output_tokens_per_request * price_out
    ) / MTOK
    cost = requests * per_request
    capped = wanted > max_requests or cost > max_cost_usd
    return Estimate(
        requests, min(cost, max_cost_usd), capped, prices.placeholder, max_requests, max_cost_usd
    )
