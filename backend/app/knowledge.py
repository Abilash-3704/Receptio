"""The clinic knowledge base: the only source the agent may answer clinic questions from.

Hours and Services are derived from the Clinic Hours and Service rows, so the FAQ cannot contradict
the scheduler. Location, price ranges and insurance are seeded text (fictional, Bengaluru).

Retrieval is deterministic keyword matching, not embeddings: five topics and a fixed vocabulary
need no vector index, and a test can pin exactly which entry answers which question. A question
that matches no topic returns nothing, and the agent says it does not know."""

import re
from dataclasses import dataclass
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import ClinicHoursRow, Service

LOCATION = (
    "We are at 14, 3rd Cross, 12th Main, Indiranagar, Bengaluru 560038, near the Indiranagar "
    "Metro station. Paid parking is available on the street outside."
)

# Service name: (low, high) in rupees. Always an estimate range, never a quote.
PRICE_RANGES: dict[str, tuple[int, int]] = {
    "Check-up": (500, 1000),
    "Cleaning": (1200, 2500),
    "Filling": (1500, 4000),
    "Root canal": (6000, 15000),
}
PRICE_NOTE = (
    "These are estimates, not a quote. The dentist confirms the exact cost after examining you."
)

INSURANCE = (
    "We accept Star Health, HDFC ERGO and ICICI Lombard dental plans, with cashless claims "
    "for Check-ups and Cleanings. For other plans we give you an invoice to claim back yourself."
)

# topic: words that start a word in a question about it.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "hours": ("hour", "open", "close", "closing", "timing", "sunday", "saturday", "weekend"),
    "location": ("where", "locat", "address", "direction", "parking", "landmark", "metro"),
    "services": ("service", "offer", "treatment", "procedure"),
    "prices": ("price", "cost", "how much", "fee", "charge", "rate", "expensive", "afford"),
    "insurance": ("insur", "cashless", "coverage", "covered", "health plan", "tpa"),
}


@dataclass(frozen=True)
class Entry:
    topic: str
    text: str


def rupees(amount: int) -> str:
    return f"Rs {amount:,}"


def clock(value: time) -> str:
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def hours_text(rows: list[ClinicHoursRow]) -> str:
    by_day = {r.weekday: (r.opens, r.closes) for r in rows}
    runs: list[list[int]] = []
    for day in sorted(by_day):
        if runs and runs[-1][-1] == day - 1 and by_day[runs[-1][0]] == by_day[day]:
            runs[-1].append(day)
        else:
            runs.append([day])
    parts: list[str] = []
    for run in runs:
        opens, closes = by_day[run[0]]
        days = DAYS[run[0]] if len(run) == 1 else f"{DAYS[run[0]]} to {DAYS[run[-1]]}"
        parts.append(f"{days}, {clock(opens)} to {clock(closes)}")
    closed = [DAYS[d] for d in range(7) if d not in by_day]
    text = "We are open " + " and ".join(parts)
    if closed:
        text += f", and closed on {' and '.join(closed)}"
    return text + "."


def entries(session: Session) -> list[Entry]:
    rows = list(session.scalars(select(ClinicHoursRow).order_by(ClinicHoursRow.weekday)))
    services = list(session.scalars(select(Service).order_by(Service.id)))
    offered = ", ".join(f"{s.name} ({s.duration_minutes} minutes)" for s in services)
    prices = "; ".join(
        f"{s.name}: {rupees(PRICE_RANGES[s.name][0])} to {rupees(PRICE_RANGES[s.name][1])}"
        for s in services
        if s.name in PRICE_RANGES
    )
    return [
        Entry("hours", hours_text(rows)),
        Entry("location", LOCATION),
        Entry("services", f"We offer: {offered}."),
        Entry("prices", f"Price ranges: {prices}. {PRICE_NOTE}"),
        Entry("insurance", INSURANCE),
    ]


def search(session: Session, question: str) -> list[Entry]:
    lowered = question.casefold()
    matched = {
        topic
        for topic, words in KEYWORDS.items()
        if any(re.search(rf"\b{re.escape(word)}", lowered) for word in words)
    }
    return [e for e in entries(session) if e.topic in matched]
