"""The LLM behind a small interface. The agent core only ever sees this module, so tests and CI
use a scripted LLM (agent/scripted_llm.py) and the real one (agent/anthropic_llm.py) is swappable.

The LLM never holds the Call's state. Each turn it gets a compact summary of the state machine
and the conversation so far, and answers with one step: say something, or ask for a tool."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class Say:
    """Speak to the Caller. Ends the agent's turn."""

    text: str


@dataclass(frozen=True)
class UseTool:
    """Ask for a tool. The result comes back as a tool message and the LLM is asked again."""

    name: str
    args: dict[str, Any] = field(default_factory=lambda: {})


Step = Say | UseTool


@dataclass(frozen=True)
class Message:
    role: Literal["caller", "agent", "tool_call", "tool"]
    content: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments


@dataclass(frozen=True)
class LLMTurn:
    summary: dict[str, Any]  # compact view of the call state machine
    messages: list[Message]
    tools: list[ToolSpec]


@dataclass(frozen=True)
class LLMUsage:
    """What one request cost, as the provider reported it. A real LLM sets `last_usage` to this
    after each `next_step`; a scripted one has none, and then nothing is recorded."""

    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class LLM(Protocol):
    def next_step(self, turn: LLMTurn) -> Step: ...
