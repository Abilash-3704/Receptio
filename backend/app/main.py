from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.clock import CLINIC_TZ, Clock, system_clock
from backend.app.config import BackendSettings
from backend.app.db import (
    AppointmentRecord,
    Base,
    CallRecord,
    Provider,
    Service,
    create_db_engine,
)
from backend.app.scheduling import HOLD_DURATION, find_availability
from backend.app.seed import seed

# Longest range one Availability query may cover.
MAX_AVAILABILITY_DAYS = 31


class ProviderOut(BaseModel):
    id: int
    name: str
    role: str


class ServiceOut(BaseModel):
    id: int
    name: str
    duration_minutes: int


class SlotOut(BaseModel):
    provider_id: int
    start: datetime
    end: datetime


class HoldIn(BaseModel):
    service_id: int
    provider_id: int
    start: datetime


class HoldOut(BaseModel):
    id: int
    service_id: int
    provider_id: int
    start: datetime
    end: datetime
    expires_at: datetime


def aware(value: datetime) -> datetime:
    """A datetime without an offset is clinic local time (Asia/Kolkata)."""
    return value.replace(tzinfo=CLINIC_TZ) if value.tzinfo is None else value


class TurnIn(BaseModel):
    speaker: str
    text: str


class CallOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    ended_at: datetime | None
    transcript: list[TurnIn]


def _out(record: CallRecord) -> CallOut:
    return CallOut(
        id=record.id,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        transcript=[TurnIn(**t) for t in record.transcript],
    )


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(settings: BackendSettings | None = None, clock: Clock = system_clock) -> FastAPI:
    settings = settings or BackendSettings()
    engine = create_db_engine(settings)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        # TODO: replace create_all with Alembic migrations (deferred in ticket 03).
        Base.metadata.create_all(engine)
        with session_factory() as session:
            seed(session, clock())
        yield
        engine.dispose()

    app = FastAPI(title="Receptio clinic backend", lifespan=lifespan)
    app.state.session_factory = session_factory

    def get_call(session: Session, call_id: int) -> CallRecord:
        record = session.get(CallRecord, call_id)
        if record is None:
            raise HTTPException(status_code=404, detail="call not found")
        return record

    @app.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    @app.get("/providers")
    def list_providers(session: SessionDep) -> list[ProviderOut]:  # pyright: ignore[reportUnusedFunction]
        rows = session.scalars(select(Provider).order_by(Provider.id))
        return [ProviderOut(id=p.id, name=p.name, role=p.role) for p in rows]

    @app.get("/services")
    def list_services(session: SessionDep) -> list[ServiceOut]:  # pyright: ignore[reportUnusedFunction]
        rows = session.scalars(select(Service).order_by(Service.id))
        return [ServiceOut(id=s.id, name=s.name, duration_minutes=s.duration_minutes) for s in rows]

    def get_service(session: Session, service_id: int) -> Service:
        service = session.get(Service, service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="service not found")
        return service

    def get_provider(session: Session, provider_id: int) -> Provider:
        provider = session.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        return provider

    @app.get("/availability")
    def availability(  # pyright: ignore[reportUnusedFunction]
        session: SessionDep,
        service_id: int,
        start: datetime,
        end: datetime,
        provider_id: int | None = None,
    ) -> list[SlotOut]:
        start, end = aware(start), aware(end)
        if end <= start or end - start > timedelta(days=MAX_AVAILABILITY_DAYS):
            raise HTTPException(
                status_code=422,
                detail=f"range must be positive and at most {MAX_AVAILABILITY_DAYS} days",
            )
        service = get_service(session, service_id)
        if provider_id is not None:
            get_provider(session, provider_id)
        slots = find_availability(session, service, provider_id, start, end, clock())
        return [SlotOut(provider_id=s.provider_id, start=s.start, end=s.end) for s in slots]

    @app.post("/holds", status_code=201)
    def place_hold(body: HoldIn, session: SessionDep) -> HoldOut:  # pyright: ignore[reportUnusedFunction]
        service = get_service(session, body.service_id)
        get_provider(session, body.provider_id)
        start = aware(body.start)
        end = start + timedelta(minutes=service.duration_minutes)
        now = clock()
        open_times = find_availability(session, service, body.provider_id, start, end, now)
        if not any(s.start == start for s in open_times):
            raise HTTPException(status_code=409, detail="that time is not available")
        hold = AppointmentRecord(
            provider_id=body.provider_id,
            service_id=service.id,
            starts_at=start,
            ends_at=end,
            status="held",
            expires_at=now + HOLD_DURATION,
        )
        session.add(hold)
        session.commit()
        return HoldOut(
            id=hold.id,
            service_id=service.id,
            provider_id=hold.provider_id,
            start=start,
            end=end,
            expires_at=now + HOLD_DURATION,
        )

    @app.post("/calls", status_code=201)
    def open_call(session: SessionDep) -> CallOut:  # pyright: ignore[reportUnusedFunction]
        record = CallRecord(started_at=clock(), transcript=[])
        session.add(record)
        session.commit()
        return _out(record)

    @app.get("/calls/{call_id}")
    def read_call(call_id: int, session: SessionDep) -> CallOut:  # pyright: ignore[reportUnusedFunction]
        return _out(get_call(session, call_id))

    @app.post("/calls/{call_id}/turns", status_code=201)
    def add_turn(  # pyright: ignore[reportUnusedFunction]
        call_id: int, turn: TurnIn, session: SessionDep
    ) -> CallOut:
        record = get_call(session, call_id)
        if record.status != "open":
            raise HTTPException(status_code=409, detail="call already ended")
        record.transcript = [*record.transcript, turn.model_dump()]
        session.commit()
        return _out(record)

    @app.post("/calls/{call_id}/end")
    def end_call(call_id: int, session: SessionDep) -> CallOut:  # pyright: ignore[reportUnusedFunction]
        record = get_call(session, call_id)
        if record.status == "open":  # idempotent: a second end changes nothing
            record.status = "ended"
            record.ended_at = clock()
            session.commit()
        return _out(record)

    return app
