"""Stand-in providers for the load test: configurable latencies, reproducible, and never real."""

import random

from agent.llm import LLMTurn, Say, UseTool
from loadtest.standins import StandInLatency, StandInLLM


def turn() -> LLMTurn:
    return LLMTurn(summary={"offered": ["10:00"]}, messages=[], tools=[])


def test_latency_draws_are_reproducible_and_centre_on_the_configured_median() -> None:
    latency = StandInLatency(llm_step_ms=1000, sigma=0.3)

    first = latency.llm_step(random.Random(7))
    again = latency.llm_step(random.Random(7))
    rng = random.Random(1)
    draws = sorted(latency.llm_step(rng) for _ in range(2001))

    assert first == again
    assert 0.9 < draws[1000] < 1.1  # the median, in seconds
    assert draws[0] < draws[-1]  # it does vary


def test_zero_sigma_is_exactly_the_configured_value() -> None:
    latency = StandInLatency(asr_ms=150, sigma=0.0)

    assert latency.asr(random.Random(3)) == 0.15


def test_the_stand_in_llm_waits_then_follows_its_script_and_reports_usage() -> None:
    slept: list[float] = []
    llm = StandInLLM(
        [UseTool("search_faq", {"question": "hours"}), Say("Offered {offered[0]}.")],
        StandInLatency(llm_step_ms=500, sigma=0.0),
        random.Random(1),
        sleep=slept.append,
    )

    first = llm.next_step(turn())
    second = llm.next_step(turn())

    assert first == UseTool("search_faq", {"question": "hours"})
    assert second == Say("Offered 10:00.")
    assert slept == [0.5, 0.5]
    assert llm.injected_seconds == 1.0
    assert llm.last_usage is not None and llm.last_usage.model == "stand-in-llm"


def test_a_script_that_no_longer_fits_degrades_to_a_reprompt_and_is_counted() -> None:
    llm = StandInLLM(
        [Say("Offered {offered[3]}.")],  # the Call did not offer four times
        StandInLatency(llm_step_ms=0, sigma=0.0),
        random.Random(1),
        sleep=lambda _: None,
    )

    step = llm.next_step(turn())
    after = llm.next_step(turn())  # and the script is now empty

    assert isinstance(step, Say) and "again" in step.text
    assert isinstance(after, Say)
    assert llm.fallbacks == 2
