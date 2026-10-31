"""Seam: the backend's knowledge base over HTTP. Retrieval is deterministic keyword matching, so
these tests can pin exact entries. Hours and Services are derived from the same data the
scheduler uses, so the knowledge base cannot contradict them."""

from typing import Any

import httpx

from tests.support import Json


def search(backend: httpx.Client, question: str) -> list[Json]:
    response = backend.get("/faq", params={"q": question})
    assert response.status_code == 200
    return response.json()


def topics(entries: list[Json]) -> list[str]:
    return [e["topic"] for e in entries]


def test_hours_are_derived_from_the_clinic_hours(backend: httpx.Client) -> None:
    [entry] = search(backend, "What are your opening hours?")

    assert entry["topic"] == "hours"
    assert entry["text"] == (
        "We are open Monday to Saturday, 9:00 AM to 6:00 PM, and closed on Sunday."
    )


def test_services_list_every_seeded_service_with_its_duration(backend: httpx.Client) -> None:
    [entry] = search(backend, "which services do you offer")
    services: list[Any] = backend.get("/services").json()

    assert entry["topic"] == "services"
    for service in services:
        assert f"{service['name']} ({service['duration_minutes']} minutes)" in entry["text"]


def test_every_service_has_a_price_range_and_never_a_single_price(backend: httpx.Client) -> None:
    [entry] = search(backend, "How much does a filling cost?")
    services: list[Any] = backend.get("/services").json()

    assert entry["topic"] == "prices"
    for service in services:
        assert f"{service['name']}: Rs " in entry["text"]
    assert entry["text"].count(" to Rs ") == len(services)  # one range per Service
    assert "estimate" in entry["text"] and "not a quote" in entry["text"]


def test_location_and_insurance_come_from_the_knowledge_base(backend: httpx.Client) -> None:
    [where] = search(backend, "Where are you located?")
    [insurance] = search(backend, "Do you accept insurance?")

    assert where["topic"] == "location" and "Bengaluru" in where["text"]
    assert insurance["topic"] == "insurance" and "Star Health" in insurance["text"]


def test_a_question_the_knowledge_base_cannot_answer_returns_nothing(
    backend: httpx.Client,
) -> None:
    assert search(backend, "Do you do teeth whitening for pets?") == []
    assert search(backend, "") == []


def test_without_a_question_every_entry_is_listed(backend: httpx.Client) -> None:
    entries = backend.get("/faq").json()

    assert topics(entries) == ["hours", "location", "services", "prices", "insurance"]
