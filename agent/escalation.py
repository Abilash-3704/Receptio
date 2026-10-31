"""Escalation, decided in code and never by the LLM: when the Caller asks for a human, or the same
step fails twice, the state machine passes the Call to clinic staff. The backend alone decides
whether that is a Handoff (during Clinic Hours) or a Callback Request (otherwise)."""

import re

# Failures of one step (one tool) before the agent stops trying and escalates.
MAX_FAILURES_PER_STEP = 2
# Asks for the Caller's number before giving up and recording the Callback Request without one.
MAX_NUMBER_ASKS = 2

HUMAN_REASON = "caller asked for a human"

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
