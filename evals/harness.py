"""Running the whole suite: one fresh backend per scenario, scripted or with a simulated Caller."""

import secrets
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent.llm import LLM
from backend.app.config import BackendSettings
from backend.app.main import create_app
from evals.metering import BudgetExceeded
from evals.runner import Scenario, ScenarioResult, load_scenario, run_scenario
from evals.simulated_caller import SimulatedCaller
from evals.state import SessionFactory

Run = tuple[Scenario, ScenarioResult]
# Monday 2 November 2026, 08:00 in Asia/Kolkata: the moment the seeded data is relative to.
START = datetime(2026, 11, 2, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
SCENARIOS = Path(__file__).parent / "scenarios"


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


# The backend, its database and the one clock the backend and the agent must share.
FreshBackend = Callable[[], AbstractContextManager[tuple[httpx.Client, SessionFactory, FakeClock]]]


@contextmanager
def sqlite_backend() -> Generator[tuple[httpx.Client, SessionFactory, FakeClock]]:
    """A new backend on in-memory SQLite at the fixed start, one per scenario."""
    # In-process throwaway credentials: this backend lives and dies inside one scenario.
    service_token, staff_password = secrets.token_hex(16), secrets.token_hex(16)
    settings = BackendSettings(
        database_url="sqlite+pysqlite:///:memory:",
        demo_controls_enabled=True,
        backend_service_token=SecretStr(service_token),
        dashboard_password=SecretStr(staff_password),
    )
    clock = FakeClock(START)
    app = create_app(settings, clock=clock)
    with TestClient(app, base_url="http://backend") as client:
        client.headers["X-Service-Token"] = service_token
        client.auth = ("staff", staff_password)
        yield cast(httpx.Client, client), app.state.session_factory, clock


@dataclass(frozen=True)
class Suite:
    runs: list[Run]
    aborted: str | None = None  # why the run stopped early (a spend cap), if it did


def load_scenarios(directory: Path, only: list[str] | None = None) -> list[Scenario]:
    scenarios = [load_scenario(p) for p in sorted(directory.glob("*.yaml"))]
    if only:
        scenarios = [s for s in scenarios if s.id in only]
    return scenarios


def run_suite(
    scenarios: list[Scenario],
    fresh_backend: FreshBackend = sqlite_backend,
    caller_for: Callable[[Scenario], SimulatedCaller] | None = None,
    llm: LLM | None = None,
) -> Suite:
    """Each scenario on its own fresh backend and database. A scenario that crashes the agent (for
    example a scripted LLM consulted after the safety turn) counts as a failure. A spend cap
    (BudgetExceeded) stops the whole suite and is recorded, so a partial run cannot pass for a
    complete one."""
    runs: list[Run] = []
    for scenario in scenarios:
        caller = caller_for(scenario) if caller_for else None
        try:
            with fresh_backend() as (backend, session_factory, clock):
                result = run_scenario(
                    scenario, backend, session_factory, clock, llm=llm, caller=caller
                )
        except BudgetExceeded as exc:
            return Suite(runs, aborted=str(exc))
        except AssertionError as exc:
            result = ScenarioResult(scenario.id, [f"agent error: {exc}"], "none")
        runs.append((scenario, result))
    return Suite(runs)
