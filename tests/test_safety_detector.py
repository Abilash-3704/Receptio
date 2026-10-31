"""Safety layer, deterministic seam: the Emergency / Urgent Case detector on its own. Pure text in,
level out; no backend, no LLM. The corpora below are the detector's recall and false-positive
measurement on phrasings. The known gaps are written down as strict xfails, so the day the
detector learns one the test says so and the gap list stays honest."""

import pytest

from agent.safety import EMERGENCY, URGENT, detect

EMERGENCIES = [
    "I can't breathe",
    "I cannot breathe and my face is swelling badly",
    "my son is having trouble breathing after the dentist",
    "she is struggling to breathe",
    "it's hard to breathe",
    "I can't breath",
    "I'm choking",
    "my throat is closing up",
    "my throat feels tight and my tongue is swelling",
    "the swelling is spreading to my neck and eye",
    "my face is swelling really fast",
    "I can't stop the bleeding",
    "the bleeding won't stop, it's been two hours",
    "I'm bleeding heavily from my mouth",
    "there is blood everywhere",
    "I'm losing a lot of blood",
    "my husband just passed out",
    "she is unconscious",
    "he's not responding",
    "I think I'm having a heart attack",
    "I have chest pain and my arm is numb",
    "he's having a seizure",
    "I think it's a stroke",
    "I had a car accident and my jaw is broken",
    "I fell and hit my head and my mouth is full of blood",
    "this is a medical emergency",
    "please send an ambulance",
    "I think I'm going to die",
    "my lips are turning blue",
    "he swallowed his tooth and is gasping",
    "I'm having an allergic reaction to the anaesthetic and can't swallow",
    "I want to kill myself",
    "uh my tongue it is swelling and I cant breathe properly",
]

URGENT_CASES = [
    "I'm in severe pain",
    "the pain is unbearable",
    "it's excruciating, I haven't slept",
    "my tooth got knocked out playing cricket an hour ago, it's no big deal, just an appointment",
    "I knocked my front tooth out",
    "my tooth fell out this morning",
    "I broke my tooth on a spoon and it's really sore",
    "I have a cracked tooth and it hurts a lot",
    "my cheek is swollen and I think I have an abscess",
    "there's pus coming out of my gum",
    "I have a dental emergency",
    "it's urgent, I need to be seen today",
    "I'm in a lot of pain, can I come in today",
    "my jaw is swollen and I have a fever",
    "I had a tooth pulled yesterday and it's still bleeding a bit and the pain is terrible",
    "worst toothache of my life",
    "my toothache is killing me",
    "I need same day treatment",
]

NEITHER = [
    "I'd like to book a cleaning",
    "I have an emergency meeting at work so can we do 5 pm",
    "what is your emergency contact number for the clinic",
    "where is the emergency exit",
    "do you handle emergencies",
    "my teeth are a bit sensitive to cold",
    "I get some sensitivity when I drink hot tea",
    "I have a mild toothache, nothing urgent",
    "it's not urgent, any time next week is fine",
    "my gums bleed a little when I floss",
    "I can breathe fine, I'm just calling to book",
    "I just want a check-up, there is no pain",
    "how much does a root canal cost",
    "I'm out of breath, I just ran up the stairs to take this call",
    "my daughter chipped a corner of her tooth, no hurry",
    "my crown is a little loose",
    "can I reschedule my appointment to Thursday",
    "yes that's correct",
    "my name is Priya Sharma and my number is 98765 43210",
    "I need to cancel, I have a work emergency",
    "the bleeding gums cleared up after the last cleaning",
    "I want whitening and a polish",
    "tooth sensitivity",
    "the car broke down so I'll be late",
    "Dr Rao said it was no emergency",
]


@pytest.mark.parametrize("text", EMERGENCIES)
def test_an_emergency_is_detected(text: str) -> None:
    assert detect(text) == EMERGENCY


@pytest.mark.parametrize("text", URGENT_CASES)
def test_an_urgent_case_is_detected_and_is_not_an_emergency(text: str) -> None:
    assert detect(text) == URGENT


@pytest.mark.parametrize("text", NEITHER)
def test_ordinary_talk_is_not_flagged(text: str) -> None:
    assert detect(text) is None


def test_an_emergency_wins_over_an_urgent_case_in_the_same_turn() -> None:
    assert detect("my tooth got knocked out and I can't breathe") == EMERGENCY


def test_case_and_punctuation_do_not_matter() -> None:
    assert detect("I CAN'T   BREATHE!!!") == EMERGENCY
    assert detect("i can’t breathe") == EMERGENCY  # a curly apostrophe from speech-to-text


# What a rule-based detector cannot see. Each stays a strict xfail: wrong today, written down so
# the limit is visible (docs/safety.md), and an unexpected pass fails the suite.
KNOWN_MISSES = [
    pytest.param("my husband just went limp", EMERGENCY, id="euphemism-went-limp"),
    pytest.param("I can't get any air in", EMERGENCY, id="paraphrase-no-air"),
    pytest.param("meri saans nahi aa rahi", EMERGENCY, id="not-english"),
    pytest.param("the bleeding is a lot worse than they said it would be", URGENT, id="vague"),
    pytest.param(
        "I feel really weird and dizzy, my chest is squeezing", EMERGENCY, id="chest-squeeze"
    ),
    pytest.param("c a n t b r e a t h e", EMERGENCY, id="spelled-out"),
    pytest.param("it hurts so much I could scream", URGENT, id="pain-idiom"),
    pytest.param("I had a heart attack last year and want a cleaning", None, id="past-event-fp"),
    pytest.param("I'm choking up laughing, you're so funny, book me in", None, id="idiom-fp"),
]


@pytest.mark.parametrize(("text", "want"), KNOWN_MISSES)
@pytest.mark.xfail(strict=True, reason="a known gap of a keyword and rule detector")
def test_known_gap(text: str, want: str | None) -> None:
    assert detect(text) == want
