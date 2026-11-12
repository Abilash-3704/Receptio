"""Cost guard: admission of a new Call against daily caps, kept in the database.

Why the database and not Redis: the stack has no Redis, the backend already owns a transactional
store, and the one thing that must be exactly right (concurrent Calls never overshooting the
global cap) is a single conditional UPDATE in a transaction. The counters are a few rows a day.
Move to Redis only if admission ever becomes a hot path.

Each counter is a row (scope, key, day). `_take` adds to it only if the result stays within the
limit, in one UPDATE ... WHERE used + amount <= limit. Postgres locks the row, and a competing
transaction re-checks the condition against the committed value once it has the lock, so two
Calls racing for the last slot cannot both get it. A refusal rolls the whole admission back, so
a Call refused on the global cap does not use up the visitor's allowance.

The global cap is minutes of Call time, so admission reserves the longest a Call may run
(`call_max_seconds`) and settlement returns what the Call did not use. A Call that never settles
(a crashed worker) stays charged in full, which errs on the side of spending less."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.config import BackendSettings
from backend.app.db import CallAdmission, CapacityCounter

VISITOR = "visitor"
IP = "ip"
GLOBAL = "global"

REFUSALS = {
    "visitor_limit": (
        "You have reached the daily limit of {n} demo calls from this browser. "
        "Please come back tomorrow."
    ),
    "ip_limit": "This network has reached today's limit of demo calls. Please come back tomorrow.",
    "global_limit": "The demo has reached its capacity for today. Please come back tomorrow.",
}


@dataclass(frozen=True)
class Refused:
    reason: str
    message: str


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _day(now: datetime) -> date:
    return now.astimezone(CLINIC_TZ).date()


def _take(session: Session, scope: str, key: str, day: date, amount: int, limit: int) -> bool:
    if session.get(CapacityCounter, (scope, key, day)) is None:
        try:
            with session.begin_nested():
                session.add(CapacityCounter(scope=scope, key=key, day=day, used=0))
        except IntegrityError:
            pass  # another request created the row first
    taken = session.execute(
        update(CapacityCounter)
        .where(
            CapacityCounter.scope == scope,
            CapacityCounter.key == key,
            CapacityCounter.day == day,
            CapacityCounter.used + amount <= limit,
        )
        .values(used=CapacityCounter.used + amount)
    )
    return taken.rowcount == 1  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownVariableType]


def admit(
    session: Session,
    settings: BackendSettings,
    now: datetime,
    room: str,
    browser: str,
    ip: str,
) -> Refused | None:
    """Admit the Call in `room`, or say why not. Admitting a room twice charges once."""
    if session.get(CallAdmission, room) is not None:
        return None
    day = _day(now)
    reserve = settings.call_max_seconds
    if settings.caps_test_mode:
        return _record(
            session,
            CallAdmission(room=room, day=day, reserved_seconds=0, settled=False, admitted_at=now),
        )
    steps = [
        (IP, _digest(ip), 1, settings.ip_calls_per_day, "ip_limit"),
        (VISITOR, _digest(browser, ip), 1, settings.visitor_calls_per_day, "visitor_limit"),
        (GLOBAL, "all", reserve, settings.global_minutes_per_day * 60, "global_limit"),
    ]
    for scope, key, amount, limit, reason in steps:
        if not _take(session, scope, key, day, amount, limit):
            session.rollback()
            if session.get(CallAdmission, room) is not None:
                return None  # a concurrent request admitted this room while this one was refused
            return Refused(reason, REFUSALS[reason].format(n=settings.visitor_calls_per_day))
    return _record(
        session,
        CallAdmission(room=room, day=day, reserved_seconds=reserve, settled=False, admitted_at=now),
    )


def _record(session: Session, admission: CallAdmission) -> None:
    """Store the admission. If a concurrent request admitted the same room first, this one is a
    repeat: roll back its charges (the room is charged once) and answer as admitted."""
    session.add(admission)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.get(CallAdmission, admission.room) is None:
            raise


def settle(session: Session, now: datetime, room: str, used_seconds: int) -> bool:
    """Return the minutes a finished Call did not use. False if the room was never admitted.
    Settling twice returns them once. The reported length is a claim: the Call is charged at
    least the time the backend itself saw pass since admission, and never more than it reserved,
    so a wrong or dishonest number cannot refund more than the Call really left unused."""
    admission = session.get(CallAdmission, room)
    if admission is None:
        return False
    closed = session.execute(
        update(CallAdmission)
        .where(CallAdmission.room == room, CallAdmission.settled.is_(False))
        .values(settled=True)
    )
    first_settlement = closed.rowcount == 1  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownVariableType]
    if first_settlement and admission.reserved_seconds > 0:
        seen = (
            0
            if admission.admitted_at is None
            else max(0, int((now - admission.admitted_at).total_seconds()))
        )
        refund = max(0, admission.reserved_seconds - max(0, used_seconds, seen))
        session.execute(
            update(CapacityCounter)
            .where(
                CapacityCounter.scope == GLOBAL,
                CapacityCounter.key == "all",
                CapacityCounter.day == admission.day,
            )
            .values(used=CapacityCounter.used - refund)
        )
    session.commit()
    return True
