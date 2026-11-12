"""The real LLM adapter, tested against a stub of the Anthropic client: no network, no key.

It proves the adapter's contract (model and timeout from configuration, tools offered, a reply
turned into a Step). It says nothing about how well the real model behaves: that needs a key
and is deliberately not exercised in CI."""

import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agent.anthropic_llm import AnthropicLLM
from agent.config import AgentSettings
from agent.llm import LLMTurn, Message, Say, UseTool
from agent.tools import TOOL_SPECS


class StubMessages:
    def __init__(self, content: list[Any]) -> None:
        self.content = content
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(content=self.content)


def stub_client(content: list[Any]) -> tuple[Any, StubMessages]:
    messages = StubMessages(content)
    return SimpleNamespace(messages=messages), messages


def a_turn() -> LLMTurn:
    return LLMTurn(
        summary={"offered": ["Tuesday 3 November at 10:00 AM with Meera Nair"]},
        messages=[Message("agent", "Hello"), Message("caller", "A cleaning please")],
        tools=TOOL_SPECS,
    )


def test_a_tool_request_from_the_model_becomes_a_use_tool_step() -> None:
    block = SimpleNamespace(
        type="tool_use", name="check_availability", input={"service": "Cleaning", "date": "x"}
    )
    client, _ = stub_client([block])

    step = AnthropicLLM(client, model="m", timeout_seconds=9).next_step(a_turn())

    assert step == UseTool("check_availability", {"service": "Cleaning", "date": "x"})


def test_plain_text_from_the_model_becomes_a_say_step() -> None:
    client, _ = stub_client([SimpleNamespace(type="text", text="Which day suits you?")])

    step = AnthropicLLM(client, model="m", timeout_seconds=9).next_step(a_turn())

    assert step == Say("Which day suits you?")


def test_the_request_uses_the_configured_model_timeout_and_the_agents_tools() -> None:
    client, messages = stub_client([SimpleNamespace(type="text", text="Hi")])

    AnthropicLLM(client, model="the-model", timeout_seconds=7).next_step(a_turn())

    [request] = messages.requests
    assert request["model"] == "the-model"
    assert request["timeout"] == 7
    assert [t["name"] for t in request["tools"]] == [t.name for t in TOOL_SPECS]
    assert request["messages"][0]["role"] == "user"  # the API needs the conversation to open so


def test_the_agent_core_imports_neither_livekit_nor_the_anthropic_sdk() -> None:
    code = (
        "import sys, agent.text_mode, agent.scripted_llm, evals.runner\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('livekit', 'anthropic')]\n"
        "sys.exit('imported: ' + ', '.join(bad) if bad else 0)"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr or result.stdout


def test_model_names_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "main-from-env")
    monkeypatch.setenv("LLM_MODEL_FAST", "fast-from-env")

    settings = AgentSettings()

    assert (settings.llm_model, settings.llm_model_fast) == ("main-from-env", "fast-from-env")


def a_turn_after(tool: str) -> LLMTurn:
    return LLMTurn(
        summary={},
        messages=[
            Message("caller", "What are your hours?"),
            Message("tool", f'{{"tool": "{tool}", "ok": true}}'),
        ],
        tools=TOOL_SPECS,
    )


def test_the_fast_model_puts_a_tool_result_into_words_and_the_main_model_plans() -> None:
    client, messages = stub_client([SimpleNamespace(type="text", text="We open at nine.")])
    llm = AnthropicLLM(client, model="main", timeout_seconds=9, fast_model="fast")

    llm.next_step(a_turn_after("search_faq"))
    llm.next_step(a_turn_after("check_availability"))  # must pick what to offer: main model
    llm.next_step(a_turn())  # the Caller just spoke: main model

    assert [r["model"] for r in messages.requests] == ["fast", "main", "main"]


