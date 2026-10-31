"""Clinic Hours: when clinic staff are available. Seeded configuration (see seed.py), read with
the injectable clock. A Handoff is only possible while the clinic is open."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import ClinicHoursRow


def is_open(session: Session, now: datetime) -> bool:
    local = now.astimezone(CLINIC_TZ)
    row = session.scalar(select(ClinicHoursRow).where(ClinicHoursRow.weekday == local.weekday()))
    return row is not None and row.opens <= local.time().replace(tzinfo=None) < row.closes
