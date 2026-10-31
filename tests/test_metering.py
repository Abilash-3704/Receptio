"""Cost metering and the hard cap for real eval runs (ticket 14). Fake client only."""

import pytest

from agent.config import AgentSettings
from evals.config import EvalSettings
from evals.metering import BudgetExceeded, Ledger, MeteredClient, PriceBook, estimate_run
from tests.fake_anthropic import FakeAnthropic, replies


def prices(**kw: float) -> PriceBook:
    # 2 USD per million input tokens, 10 per million output, for every model, for exact sums.
    return PriceBook({"m": (2.0, 10.0)}, placeholder=kw.get("placeholder", 0.0) > 0)


def test_cost_is_tokens_times_the_configured_price() -> None:
    ledger = Ledger()
    client = MeteredClient(
        FakeAnthropic(replies("hi"), input_tokens=500_000, output_tokens=100_000),
        ledger,
        prices(),
        max_requests=10,
        max_cost_usd=100.0,
    )

    client.messages.create(model="m", messages=[])

    # 0.5M x 2 + 0.1M x 10 = 2.0 USD
    assert ledger.requests == 1
    assert ledger.input_tokens == 500_000 and ledger.output_tokens == 100_000
    assert ledger.cost_usd(prices()) == pytest.approx(2.0)


def test_a_model_without_a_configured_price_is_refused_not_guessed() -> None:
    client = MeteredClient(
        FakeAnthropic(replies("hi")), Ledger(), prices(), max_requests=5, max_cost_usd=1.0
    )

    with pytest.raises(KeyError, match="other-model"):
        client.messages.create(model="other-model", messages=[])


def test_the_request_cap_stops_the_run_before_the_request_is_sent() -> None:
    fake = FakeAnthropic(replies("a", "b", "c"))
    client = MeteredClient(fake, Ledger(), prices(), max_requests=2, max_cost_usd=100.0)

    client.messages.create(model="m", messages=[])
    client.messages.create(model="m", messages=[])
    with pytest.raises(BudgetExceeded, match="requests"):
        client.messages.create(model="m", messages=[])

    assert len(fake.requests) == 2


def test_the_cost_cap_stops_the_run_once_spent() -> None:
    fake = FakeAnthropic(replies("a", "b"), input_tokens=500_000, output_tokens=0)  # 1 USD each
    client = MeteredClient(fake, Ledger(), prices(), max_requests=100, max_cost_usd=1.5)

    client.messages.create(model="m", messages=[])
    client.messages.create(model="m", messages=[])  # now 2.0 spent, over the cap
    with pytest.raises(BudgetExceeded, match="cost"):
        client.messages.create(model="m", messages=[])

    assert len(fake.requests) == 2


def test_prices_come_from_settings_and_say_whether_they_are_placeholders() -> None:
    default = EvalSettings(_env_file=None)  # type: ignore[call-arg]
    configured = EvalSettings(_env_file=None, price_main_input_per_mtok=1.0)  # type: ignore[call-arg]
    agent = AgentSettings(_env_file=None)  # type: ignore[call-arg]

    assert default.price_book(agent).placeholder is True
    assert configured.price_book(agent).placeholder is False
    assert agent.llm_model in default.price_book(agent).per_mtok
    assert default.sim_model in default.price_book(agent).per_mtok


def test_a_dry_run_estimate_is_a_worst_case_at_the_caps_and_calls_nothing() -> None:
    settings = EvalSettings(  # type: ignore[call-arg]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        est_input_tokens_per_request=1000,
        est_output_tokens_per_request=100,
    )
    book = PriceBook({"m": (2.0, 10.0)}, placeholder=True)

    estimate = estimate_run(
        scenarios=3,
        turns_per_scenario=5,
        requests_per_turn=4,
        settings=settings,
        prices=book,
        price_model="m",
        max_requests=1000,
        max_cost_usd=50.0,
    )

    # 3 x 5 turns x 4 requests = 60 requests; each 1000 in + 100 out = 0.002 + 0.001 = 0.003 USD
    assert estimate.requests == 60
    assert estimate.cost_usd == pytest.approx(0.18)
    assert estimate.capped is False
    assert "placeholder" in estimate.describe().lower()


def test_an_estimate_over_the_caps_reports_the_cap_it_will_hit() -> None:
    settings = EvalSettings(_env_file=None)  # type: ignore[call-arg]
    book = PriceBook({"m": (2.0, 10.0)}, placeholder=False)

    estimate = estimate_run(
        scenarios=100,
        turns_per_scenario=8,
        requests_per_turn=4,
        settings=settings,
        prices=book,
        price_model="m",
        max_requests=50,
        max_cost_usd=50.0,
    )

    assert estimate.capped and estimate.requests == 50


def test_prices_set_in_the_environment_are_used_and_not_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVAL_PRICE_MAIN_INPUT_PER_MTOK", "7.5")
    monkeypatch.setenv("EVAL_PRICE_MAIN_OUTPUT_PER_MTOK", "30")
    agent = AgentSettings(_env_file=None)  # type: ignore[call-arg]

    book = EvalSettings(_env_file=None).price_book(agent)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]

    assert book.placeholder is False
    assert book.price(agent.llm_model) == (7.5, 30.0)
