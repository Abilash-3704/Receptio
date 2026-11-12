"""The FAQ and scope-limit scenarios (tricky call 11, medical half), and the `invented_price` rule.

Scripted-LLM mode only: these scenarios prove the agent core's routing and guards, not that the
real model behaves. The rule re-reads the trace rather than trusting the agent's own guard."""

from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from agent.text_mode import Turn
from agent.tools import ToolCall
from evals.runner import Observation, load_scenario, run_scenario, violations_of
from evals.state import FinalState

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"

SCENARIO_FILES = [
    "faq_hours_016",
    "faq_location_016",
    "faq_services_016",
    "faq_prices_016",
    "faq_insurance_016",
    "faq_unknown_callback_016",
    "medical_advice_redirect_011",
]


def session_factory(backend: httpx.Client) -> Any:
    return cast(Any, backend).app.state.session_factory


@pytest.mark.parametrize("name", SCENARIO_FILES)
def test_the_scenario_passes_against_the_real_backend(backend: httpx.Client, name: str) -> None:
    scenario = load_scenario(SCENARIOS / f"{name}.yaml")

    result = run_scenario(scenario, backend, session_factory(backend))

    assert result.failures == []


def observation(said: str, facts: list[str]) -> Observation:
    empty = FinalState([], 0, [], [])
    lookup = ToolCall("search_faq", {}, True, False, {"found": True, "facts": facts})
    return Observation(
        empty, empty, [lookup], [Turn("agent", said)], caller_turns=1, clinic_open=True
    )


def test_invented_price_flags_an_amount_the_knowledge_base_never_gave() -> None:
    facts = ["Price ranges: Filling: Rs 1,500 to Rs 4,000."]

    found = violations_of("invented_price", observation("A filling is Rs 2,000.", facts))

    assert len(found) == 1 and "2000" in found[0]


def test_invented_price_accepts_the_ranges_the_knowledge_base_gave() -> None:
    facts = ["Price ranges: Filling: Rs 1,500 to Rs 4,000."]

    assert violations_of("invented_price", observation("Rs 1,500 to Rs 4,000.", facts)) == []
