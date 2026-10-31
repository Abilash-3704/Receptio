import os
from collections.abc import Callable, Iterator
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app.config import BackendSettings
from backend.app.main import create_app
from backend.app.trace import load_trace
from tests.support import START_OF_TEST, FakeClock

# Set to a Postgres URL to run the whole backend suite (and the concurrency tests) against a
# real Postgres. The schema in that database is dropped and rebuilt by migrations per test.
POSTGRES_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START_OF_TEST)


@pytest.fixture
def backend(clock: FakeClock) -> Iterator[httpx.Client]:
    if POSTGRES_URL:
        engine = create_engine(POSTGRES_URL)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()
    settings = BackendSettings(
        database_url=POSTGRES_URL or "sqlite+pysqlite:///:memory:", demo_controls_enabled=True
    )
    with TestClient(create_app(settings, clock=clock), base_url="http://backend") as client:
        # starlette's TestClient subclasses httpx2.Client in this dependency set, not
        # httpx.Client. It is call-compatible, so the agent code (typed on httpx) runs unchanged.
        yield cast(httpx.Client, client)


@pytest.fixture
def postgres_backend(backend: httpx.Client) -> httpx.Client:
    if not POSTGRES_URL:
        pytest.skip("concurrency needs a real Postgres: set TEST_DATABASE_URL")
    return backend


@pytest.fixture
def trace(backend: httpx.Client) -> Callable[[int], dict[str, Any]]:
    """Read a stored Call back the way the trace viewer and the eval harness do: straight from the
    database through `backend.app.trace`, not through any HTTP endpoint."""
    factory = cast(Any, backend).app.state.session_factory

    def read(call_id: int) -> dict[str, Any]:
        with factory() as session:
            return load_trace(session, call_id, cast(Any, backend).app.state.prices)

    return read
