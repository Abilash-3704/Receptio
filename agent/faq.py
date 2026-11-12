"""FAQ and scope limits, decided in code where code can decide.

- Clinic facts come only from the backend knowledge base (`search_faq` tool). Whatever facts were
  looked up during this Call are the only clock times and rupee amounts the agent may say.
- A question the knowledge base cannot answer gets a fixed honest reply and an offer of a
  Callback Request (accepted, it goes through the Escalation path).
- A request for medical advice is refused with a fixed redirect before the LLM is consulted.
  The patterns are deliberately narrow: a missed phrasing falls through to the LLM, whose prompt
  also forbids medical advice; a false match costs one redirect."""

import re

from agent.time_guard import clock_times_in

NO_ANSWER_SPEECH = (
    "I'm sorry, I don't have that information. I can have our staff call you back about it. "
    "Would you like that?"
)
MEDICAL_SPEECH = (
    "I'm sorry, I can't give medical advice. Please speak to a dentist about that. "
    "I can book you an appointment, or have our staff call you back. Which would you prefer?"
)
NO_ANSWER_REASON = "question the knowledge base could not answer: {question}"
MEDICAL_REASON = "caller asked for medical advice, which the agent does not give"


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", text.casefold().replace("'", "")).split())


_MEDICAL = [
    re.compile(
        r"\b(ibuprofen|paracetamol|acetaminophen|aspirin|painkillers?|pain killers?|"
        r"antibiotics?|amoxicillin|clove oil|dosage|dose|medicine|medication|tablets?)\b"
    ),
    re.compile(r"\bshould i (take|use|put|apply|stop|eat|ignore)\b"),
    re.compile(r"\bwhat (can|should) i (take|use|put|apply)\b"),
    re.compile(r"\b(is it|would it be) (ok|okay|safe) to\b"),
    re.compile(r"\bhow (do|can|should) i (treat|cure|ease|relieve|soothe|fix)\b"),
    re.compile(r"\b(diagnose|diagnosis|is this infected|is it infected)\b"),
]


def asks_for_medical_advice(text: str) -> bool:
    plain = _plain(text)
    return any(pattern.search(plain) for pattern in _MEDICAL)


_NEGATIVE = re.compile(r"\b(no|not|dont|nope|nah|never|cancel)\b")
_CALL_ME = re.compile(r"\b(call|ring|phone) me\b|\bcall back\b|\bcallback\b|\bring me\b")


def accepts_callback(text: str) -> bool:
    """Did the Caller ask for the staff callback in so many words ("yes, call me back")? A bare
    "yes" is handled by the state machine's explicit-yes path; "yes, tomorrow then" is neither."""
    plain = _plain(text)
    return not _NEGATIVE.search(plain) and bool(_CALL_ME.search(plain))


def declines_callback(text: str) -> bool:
    return bool(_NEGATIVE.search(_plain(text)))


AMOUNT = re.compile(
    r"(?:\brs\.?|₹|\binr)\s*(?P<a>\d[\d,]*)|(?P<b>\d[\d,]*)\s*(?:rupees|\brs\b)", re.IGNORECASE
)


def amounts_in(text: str) -> set[int]:
    return {int((m["a"] or m["b"]).replace(",", "")) for m in AMOUNT.finditer(text)}


def unknown_amounts(speech: str, known_facts: list[str]) -> set[int]:
    """Rupee amounts in `speech` that no looked-up fact contains: invented prices or quotes."""
    known = {a for fact in known_facts for a in amounts_in(fact)}
    return amounts_in(speech) - known


def known_clock_times(known_facts: list[str]) -> set[tuple[int, int]]:
    return {t for fact in known_facts for t in clock_times_in(fact)}
