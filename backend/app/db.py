from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Dialect,
    Engine,
    ForeignKey,
    String,
    Time,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from backend.app.config import BackendSettings


class Base(DeclarativeBase):
    pass


class UtcDateTime(TypeDecorator[datetime]):
    """Aware datetimes in, aware UTC datetimes out, on Postgres and SQLite alike."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(32))  # dentist or hygienist


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    duration_minutes: Mapped[int]


class ProviderService(Base):
    """Which Services a Provider performs."""

    __tablename__ = "provider_services"

    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)


class WorkingHours(Base):
    """A Provider's working hours on one weekday (0 = Monday), clinic local time."""

    __tablename__ = "provider_working_hours"

    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), primary_key=True)
    weekday: Mapped[int] = mapped_column(primary_key=True)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)


class ClinicHoursRow(Base):
    """Clinic Hours on one weekday (0 = Monday), clinic local time. A missing day is closed."""

    __tablename__ = "clinic_hours"

    weekday: Mapped[int] = mapped_column(primary_key=True)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)


class Patient(Base):
    """The person an Appointment is for. Created when a new Caller's first Appointment is
    Confirmed, never for a Hold. A Patient is identified by full name and phone together."""

    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("name_key", "phone"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(80))
    name_key: Mapped[str] = mapped_column(String(80))  # casefolded full_name, for matching
    phone: Mapped[str] = mapped_column(String(20))  # normalised: optional +, then digits


class IdempotencyRecord(Base):
    """The stored result of a write, replayed when the same Idempotency-Key is retried."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(200))  # method and path
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(default=None)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)


class AppointmentRecord(Base):
    """An Appointment. A Hold is a held Appointment: it blocks time only until expires_at."""

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ends_at: Mapped[datetime] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16))  # held, confirmed, cancelled or released
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    # Set when the Appointment is Confirmed; a Hold has no Patient.
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), default=None)
    # The Caller's recorded "yes" to the read-back of this exact Hold.
    read_back_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    # A reschedule Hold names the Appointment it replaces; confirming it cancels that one.
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), default=None)


class ConfirmationMessage(Base):
    """The mocked message "sent" to the Patient's phone when an Appointment is Confirmed. Nothing
    is delivered; this record is what the screen and the dashboard show. One per Appointment."""

    __tablename__ = "confirmation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), unique=True)
    to_phone: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class EscalationRecord(Base):
    """An Escalation: the agent passing a Call to clinic staff. Always has a reason. A Callback
    Request also keeps the Caller's number; a Handoff connects to (simulated) staff instead."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"))
    kind: Mapped[str] = mapped_column(String(16))  # handoff or callback_request
    reason: Mapped[str] = mapped_column(String(200))
    caller_phone: Mapped[str | None] = mapped_column(String(20), default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class CallRecord(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    mode: Mapped[str] = mapped_column(String(8), default="text")  # text or voice
    # How the Call ended: completed, escalated or dropped. None while open. The reason is the
    # Escalation's reason, or why the Call counts as dropped.
    outcome: Mapped[str | None] = mapped_column(String(16), default=None)
    outcome_reason: Mapped[str | None] = mapped_column(String(200), default=None)
    # Cost of the whole Call, written when it ends (backend/app/cost.py): every component says
    # whether it was measured or estimated, and whether its price is a placeholder.
    cost: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)


class CallTurn(Base):
    """One thing said in a Call, in order. The transcript is these rows. Personal data (names,
    phone numbers) is stored here in full and nowhere else but the other trace tables."""

    __tablename__ = "call_turns"
    __table_args__ = (UniqueConstraint("call_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    seq: Mapped[int]  # 1-based position in the Call
    speaker: Mapped[str] = mapped_column(String(8))  # caller or agent
    text: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class CallToolCall(Base):
    """A tool the agent ran, with what it was asked and what came back, under the Caller turn
    that caused it."""

    __tablename__ = "call_tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    turn_seq: Mapped[int]  # seq of the Caller turn being answered
    name: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict[str, Any]] = mapped_column(JSON)
    ok: Mapped[bool] = mapped_column(Boolean)
    refused: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class CallLlmCall(Base):
    """One request to the LLM, with the token usage the provider reported for it. Cost is
    tokens times the configured price for the model; null when the model has no price."""

    __tablename__ = "call_llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    turn_seq: Mapped[int]
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    cost_usd: Mapped[float | None] = mapped_column(default=None)
    price_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class CallTurnTiming(Base):
    """Voice latency of one Caller turn, as the pipeline measured it (voice/timing.py). Null
    where a moment was not seen. `caller_turn_seq` links it to the transcript when known."""

    __tablename__ = "call_turn_timings"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    caller_turn_seq: Mapped[int | None] = mapped_column(default=None)
    asr_final_ms: Mapped[int | None] = mapped_column(default=None)
    llm_first_token_ms: Mapped[int | None] = mapped_column(default=None)
    tts_first_audio_ms: Mapped[int | None] = mapped_column(default=None)
    total_ms: Mapped[int | None] = mapped_column(default=None)
    total_from_speech_end_ms: Mapped[int | None] = mapped_column(default=None)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)


def create_db_engine(settings: BackendSettings) -> Engine:
    if make_url(settings.database_url).get_backend_name() == "sqlite":
        # Tests only: a single shared in-memory connection.
        engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # SQLAlchemy's documented recipe so SAVEPOINT (begin_nested) works with pysqlite.
        @event.listens_for(engine, "connect")
        def _no_driver_begin(dbapi_connection: Any, _: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _emit_begin(connection: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            connection.exec_driver_sql("BEGIN")

        return engine
    return create_engine(
        settings.database_url,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        pool_pre_ping=True,
    )
