"""Text-mode Call: the seam used by tests and evals. Never imports LiveKit.

`TextCall.hear` is the call state machine's turn loop: Caller text in, what the Caller hears out.
The state machine records the read-back "yes" itself (ADR-0001); the LLM never can."""

import json
import re
from dataclasses import dataclass

import httpx

from agent.call_state import CallState
from agent.escalation import (
    ASK_NUMBER,
    ASK_NUMBER_AGAIN,
    CALLBACK_NO_NUMBER_SPEECH,
    CALLBACK_SPEECH,
    HANDOFF_SPEECH,
    HUMAN_REASON,
    MAX_FAILURES_PER_STEP,
    MAX_NUMBER_ASKS,
    asks_for_human,
    phone_in,
)
from agent.faq import (
    MEDICAL_REASON,
    MEDICAL_SPEECH,
    accepts_callback,
    asks_for_medical_advice,
    known_clock_times,
    unknown_amounts,
)
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
        if self._state.escalated:
            return []  # the Call is with staff now
        if self._state.callback_reason is not None:
            return self._take_callback_number(caller_text)
        if asks_for_human(caller_text):
            return self._escalate(HUMAN_REASON)
        offered_reason, self._state.callback_offer_reason = self._state.callback_offer_reason, None
        if offered_reason is not None and accepts_callback(caller_text):
            return self._escalate(offered_reason)
        if asks_for_medical_advice(caller_text):
            self._state.callback_offer_reason = MEDICAL_REASON
            spoken: list[str] = []
            self._speak(MEDICAL_SPEECH, spoken)
            return spoken
        if self._state.awaiting_read_back_answer and is_explicit_yes(caller_text):
            self._tools.record_read_back_agreement()
        spoken = []
        for _ in range(MAX_STEPS_PER_TURN):
            step = self._llm.next_step(
                LLMTurn(self._state.summary(), list(self._messages), TOOL_SPECS)
            )
            if isinstance(step, Say):
                facts = self._state.known_facts
                if set(unoffered_times(step.text, self._state.ever_offered)) - known_clock_times(
                    facts
                ):
                    # Withheld: the Caller never hears a time Availability did not return.
                    error = {"error": "you named a time that Availability did not return"}
                    self._messages.append(Message("tool", json.dumps(error)))
                    continue
                if unknown_amounts(step.text, facts):
                    # Withheld: only price ranges that search_faq returned may be said.
                    error = {"error": "you named a price that search_faq did not return"}
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
            failures = self._state.record_outcome(step.name, result.ok)
            if failures >= MAX_FAILURES_PER_STEP:
                return spoken + self._escalate(f"{step.name} failed twice in a row")
            if result.speak is not None:
                self._speak(result.speak, spoken)
                return spoken
            payload = {"tool": step.name, "ok": result.ok, **result.data}
            self._messages.append(Message("tool", json.dumps(payload)))
        self._speak(STUCK, spoken)
        return spoken

    def _escalate(self, reason: str) -> list[str]:
        """Pass the Call to staff. Handoff or Callback Request is the backend's decision from
        Clinic Hours; a Callback Request needs a number, so ask if the Call has none yet."""
        spoken: list[str] = []
        state = self._state
        is_open: bool = self._backend.get("/clinic-status").json()["open"]
        if not is_open and state.patient_phone is None:
            state.callback_reason = reason
            state.number_asks = 1
            self._speak(ASK_NUMBER, spoken)
            return spoken
        return self._record_escalation(reason, state.patient_phone)

    def _take_callback_number(self, caller_text: str) -> list[str]:
        state = self._state
        assert state.callback_reason is not None
        phone = phone_in(caller_text)
        if phone is None and state.number_asks < MAX_NUMBER_ASKS:
            state.number_asks += 1
            spoken: list[str] = []
            self._speak(ASK_NUMBER_AGAIN, spoken)
            return spoken
        return self._record_escalation(state.callback_reason, phone)

    def _record_escalation(self, reason: str, phone: str | None) -> list[str]:
        response = self._backend.post(
            f"/calls/{self.call_id}/escalations",
            headers={"Idempotency-Key": f"call{self.call_id}-escalation"},
            json={"reason": reason, "caller_phone": phone},
        )
        response.raise_for_status()
        self._state.escalated = True
        self._state.callback_reason = None
        if response.json()["kind"] == "handoff":
            speech = HANDOFF_SPEECH
        else:
            speech = CALLBACK_SPEECH if phone else CALLBACK_NO_NUMBER_SPEECH
        spoken: list[str] = []
        self._speak(speech, spoken)
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
