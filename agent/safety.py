# ruff: noqa: E501  (the rule table is regular expressions, kept one rule per entry)
"""Safety layer: the Emergency and Urgent Case detector, and the fixed Emergency script.

The detector runs in code on every Caller turn, before the LLM is consulted, so a mid-scheduling
Emergency cannot be talked past by the model. It is a deliberate rule list, not a classifier: it
is deterministic, auditable and testable. It over-flags on purpose where a miss costs more than a
needless call to staff. docs/safety.md says what it cannot cover.

Emergency (life-threatening: breathing, uncontrolled bleeding, severe trauma, collapse) gets the
fixed script and an Escalation. Urgent Case (same-day dental problem, not life-threatening) gets
an Escalation and no script. See GLOSSARY.md: the two are distinct."""

import re
from typing import Final

EMERGENCY: Final = "emergency"
URGENT: Final = "urgent_case"

# Fixed, versioned text, true whether or not the clinic is open (spec: Further Notes). It is never
# generated and nothing else is said on the turn it is spoken. Change the text only together with
# the version, so a trace says which wording a Caller heard.
EMERGENCY_SCRIPT_VERSION: Final = "emergency-script-v1"
EMERGENCY_SCRIPT: Final = (
    "This sounds like a medical emergency. Please end this call and call 112 right now, "
    "or go to your nearest emergency room. I'm alerting our clinic staff as well."
)

EMERGENCY_REASON: Final = "Emergency: Caller described a life-threatening situation"
URGENT_REASON: Final = "Urgent Case: Caller described a same-day problem for staff triage"

# Said by code for an Urgent Case when the Call is with staff: no advice, no booking.
URGENT_HANDOFF_SPEECH: Final = (
    "That sounds like it needs attention today. I'm connecting you to a member of our staff "
    "now so they can look after you. Please hold."
)

_NEGATIONS = {"no", "not", "nothing", "never", "without", "isnt", "dont", "doesnt", "wasnt"}
_NEGATION_WINDOW = 2


def _normalise(text: str) -> str:
    """Lower case, apostrophes dropped (can't -> cant), sentence punctuation kept as a `|`
    boundary so a rule never reads across two sentences, everything else spaces."""
    lowered = text.casefold().replace("’", "'").replace("'", "")
    lowered = re.sub(r"[.!?;:,\n]", " | ", lowered)
    lowered = re.sub(r"[^a-z0-9| ]", " ", lowered)
    return " ".join(lowered.split())


