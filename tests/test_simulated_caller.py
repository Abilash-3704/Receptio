"""The simulated Caller (ticket 14): an LLM plays a persona with a goal and answers the agent.
Tested with a fake client, never a real API."""

from pathlib import Path

from agent.text_mode import Turn
from evals.personas import Persona, load_personas
from evals.simulated_caller import HANG_UP, SimulatedCaller
from tests.fake_anthropic import FakeAnthropic, replies

PERSONAS = load_personas()


def make(client: FakeAnthropic, persona: str = "patient", max_turns: int = 6) -> SimulatedCaller:
    return SimulatedCaller(
        client, "sim-model", PERSONAS[persona], "Book a cleaning for tomorrow.", max_turns
    )


def test_the_four_personas_ship_and_each_has_its_own_style() -> None:
    assert {"patient", "rushed", "confused", "hostile"} <= set(PERSONAS)
    assert len({p.style for p in PERSONAS.values()}) == len(PERSONAS)


def test_the_persona_and_goal_reach_the_model_so_personas_change_behaviour() -> None:
    client = FakeAnthropic(replies("hi", "hello"))
    make(client, "hostile").respond([Turn("agent", "Hello")])
    make(client, "patient").respond([Turn("agent", "Hello")])

    hostile, patient = (r["system"] for r in client.requests)
    assert PERSONAS["hostile"].style in hostile and PERSONAS["hostile"].style not in patient
    assert "Book a cleaning for tomorrow." in hostile and "Book a cleaning" in patient
    assert client.requests[0]["model"] == "sim-model"


def test_the_agent_is_the_other_side_of_the_conversation() -> None:
    client = FakeAnthropic(replies("Tomorrow please"))
    transcript = [
        Turn("agent", "Hello, how can I help?"),
        Turn("caller", "Hi"),
        Turn("agent", "What would you like?"),
    ]

    said = make(client).respond(transcript)

    assert said == "Tomorrow please"
    roles = [(m["role"], m["content"]) for m in client.requests[0]["messages"]]
    assert roles == [
        ("user", "Hello, how can I help?"),
        ("assistant", "Hi"),
        ("user", "What would you like?"),
    ]


def test_consecutive_turns_by_one_side_are_merged_so_roles_alternate() -> None:
    client = FakeAnthropic(replies("ok"))
    make(client).respond([Turn("agent", "One."), Turn("agent", "Two.")])

    messages = client.requests[0]["messages"]
    assert [m["role"] for m in messages] == ["user"]
    assert "One." in messages[0]["content"] and "Two." in messages[0]["content"]


def test_the_hang_up_marker_is_stripped_and_ends_the_call_after_the_goodbye() -> None:
    client = FakeAnthropic(replies(f"Thanks, bye. {HANG_UP}"))
    caller = make(client)

    assert caller.respond([Turn("agent", "All booked.")]) == "Thanks, bye."
    assert caller.hung_up
    assert caller.respond([Turn("agent", "Bye")]) is None


def test_a_bare_hang_up_says_nothing() -> None:
    client = FakeAnthropic(replies(HANG_UP))
    caller = make(client)

    assert caller.respond([Turn("agent", "All booked.")]) is None
    assert caller.hung_up


def test_a_persona_with_a_turn_limit_hangs_up_without_asking_the_model() -> None:
    impatient = Persona(name="x", style="short fuse", max_turns=1)
    client = FakeAnthropic(replies("one", "two"))
    caller = SimulatedCaller(client, "m", impatient, "goal", max_turns=6)

    assert caller.respond([Turn("agent", "Hi")]) == "one"
    assert caller.respond([Turn("agent", "Hi"), Turn("caller", "one"), Turn("agent", "?")]) is None
    assert len(client.requests) == 1


def test_the_scenarios_own_turn_cap_also_ends_the_call() -> None:
    client = FakeAnthropic(replies("a", "b", "c"))
    caller = make(client, max_turns=2)
    transcript = [Turn("agent", "Hi")]

    assert caller.respond(transcript) == "a"
    assert caller.respond(transcript) == "b"
    assert caller.respond(transcript) is None


def test_a_custom_personas_file_can_add_a_persona(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("- name: whisperer\n  style: Speaks very softly and mumbles.\n")

    assert set(load_personas(path)) == {"whisperer"}
