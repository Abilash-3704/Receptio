from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app import booking, clinic_hours, knowledge
from backend.app.clock import CLINIC_TZ, Clock, system_clock
from backend.app.config import BackendSettings
from backend.app.cost import Prices, call_cost
from backend.app.db import (
    AppointmentRecord,
    CallLlmCall,
    CallRecord,
    CallToolCall,
    CallTurn,
    CallTurnTiming,
    ConfirmationMessage,
    EscalationRecord,
    Patient,
    Provider,
    Service,
    create_db_engine,
)
from backend.app.idempotency import run_idempotent
from backend.app.migrate import upgrade
from backend.app.scheduling import find_availability
from backend.app.seed import seed

# Longest range one Availability query may cover.
MAX_AVAILABILITY_DAYS = 31

# Ids are Postgres 4-byte integers. A path or query id outside that range cannot exist, and a body
# id must be a real integer (not true, 1.5 or "1"): reject it as a 422 instead of passing it on.
MAX_ID = 2**31 - 1
RowId = Annotated[int, Field(ge=1, le=MAX_ID)]
BodyId = Annotated[int, Field(ge=1, le=MAX_ID, strict=True)]


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
    service_id: BodyId
    provider_id: BodyId
    start: datetime


class HoldOut(BaseModel):
    id: int
    service_id: int
    provider_id: int
    start: datetime
    end: datetime
    expires_at: datetime
    read_back_recorded: bool
    replaces_id: int | None


class ConfirmIn(BaseModel):
    patient_name: str
    patient_phone: str


class IdentityIn(BaseModel):
    patient_name: str
    patient_phone: str


class RescheduleIn(IdentityIn):
    provider_id: BodyId
    start: datetime
    service_id: BodyId | None = None  # defaults to the original Appointment's Service


class AppointmentSummaryOut(BaseModel):
    id: int
    service: str
    provider: str
    start: datetime
    end: datetime


class AppointmentOut(BaseModel):
    id: int
    status: str
    service_id: int
    provider_id: int
    start: datetime
    end: datetime
    patient_id: int | None
    patient_name: str | None
    replaces_id: int | None


class ConfirmationMessageOut(BaseModel):
    id: int
    appointment_id: int
    to_phone: str
    body: str
    created_at: datetime


def aware(value: datetime) -> datetime:
    """A datetime without an offset is clinic local time (Asia/Kolkata)."""
    return value.replace(tzinfo=CLINIC_TZ) if value.tzinfo is None else value


class ClinicOverrideIn(BaseModel):
    mode: Literal["open", "closed"] | None


class EscalationIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    caller_phone: str | None = None
    # Normally absent: the backend decides from Clinic Hours. A Callback Request can be forced
    # (a request too soon to book, failed identification); a Handoff only while open.
    kind: Literal["handoff", "callback_request"] | None = None


class EscalationOut(BaseModel):
    id: int
    call_id: int
    kind: str  # handoff or callback_request
    reason: str
    caller_phone: str | None
    created_at: datetime


def escalation_out(record: EscalationRecord) -> EscalationOut:
    return EscalationOut(
        id=record.id,
        call_id=record.call_id,
        kind=record.kind,
        reason=record.reason,
        caller_phone=record.caller_phone,
        created_at=record.created_at,
    )


# Longest single utterance stored in a transcript. Caller speech is untrusted input.
MAX_TURN_CHARS = 2000


class FaqEntryOut(BaseModel):
    topic: str
    text: str


class TurnIn(BaseModel):
    speaker: Literal["caller", "agent"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_TURN_CHARS)]


class CallOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    ended_at: datetime | None
    transcript: list[TurnIn]
    outcome: str | None = None  # completed, escalated or dropped; None while the Call is open
    outcome_reason: str | None = None


class EndIn(BaseModel):
    """What the agent says about how the Call ended. The backend overrules it with the facts it
    holds: a Call with an Escalation is escalated whatever was claimed."""

    outcome: Literal["completed", "dropped"] | None = None
    reason: Annotated[str, Field(max_length=200)] | None = None


class OpenCallIn(BaseModel):
    mode: Literal["text", "voice"] = "text"