def _rule(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_NOSE = r"(?! (through|out of) (my |his |her )?nose)"
_BREATHE = r"(breathe|breathing|breath)"

# (level, pattern, negatable). `negatable`: a "no"/"not"/"nothing" just before the match cancels
# it. Emergency rules that are worded as a negation themselves ("not breathing") never are, so
# "no, he is not breathing" cannot cancel itself.
_RULES: list[tuple[str, re.Pattern[str], bool]] = [
    # Breathing and airway.
    (
        EMERGENCY,
        _rule(
            rf"\b(cant|cannot|can not|couldnt|unable to|struggling to|struggle to) (\w+ )?{_BREATHE}{_NOSE}"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            rf"\b(trouble|difficulty|problems?|hard to|hardly able to|barely able to) {_BREATHE}{_NOSE}"
        ),
        True,
    ),
    (EMERGENCY, _rule(rf"\b(not|stopped|stop|stops|stopping) {_BREATHE}"), False),
    (
        EMERGENCY,
        _rule(
            r"\b(short of breath|shortness of breath|gasping|gasp for|choking|choke[sd]?|suffocat\w*|wheezing)\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(r"\b(cant|cannot|unable to|trouble|difficulty) (swallow|swallowing)\b"),
        True,
    ),
    (
        EMERGENCY,
        _rule(r"\b(lips?|face|skin) (is |are |turning |going |has gone |gone )?(turning )?blue\b"),
        False,
    ),
    (EMERGENCY, _rule(r"\bblue lips\b"), False),
    # Swelling that threatens the airway or spreads fast.
    (
        EMERGENCY,
        _rule(
            r"\b(throat|tongue|neck|airway)\b[^|]{0,25}\b(swell\w*|swollen|closing|tight\w*|shut)\b"
        ),
        True,
    ),
    (EMERGENCY, _rule(r"\bswell\w*\b[^|]{0,20}\b(neck|eye|eyes|throat|tongue)\b"), True),
    (
        EMERGENCY,
        _rule(
            r"\b(face|mouth|lip|lips|cheek|jaw)\b[^|]{0,15}\b(swell\w*|swollen)\b[^|]{0,12}\b(badly|fast|rapidly|quickly|massively|spreading)\b"
        ),
        True,
    ),
    (EMERGENCY, _rule(r"\bswelling (is )?(spreading|getting worse fast)\b"), True),
    (EMERGENCY, _rule(r"\bswollen shut\b"), False),
    # Bleeding that is not under control.
    (
        EMERGENCY,
        _rule(
            r"\b(cant|cannot|unable to|wont|will not|couldnt) (seem to )?stop (the |my |his |her )?bleeding\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            r"\bbleeding\b[^|]{0,30}\b(wont|will not|doesnt|isnt|hasnt|not|cant be) (stop\w*|slow\w*)\b"
        ),
        False,
    ),
    (EMERGENCY, _rule(r"\b(uncontrolled|uncontrollable|profuse|heavy|severe) bleeding\b"), True),
    (
        EMERGENCY,
        _rule(
            r"\bbleeding (heavily|profusely|badly|nonstop|non stop|constantly|uncontrollably|a lot|for (hours|\d+ hours))\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            r"\b(blood (is )?(everywhere|pouring|gushing|spurting)|mouth (is )?full of blood|haemorrhag\w*|hemorrhag\w*)\b"
        ),
        False,
    ),
    (EMERGENCY, _rule(r"\b(losing|lost) (a lot of |lots of |so much |too much )?blood\b"), False),
    # Collapse, trauma, medical events.
    (
        EMERGENCY,
        _rule(
            r"\b(unconscious|unresponsive|passed out|blacked out|collapsed|fainted|knocked out cold|knocked unconscious)\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(r"\b(not responding|wont wake up|not waking up|isnt waking up|isnt responding)\b"),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            r"\b(heart attack|chest pain|pain in (my|his|her) chest|chest (tightness|pressure)|cardiac arrest)\b"
        ),
        False,
    ),
    (EMERGENCY, _rule(r"\b(having|think|thinks|suspect|looks like) (\w+ ){0,2}stroke\b"), False),
    (
        EMERGENCY,
        _rule(
            r"\b(having a seizure|seizing|convuls\w*|overdos\w*|anaphyla\w*|having an allergic reaction|allergic reaction and)\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(r"\b(car|bike|road|motorbike|motorcycle|scooter|traffic) (accident|crash)\b"),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            r"\b(hit by a (car|bus|truck|vehicle)|hit (my|his|her|their) head|head injury|banged (my|his|her) head)\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            r"\b(jaw (is |was |looks )?(broken|fractured|dislocated)|(broken|fractured|dislocated) jaw)\b"
        ),
        False,
    ),
    (
        EMERGENCY,
        _rule(r"\b(medical emergency|life threatening|life-threatening|ambulance)\b"),
        False,
    ),
    (
        EMERGENCY,
        _rule(
            r"\b(kill myself|killing myself|suicid\w*|end my life|want to die|going to die|gonna die|am dying|im dying|is dying)\b"
        ),
        False,
    ),
    # Urgent Case: a dental problem for same-day staff triage.
    (
        URGENT,
        _rule(
            r"\b(severe|serious|extreme|intense|terrible|horrible|awful|excruciating|unbearable|agonising|agonizing|really bad|so bad|bad) (\w+ )?(pain|toothache|tooth ache)\b"
        ),
        True,
    ),
    (
        URGENT,
        _rule(
            r"\b(pain|toothache|tooth ache|tooth) (is |was |has been |feels |got )?(\w+ )?(severe|unbearable|excruciating|terrible|horrible|awful|agonising|agonizing|really bad|so bad)\b"
        ),
        True,
    ),
    (
        URGENT,
        _rule(r"\b(excruciating|unbearable|in agony|killing me|worst (tooth ?ache|pain))\b"),
        True,
    ),
    (URGENT, _rule(r"\b(a lot of|so much|lots of|too much) pain\b"), True),
    (URGENT, _rule(r"\bknocked (\w+ ){0,3}out\b"), False),
    (URGENT, _rule(r"\b(tooth|teeth) (\w+ )?(fell|came|popped|dropped|got) (\w+ )?out\b"), False),
    (
        URGENT,
        _rule(
            r"\b(broke|broken|cracked|snapped|split|fractured) (my |a |the |her |his |their |our )?(front |back |upper |lower |molar )?(tooth|teeth)\b"
        ),
        True,
    ),
    (
        URGENT,
        _rule(r"\b(tooth|teeth) (is |are |has |have )?(broken|cracked|snapped|fractured|broke)\b"),
        True,
    ),
    (
        URGENT,
        _rule(r"\b(cheek|face|jaw|gum|gums|mouth|lip)\b[^|]{0,12}\b(swollen|swelling|puffy)\b"),
        True,
    ),
    (URGENT, _rule(r"\b(swollen|puffy) (cheek|face|jaw|gum|gums)\b"), True),
    (URGENT, _rule(r"\b(abscess|pus|dry socket|infected tooth)\b"), True),
    (URGENT, _rule(r"\bstill bleeding\b"), True),
    (
        URGENT,
        _rule(
            r"\bbleeding (after|since) (my |the )?(extraction|tooth was pulled|surgery|procedure)\b"
        ),
        True,
    ),
    (
        URGENT,
        _rule(
            r"\b(a |an |the )?(dental |tooth )emergency(?! (meeting|contact|exit|number|room|plan|policy|services?|line|hours|call))\b"
        ),
        True,
    ),
    (
        URGENT,
        _rule(
            r"\b(have|had|got|its|this is|thats|is) an emergency(?! (meeting|contact|exit|number|room|plan|policy|services?|line|hours|call))\b"
        ),
        True,
    ),
    (URGENT, _rule(r"\b(urgent|urgently|same day|emergency appointment)\b"), True),
]


def _negated(words: list[str], start_word: int) -> bool:
    window = words[max(0, start_word - _NEGATION_WINDOW) : start_word]
    if "|" in window:
        window = window[len(window) - 1 - window[::-1].index("|") + 1 :]
    return any(word in _NEGATIONS for word in window)


def detect(text: str) -> str | None:
    """EMERGENCY, URGENT or None for what the Caller just said. An Emergency wins over an Urgent
    Case in the same turn."""
    normalised = _normalise(text)
    words = normalised.split()
    found: set[str] = set()
    for level, pattern, negatable in _RULES:
        if level in found:
            continue
        for match in pattern.finditer(normalised):
            start_word = len(normalised[: match.start()].split())
            if negatable and _negated(words, start_word):
                continue
            found.add(level)
            break
    if EMERGENCY in found:
        return EMERGENCY
    return URGENT if URGENT in found else None
