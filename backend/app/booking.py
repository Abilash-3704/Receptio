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


def _lock_hold_for(
    session: Session, hold_id: int, identity: tuple[str, str] | None
) -> AppointmentRecord:
    """Lock a Hold on behalf of a Caller. A reschedule Hold belongs to the Patient of the
    Appointment it replaces: for anyone else (wrong name or phone, no details, unknown Hold) this
    fails with the same 403 as every other identification failure, before anything about the
    Hold's state is revealed or changed."""
    hold = session.get(AppointmentRecord, hold_id)
    if hold is None:
        raise reject(403, IDENTIFICATION_FAILED)
    lock_provider(session, hold.provider_id)
    session.refresh(hold, with_for_update=True)
    if hold.replaces_id is not None:
        if identity is None:
            raise reject(403, IDENTIFICATION_FAILED)
        patient = identify(session, *identity)
        original = session.get(AppointmentRecord, hold.replaces_id)
        if original is None or original.patient_id != patient.id:
            raise reject(403, IDENTIFICATION_FAILED)
    return hold


def release_hold(session: Session, hold_id: int, now: datetime) -> AppointmentRecord:
    """Free a Hold's time before it expires, for example when the Caller changes their mind.
    Releasing a Hold that is already released changes nothing."""
    hold = _lock_hold(session, hold_id)
    if hold.status == "confirmed":
        raise reject(409, "a confirmed Appointment is not a Hold: cancel it instead")
    if hold.status == "held":
        hold.status = "released"
        hold.expires_at = now
        session.flush()
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
    """Confirm a Hold. `patient` is the (name, phone) of a new booking's Patient, or the identity
    of the Patient who owns the Appointment a reschedule Hold replaces, which must match."""
    hold = _lock_hold_for(session, hold_id, patient)
    _require_live_hold(hold, now)
    if hold.read_back_at is None:
        raise reject(409, "no read-back agreement is recorded for this Hold")

    original: AppointmentRecord | None = None
    if hold.replaces_id is not None:
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


IDENTIFICATION_FAILED = "identification failed"


def identify(session: Session, name: str, phone: str) -> Patient:
    """The Patient whose full name AND phone on file both match, else one uniform 403. Malformed
    details fail the same way as a wrong match, so a Caller learns nothing from the difference."""
    try:
        _, key = normalise_name(name)
        number = normalise_phone(phone)
    except HTTPException:
        raise reject(403, IDENTIFICATION_FAILED) from None
    patient = session.scalar(
        select(Patient).where(Patient.name_key == key, Patient.phone == number)
    )
    if patient is None:
        raise reject(403, IDENTIFICATION_FAILED)
    return patient


def owned_appointment(
    session: Session, appointment_id: int, identity: tuple[str, str]
) -> AppointmentRecord:
    """An Appointment, only for the Patient it belongs to. A missing id, a Hold and someone
    else's Appointment all fail with the same 403 as a wrong name or phone."""
    patient = identify(session, *identity)
    appointment = session.get(AppointmentRecord, appointment_id)
    if appointment is None or appointment.patient_id != patient.id:
        raise reject(403, IDENTIFICATION_FAILED)
    return appointment


def upcoming_appointments(
    session: Session, identity: tuple[str, str], now: datetime
) -> list[AppointmentRecord]:
    patient = identify(session, *identity)
    return list(
        session.scalars(
            select(AppointmentRecord)
            .where(
                AppointmentRecord.patient_id == patient.id,
                AppointmentRecord.status == "confirmed",
                AppointmentRecord.starts_at > now,
            )
            .order_by(AppointmentRecord.starts_at)
        )
    )


def cancel(session: Session, appointment_id: int, identity: tuple[str, str]) -> AppointmentRecord:
    appointment = owned_appointment(session, appointment_id, identity)
    session.refresh(appointment, with_for_update=True)
    appointment.status = "cancelled"  # cancelling twice changes nothing
    session.flush()
    return appointment


def start_reschedule(
    session: Session, appointment_id: int, identity: tuple[str, str]
) -> AppointmentRecord:
    original = owned_appointment(session, appointment_id, identity)
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