class ToolCallIn(BaseModel):
    turn_seq: Annotated[int, Field(ge=1, le=MAX_ID)]
    name: Annotated[str, Field(min_length=1, max_length=40)]
    args: dict[str, Any]
    ok: bool
    refused: bool
    result: dict[str, Any]


class LlmCallIn(BaseModel):
    """One LLM request's token usage, as the provider reported it."""

    turn_seq: Annotated[int, Field(ge=1, le=MAX_ID)]
    model: Annotated[str, Field(min_length=1, max_length=80)]
    input_tokens: Annotated[int, Field(ge=0, le=10**9)]
    output_tokens: Annotated[int, Field(ge=0, le=10**9)]
    duration_ms: Annotated[int, Field(ge=0, le=10**7)] | None = None


class TurnTimingIn(BaseModel):
    """One line of voice/timing.py's per-turn record."""

    caller_turn_seq: Annotated[int, Field(ge=1, le=MAX_ID)] | None = None
    asr_final_ms: int | None = None
    llm_first_token_ms: int | None = None
    tts_first_audio_ms: int | None = None
    total_ms: int | None = None
    total_from_speech_end_ms: int | None = None
    interrupted: bool = False


def _out(session: Session, record: CallRecord) -> CallOut:
    turns = session.scalars(
        select(CallTurn).where(CallTurn.call_id == record.id).order_by(CallTurn.seq)
    )
    return CallOut(
        id=record.id,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        transcript=[TurnIn.model_validate({"speaker": t.speaker, "text": t.text}) for t in turns],
        outcome=record.outcome,
        outcome_reason=record.outcome_reason,
    )


def message_out(message: ConfirmationMessage) -> ConfirmationMessageOut:
    return ConfirmationMessageOut(
        id=message.id,
        appointment_id=message.appointment_id,
        to_phone=message.to_phone,
        body=message.body,
        created_at=message.created_at,
    )


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[str, Header(min_length=1, max_length=128)]


