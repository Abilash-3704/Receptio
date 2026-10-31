from datetime import datetime

from sqlalchemy import JSON, DateTime, Engine, String, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from backend.app.config import BackendSettings


class Base(DeclarativeBase):
    pass


class CallRecord(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    transcript: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)


def create_db_engine(settings: BackendSettings) -> Engine:
    if make_url(settings.database_url).get_backend_name() == "sqlite":
        # Tests only: a single shared in-memory connection.
        return create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(
        settings.database_url,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        pool_pre_ping=True,
    )
