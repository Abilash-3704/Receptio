"""Objection to recording, decided in code. The disclosure says the Call may be recorded; a Caller
who objects gets a Callback Request instead of a Call, and nothing more of the Call is stored.

The patterns are deliberately narrow, as for medical advice: a missed phrasing falls through to
the normal flow (the Call stays recorded, as disclosed); a false match costs the Caller a
Callback Request they did not need. Asking *whether* the Call is recorded is not an objection."""

import re

RECORDING_OBJECTION_REASON = "caller objected to the Call being recorded"

RECORDING_OBJECTION_SPEECH = (
    "Understood, I will stop recording this call. I can't continue it unrecorded, "
    "but I can have our staff call you back. What number should they ring?"
)

_RECORD = r"record(?:ed|ing|ings)?"
_OBJECTIONS = [
    re.compile(
        rf"\b(?:dont|do not|wont|will not|cannot|cant)\s+"
        rf"(?:want|wish|consent|agree|like|allow|permit|give permission)\b[^.?!]*\b{_RECORD}\b"
    ),
    re.compile(r"\b(?:dont|do not|never)\s+record\b"),
    re.compile(rf"\bstop (?:the |this |any |all |my )?(?:call )?{_RECORD}\b"),
    re.compile(rf"\bobject(?:ing)? to\b[^.?!]*\b{_RECORD}\b"),
    re.compile(rf"\bnot (?:comfortable|happy|ok|okay|fine) (?:with|about)\b[^.?!]*\b{_RECORD}\b"),
    re.compile(rf"\b(?:rather|prefer to) not\b[^.?!]*\b{_RECORD}\b"),
    re.compile(rf"\b(?:no|without) (?:call )?{_RECORD}\b"),
]


def objects_to_recording(text: str) -> bool:
    plain = " ".join(re.sub(r"[^a-z0-9' ]", " ", text.casefold().replace("'", "")).split())
    return any(pattern.search(plain) for pattern in _OBJECTIONS)
