"""Escalation, decided in code and never by the LLM: when the Caller asks for a human, or the same
step fails twice, the state machine passes the Call to clinic staff. The backend alone decides
whether that is a Handoff (during Clinic Hours) or a Callback Request (otherwise), unless the
agent forces a Callback Request (see `forced_kind`)."""

import re

from agent.call_limits import DURATION_REASON
from agent.recording import RECORDING_OBJECTION_REASON

# Failures of one step (one tool) before the agent stops trying and escalates.
MAX_FAILURES_PER_STEP = 2
# Asks for the Caller's number before giving up and recording the Callback Request without one.
MAX_NUMBER_ASKS = 2

HUMAN_REASON = "caller asked for a human"
TOO_SOON_REASON = "wanted an Appointment sooner than 2 hours ahead"
IDENTIFICATION_STEP = "identify_patient"


def failed_twice_reason(step: str) -> str:
    return f"{step} failed twice in a row"


LLM_DOWN_REASON = "the assistant's language model was unavailable"
DROPPED_PREFIX = "call dropped before finishing"
DROPPED_REASON = DROPPED_PREFIX + ": {step}"
SPEECH_FAILED_REASON = "the assistant's voice failed"
SPEECH_UNHEARD_REASON = "the assistant could not hear the Caller"


def unavailable_reason(step: str) -> str:
    return f"{step}: the clinic system did not answer"


def forced_kind(reason: str) -> str | None:
    """The cases that always mean a Callback Request, even during Clinic Hours: a request sooner
    than the booking window allows, failed identification, the Call reaching its time limit, and
    an objection to recording (the Call cannot go on). Everything else (a human asked for,
    Emergency, Urgent Case, other failures) leaves the kind to the backend."""
    if (
        reason
        in (
            TOO_SOON_REASON,
            failed_twice_reason(IDENTIFICATION_STEP),
            DURATION_REASON,
            RECORDING_OBJECTION_REASON,
            SPEECH_FAILED_REASON,  # the Caller cannot hear a Handoff either
        )
        or reason.startswith(DROPPED_PREFIX)  # the Caller has hung up
    ):
        return "callback_request"
    return None


SORRY_FOR_THE_WAIT = "Sorry for the wait."
# The offer after a failure and its one retry. The Caller answers yes to pass the Call to staff.
OFFER_AFTER_FAILURE = (
    "I'm sorry, I'm having trouble with that right now. I can pass this to our staff, who will "
    "either take over or call you back. Would you like that?"
)
# For a booking or cancellation whose outcome is unknown: nothing is claimed as done.
OFFER_OUTCOME_UNKNOWN = (
    "I'm sorry, I couldn't tell whether that went through, so I can't say it is done. I can ask "
    "our staff to check and call you back. Would you like that?"
)
ESCALATION_UNAVAILABLE = (
    "I'm sorry, I can't reach our staff system either. Please call the clinic directly, and "
    "thank you for your patience."
)
ASK_AGAIN_AFTER_DROPOUT = "I'm sorry, I didn't catch that. Could you say it again?"
OFFER_AFTER_DROPOUT = (
    "I'm sorry, I'm still having trouble hearing you. I can pass this to our staff, who will "
    "either take over or call you back. Would you like that?"
)
HANDOFF_SPEECH = "Of course. I'm connecting you to a member of our staff now. Please hold."
ASK_NUMBER = (
    "I'm sorry, our staff are not available right now, but I can have them call you back. "
    "What number should they ring?"
)
ASK_NUMBER_AGAIN = "I didn't catch a phone number. What number should our staff ring you on?"
CALLBACK_SPEECH = "Thank you. I've passed this to our staff and they will call you back."
CALLBACK_NO_NUMBER_SPEECH = (
    "I've passed this to our staff, but without a number they won't be able to call you back."
)

_ARTICLE = r"(a |an |the |your )?"
_HUMAN_WORDS = (
    r"(human|real person|actual person|live person|a person|someone|somebody|manager|"
    r"receptionist|operator|staff|representative|supervisor)"
)
_ASKS_FOR_HUMAN = [
    re.compile(rf"\b(talk|speak|chat) (to|with) {_ARTICLE}{_HUMAN_WORDS}\b"),
    re.compile(
        rf"\b(get|give|put|connect|transfer) me (through )?(to |with )?{_ARTICLE}{_HUMAN_WORDS}\b"
    ),
    re.compile(rf"\b(need|want) (to (see|talk to|speak to) )?{_ARTICLE}{_HUMAN_WORDS}\b"),
    re.compile(r"\b(human|real person|live person)\b"),
]


def asks_for_human(text: str) -> bool:
    lowered = re.sub(r"[^a-z' ]", " ", text.casefold().replace("'", ""))
    lowered = " ".join(lowered.split())
    return any(pattern.search(lowered) for pattern in _ASKS_FOR_HUMAN)


def phone_in(text: str) -> str | None:
    """A phone number the Caller said: 10 to 15 digits, optional leading +."""
    for match in re.finditer(r"\+?\d[\d\s\-()]{8,}\d", text):
        cleaned = re.sub(r"[\s\-()]", "", match.group())
        if 10 <= len(cleaned.lstrip("+")) <= 15:
            return cleaned
    return None
