"""Text-mode Call: the seam used by tests and evals. Never imports LiveKit.

`TextCall.hear` is the call state machine's turn loop: Caller text in, what the Caller hears out.
The state machine records the read-back "yes" itself (ADR-0001); the LLM never can."""

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from agent.call_limits import CLOSING_SPEECH, DURATION_REASON, CallLimits
from agent.call_state import CallState
from agent.escalation import (
    ASK_NUMBER,
    ASK_AGAIN_AFTER_DROPOUT,
    ASK_NUMBER_AGAIN,
    CALLBACK_NO_NUMBER_SPEECH,
    CALLBACK_SPEECH,
    DROPPED_REASON,
    ESCALATION_UNAVAILABLE,
    HANDOFF_SPEECH,
    HUMAN_REASON,
    LLM_DOWN_REASON,
    MAX_FAILURES_PER_STEP,
    MAX_NUMBER_ASKS,
    OFFER_AFTER_DROPOUT,
    OFFER_AFTER_FAILURE,
    OFFER_OUTCOME_UNKNOWN,
    SORRY_FOR_THE_WAIT,
    SPEECH_FAILED_REASON,
    SPEECH_UNHEARD_REASON,
    asks_for_human,
    failed_twice_reason,
    forced_kind,
    phone_in,
    unavailable_reason,
)
from agent.faq import (
    MEDICAL_REASON,
    MEDICAL_SPEECH,
    accepts_callback,
    asks_for_medical_advice,
    declines_callback,
    known_clock_times,
    unknown_amounts,
)
from agent.llm import LLM, LLMError, LLMTurn, LLMUsage, Message, Say, Step, UseTool
from agent.recording import (
    RECORDING_OBJECTION_REASON,
    RECORDING_OBJECTION_SPEECH,
    objects_to_recording,
)
from agent.safety import (
    EMERGENCY,
    EMERGENCY_REASON,
    EMERGENCY_SCRIPT,
    URGENT,
    URGENT_HANDOFF_SPEECH,
    URGENT_REASON,
    detect,
)
from agent.time_guard import unoffered_times
from agent.tools import TOOL_SPECS, BackendUnavailable, ToolBox, ToolCall, ToolResult
from backend.app.clock import Clock, system_clock

logger = logging.getLogger("receptio.agent")

DISCLOSURE = (
    "Hello, you've reached the clinic's receptionist. I'm an AI assistant, "
    "and this call may be recorded. How can I help you today?"
)

# Most tool steps the LLM gets in one turn before the agent gives up on it.
MAX_STEPS_PER_TURN = 8
STUCK = "I'm sorry, I'm having trouble with that. Could you say it again?"
# Tools whose unanswered request leaves a booking or cancellation in an unknown state.
OUTCOME_UNKNOWN_TOOLS = {"confirm_appointment", "cancel_appointment"}


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


def is_explicit_yes(text: str, action: str | None = None) -> bool:
    """A plain yes. With `action`, the word for what the read-back asked ("cancel", "book") is
    also allowed once, so "yes, cancel it" counts for the cancel read-back only. It must follow
    the affirmative, and every other word must still be filler: "yes, book it" does not answer
    a cancel read-back, and "yes, but..." or "yes, cancel it tomorrow" answer nothing."""
    words = re.sub(r"[^a-z' ]", " ", text.casefold().replace("'", "")).split()
    if "?" in text or not words or words[0] not in AFFIRMATIVES:
        return False
    allowed = FILLER | AFFIRMATIVES
    if action is not None:
        allowed = allowed | {action, "and"}
        if words.count(action) > 1:
            return False
    return all(word in allowed for word in words)


