"""Text-mode Call: the seam used by tests and evals. Never imports LiveKit.

`TextCall.hear` is the call state machine's turn loop: Caller text in, what the Caller hears out.
The state machine records the read-back "yes" itself (ADR-0001); the LLM never can."""

import json
import re
from dataclasses import dataclass

import httpx

from agent.call_state import CallState
from agent.llm import LLM, LLMTurn, Message, Say
from agent.time_guard import unoffered_times
from agent.tools import TOOL_SPECS, ToolBox, ToolCall

DISCLOSURE = (
    "Hello, you've reached the clinic's receptionist. I'm an AI assistant, "
    "and this call may be recorded. How can I help you today?"
)

# Most tool steps the LLM gets in one turn before the agent gives up on it.
MAX_STEPS_PER_TURN = 8
STUCK = "I'm sorry, I'm having trouble with that. Could you say it again?"


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


# An explicit yes starts with an affirmative and says nothing else of substance. Anything vague
# ("uh-huh", "I guess"), hedged ("yes, but...") or a question is NOT a yes: the Caller is asked
# again, and the LLM cannot confirm. Wrongly refusing a yes costs one more question; wrongly
# accepting one books the wrong Appointment.
AFFIRMATIVES = {"yes", "yeah", "yep", "yup", "correct", "absolutely", "definitely", "exactly"}
FILLER = {
    "please", "thanks", "thank", "you", "that", "thats", "is", "it", "right", "correct", "all",
    "good", "works", "perfect", "sounds", "go", "ahead", "confirm", "sure", "yes", "yeah", "great",
}  # fmt: skip


def is_explicit_yes(text: str) -> bool:
    words = re.sub(r"[^a-z' ]", " ", text.casefold().replace("'", "")).split()
    if "?" in text or not words or words[0] not in AFFIRMATIVES:
        return False
    return all(word in FILLER or word in AFFIRMATIVES for word in words)


class TextCall:
    def __init__(self, backend: httpx.Client, call_id: int, llm: LLM | None = None) -> None:
        self._backend = backend
        self.call_id = call_id
        self._llm = llm
        self.transcript: list[Turn] = []
        self.tool_calls: list[ToolCall] = []
        self._state = CallState()
        self._tools = ToolBox(backend, call_id, self._state)
        self._messages: list[Message] = []

    def say(self, speaker: str, text: str) -> None:
        self._backend.post(
            f"/calls/{self.call_id}/turns", json={"speaker": speaker, "text": text}
        ).raise_for_status()
        self.transcript.append(Turn(speaker, text))
        role = "caller" if speaker == "caller" else "agent"
        self._messages.append(Message(role, text))

    def hear(self, caller_text: str) -> list[str]:
        """The Caller says something. Returns what the Caller hears in reply."""
        assert self._llm is not None, "this Call has no LLM"
        self.say("caller", caller_text)
        if self._state.awaiting_read_back_answer and is_explicit_yes(caller_text):
            self._tools.record_read_back_agreement()
        spoken: list[str] = []
        for _ in range(MAX_STEPS_PER_TURN):
            step = self._llm.next_step(
                LLMTurn(self._state.summary(), list(self._messages), TOOL_SPECS)
            )
            if isinstance(step, Say):
                if unoffered_times(step.text, self._state.ever_offered):
                    # Withheld: the Caller never hears a time Availability did not return.
                    error = {"error": "you named a time that Availability did not return"}
                    self._messages.append(Message("tool", json.dumps(error)))
                    continue
                self._speak(step.text, spoken)
                return spoken
            call_text = json.dumps({"tool": step.name, "args": step.args})
            self._messages.append(Message("tool_call", call_text))
            result = self._tools.run(step.name, step.args)
            self.tool_calls.append(
                ToolCall(step.name, step.args, result.ok, result.refused, result.data)
            )
            if result.speak is not None:
                self._speak(result.speak, spoken)
                return spoken
            payload = {"tool": step.name, "ok": result.ok, **result.data}
            self._messages.append(Message("tool", json.dumps(payload)))
        self._speak(STUCK, spoken)
        return spoken

    def _speak(self, text: str, spoken: list[str]) -> None:
        self.say("agent", text)
        spoken.append(text)

    def end(self) -> None:
        self._backend.post(f"/calls/{self.call_id}/end").raise_for_status()


def open_text_call(backend: httpx.Client, llm: LLM | None = None) -> TextCall:
    response = backend.post("/calls")
    response.raise_for_status()
    call = TextCall(backend, int(response.json()["id"]), llm)
    call.say("agent", DISCLOSURE)
    return call
