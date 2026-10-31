"""The call state machine's state. Lives in code, outside the LLM context (spec: Modules).

The LLM sees only `summary()`. Facts that matter for correctness (what was offered, which Hold is
live, whether the Caller agreed to the read-back) are recorded here by code, never by the LLM."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.app.clock import CLINIC_TZ


def format_time(moment: datetime) -> str:
    """How a time is said aloud, for example 'Tuesday 3 November at 10:00 AM'."""
    local = moment.astimezone(CLINIC_TZ)
    hour12 = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return f"{local:%A} {local.day} {local:%B} at {hour12}:{local.minute:02d} {meridiem}"


@dataclass(frozen=True)
class Offer:
    """A time that Availability returned for the requested Service."""

    provider_id: int
    provider: str
    start: datetime

    @property
    def label(self) -> str:
        return f"{format_time(self.start)} with {self.provider}"


@dataclass(frozen=True)
class HoldInfo:
    id: int
    offer: Offer


@dataclass
class CallState:
    service_id: int | None = None
    service: str | None = None
    patient_name: str | None = None
    patient_phone: str | None = None
    offers: list[Offer] = field(default_factory=lambda: [])
    # Every start time Availability has returned and the agent offered during this Call.
    ever_offered: set[datetime] = field(default_factory=lambda: set())
    hold: HoldInfo | None = None
    awaiting_read_back_answer: bool = False
    read_back_agreed: bool = False
    appointment_id: int | None = None
    # Escalation (agent/escalation.py): failed attempts in a row per step, whether the Call has
    # been passed to staff, and the reason held while waiting for a number to ring back.
    failures: dict[str, int] = field(default_factory=lambda: {})
    escalated: bool = False
    callback_reason: str | None = None
    number_asks: int = 0
    # Safety layer (agent/safety.py): the highest level the detector has raised in this Call.
    safety_level: str | None = None

    def abandon_scheduling(self) -> None:
        """Forget everything about the booking in progress. Scheduling is never resumed."""
        self.offers = []
        self.hold = None
        self.awaiting_read_back_answer = False
        self.read_back_agreed = False

    def record_outcome(self, step: str, ok: bool) -> int:
        """Count consecutive failures of one step; a success resets it. Returns the count."""
        self.failures[step] = 0 if ok else self.failures.get(step, 0) + 1
        return self.failures[step]

    def summary(self) -> dict[str, Any]:
        """The compact per-turn view the LLM gets."""
        missing = [
            name
            for name, value in (
                ("service", self.service),
                ("patient_name", self.patient_name),
                ("patient_phone", self.patient_phone),
            )
            if value is None
        ]
        return {
            "intent": "book_appointment",
            "service": self.service,
            "patient_name": self.patient_name,
            "patient_phone": self.patient_phone,
            "missing": missing,
            "offered": [o.label for o in self.offers],
            "hold": None if self.hold is None else self.hold.offer.label,
            "awaiting_read_back_answer": self.awaiting_read_back_answer,
            "read_back_agreed": self.read_back_agreed,
            "appointment_confirmed": self.appointment_id is not None,
        }
