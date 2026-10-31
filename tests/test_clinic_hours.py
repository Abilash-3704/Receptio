"""Clinic Hours and the demo-only open/closed override, through the backend's HTTP interface."""

from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from backend.app.config import BackendSettings
from backend.app.main import create_app
from tests.support import FakeClock


def is_open(backend: httpx.Client) -> bool:
    response = backend.get("/clinic-status")
    assert response.status_code == 200
    return response.json()["open"]


def test_the_clinic_is_open_inside_clinic_hours_and_closed_outside(
    backend: httpx.Client, clock: FakeClock
) -> None:
    # Monday 2 November 2026, 08:00 IST: before opening.
    assert not is_open(backend)
    clock.advance(timedelta(hours=2))  # 10:00
    assert is_open(backend)
    clock.advance(timedelta(hours=8))  # 18:00, closing time
    assert not is_open(backend)


def test_the_clinic_is_closed_on_sunday(backend: httpx.Client, clock: FakeClock) -> None:
    clock.advance(timedelta(days=6, hours=4))  # Sunday 8 November, 12:00
    assert not is_open(backend)
    clock.advance(timedelta(days=1))  # Monday 9 November, 12:00
    assert is_open(backend)


def test_the_clinic_is_open_on_saturday(backend: httpx.Client, clock: FakeClock) -> None:
    clock.advance(timedelta(days=5, hours=4))  # Saturday 7 November, 12:00
    assert is_open(backend)


def set_override(backend: httpx.Client, mode: str | None) -> httpx.Response:
    return backend.put("/demo/clinic-override", json={"mode": mode})


def test_the_override_forces_the_clinic_open_or_closed_without_a_restart(
    backend: httpx.Client, clock: FakeClock
) -> None:
    assert not is_open(backend)  # Monday 08:00, closed

    assert set_override(backend, "open").status_code == 200
    assert is_open(backend)

    clock.advance(timedelta(hours=2))  # 10:00, open by the clock
    assert set_override(backend, "closed").status_code == 200
    assert not is_open(backend)

    assert set_override(backend, None).status_code == 200  # back to the real Clinic Hours
    assert is_open(backend)


def test_the_override_is_off_by_default(backend: httpx.Client) -> None:
    assert backend.get("/clinic-status").json()["override"] is None


def test_the_override_endpoint_does_not_exist_unless_demo_controls_are_enabled() -> None:
    settings = BackendSettings(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(create_app(settings), base_url="http://backend") as client:
        assert settings.demo_controls_enabled is False
        assert client.put("/demo/clinic-override", json={"mode": "open"}).status_code == 404