class TextCall:
    def __init__(
        self,
        backend: httpx.Client,
        call_id: int,
        llm: LLM | None = None,
        clock: Clock = system_clock,
        limits: CallLimits | None = None,
    ) -> None:
        self._backend = backend
        self._clock = clock
        self._limits = limits
        self._started = clock()
        self._lock = threading.RLock()
        self._recording = True
        self.call_id = call_id
        self._llm = llm
        self.transcript: list[Turn] = []
        self.tool_calls: list[ToolCall] = []
        self._state = CallState()
        self._tools = ToolBox(backend, call_id, self._state, clock)
        self._messages: list[Message] = []
        # Transcript position of the Caller turn being answered: tool calls are filed under it.
        self._caller_turn_seq = 0
        # Trace writes that failed. The Call carries on; the trace is then incomplete.
        self.trace_failures = 0

    @property
    def caller_turn_seq(self) -> int:
        """Transcript position (1-based) of the latest Caller turn."""
        return self._caller_turn_seq

    def say(self, speaker: str, text: str) -> None:
        if self._recording:  # after an objection to recording, nothing more is stored
            try:
                response = self._backend.post(
                    f"/calls/{self.call_id}/turns", json={"speaker": speaker, "text": text}
                )
                response.raise_for_status()
                if speaker == "caller":
                    self._caller_turn_seq = len(response.json()["transcript"])
            except httpx.HTTPError:
                # The Caller must still be answered when the transcript cannot be stored (for
                # example when the backend is down): the stored record is then incomplete.
                self.trace_failures += 1
                logger.warning("call %s: storing a turn failed", self.call_id)
                if speaker == "caller":
                    self._caller_turn_seq = len(self.transcript) + 1
        self.transcript.append(Turn(speaker, text))
        role = "caller" if speaker == "caller" else "agent"
        self._messages.append(Message(role, text))

    @property
    def expired(self) -> bool:
        """The Call has reached its maximum duration and has been closed."""
        return self._state.limit_reached

    def _elapsed(self) -> float:
        return (self._clock() - self._started).total_seconds()

    def _limit_speech(self) -> list[str]:
        """What is due from the duration cap right now: the spoken close at the maximum, or the
        warning (once) from the warning time on. The warning offers a Callback Request, so a
        Caller who says yes reaches the same forced Callback Request path as any other offer."""
        limits, state = self._limits, self._state
        if limits is None:
            return []
        spoken: list[str] = []
        if self._elapsed() >= limits.max_seconds:
            if not state.limit_reached:
                state.limit_reached = True
                self._speak(CLOSING_SPEECH, spoken)
            return spoken
        warn = not (state.limit_warned or state.escalated or state.callback_reason is not None)
        if self._elapsed() >= limits.warning_seconds and warn:
            state.limit_warned = True
            state.callback_offer = DURATION_REASON
            self._speak(limits.warning_speech(), spoken)
        return spoken

    def time_check(self) -> list[str]:
        """For a Caller who is silent: what the duration cap says now, if anything is due. The
        voice worker calls this on a timer; it is spoken and stored like any agent turn."""
        with self._lock:
            return self._limit_speech()

    def hear(self, caller_text: str) -> list[str]:
        """The Caller says something. Returns what the Caller hears in reply."""
        assert self._llm is not None, "this Call has no LLM"
        with self._lock:
            if self._state.limit_reached or (
                self._limits is not None and self._elapsed() >= self._limits.max_seconds
            ):
                self.say("caller", caller_text)
                # Closed: say so (again), never silence, and never consult the LLM.
                return self._limit_speech() or [CLOSING_SPEECH]
            return self._turn(caller_text) + self._limit_speech()

    def _turn(self, caller_text: str) -> list[str]:
        assert self._llm is not None, "this Call has no LLM"
        objection = objects_to_recording(caller_text)
        if objection:
            self._recording = False  # the objection itself is not stored either
        self.say("caller", caller_text)
        # Safety first, in code, on every turn: before any other state and before the LLM.
        level = detect(caller_text)
        if level == EMERGENCY:
            return self._emergency(caller_text)
        if level == URGENT and self._state.safety_level is None:
            return self._urgent(caller_text)
        if self._state.escalated:
            return []  # the Call is with staff now
        self._state.asr_dropouts = 0  # the Caller was heard
        if self._state.callback_reason is not None:
            return self._take_callback_number(caller_text)
        if objection:
            return self._record_objection()
        if asks_for_human(caller_text):
            return self._escalate(HUMAN_REASON)
        state = self._state
        if state.failure_offer is not None:
            # Staff were offered after a failure; the Caller's next words answer that offer.
            reason, state.failure_offer = state.failure_offer, None
            if is_explicit_yes(caller_text) or accepts_callback(caller_text):
                return self._escalate(reason)
        if state.callback_offer is not None and not (
            state.awaiting_read_back_answer or state.pending_date is not None
        ):
            if accepts_callback(caller_text):
                reason, state.callback_offer = state.callback_offer, None
                return self._escalate(reason)
            if declines_callback(caller_text):
                state.callback_offer = None
        if asks_for_medical_advice(caller_text):
            state.callback_offer = MEDICAL_REASON
            medical: list[str] = []
            self._speak(MEDICAL_SPEECH, medical)
            return medical
        spoken: list[str] = []
        if is_explicit_yes(caller_text, "book" if state.awaiting_read_back_answer else None):
            if self._state.awaiting_read_back_answer:
                if not self._record_read_back_agreement(spoken):
                    return spoken
            elif self._state.pending_date is not None:
                # The state machine, not the LLM, records that the Caller agreed to this date.
                self._state.confirmed_dates.add(self._state.pending_date)
            elif self._state.callback_offer is not None:
                reason, self._state.callback_offer = self._state.callback_offer, None
                return self._escalate(reason)
        if self._state.awaiting_cancel_answer:
            self._tools.record_cancel_answer(is_explicit_yes(caller_text, "cancel"))
        for _ in range(MAX_STEPS_PER_TURN):
            step = self._next_step(spoken)
            if step is None:
                return spoken  # the LLM is down: the Caller has been offered staff
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
            result = self._run_tool(step, spoken)
            self.tool_calls.append(
                ToolCall(step.name, step.args, result.ok, result.refused, result.data)
            )
            self._trace(
                "tool-calls",
                {
                    "turn_seq": self._caller_turn_seq,
                    "name": step.name,
                    "args": step.args,
                    "ok": result.ok,
                    "refused": result.refused,
                    "result": result.data,
                },
            )
            if result.unavailable:
                # The clinic system did not answer, twice. Whether it did the work is unknown,
                # so nothing is claimed as done (fail closed); staff are offered instead.
                unknown = step.name in OUTCOME_UNKNOWN_TOOLS
                self._offer_staff(
                    unavailable_reason(step.name),
                    OFFER_OUTCOME_UNKNOWN if unknown else OFFER_AFTER_FAILURE,
                    spoken,
                )
                return spoken
            failures = self._state.record_outcome(step.name, result.ok)
            if failures >= MAX_FAILURES_PER_STEP:
                return spoken + self._escalate(failed_twice_reason(step.name))
            if result.speak is not None:
                self._speak(result.speak, spoken)
                return spoken
            payload = {"tool": step.name, "ok": result.ok, **result.data}
            self._messages.append(Message("tool", json.dumps(payload)))
        self._speak(STUCK, spoken)
        return spoken

    def _next_step(self, spoken: list[str]) -> Step | None:
        """Ask the LLM for the next step; if it fails, once more. Twice failing is an LLM outage:
        the Caller is told so and offered staff, and None is returned. Safety, escalation and the
        Caller's answer to that offer are all code, so they work with the LLM down."""
        assert self._llm is not None
        for attempt in (1, 2):
            try:
                step = self._llm.next_step(
                    LLMTurn(self._state.summary(), list(self._messages), TOOL_SPECS)
                )
            except LLMError:
                logger.warning("call %s: the LLM failed (attempt %d)", self.call_id, attempt)
                continue
            self._trace_usage()
            if attempt == 2:
                self._speak(SORRY_FOR_THE_WAIT, spoken)
            return step
        self._offer_staff(LLM_DOWN_REASON, OFFER_AFTER_FAILURE, spoken)
        return None

    def _run_tool(self, step: UseTool, spoken: list[str]) -> ToolResult:
        """Run a tool; if the clinic system did not answer, once more. A write is retried under
        the same Idempotency-Key, so a request that did commit is never done twice."""
        result = self._tools.run(step.name, step.args)
        if result.unavailable:
            result = self._tools.run(step.name, step.args)
            if not result.unavailable:
                self._speak(SORRY_FOR_THE_WAIT, spoken)
        return result

    def _record_read_back_agreement(self, spoken: list[str]) -> bool:
        """Store the Caller's yes to the read-back (same retry rule). False: the clinic system did
        not answer; the Caller has been offered staff and nothing is recorded."""
        for attempt in (1, 2):
            try:
                self._tools.record_read_back_agreement()
            except (BackendUnavailable, httpx.TransportError):
                continue
            if attempt == 2:
                self._speak(SORRY_FOR_THE_WAIT, spoken)
            return True
        self._offer_staff(unavailable_reason("read_back"), OFFER_AFTER_FAILURE, spoken)
        return False

    def _offer_staff(self, reason: str, speech: str, spoken: list[str]) -> None:
        """Apologise and offer a Callback Request or Handoff. A yes on the Caller's next turn
        passes the Call to staff (`_escalate`), which never needs the LLM."""
        self._state.failure_offer = reason
        self._speak(speech, spoken)

    def asr_dropout(self) -> list[str]:
        """The speech recogniser lost the Caller (the voice layer calls this). The first time in a
        row: an apology and a request to repeat. The second: an apology and an offer of staff."""
        with self._lock:
            state = self._state
            if state.escalated or state.limit_reached:
                return []
            state.asr_dropouts += 1
            spoken: list[str] = []
            if state.asr_dropouts == 1:
                self._speak(ASK_AGAIN_AFTER_DROPOUT, spoken)
            else:
                self._offer_staff(SPEECH_UNHEARD_REASON, OFFER_AFTER_DROPOUT, spoken)
            return spoken

    def speech_failed(self) -> None:
        """Speech synthesis failed even after a retry (the voice layer calls this): the Caller
        cannot be told anything. A Callback Request is recorded, with the number if one was given;
        the Hold goes back."""
        with self._lock:
            if self._state.escalated:
                return
            self._tools.release_hold()
            try:
                self._record_escalation(
                    SPEECH_FAILED_REASON, self._state.patient_phone, key="speech-failed", speak=False
                )
            except httpx.HTTPError:
                logger.warning("call %s: recording the voice failure failed", self.call_id)

    def _twice(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Send a request; if the backend did not answer (timeout, 5xx), once more. Only for
        requests that are safe to repeat (reads, and writes with a fixed Idempotency-Key)."""
        for attempt in (1, 2):
            try:
                response = send()
            except httpx.TransportError:
                if attempt == 2:
                    raise
                continue
            if response.status_code >= 500 and attempt == 1:
                continue
            return response
        raise AssertionError("unreachable")

    def _escalation_unavailable(self, reason: str, key: str) -> list[str]:
        """Staff could not be reached either: say so plainly. The offer stays open, so a yes on
        the next turn tries again (not for Emergency or Urgent: those were already spoken)."""
        logger.warning("call %s: the escalation could not be recorded", self.call_id)
        spoken: list[str] = []
        if key == "escalation":
            self._state.failure_offer = reason
        self._speak(ESCALATION_UNAVAILABLE, spoken)
        return spoken

    def _record_objection(self) -> list[str]:
        """A Callback Request instead of a Call. It is always a Callback Request, never a
        Handoff, because the Call itself cannot go on unrecorded."""
        spoken: list[str] = []
        self._tools.release_hold()
        phone = self._state.patient_phone
        if phone is None:
            self._state.callback_reason = RECORDING_OBJECTION_REASON
            self._state.number_asks = 1
            self._speak(RECORDING_OBJECTION_SPEECH, spoken)
            return spoken
        try:
            return self._record_escalation(RECORDING_OBJECTION_REASON, phone)
        except httpx.HTTPError:
            return self._escalation_unavailable(RECORDING_OBJECTION_REASON, "escalation")

    def _emergency(self, caller_text: str) -> list[str]:
        """The fixed script is the only thing said. The Escalation is recorded straight away,
        with whatever number is known and without asking for one: the Caller has been told to
        hang up and call 112."""
        spoken: list[str] = []
        self._speak(EMERGENCY_SCRIPT, spoken)
        state = self._state
        if state.safety_level != EMERGENCY:  # a repeat repeats the script, not the Escalation
            state.safety_level = EMERGENCY
            self._tools.release_hold()
            state.callback_reason = None
            phone = state.patient_phone or phone_in(caller_text)
            try:
                self._record_escalation(EMERGENCY_REASON, phone, key="emergency", speak=False)
            except httpx.HTTPError:
                # The Caller still hears the script (it is already in `spoken`). The record is
                # tried again if the Caller says more.
                logger.warning("call %s: the Emergency could not be recorded", self.call_id)
                state.safety_level = None
        return spoken

    def _urgent(self, caller_text: str) -> list[str]:
        """Same-day staff triage: no 112 script, no scheduling, no advice."""
        self._state.safety_level = URGENT
        self._tools.release_hold()
        return self._escalate(URGENT_REASON, phone_in(caller_text), key="urgent")

    def _escalate(
        self, reason: str, phone: str | None = None, key: str = "escalation"
    ) -> list[str]:
        """Pass the Call to staff. Handoff or Callback Request is the backend's decision from
        Clinic Hours, except for the reasons that always mean a Callback Request (too soon to book,
        failed identification). A Callback Request needs a number, so ask if the Call has none."""
        spoken: list[str] = []
        state = self._state
        # Any Escalation ends scheduling: the Hold goes back at once rather than lapsing.
        self._tools.release_hold()
        phone = state.patient_phone or phone
        try:
            status = self._twice(lambda: self._backend.get("/clinic-status"))
            status.raise_for_status()
            is_open: bool = status.json()["open"]
            if (not is_open or forced_kind(reason) is not None) and phone is None:
                state.callback_reason = reason
                state.number_asks = 1
                self._speak(ASK_NUMBER, spoken)
                return spoken
            return self._record_escalation(reason, phone, key=key)
        except httpx.HTTPError:
            return self._escalation_unavailable(reason, key)

    def _take_callback_number(self, caller_text: str) -> list[str]:
        state = self._state
        assert state.callback_reason is not None
        phone = phone_in(caller_text)
        if phone is None and state.number_asks < MAX_NUMBER_ASKS:
            state.number_asks += 1
            spoken: list[str] = []
            self._speak(ASK_NUMBER_AGAIN, spoken)
            return spoken
        key = "urgent" if state.safety_level == URGENT else "escalation"
        try:
            return self._record_escalation(state.callback_reason, phone, key=key)
        except httpx.HTTPError:
            return self._escalation_unavailable(state.callback_reason, key)

    def _record_escalation(
        self, reason: str, phone: str | None, key: str = "escalation", speak: bool = True
    ) -> list[str]:
        """Record the Escalation (a fixed Idempotency-Key, so a retry never records a second).
        Raises `httpx.HTTPError` if the backend still did not answer after one retry."""
        response = self._twice(
            lambda: self._backend.post(
                f"/calls/{self.call_id}/escalations",
                headers={"Idempotency-Key": f"call{self.call_id}-{key}"},
                json={"reason": reason, "caller_phone": phone, "kind": forced_kind(reason)},
            )
        )
        response.raise_for_status()
        self._state.escalated = True
        self._state.callback_reason = None
        if not speak:
            return []
        if response.json()["kind"] == "handoff":
            speech = URGENT_HANDOFF_SPEECH if key == "urgent" else HANDOFF_SPEECH
        else:
            speech = CALLBACK_SPEECH if phone else CALLBACK_NO_NUMBER_SPEECH
        spoken: list[str] = []
        self._speak(speech, spoken)
        return spoken

    def _speak(self, text: str, spoken: list[str]) -> None:
        self.say("agent", text)
        spoken.append(text)

    def _trace_usage(self) -> None:
        usage: LLMUsage | None = getattr(self._llm, "last_usage", None)
        if usage is not None:
            self._trace(
                "llm-calls",
                {
                    "turn_seq": self._caller_turn_seq,
                    "model": usage.model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "duration_ms": usage.duration_ms,
                },
            )

    def _trace(self, path: str, body: dict[str, Any]) -> None:
        """Best-effort write of one trace record: a Caller is never failed because the trace
        could not be stored. Logs the Call id only, never what was said."""
        if not self._recording and path == "tool-calls":
            return  # tool arguments can hold what the Caller said: not after an objection
        try:
            self._backend.post(f"/calls/{self.call_id}/{path}", json=body).raise_for_status()
        except Exception:  # noqa: BLE001 - best effort by design; see the docstring
            self.trace_failures += 1
            logger.warning("call %s: trace write to %s failed", self.call_id, path)

    def end(self) -> None:
        """The Call is over: the Caller hung up or left. It is recorded as dropped when the Caller
        never spoke, or left mid-flow with nothing confirmed. (An Escalation makes it escalated,
        which the backend decides from the Escalation it holds.) A Hold left behind is not
        released: it lapses on its own."""
        body: dict[str, Any] = {"outcome": "completed"}
        left_at: str | None = None
        if not any(t.speaker == "caller" for t in self.transcript):
            body = {"outcome": "dropped", "reason": "caller silent: no speech"}
        elif (left_at := self._state.unfinished_step()) is not None:
            body = {"outcome": "dropped", "reason": f"caller left before confirming: {left_at}"}
        self._twice(
            lambda: self._backend.post(f"/calls/{self.call_id}/end", json=body)
        ).raise_for_status()
        if left_at is not None:
            self._follow_up_dropped_call(left_at)

    def _follow_up_dropped_call(self, step: str) -> None:
        """A Caller who left mid-flow and gave a number in this Call is owed a Callback Request,
        so staff can finish what was started. It is recorded after the Call is ended, so the Call
        keeps its dropped outcome (the backend marks a Call escalated only if an Escalation exists
        when it ends). Without a number there is nobody to ring: only the outcome is stored."""
        phone = self._state.patient_phone
        if phone is None:
            return
        try:
            self._record_escalation(
                DROPPED_REASON.format(step=step), phone, key="dropped", speak=False
            )
        except httpx.HTTPError:
            logger.warning("call %s: the follow-up Callback Request was not stored", self.call_id)


def open_text_call(
    backend: httpx.Client,
    llm: LLM | None = None,
    clock: Clock = system_clock,
    limits: CallLimits | None = None,
    mode: Literal["text", "voice"] = "text",
) -> TextCall:
    response = backend.post("/calls", json={"mode": mode})
    response.raise_for_status()
    call = TextCall(backend, int(response.json()["id"]), llm, clock, limits)
    call.say("agent", DISCLOSURE)
    return call