def create_app(settings: BackendSettings | None = None, clock: Clock = system_clock) -> FastAPI:
    settings = settings or BackendSettings()
    engine = create_db_engine(settings)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        upgrade(engine)
        with session_factory() as session:
            seed(session, clock())
        yield
        engine.dispose()

    prices = Prices.from_settings(settings)

    app = FastAPI(title="Receptio clinic backend", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.prices = prices

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
        service_id: RowId,
        start: datetime,
        end: datetime,
        provider_id: RowId | None = None,
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

    def hold_out(hold: AppointmentRecord) -> HoldOut:
        assert hold.expires_at is not None
        return HoldOut(
            id=hold.id,
            service_id=hold.service_id,
            provider_id=hold.provider_id,
            start=hold.starts_at,
            end=hold.ends_at,
            expires_at=hold.expires_at,
            read_back_recorded=hold.read_back_at is not None,
            replaces_id=hold.replaces_id,
        )

    def appointment_out(session: Session, record: AppointmentRecord) -> AppointmentOut:
        patient = None if record.patient_id is None else session.get(Patient, record.patient_id)
        return AppointmentOut(
            id=record.id,
            status=record.status,
            service_id=record.service_id,
            provider_id=record.provider_id,
            start=record.starts_at,
            end=record.ends_at,
            patient_id=record.patient_id,
            patient_name=None if patient is None else patient.full_name,
            replaces_id=record.replaces_id,
        )

    @app.post("/holds", status_code=201, response_model=None)
    def place_hold(  # pyright: ignore[reportUnusedFunction]
        body: HoldIn, session: SessionDep, idempotency_key: IdempotencyKey
    ) -> JSONResponse:
        def work() -> tuple[int, BaseModel]:
            service = get_service(session, body.service_id)
            get_provider(session, body.provider_id)
            hold = booking.place_hold(
                session, service, body.provider_id, aware(body.start), clock()
            )
            return 201, hold_out(hold)

        return run_idempotent(
            session, idempotency_key, "POST /holds", body.model_dump(mode="json"), work
        )

    @app.post("/holds/{hold_id}/release", response_model=None)
    def release_hold(  # pyright: ignore[reportUnusedFunction]
        hold_id: int, session: SessionDep, idempotency_key: IdempotencyKey
    ) -> JSONResponse:
        def work() -> tuple[int, BaseModel]:
            return 200, hold_out(booking.release_hold(session, hold_id, clock()))

        return run_idempotent(session, idempotency_key, f"POST /holds/{hold_id}/release", {}, work)

    @app.post("/holds/{hold_id}/read-back", response_model=None)
    def record_read_back(  # pyright: ignore[reportUnusedFunction]
        hold_id: RowId,
        session: SessionDep,
        idempotency_key: IdempotencyKey,
        body: IdentityIn | None = None,
    ) -> JSONResponse:
        def work() -> tuple[int, BaseModel]:
            identity = None if body is None else (body.patient_name, body.patient_phone)
            return 200, hold_out(booking.record_read_back(session, hold_id, identity, clock()))

        payload = {} if body is None else body.model_dump()
        return run_idempotent(
            session, idempotency_key, f"POST /holds/{hold_id}/read-back", payload, work
        )

    @app.post("/holds/{hold_id}/confirm", status_code=201, response_model=None)
    def confirm_hold(  # pyright: ignore[reportUnusedFunction]
        hold_id: RowId,
        session: SessionDep,
        idempotency_key: IdempotencyKey,
        body: ConfirmIn | None = None,
    ) -> JSONResponse:
        def work() -> tuple[int, BaseModel]:
            patient = None if body is None else (body.patient_name, body.patient_phone)
            appointment = booking.confirm(session, hold_id, patient, clock())
            return 201, appointment_out(session, appointment)

        payload = {} if body is None else body.model_dump()
        return run_idempotent(
            session, idempotency_key, f"POST /holds/{hold_id}/confirm", payload, work
        )

    @app.post("/appointments/{appointment_id}/read")
    def read_appointment(  # pyright: ignore[reportUnusedFunction]
        appointment_id: RowId, body: IdentityIn, session: SessionDep
    ) -> AppointmentOut:
        """An Appointment, only for the Patient whose full name AND phone match. An unknown id, a
        Hold and someone else's Appointment all fail with the same 403 as a wrong identity."""
        record = booking.owned_appointment(
            session, appointment_id, (body.patient_name, body.patient_phone)
        )
        return appointment_out(session, record)

    @app.post("/appointments/lookup")
    def lookup_appointments(  # pyright: ignore[reportUnusedFunction]
        body: IdentityIn, session: SessionDep
    ) -> list[AppointmentSummaryOut]:
        """A Patient's upcoming Confirmed Appointments, only if full name AND phone both match.
        Any failure is the same 403, so nothing leaks about who or what exists."""
        found = booking.upcoming_appointments(
            session, (body.patient_name, body.patient_phone), clock()
        )
        summaries: list[AppointmentSummaryOut] = []
        for a in found:
            service, provider = (
                session.get(Service, a.service_id),
                session.get(Provider, a.provider_id),
            )
            assert service is not None and provider is not None
            summaries.append(
                AppointmentSummaryOut(
                    id=a.id,
                    service=service.name,
                    provider=provider.name,
                    start=a.starts_at,
                    end=a.ends_at,
                )
            )
        return summaries

    @app.post("/appointments/{appointment_id}/cancel", response_model=None)
    def cancel_appointment(  # pyright: ignore[reportUnusedFunction]
        appointment_id: RowId,
        body: IdentityIn,
        session: SessionDep,
        idempotency_key: IdempotencyKey,
    ) -> JSONResponse:
        def work() -> tuple[int, BaseModel]:
            identity = (body.patient_name, body.patient_phone)
            return 200, appointment_out(session, booking.cancel(session, appointment_id, identity))

        return run_idempotent(
            session,
            idempotency_key,
            f"POST /appointments/{appointment_id}/cancel",
            body.model_dump(),
            work,
        )

    @app.post("/appointments/{appointment_id}/reschedule", status_code=201, response_model=None)
    def reschedule_appointment(  # pyright: ignore[reportUnusedFunction]
        appointment_id: RowId,
        body: RescheduleIn,
        session: SessionDep,
        idempotency_key: IdempotencyKey,
    ) -> JSONResponse:
        """Places a Hold on the new time. Nothing about the Appointment changes until that Hold
        is read back and confirmed, which cancels this Appointment in the same transaction."""

        def work() -> tuple[int, BaseModel]:
            original = booking.start_reschedule(
                session, appointment_id, (body.patient_name, body.patient_phone)
            )
            service = get_service(session, body.service_id or original.service_id)
            get_provider(session, body.provider_id)
            hold = booking.place_hold(
                session, service, body.provider_id, aware(body.start), clock(), replaces=original
            )
            return 201, hold_out(hold)

        return run_idempotent(
            session,
            idempotency_key,
            f"POST /appointments/{appointment_id}/reschedule",
            body.model_dump(mode="json"),
            work,
        )

    @app.post(
        "/appointments/{appointment_id}/confirmation-message",
        status_code=201,
        response_model=None,
    )
    def send_confirmation_message(  # pyright: ignore[reportUnusedFunction]
        appointment_id: RowId, session: SessionDep, idempotency_key: IdempotencyKey
    ) -> JSONResponse:
        """Record the mocked Confirmation Message for a Confirmed Appointment."""

        def work() -> tuple[int, BaseModel]:
            message = booking.record_confirmation_message(session, appointment_id, clock())
            return 201, message_out(message)

        return run_idempotent(
            session,
            idempotency_key,
            f"POST /appointments/{appointment_id}/confirmation-message",
            {},
            work,
        )

    @app.get("/confirmation-messages")
    def list_confirmation_messages(  # pyright: ignore[reportUnusedFunction]
        session: SessionDep, appointment_id: RowId | None = None
    ) -> list[ConfirmationMessageOut]:
        query = select(ConfirmationMessage).order_by(ConfirmationMessage.id)
        if appointment_id is not None:
            query = query.where(ConfirmationMessage.appointment_id == appointment_id)
        return [message_out(m) for m in session.scalars(query)]

    # The demo-only override: "open", "closed" or None (follow the real Clinic Hours).
    clinic_override: list[str | None] = [None]

    def clinic_is_open(session: Session) -> bool:
        if clinic_override[0] is not None:
            return clinic_override[0] == "open"
        return clinic_hours.is_open(session, clock())

    @app.get("/faq")
    def faq(session: SessionDep, q: str | None = None) -> list[FaqEntryOut]:  # pyright: ignore[reportUnusedFunction]
        """The knowledge base. With `q`, only the entries that answer it (possibly none)."""
        found = knowledge.entries(session) if q is None else knowledge.search(session, q)
        return [FaqEntryOut(topic=e.topic, text=e.text) for e in found]

    @app.get("/clinic-status")
    def clinic_status(session: SessionDep) -> dict[str, bool | str | None]:  # pyright: ignore[reportUnusedFunction]
        return {"open": clinic_is_open(session), "override": clinic_override[0]}

    if settings.demo_controls_enabled:

        @app.put("/demo/clinic-override")
        def set_clinic_override(body: ClinicOverrideIn) -> dict[str, str | None]:  # pyright: ignore[reportUnusedFunction]
            clinic_override[0] = body.mode
            return {"override": body.mode}

    @app.post("/calls/{call_id}/escalations", status_code=201, response_model=None)
    def escalate(  # pyright: ignore[reportUnusedFunction]
        call_id: int, body: EscalationIn, session: SessionDep, idempotency_key: IdempotencyKey
    ) -> JSONResponse:
        """Pass the Call to staff. The backend decides the kind: a Handoff during Clinic Hours,
        otherwise a Callback Request. A Callback Request may be asked for at any time; a Handoff
        never while the clinic is closed."""

        def work() -> tuple[int, BaseModel]:
            get_call(session, call_id)
            is_open = clinic_is_open(session)
            if body.kind == "handoff" and not is_open:
                raise HTTPException(status_code=422, detail="no Handoff while the clinic is closed")
            kind = body.kind or ("handoff" if is_open else "callback_request")
            phone = None if not body.caller_phone else booking.normalise_phone(body.caller_phone)
            record = EscalationRecord(
                call_id=call_id,
                kind=kind,
                reason=body.reason,
                caller_phone=phone,
                created_at=clock(),
            )
            session.add(record)
            session.flush()
            return 201, escalation_out(record)

        return run_idempotent(
            session,
            idempotency_key,
            f"POST /calls/{call_id}/escalations",
            body.model_dump(),
            work,
        )

    @app.get("/escalations")
    def list_escalations(session: SessionDep) -> list[EscalationOut]:  # pyright: ignore[reportUnusedFunction]
        query = select(EscalationRecord).order_by(EscalationRecord.id)
        return [escalation_out(r) for r in session.scalars(query)]

    @app.post("/calls", status_code=201)
    def open_call(session: SessionDep, body: OpenCallIn | None = None) -> CallOut:  # pyright: ignore[reportUnusedFunction]
        record = CallRecord(started_at=clock(), mode=(body or OpenCallIn()).mode)
        session.add(record)
        session.commit()
        return _out(session, record)

    @app.get("/calls/{call_id}")
    def read_call(call_id: RowId, session: SessionDep) -> CallOut:  # pyright: ignore[reportUnusedFunction]
        return _out(session, get_call(session, call_id))

    @app.post("/calls/{call_id}/turns", status_code=201)
    def add_turn(  # pyright: ignore[reportUnusedFunction]
        call_id: RowId, turn: TurnIn, session: SessionDep
    ) -> CallOut:
        record = get_call(session, call_id)
        if record.status != "open":
            raise HTTPException(status_code=409, detail="call already ended")
        last = session.scalar(select(func.max(CallTurn.seq)).where(CallTurn.call_id == call_id))
        session.add(
            CallTurn(
                call_id=call_id,
                seq=(last or 0) + 1,
                speaker=turn.speaker,
                text=turn.text,
                created_at=clock(),
            )
        )
        session.commit()
        return _out(session, record)

    @app.post("/calls/{call_id}/tool-calls", status_code=201)
    def add_tool_call(call_id: RowId, body: ToolCallIn, session: SessionDep) -> dict[str, int]:  # pyright: ignore[reportUnusedFunction]
        get_call(session, call_id)
        row = CallToolCall(call_id=call_id, created_at=clock(), **body.model_dump())
        session.add(row)
        session.commit()
        return {"id": row.id}

    @app.post("/calls/{call_id}/llm-calls", status_code=201)
    def add_llm_call(call_id: RowId, body: LlmCallIn, session: SessionDep) -> dict[str, int]:  # pyright: ignore[reportUnusedFunction]
        get_call(session, call_id)
        row = CallLlmCall(
            call_id=call_id,
            created_at=clock(),
            cost_usd=prices.llm_cost(body.model, body.input_tokens, body.output_tokens),
            price_placeholder=prices.llm_placeholder,
            **body.model_dump(),
        )
        session.add(row)
        session.commit()
        return {"id": row.id}

    @app.post("/calls/{call_id}/turn-timings", status_code=201)
    def add_turn_timing(call_id: RowId, body: TurnTimingIn, session: SessionDep) -> dict[str, int]:  # pyright: ignore[reportUnusedFunction]
        get_call(session, call_id)
        row = CallTurnTiming(call_id=call_id, **body.model_dump())
        session.add(row)
        session.commit()
        return {"id": row.id}

    @app.post("/calls/{call_id}/end")
    def end_call(  # pyright: ignore[reportUnusedFunction]
        call_id: RowId, session: SessionDep, body: EndIn | None = None
    ) -> CallOut:
        record = get_call(session, call_id)
        if record.status == "open":  # idempotent: a second end changes nothing
            claimed = body or EndIn()
            first_escalation = session.scalars(
                select(EscalationRecord)
                .where(EscalationRecord.call_id == call_id)
                .order_by(EscalationRecord.id)
            ).first()
            record.status = "ended"
            ended_at = record.ended_at = clock()
            if first_escalation is not None:
                record.outcome, record.outcome_reason = "escalated", first_escalation.reason
            else:
                record.outcome = claimed.outcome or "completed"
                record.outcome_reason = claimed.reason
            llm_calls = session.scalars(select(CallLlmCall).where(CallLlmCall.call_id == call_id))
            spoken = session.scalars(
                select(CallTurn.text).where(
                    CallTurn.call_id == call_id, CallTurn.speaker == "agent"
                )
            )
            record.cost = call_cost(
                prices,
                record.mode,
                record.started_at,
                ended_at,
                [(c.model, c.input_tokens, c.output_tokens) for c in llm_calls],
                sum(len(text) for text in spoken),
            )
            session.commit()
        return _out(session, record)

    return app