def test_the_usage_names_the_model_that_answered() -> None:
    class Messages:
        def create(self, **_: Any) -> Any:
            usage = SimpleNamespace(input_tokens=1, output_tokens=2)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Hi")], usage=usage)

    llm = AnthropicLLM(SimpleNamespace(messages=Messages()), "main", 9, fast_model="fast")

    llm.next_step(a_turn_after("search_faq"))

    assert llm.last_usage is not None and llm.last_usage.model == "fast"


def test_thinking_is_switched_off_the_way_each_model_accepts() -> None:
    client, messages = stub_client([SimpleNamespace(type="text", text="Hi")])

    for model in ("claude-sonnet-5-5", "claude-haiku-5-5", "some-other-model"):
        AnthropicLLM(client, model, 9).next_step(a_turn())

    sonnet, haiku, other = messages.requests
    assert sonnet["thinking"] == {"type": "between_tools"}  # "disabled" is a 400 on Sonnet 5.5
    assert haiku["thinking"] == {"type": "disabled"}
    assert "thinking" not in other  # an unknown model keeps its own default


def blocks(api_messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(m["role"], b["type"]) for m in api_messages for b in m["content"]]


def test_tool_calls_and_results_reach_the_api_as_tool_blocks_never_as_text() -> None:
    from agent.anthropic_llm import to_api_messages

    messages = [
        Message("caller", "Do you do fillings?"),
        Message("tool_call", '{"tool": "search_faq", "args": {"question": "fillings"}}'),
        Message("tool", '{"tool": "search_faq", "ok": true, "found": true}'),
    ]

    api = to_api_messages(messages)

    assert blocks(api) == [
        ("user", "text"),  # the call has connected
        ("user", "text"),
        ("assistant", "tool_use"),
        ("user", "tool_result"),
    ]
    tool_use = api[-2]["content"][0]
    assert tool_use["name"] == "search_faq" and tool_use["input"] == {"question": "fillings"}
    assert api[-1]["content"][0]["tool_use_id"] == tool_use["id"]
    assert "(tool call)" not in str(api) and "(tool result)" not in str(api)


def test_a_tool_whose_reply_was_spoken_still_gets_a_result_before_the_next_caller_turn() -> None:
    from agent.anthropic_llm import to_api_messages

    messages = [
        Message("caller", "The second one."),
        Message("tool_call", '{"tool": "place_hold", "args": {"option": 2}}'),  # chained, no result
        Message("tool_call", '{"tool": "read_back", "args": {}}'),
        Message("agent", "Let me read that back. Is that all correct?"),
        Message("caller", "Yes"),
    ]

    api = to_api_messages(messages)

    uses = [b["id"] for m in api for b in m["content"] if b["type"] == "tool_use"]
    answered = [b["tool_use_id"] for m in api for b in m["content"] if b["type"] == "tool_result"]
    assert uses == answered and len(set(uses)) == 2
    assert api[-1]["role"] == "user" and api[-1]["content"][-1]["text"] == "Yes"  # results first


def test_a_note_from_the_core_is_plain_user_text_and_a_failed_tool_is_an_error_result() -> None:
    from agent.anthropic_llm import to_api_messages

    messages = [
        Message("caller", "Any time?"),
        Message("tool", '{"error": "you named a time that Availability did not return"}'),
        Message("tool_call", '{"tool": "place_hold", "args": {"option": 9}}'),
        Message("tool", '{"tool": "place_hold", "ok": false, "error": "not offered"}'),
    ]

    api = to_api_messages(messages)

    assert "(note from the system)" in str(api[0]["content"])  # same speaker: one turn
    assert api[-1]["content"][0]["is_error"] is True


def test_text_written_with_a_tool_request_is_dropped_never_spoken() -> None:
    narration = SimpleNamespace(
        type="text", text="The state shows I can confirm. Sorry, my last step was wrong."
    )
    block = SimpleNamespace(type="tool_use", name="search_faq", input={"question": "price"})
    client, _ = stub_client([narration, block])

    step = AnthropicLLM(client, "m", 9).next_step(a_turn())

    assert step == UseTool("search_faq", {"question": "price"})  # no text rides along
