"""The call state machine's state. Lives in code, outside the LLM context (spec: Modules).

The LLM sees only `summary()`. Facts that matter for correctness (what was offered, which Hold is
live, whether the Caller agreed to the read-back) are recorded here by code, never by the LLM."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from agent.times import say_date
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


@dataclass(frozen=True)
class KnownAppointment:
    """An existing Appointment of an identified Patient. Only exists after identification."""

    id: int
    service: str
    provider: str
    start: datetime

    @property
    def label(self) -> str:
        return f"{self.service} on {format_time(self.start)} with {self.provider}"


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
    # A date the agent asked the Caller to agree to aloud, and the dates they have agreed to.
    pending_date: date | None = None
    confirmed_dates: set[date] = field(default_factory=lambda: set())
    # Set when a Hold was lost (expired or taken) before the Caller's yes.
    hold_lost: str | None = None
    # Why a Callback Request was offered (the request was sooner than the booking window allows).
    # Taken up only if the Caller's next answer is a plain yes.
    callback_offer: str | None = None
    # The duration cap (agent/call_limits.py): the warning was given / the Call was closed.
    limit_warned: bool = False
    limit_reached: bool = False
    awaiting_read_back_answer: bool = False
    read_back_agreed: bool = False
    appointment_id: int | None = None
    # The Hold that became the Confirmed Appointment above: a second confirm of it is a success.
    confirmed_hold_id: int | None = None
    # Identification (ticket 08): the Patient's name and phone as the backend matched them, their
    # upcoming Appointments, the one in play, and the change waiting for a yes. None of this is
    # filled before both name and phone match.
    identity: tuple[str, str] | None = None
    known_appointments: list[KnownAppointment] = field(default_factory=lambda: [])
    target: KnownAppointment | None = None
    rescheduling: bool = False
    awaiting_cancel_answer: bool = False
    cancel_agreed: bool = False
    appointment_cancelled: bool = False
    # Escalation (agent/escalation.py): failed attempts in a row per step, whether the Call has
    # been passed to staff, and the reason held while waiting for a number to ring back.
    failures: dict[str, int] = field(default_factory=lambda: {})
    escalated: bool = False
    callback_reason: str | None = None
    number_asks: int = 0
    # FAQ (agent/faq.py): facts looked up this Call, the only prices and clock times the agent may
    # say besides offered times. (A Callback Request offered by the FAQ shares `callback_offer`.)
    known_facts: list[str] = field(default_factory=lambda: [])
    # Failure handling (ticket 19): why help from staff was offered after a failure (the clinic
    # system or the LLM did not answer, the Caller could not be heard), pending the Caller's
    # answer; and speech-recognition dropouts in a row.
    failure_offer: str | None = None
    asr_dropouts: int = 0
    # Safety layer (agent/safety.py): the highest level the detector has raised in this Call.
    safety_level: str | None = None

    def abandon_scheduling(self) -> None:
        """Forget the booking, reschedule or cancellation in progress. Never resumed."""
        self.offers = []
        self.hold = None
        self.pending_date = None
        self.hold_lost = None
        self.callback_offer = None
        self.awaiting_read_back_answer = False
        self.read_back_agreed = False
        self.target = None
        self.rescheduling = False
        self.awaiting_cancel_answer = False
        self.cancel_agreed = False

    def unfinished_step(self) -> str | None:
        """Where the Call stands if the Caller left now, when that is mid-flow: a booking,
        cancellation or callback that was started and not finished. None when nothing is owed.
        Used for the Call's outcome (dropped or not); the wording is stored, so no personal data."""
        if self.escalated:
            return None
        if self.awaiting_cancel_answer:
            return "cancellation not confirmed"
        if self.callback_reason is not None:
            return "callback number not given"
        if self.appointment_id is not None or self.appointment_cancelled:
            return None
        if self.awaiting_read_back_answer:
            return "Hold read back, no answer"
        if self.hold is not None:
            return "Hold placed, not confirmed"
        if self.offers:
            return "times offered, no Hold"
        if self.service is not None or self.patient_name is not None:
            return "booking details given, no times chosen"
        return None

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
            "hold_lost": self.hold_lost,
            "callback_offered": self.callback_offer is not None,
            "date_awaiting_caller_yes": None
            if self.pending_date is None
            else say_date(self.pending_date),
            "awaiting_read_back_answer": self.awaiting_read_back_answer,
            "read_back_agreed": self.read_back_agreed,
            "appointment_confirmed": self.appointment_id is not None,
            "identified": self.identity is not None,
            "appointments": [a.label for a in self.known_appointments],
            "selected_appointment": None if self.target is None else self.target.label,
            "rescheduling": self.rescheduling,
            "awaiting_cancel_answer": self.awaiting_cancel_answer,
            "cancel_agreed": self.cancel_agreed,
            "appointment_cancelled": self.appointment_cancelled,
        }
