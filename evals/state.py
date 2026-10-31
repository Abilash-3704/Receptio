"""Reads the clinic database the way a scenario asserts on it: what exists after a Call."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import (
    AppointmentRecord,
    CallRecord,
    ConfirmationMessage,
    EscalationRecord,
    Patient,
    Provider,
    Service,
)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


@dataclass(frozen=True)
class AppointmentRow:
    id: int
    service: str
    provider: str
    starts_at: datetime  # clinic local time
    ends_at: datetime
    provider_id: int
    patient_id: int
    patient: str
    patient_phone: str
    read_back_recorded: bool


@dataclass(frozen=True)
class MessageRow:
    appointment_id: int
    to_phone: str
    body: str


@dataclass(frozen=True)
class EscalationRow:
    call_id: int
    kind: str  # handoff or callback_request
    reason: str
    caller_phone: str | None


@dataclass(frozen=True)
class CallRow:
    id: int
    outcome: str | None  # completed, escalated or dropped
    outcome_reason: str | None


@dataclass(frozen=True)
class FinalState:
    confirmed: list[AppointmentRow]
    held: int  # Appointments still in the Held state (Holds, expired or not)
    patients: list[str]  # Patient names
    messages: list[MessageRow]
    escalations: list[EscalationRow] = field(default_factory=lambda: [])
    # Holds that are Held and not yet expired at the moment of reading (needs `now`).
    live_holds: int = 0
    # Calls recorded so far. Left out of equality: a Call existing is not a change to the clinic's
    # data, and "state == before" asserts that nothing the Call touched changed.
    calls: list[CallRow] = field(default_factory=lambda: [], compare=False)


def read_final_state(session_factory: SessionFactory, now: datetime | None = None) -> FinalState:
    with session_factory() as session:
        confirmed: list[AppointmentRow] = []
        rows = session.scalars(
            select(AppointmentRecord)
            .where(AppointmentRecord.status == "confirmed")
            .order_by(AppointmentRecord.starts_at)
        )
        for a in rows:
            patient = session.get(Patient, a.patient_id)
            service = session.get(Service, a.service_id)
            provider = session.get(Provider, a.provider_id)
            assert patient is not None and service is not None and provider is not None
            confirmed.append(
                AppointmentRow(
                    id=a.id,
                    service=service.name,
                    provider=provider.name,
                    starts_at=a.starts_at.astimezone(CLINIC_TZ),
                    ends_at=a.ends_at.astimezone(CLINIC_TZ),
                    provider_id=a.provider_id,
                    patient_id=patient.id,
                    patient=patient.full_name,
                    patient_phone=patient.phone,
                    read_back_recorded=a.read_back_at is not None,
                )
            )
        held = len(
            session.scalars(
                select(AppointmentRecord.id).where(AppointmentRecord.status == "held")
            ).all()
        )
        live_holds = 0
        if now is not None:
            expiries = session.scalars(
                select(AppointmentRecord.expires_at).where(AppointmentRecord.status == "held")
            )
            live_holds = sum(1 for e in expiries if e is not None and e > now)
        patients = [p.full_name for p in session.scalars(select(Patient).order_by(Patient.id))]
        messages = [
            MessageRow(m.appointment_id, m.to_phone, m.body)
            for m in session.scalars(select(ConfirmationMessage).order_by(ConfirmationMessage.id))
        ]
        escalations = [
            EscalationRow(e.call_id, e.kind, e.reason, e.caller_phone)
            for e in session.scalars(select(EscalationRecord).order_by(EscalationRecord.id))
        ]
        calls = [
            CallRow(c.id, c.outcome, c.outcome_reason)
            for c in session.scalars(select(CallRecord).order_by(CallRecord.id))
        ]
    return FinalState(confirmed, held, patients, messages, escalations, live_holds, calls)
