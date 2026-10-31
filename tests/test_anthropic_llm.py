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
