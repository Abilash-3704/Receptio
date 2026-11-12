"""Seam: a stored Call read back whole (`backend.app.trace.load_trace`), after a real text-mode
Call through the agent core and the backend. The trace viewer and the eval harness read this."""

from collections.abc import Callable
from typing import Any

import httpx

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call

Trace = Callable[[int], dict[str, Any]]


def test_a_text_call_is_stored_turn_by_turn_with_its_mode(
    backend: httpx.Client, trace: Trace
) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Sure, which service?")]))
    call.hear("I'd like an appointment")
    call.end()

    stored = trace(call.call_id)

    assert stored["call"]["mode"] == "text"
    assert [(t["seq"], t["speaker"]) for t in stored["turns"]] == [
        (1, "agent"),
        (2, "caller"),
        (3, "agent"),
    ]
    assert stored["turns"][1]["text"] == "I'd like an appointment"
    assert stored["turns"][2]["text"] == "Sure, which service?"


def test_a_voice_call_is_opened_as_voice(backend: httpx.Client, trace: Trace) -> None:
    call = open_text_call(backend, mode="voice")

    assert trace(call.call_id)["call"]["mode"] == "voice"


def test_the_transcript_endpoint_still_returns_the_same_turns(backend: httpx.Client) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Hi.")]))
    call.hear("hello")

    stored = backend.get(f"/calls/{call.call_id}").json()

    assert [(t["speaker"], t["text"]) for t in stored["transcript"]] == [
        ("agent", call.transcript[0].text),
        ("caller", "hello"),
        ("agent", "Hi."),
    ]


def test_tool_calls_are_stored_under_the_caller_turn_that_caused_them(
    backend: httpx.Client, trace: Trace
) -> None:
    llm = ScriptedLLM(
        [
            UseTool("search_faq", {"question": "where are you"}),
            Say("We are in Bengaluru."),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("Where are you?")

    stored = trace(call.call_id)

    caller_turn = stored["turns"][1]
    [tool] = caller_turn["tool_calls"]
    assert (tool["name"], tool["args"], tool["ok"], tool["refused"]) == (
        "search_faq",
        {"question": "where are you"},
        True,
        False,
    )
    assert tool["result"] == call.tool_calls[0].data
    assert stored["turns"][0]["tool_calls"] == []
