from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import BackendSettings
from backend.app.db import Base, CallRecord, create_db_engine


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


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    settings = settings or BackendSettings()
    engine = create_db_engine(settings)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        # TODO: replace create_all with Alembic migrations when the first real schema lands.
        Base.metadata.create_all(engine)
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

    @app.post("/calls", status_code=201)
    def open_call(session: SessionDep) -> CallOut:  # pyright: ignore[reportUnusedFunction]
        record = CallRecord(started_at=datetime.now(UTC), transcript=[])
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
            record.ended_at = datetime.now(UTC)
            session.commit()
        return _out(record)

    return app
