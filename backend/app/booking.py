"""Appointment correctness (ADR-0001). The backend alone decides: an Appointment is Confirmed
only from an unexpired Hold with a recorded read-back for that exact Hold, a Patient never has
two overlapping Confirmed Appointments, and a reschedule cancels the old Appointment in the same
transaction that confirms the new one. Every rule fails closed: reject rather than guess.

Concurrency: all writes that touch a Provider's time take a row lock on that Provider first, then
on the Hold, then on the Patient (always in that order). On Postgres, partial exclusion
constraints (migration 0002) are the backstop if a lock were ever missed."""

import re
import unicodedata
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import (
    AppointmentRecord,
    ConfirmationMessage,
    Patient,
    Provider,
    Service,
)
from backend.app.scheduling import HOLD_DURATION, blocking_appointments, find_availability


def reject(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


NAME_PUNCTUATION = set(" '\u2019-.")


def normalise_name(raw: str) -> tuple[str, str]:
    """A name is letters (any script), combining marks, spaces and ' - . only. Anything else, such
    as newlines, control characters, brackets or markup, is not a name: caller speech reaches this
    field, and the name is later written into a Confirmation Message."""
    name = re.sub(" +", " ", raw).strip()
    if not name or len(name) > 80:
        raise reject(422, "patient name must be 1 to 80 characters")
    if not all(unicodedata.category(c)[0] in "LM" or c in NAME_PUNCTUATION for c in name):
        raise reject(422, "patient name may contain only letters, spaces, apostrophes, - and .")
    return name, name.casefold()


def normalise_phone(raw: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", raw)
    if not re.fullmatch(r"\+?\d{10,15}", cleaned):
        raise reject(422, "patient phone must be 10 to 15 digits, with an optional leading +")
    return cleaned


def lock_provider(session: Session, provider_id: int) -> None:
    """Serialise every write that claims this Provider's time. A no-op on SQLite."""
    session.scalar(select(Provider.id).where(Provider.id == provider_id).with_for_update())


def place_hold(
    session: Session,
    service: Service,
    provider_id: int,
    start: datetime,
    now: datetime,
    replaces: AppointmentRecord | None = None,
) -> AppointmentRecord:
    lock_provider(session, provider_id)
    end = start + timedelta(minutes=service.duration_minutes)
    open_times = find_availability(session, service, provider_id, start, end, now)
    if not any(s.start == start for s in open_times):
        raise reject(409, "that time is not available")
    hold = AppointmentRecord(
        provider_id=provider_id,
        service_id=service.id,
        starts_at=start,
        ends_at=end,
        status="held",
        expires_at=now + HOLD_DURATION,
        replaces_id=None if replaces is None else replaces.id,
    )
    session.add(hold)
    session.flush()
    return hold


def _lock_hold(session: Session, hold_id: int) -> AppointmentRecord:
    hold = session.get(AppointmentRecord, hold_id)
    if hold is None:
        raise reject(404, "hold not found")
    lock_provider(session, hold.provider_id)
    session.refresh(hold, with_for_update=True)
    return hold


def _require_live_hold(hold: AppointmentRecord, now: datetime) -> None:
    if hold.status == "confirmed":
        raise reject(409, "this Hold is already confirmed")
    if hold.status != "held":
        raise reject(409, f"this Hold is {hold.status}")
    if hold.expires_at is None or hold.expires_at <= now:
        raise reject(409, "the Hold has expired: check Availability again before confirming")


def record_read_back(session: Session, hold_id: int, now: datetime) -> AppointmentRecord:
    hold = _lock_hold(session, hold_id)
    _require_live_hold(hold, now)
    hold.read_back_at = now
    session.flush()
    return hold


def release_hold(session: Session, hold_id: int) -> AppointmentRecord:
    """Free a Hold's time now. Releasing twice, or a Hold that already expired, changes nothing;
    a Confirmed Appointment is never released (cancel it instead)."""
    hold = _lock_hold(session, hold_id)
    if hold.status == "confirmed":
        raise reject(409, "this is a Confirmed Appointment, not a Hold: it cannot be released")
    if hold.status == "held":
        hold.status = "cancelled"
        session.flush()
    return hold


def _find_or_create_patient(session: Session, name: str, phone: str) -> Patient:
    full_name, key = normalise_name(name)
    phone = normalise_phone(phone)
    lookup = select(Patient).where(Patient.name_key == key, Patient.phone == phone)
    patient = session.scalar(lookup.with_for_update())
    if patient is None:
        try:
            with session.begin_nested():
                patient = Patient(full_name=full_name, name_key=key, phone=phone)
                session.add(patient)
        except IntegrityError:  # a concurrent confirm created the same Patient first
            patient = session.scalars(lookup.with_for_update()).one()
    return patient


def confirm(
    session: Session, hold_id: int, patient: tuple[str, str] | None, now: datetime
) -> AppointmentRecord:
    """Confirm a Hold. `patient` is (name, phone) for a new booking, None for a reschedule Hold,
    whose Patient is the original Appointment's."""
    hold = _lock_hold(session, hold_id)
    _require_live_hold(hold, now)
    if hold.read_back_at is None:
        raise reject(409, "no read-back agreement is recorded for this Hold")

    original: AppointmentRecord | None = None
    if hold.replaces_id is not None:
        if patient is not None:
            raise reject(
                422, "a reschedule keeps the original Patient: do not send patient details"
            )
        original = session.get(AppointmentRecord, hold.replaces_id)
        assert original is not None and original.patient_id is not None
        session.refresh(original, with_for_update=True)
        if original.status != "confirmed":
            raise reject(409, "the Appointment being rescheduled is no longer confirmed")
        owner = session.get(Patient, original.patient_id)
        assert owner is not None
        session.refresh(owner, with_for_update=True)
    else:
        if patient is None:
            raise reject(422, "patient name and phone are required to confirm")
        owner = _find_or_create_patient(session, *patient)

    ignoring = {hold.id, *([original.id] if original else [])}
    clashes = [
        a
        for a in blocking_appointments(session, hold.provider_id, hold.starts_at, hold.ends_at, now)
        if a.id not in ignoring
    ]
    if clashes:
        raise reject(409, "that time is no longer available")
    overlapping = session.scalars(
        select(AppointmentRecord.id).where(
            AppointmentRecord.patient_id == owner.id,
            AppointmentRecord.status == "confirmed",
            AppointmentRecord.starts_at < hold.ends_at,
            AppointmentRecord.ends_at > hold.starts_at,
            AppointmentRecord.id.not_in(ignoring),
        )
    ).first()
    if overlapping is not None:
        raise reject(409, "the Patient already has a confirmed Appointment overlapping that time")

    try:
        if original is not None:  # same transaction: old cancelled, new confirmed, or neither
            original.status = "cancelled"
            session.flush()
        hold.status = "confirmed"
        hold.patient_id = owner.id
        hold.expires_at = None
        session.flush()
    except IntegrityError as exc:  # database backstop (Postgres exclusion constraints)
        raise reject(409, "that time is no longer available") from exc
    return hold


def cancel(session: Session, appointment_id: int) -> AppointmentRecord:
    appointment = session.get(AppointmentRecord, appointment_id)
    if appointment is None:
        raise reject(404, "appointment not found")
    session.refresh(appointment, with_for_update=True)
    if appointment.status == "held":
        raise reject(409, "a Hold is not an Appointment: it expires on its own")
    appointment.status = "cancelled"  # cancelling twice changes nothing
    session.flush()
    return appointment


def start_reschedule(session: Session, appointment_id: int) -> AppointmentRecord:
    original = session.get(AppointmentRecord, appointment_id)
    if original is None:
        raise reject(404, "appointment not found")
    if original.status != "confirmed":
        raise reject(409, "only a confirmed Appointment can be rescheduled")
    return original


def record_confirmation_message(
    session: Session, appointment_id: int, now: datetime
) -> ConfirmationMessage:
    """Mocked send: only a Confirmed Appointment gets one, and only one per Appointment."""
    appointment = session.get(AppointmentRecord, appointment_id)
    if appointment is None:
        raise reject(404, "appointment not found")
    if appointment.status != "confirmed" or appointment.patient_id is None:
        raise reject(409, "only a Confirmed Appointment gets a Confirmation Message")
    existing = session.scalars(
        select(ConfirmationMessage).where(ConfirmationMessage.appointment_id == appointment_id)
    ).first()
    if existing is not None:
        return existing
    patient = session.get(Patient, appointment.patient_id)
    service = session.get(Service, appointment.service_id)
    provider = session.get(Provider, appointment.provider_id)
    assert patient is not None and service is not None and provider is not None
    when = appointment.starts_at.astimezone(CLINIC_TZ)
    body = (
        f"Hi {patient.full_name}, your {service.name} with {provider.name} is confirmed for "
        f"{when:%A %d %B %Y} at {when:%I:%M %p}."
    )
    message = ConfirmationMessage(
        appointment_id=appointment.id, to_phone=patient.phone, body=body, created_at=now
    )
    session.add(message)
    session.flush()
    return message
