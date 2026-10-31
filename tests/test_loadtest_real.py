"""The real-provider load test is opt-in and guarded: dry-run by default, a cost estimate first, a
hard cap, and no provider is ever contacted by these tests (the executor is a spy)."""

import pytest

from loadtest.real import HARD_CAP_USD, Assumptions, Estimate, Prices, estimate, main

PRICES = Prices(
    claude_input_per_mtok=3.0,
    claude_output_per_mtok=15.0,
    deepgram_per_minute=0.01,
    cartesia_per_1k_chars=0.05,
    placeholder=False,
)
ASSUME = Assumptions(
    turns_per_call=3,
    llm_steps_per_turn=3,
    input_tokens_per_step=6000,
    output_tokens_per_step=300,
    chars_per_reply=500,
    caller_chars_per_turn=0,
    call_max_seconds=300,
)


def test_the_estimate_is_the_sum_of_each_providers_worst_case_for_every_call() -> None:
    result: Estimate = estimate(20, ASSUME, PRICES)

    # Per Call: Deepgram 300 s = 5 min * 0.01; Cartesia 3*500 chars = 1.5k * 0.05;
    # Claude 3 turns * 3 steps = 9 steps * (6000 * 3/1M + 300 * 15/1M).
    assert result.deepgram_usd == pytest.approx(20 * 0.05)
    assert result.cartesia_usd == pytest.approx(20 * 0.075)
    assert result.claude_usd == pytest.approx(20 * 9 * (0.018 + 0.0045))
    assert result.total_usd == pytest.approx(1.0 + 1.5 + 4.05)
    assert result.placeholder_prices is False


def test_placeholder_prices_are_called_out_in_the_estimate() -> None:
    placeholder = Prices(3.0, 15.0, 0.01, 0.05, placeholder=True)

    assert estimate(1, ASSUME, placeholder).placeholder_prices is True


def run(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, list[int]]:
    launched: list[int] = []
    output: list[str] = []
    code = main(
        argv,
        env=env if env is not None else {},
        out=output.append,
        execute_real=lambda calls, turns: launched.append(calls),
    )
    return code, "\n".join(output), launched


def test_by_default_it_prints_the_estimate_and_the_command_and_runs_nothing() -> None:
    code, text, launched = run(["--calls", "20"])

    assert code == 0 and launched == []
    assert "DRY RUN" in text
    assert "worst-case" in text.lower() and "$" in text
    assert "--execute" in text  # the exact command to run for real is printed


def test_executing_needs_the_explicit_environment_confirmation_too() -> None:
    code, text, launched = run(["--calls", "20", "--execute"])

    assert code == 2 and launched == []
    assert "LOADTEST_REAL_SPEND_OK" in text


def test_executing_with_both_confirmations_and_an_estimate_under_the_cap_launches() -> None:
    env = {
        "LOADTEST_REAL_SPEND_OK": "yes",
        "DEEPGRAM_API_KEY": "dg-secret-value",
        "CARTESIA_API_KEY": "ct-secret-value",
        "ANTHROPIC_API_KEY": "an-secret-value",
    }

    code, text, launched = run(["--calls", "20", "--execute", "--max-cost-usd", "20"], env)

    assert code == 0 and launched == [20]
    for value in env.values():
        assert value not in text  # keys are never printed


def test_an_estimate_over_the_cap_is_refused_even_with_every_confirmation() -> None:
    env = {"LOADTEST_REAL_SPEND_OK": "yes"}

    code, text, launched = run(["--calls", "20", "--execute", "--max-cost-usd", "0.50"], env)

    assert code == 2 and launched == []
    assert "exceeds" in text


def test_the_cap_cannot_be_raised_past_the_hard_limit() -> None:
    env = {"LOADTEST_REAL_SPEND_OK": "yes"}

    code, text, launched = run(
        ["--calls", "20", "--execute", "--max-cost-usd", str(HARD_CAP_USD + 1)], env
    )

    assert code == 2 and launched == []
    assert "hard" in text.lower()


def test_more_than_twenty_calls_are_refused() -> None:
    code, text, launched = run(["--calls", "21"])

    assert code == 2 and launched == []
    assert "20" in text


def test_missing_provider_keys_are_named_but_never_shown() -> None:
    env = {"LOADTEST_REAL_SPEND_OK": "yes", "DEEPGRAM_API_KEY": "dg-secret-value"}

    code, text, launched = run(["--calls", "2", "--execute", "--max-cost-usd", "20"], env)

    assert code == 2 and launched == []
    assert "CARTESIA_API_KEY" in text and "ANTHROPIC_API_KEY" in text
    assert "DEEPGRAM_API_KEY" not in text.split("missing")[-1]
    assert "dg-secret-value" not in text
