"""Availability: Provider working hours within Clinic Hours, fitted to a Service's duration,
minus Appointments and unexpired Holds, within the booking window. Pure of HTTP; time comes in
as an argument."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import (
    AppointmentRecord,
    ClinicHoursRow,
    Provider,
    ProviderService,
    Service,
    WorkingHours,
)

SLOT_STEP = timedelta(minutes=15)
HOLD_DURATION = timedelta(minutes=5)
# The booking window: Appointments start from 2 hours to 60 days ahead.
MIN_NOTICE = timedelta(hours=2)
MAX_AHEAD = timedelta(days=60)


@dataclass(frozen=True)
class Slot:
    provider_id: int
    start: datetime
    end: datetime


def _local(day: datetime, at: time) -> datetime:
    return datetime.combine(day.date(), at, tzinfo=CLINIC_TZ)


def blocking_appointments(
    session: Session, provider_id: int, start: datetime, end: datetime, now: datetime
) -> list[AppointmentRecord]:
    """Confirmed Appointments and unexpired Holds overlapping [start, end) for a Provider."""
    candidates = session.scalars(
        select(AppointmentRecord).where(
            AppointmentRecord.provider_id == provider_id,
            AppointmentRecord.starts_at < end,
            AppointmentRecord.ends_at > start,
            AppointmentRecord.status.in_(("held", "confirmed")),
        )
    )
    return [
        a
        for a in candidates
        if a.status == "confirmed" or (a.expires_at is not None and a.expires_at > now)
    ]


def find_availability(
    session: Session,
    service: Service,
    provider_id: int | None,
    start: datetime,
    end: datetime,
    now: datetime,
) -> list[Slot]:
    duration = timedelta(minutes=service.duration_minutes)
    clinic_hours = {row.weekday: row for row in session.scalars(select(ClinicHoursRow))}
    performing = select(ProviderService.provider_id).where(ProviderService.service_id == service.id)
    providers = select(Provider.id).where(Provider.id.in_(performing))
    if provider_id is not None:
        providers = providers.where(Provider.id == provider_id)

    slots: list[Slot] = []
    for pid in session.scalars(providers.order_by(Provider.id)):
        working = {
            w.weekday: w
            for w in session.scalars(select(WorkingHours).where(WorkingHours.provider_id == pid))
        }
        taken = blocking_appointments(session, pid, start, end, now)
        day = start.astimezone(CLINIC_TZ)
        last_day = end.astimezone(CLINIC_TZ).date()
        while day.date() <= last_day:
            hours, clinic = working.get(day.weekday()), clinic_hours.get(day.weekday())
            if hours is not None and clinic is not None:
                opens = _local(day, max(hours.opens, clinic.opens))
                closes = _local(day, min(hours.closes, clinic.closes))
                # Starts stay on a grid anchored at opening time.
                first = max(opens, start.astimezone(CLINIC_TZ))
                slot_start = opens + -(-(first - opens) // SLOT_STEP) * SLOT_STEP
                while slot_start + duration <= min(closes, end):
                    slot_end = slot_start + duration
                    clash = any(a.starts_at < slot_end and slot_start < a.ends_at for a in taken)
                    if now + MIN_NOTICE <= slot_start <= now + MAX_AHEAD and not clash:
                        slots.append(Slot(pid, slot_start, slot_end))
                    slot_start += SLOT_STEP
            day += timedelta(days=1)
    return slots
