"""Seed data: a small fictional clinic in Bengaluru. Idempotent; runs at startup."""

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

SERVICES = {  # name: duration in minutes
    "Check-up": 30,
    "Cleaning": 45,
    "Filling": 60,
    "Root canal": 90,
}

# name: (role, services, weekdays (0 = Monday), opens, closes)
PROVIDERS = {
    "Dr. Anita Rao": ("dentist", list(SERVICES), range(0, 6), time(9), time(18)),
    "Dr. Vikram Shetty": (
        "dentist",
        ["Check-up", "Filling", "Root canal"],
        range(0, 5),
        time(10),
        time(16),
    ),
    "Meera Nair": ("hygienist", ["Check-up", "Cleaning"], (1, 3, 5), time(9), time(14)),
}

# Clinic Hours: Monday to Saturday 09:00-18:00, closed Sunday.
CLINIC_OPEN, CLINIC_CLOSE = time(9), time(18)


def seed(session: Session, now: datetime) -> None:
    if session.scalar(select(Provider.id)) is not None:
        return
    for weekday in range(0, 6):
        session.add(ClinicHoursRow(weekday=weekday, opens=CLINIC_OPEN, closes=CLINIC_CLOSE))
    services = {n: Service(name=n, duration_minutes=m) for n, m in SERVICES.items()}
    session.add_all(services.values())
    providers: dict[str, Provider] = {}
    for name, (role, performs, weekdays, opens, closes) in PROVIDERS.items():
        provider = Provider(name=name, role=role)
        session.add(provider)
        session.flush()
        providers[name] = provider
        for service_name in performs:
            session.add(
                ProviderService(provider_id=provider.id, service_id=services[service_name].id)
            )
        for weekday in weekdays:
            session.add(
                WorkingHours(provider_id=provider.id, weekday=weekday, opens=opens, closes=closes)
            )
    session.flush()

    # One existing Appointment so Availability has something to subtract: Dr. Rao, a Filling
    # at 10:00-11:00 on the first working day after today (Monday to Saturday).
    day = now.astimezone(CLINIC_TZ) + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    starts_at = datetime.combine(day.date(), time(10), tzinfo=CLINIC_TZ)
    session.add(
        AppointmentRecord(
            provider_id=providers["Dr. Anita Rao"].id,
            service_id=services["Filling"].id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=SERVICES["Filling"]),
            status="confirmed",
            patient_name="Seeded Patient",
        )
    )
    session.commit()
