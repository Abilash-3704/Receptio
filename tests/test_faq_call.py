"""Seam 1 for the FAQ and scope limits: a text-mode Call through the real agent core and the real
backend, with a scripted LLM. The script plays "the LLM looked the fact up and paraphrased it".

What this can prove: answers are routed through the knowledge base, the output guards withhold a
price or clock time the knowledge base did not give this Call, a question with no answer gets a
fixed honest reply and a Callback Request, and a medical-advice request is refused in code before
the LLM is consulted. What it cannot prove: that the real model would paraphrase facts faithfully
or refuse medical advice on its own. That is a prompt rule plus the guards, checked by hand."""

from typing import Any

import httpx
import pytest

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def faq_text(backend: httpx.Client, topic: str) -> str:
    entries: list[dict[str, str]] = backend.get("/faq").json()
    return next(e["text"] for e in entries if e["topic"] == topic)


def test_hours_are_answered_from_the_knowledge_base_including_clock_times(
    backend: httpx.Client,
) -> None:
    hours = faq_text(backend, "hours")
    llm = ScriptedLLM([UseTool("search_faq", {"question": "when are you open"}), Say(hours)])
    call = open_text_call(backend, llm=llm)

    heard = call.hear("When are you open?")

    assert heard == [hours]  # "9:00 AM" is not withheld as an unoffered time
    [lookup] = call.tool_calls
    assert lookup.name == "search_faq" and lookup.data["found"] is True


def test_prices_are_stated_as_ranges(backend: httpx.Client) -> None:
    llm = ScriptedLLM(
        [
            UseTool("search_faq", {"question": "how much is a filling"}),
            Say("A Filling is usually Rs 1,500 to Rs 4,000. That is an estimate, not a quote."),
        ]
    )
    call = open_text_call(backend, llm=llm)

    [heard] = call.hear("How much is a filling?")

    assert "Rs 1,500 to Rs 4,000" in heard


def test_a_single_price_that_is_not_in_the_knowledge_base_is_withheld(
    backend: httpx.Client,
) -> None:
    llm = ScriptedLLM(
        [
            UseTool("search_faq", {"question": "how much is a filling"}),
            Say("A filling is Rs 2,000."),  # a quote the knowledge base never gave
            Say("A Filling is usually Rs 1,500 to Rs 4,000."),
        ]
    )
    call = open_text_call(backend, llm=llm)

    assert call.hear("How much is a filling?") == ["A Filling is usually Rs 1,500 to Rs 4,000."]


def test_a_price_without_looking_it_up_first_is_withheld(backend: httpx.Client) -> None:
    llm = ScriptedLLM([Say("A cleaning is Rs 1,200 to Rs 2,500."), Say("Let me check on that.")])
    call = open_text_call(backend, llm=llm)

    assert call.hear("How much is a cleaning?") == ["Let me check on that."]


def test_an_unanswerable_question_gets_an_honest_no_and_a_callback_offer(
    backend: httpx.Client,
) -> None:
    llm = ScriptedLLM([UseTool("search_faq", {"question": "do you do teeth whitening for pets"})])
    call = open_text_call(backend, llm=llm)

    [heard] = call.hear("Do you do teeth whitening for pets?")

    assert "don't have that information" in heard and "call you back" in heard
    assert escalations(backend) == []  # offered, not yet accepted


def test_accepting_the_offer_records_a_callback_request_with_the_question(
    backend: httpx.Client,
) -> None:
    backend.put("/demo/clinic-override", json={"mode": "closed"})
    llm = ScriptedLLM([UseTool("search_faq", {"question": "do you sell toothbrushes"})])
    call = open_text_call(backend, llm=llm)
    call.hear("Do you sell electric toothbrushes?")

    asked = call.hear("Yes please, call me back")
    heard = call.hear("98450 12345")

    assert "number" in asked[0]
    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert "do you sell toothbrushes" in stored["reason"]
    assert "call you back" in heard[0]


def test_a_plain_yes_to_the_callback_offer_escalates(backend: httpx.Client) -> None:
    backend.put("/demo/clinic-override", json={"mode": "open"})
    llm = ScriptedLLM([UseTool("search_faq", {"question": "do you sell toothbrushes"})])
    call = open_text_call(backend, llm=llm)
    call.hear("Do you sell electric toothbrushes?")

    call.hear("Yes please")

    [stored] = escalations(backend)
    assert stored["kind"] == "handoff" and "do you sell toothbrushes" in stored["reason"]


def test_declining_the_offer_records_nothing_and_the_call_goes_on(
    backend: httpx.Client,
) -> None:
    llm = ScriptedLLM([UseTool("search_faq", {"question": "do you sell toothbrushes"}), Say("Ok.")])
    call = open_text_call(backend, llm=llm)
    call.hear("Do you sell electric toothbrushes?")

    assert call.hear("No thanks, I'd like to book a cleaning") == ["Ok."]
    assert escalations(backend) == []


@pytest.mark.parametrize(
    "text",
    [
        "Should I take ibuprofen for my toothache?",
        "What dose of paracetamol is safe for a tooth infection?",
        "Is it okay to put clove oil on my gum?",
        "How can I treat this swelling at home?",
        "Do I need antibiotics?",
    ],
)
def test_medical_advice_is_refused_in_code_with_a_redirect(
    backend: httpx.Client, text: str
) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([]))  # the LLM is never consulted

    [heard] = call.hear(text)

    assert "can't give medical advice" in heard
    assert "dentist" in heard and "book" in heard and "call you back" in heard
    assert escalations(backend) == []


def test_accepting_the_callback_after_a_medical_redirect_escalates(
    backend: httpx.Client,
) -> None:
    backend.put("/demo/clinic-override", json={"mode": "open"})
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("Should I take ibuprofen for a toothache?")

    call.hear("yes, get someone to call me")

    [stored] = escalations(backend)
    assert stored["kind"] == "handoff" and "medical advice" in stored["reason"]


@pytest.mark.parametrize(
    "text",
    [
        "How much does a root canal cost?",
        "Is it open on Sunday?",
        "I'd like a cleaning, is that painful for the Patient?",
        "Do you take Star Health insurance?",
    ],
)
def test_ordinary_questions_are_not_mistaken_for_medical_advice(
    backend: httpx.Client, text: str
) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Sure.")]))

    assert call.hear(text) == ["Sure."]
