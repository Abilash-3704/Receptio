import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app.config import BackendSettings
from backend.app.main import create_app
from tests.support import START_OF_TEST, FakeClock

# Set to a Postgres URL to run the whole backend suite (and the concurrency tests) against a
# real Postgres. The schema in that database is dropped and rebuilt by migrations per test.
POSTGRES_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START_OF_TEST)


BackendFactory = Callable[..., httpx.Client]


@pytest.fixture
def backend_factory(clock: FakeClock) -> Iterator[BackendFactory]:
    """Start a backend with settings overrides (a fresh schema on Postgres each time)."""
    stack = ExitStack()

    def make(**overrides: Any) -> httpx.Client:
        if POSTGRES_URL:
            engine = create_engine(POSTGRES_URL)
            with engine.begin() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
            engine.dispose()
        settings = BackendSettings(
            database_url=POSTGRES_URL or "sqlite+pysqlite:///:memory:",
            **{"demo_controls_enabled": True, **overrides},
        )
        client = stack.enter_context(
            TestClient(create_app(settings, clock=clock), base_url="http://backend")
        )
        # starlette's TestClient subclasses httpx2.Client in this dependency set, not
        # httpx.Client. It is call-compatible, so the agent code (typed on httpx) runs unchanged.
        return cast(httpx.Client, client)

    with stack:
        yield make


@pytest.fixture
def backend(backend_factory: BackendFactory) -> httpx.Client:
    return backend_factory()


@pytest.fixture
def postgres_backend(backend: httpx.Client) -> httpx.Client:
    if not POSTGRES_URL:
        pytest.skip("concurrency needs a real Postgres: set TEST_DATABASE_URL")
    return backend
