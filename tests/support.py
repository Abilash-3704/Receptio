"""Shared helpers for backend API tests: everything goes through the HTTP interface."""

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
Json = dict[str, Any]

# Monday 2 November 2026, 08:00 in Asia/Kolkata. The seeded existing Appointment is made
# relative to this moment (see backend/app/seed.py). Note the 2-hour booking notice: from this
# moment the first bookable time is 10:00 today.
START_OF_TEST = datetime(2026, 11, 2, 8, 0, tzinfo=IST)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def provider_id(backend: httpx.Client, name: str) -> int:
    providers: list[Json] = backend.get("/providers").json()
    return next(p["id"] for p in providers if p["name"] == name)


def service_id(backend: httpx.Client, name: str) -> int:
    services: list[Json] = backend.get("/services").json()
    return next(s["id"] for s in services if s["name"] == name)


def availability(
    backend: httpx.Client,
    service: str,
    day: str,
    provider: str | None = None,
    end_day: str | None = None,
) -> list[Json]:
    params: dict[str, Any] = {
        "service_id": service_id(backend, service),
        "start": f"{day}T00:00:00+05:30",
        "end": f"{end_day or day}T23:59:00+05:30",
    }
    if provider is not None:
        params["provider_id"] = provider_id(backend, provider)
    response = backend.get("/availability", params=params)
    assert response.status_code == 200
    return response.json()


def local(slots: list[Json]) -> list[str]:
    """Slot start times as local 'YYYY-MM-DD HH:MM' strings, for readable assertions."""
    return [
        datetime.fromisoformat(s["start"]).astimezone(IST).strftime("%Y-%m-%d %H:%M") for s in slots
    ]


def new_key() -> str:
    return uuid.uuid4().hex


def place_hold(
    backend: httpx.Client, service: str, provider: str, start: str, key: str | None = None
) -> httpx.Response:
    return backend.post(
        "/holds",
        headers={"Idempotency-Key": key or new_key()},
        json={
            "service_id": service_id(backend, service),
            "provider_id": provider_id(backend, provider),
            "start": f"{start.replace(' ', 'T')}:00+05:30",
        },
    )


def read_back(backend: httpx.Client, hold_id: int, key: str | None = None) -> httpx.Response:
    """Record the Caller's explicit agreement to the read-back for this exact Hold."""
    return backend.post(
        f"/holds/{hold_id}/read-back", headers={"Idempotency-Key": key or new_key()}
    )


def confirm(
    backend: httpx.Client,
    hold_id: int,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
    key: str | None = None,
) -> httpx.Response:
    return backend.post(
        f"/holds/{hold_id}/confirm",
        headers={"Idempotency-Key": key or new_key()},
        json={"patient_name": name, "patient_phone": phone},
    )


def held(backend: httpx.Client, service: str, provider: str, start: str) -> int:
    response = place_hold(backend, service, provider, start)
    assert response.status_code == 201, response.text
    return response.json()["id"]
