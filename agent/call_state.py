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
    hold: HoldInfo | None = None
    awaiting_read_back_answer: bool = False
    read_back_agreed: bool = False
    appointment_id: int | None = None

    @property
    def offered_times(self) -> set[datetime]:
        return {o.start for o in self.offers}

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
