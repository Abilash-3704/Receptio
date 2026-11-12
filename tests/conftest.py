import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from agent.config import AgentSettings
from backend.app.config import BackendSettings
from backend.app.main import create_app
from backend.app.trace import load_trace
from evals.config import EvalSettings
from tests.support import SERVICE_TOKEN, STAFF_PASSWORD, START_OF_TEST, FakeClock
from voice.config import VoiceSettings

# Set to a Postgres URL to run the whole backend suite (and the concurrency tests) against a
# real Postgres. The schema in that database is dropped and rebuilt by migrations per test.
POSTGRES_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def system_clock_is_start_of_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's tools and the eval runner default to the real clock, while the backend gets
    the fake one. Scenarios and tests name fixed dates (2026-11-03), so pin the real clock to the
    test start: otherwise they rot the day those dates pass."""
    import backend.app.clock as clock_module

    class PinnedDatetime(clock_module.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return START_OF_TEST.astimezone(tz) if tz is not None else START_OF_TEST

    monkeypatch.setattr(clock_module, "datetime", PinnedDatetime)


@pytest.fixture(autouse=True)
def no_dotenv_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """No settings class reads a developer's local `.env`: every test starts from the defaults
    plus what it sets itself, so a value in `.env` can never change a result."""
    for settings_class in (BackendSettings, AgentSettings, VoiceSettings, EvalSettings):
        monkeypatch.setitem(settings_class.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def backend_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every BackendSettings() a test builds finds the test secrets in its environment."""
    monkeypatch.setenv("BACKEND_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("DASHBOARD_PASSWORD", STAFF_PASSWORD)


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
        merged: dict[str, Any] = {
            "demo_controls_enabled": True,
            "backend_service_token": SERVICE_TOKEN,
            "dashboard_password": STAFF_PASSWORD,
            **overrides,
        }
        settings = BackendSettings(
            database_url=POSTGRES_URL or "sqlite+pysqlite:///:memory:", **merged
        )
        client = stack.enter_context(
            TestClient(create_app(settings, clock=clock), base_url="http://backend")
        )
        # The default client is both the agent (service token) and staff (Basic), so one client
        # can drive a whole scenario. tests/test_auth.py uses credential-free clients.
        if merged["backend_service_token"] is not None:
            client.headers["X-Service-Token"] = merged["backend_service_token"]
        if merged["dashboard_password"] is not None:
            client.auth = ("staff", merged["dashboard_password"])
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


@pytest.fixture
def trace(backend: httpx.Client) -> Callable[[int], dict[str, Any]]:
    """Read a stored Call back the way the trace viewer and the eval harness do: straight from the
    database through `backend.app.trace`, not through any HTTP endpoint."""
    factory = cast(Any, backend).app.state.session_factory

    def read(call_id: int) -> dict[str, Any]:
        with factory() as session:
            return load_trace(session, call_id, cast(Any, backend).app.state.prices)

    return read
