"""Clinic backend API, deterministic seam: Availability and Holds. No LLM, no sleeping."""

from datetime import datetime, timedelta

import httpx

from tests.support import (
    IST,
    FakeClock,
    availability,
    local,
    place_hold,
    provider_id,
    service_id,
)


def test_availability_is_fitted_to_service_duration_within_working_hours(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.advance(timedelta(hours=-2))  # 06:00, so the 2-hour notice does not hide 09:00
    slots = local(availability(backend, "Root canal", "2026-11-02", provider="Dr. Anita Rao"))

    # Root canal takes 90 minutes; Dr. Rao works 09:00-18:00, so the last start is 16:30.
    assert slots[0] == "2026-11-02 09:00"
    assert slots[-1] == "2026-11-02 16:30"
    assert "2026-11-02 16:45" not in slots
    assert len(slots) == 31  # every 15 minutes from 09:00 to 16:30


def test_services_of_different_lengths_fit_differently(backend: httpx.Client) -> None:
    check_up = local(availability(backend, "Check-up", "2026-11-02", provider="Dr. Anita Rao"))
    cleaning = local(availability(backend, "Cleaning", "2026-11-02", provider="Dr. Anita Rao"))

    assert check_up[-1] == "2026-11-02 17:30"  # 30 minutes
    assert cleaning[-1] == "2026-11-02 17:15"  # 45 minutes


def test_clinic_is_closed_on_sunday(backend: httpx.Client) -> None:
    assert availability(backend, "Check-up", "2026-11-08") == []  # Sunday


def test_clinic_is_open_on_saturday_but_providers_follow_their_own_hours(
    backend: httpx.Client,
) -> None:
    rao = local(availability(backend, "Check-up", "2026-11-07", provider="Dr. Anita Rao"))
    shetty = availability(backend, "Check-up", "2026-11-07", provider="Dr. Vikram Shetty")
    nair = local(availability(backend, "Check-up", "2026-11-07", provider="Meera Nair"))

    assert rao[0] == "2026-11-07 09:00"
    assert shetty == []  # Dr. Shetty does not work Saturdays
    assert nair[-1] == "2026-11-07 13:30"  # Meera Nair works until 14:00


def test_without_a_provider_availability_covers_every_provider_who_does_the_service(
    backend: httpx.Client,
) -> None:
    wednesday = availability(backend, "Cleaning", "2026-11-04")  # Nair is off
    assert {s["provider_id"] for s in wednesday} == {provider_id(backend, "Dr. Anita Rao")}

    tuesday = availability(backend, "Cleaning", "2026-11-03")
    assert {s["provider_id"] for s in tuesday} == {
        provider_id(backend, "Dr. Anita Rao"),
        provider_id(backend, "Meera Nair"),
    }


def test_a_provider_who_does_not_perform_the_service_has_no_availability(
    backend: httpx.Client,
) -> None:
    assert availability(backend, "Root canal", "2026-11-03", provider="Meera Nair") == []


def test_existing_appointment_is_removed_from_availability(backend: httpx.Client) -> None:
    # Seeded: Dr. Rao has a Filling 10:00-11:00 on Tuesday 3 November.
    slots = local(availability(backend, "Check-up", "2026-11-03", provider="Dr. Anita Rao"))

    assert "2026-11-03 09:30" in slots  # 09:30-10:00 ends exactly as the Appointment starts
    assert "2026-11-03 09:45" not in slots  # overlaps the start
    assert "2026-11-03 10:00" not in slots
    assert "2026-11-03 10:45" not in slots  # overlaps the end
    assert "2026-11-03 11:00" in slots  # starts exactly as the Appointment ends


def test_a_hold_removes_its_time_from_availability_for_everyone_else(
    backend: httpx.Client,
) -> None:
    response = place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    assert response.status_code == 201

    other_service = local(availability(backend, "Check-up", "2026-11-02", provider="Dr. Anita Rao"))
    assert "2026-11-02 14:00" not in other_service
    assert "2026-11-02 14:45" not in other_service  # inside the held hour
    assert "2026-11-02 13:30" in other_service
    assert "2026-11-02 15:00" in other_service

    # A second Caller cannot take the held time, but another Provider is unaffected.
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:30").status_code == 409
    other = place_hold(backend, "Filling", "Dr. Vikram Shetty", "2026-11-02 14:00")
    assert other.status_code == 201


def test_a_hold_expires_after_five_minutes_and_the_time_is_available_again(
    backend: httpx.Client, clock: FakeClock
) -> None:
    response = place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    assert response.status_code == 201
    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    assert expires_at == clock.now + timedelta(minutes=5)

    clock.advance(timedelta(minutes=4, seconds=59))
    held = local(availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao"))
    assert "2026-11-02 14:00" not in held

    clock.advance(timedelta(seconds=1))
    freed = local(availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao"))
    assert "2026-11-02 14:00" in freed
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00").status_code == 201


def test_holds_outside_availability_are_refused(backend: httpx.Client, clock: FakeClock) -> None:
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 17:30").status_code == 409
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-08 10:00").status_code == 409
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-03 10:00").status_code == 409
    assert place_hold(backend, "Cleaning", "Dr. Anita Rao", "2026-11-02 14:07").status_code == 409
    assert place_hold(backend, "Root canal", "Meera Nair", "2026-11-03 10:00").status_code == 409

    clock.now = datetime(2026, 11, 2, 12, 0, tzinfo=IST)
    assert place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-02 11:00").status_code == 409


def test_times_without_an_offset_are_clinic_local_time(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.advance(timedelta(hours=-2))  # 06:00, so the 2-hour notice does not hide 09:00
    response = backend.get(
        "/availability",
        params={
            "service_id": service_id(backend, "Check-up"),
            "provider_id": provider_id(backend, "Dr. Anita Rao"),
            "start": "2026-11-02T00:00:00",
            "end": "2026-11-02T23:59:00",
        },
    )

    assert local(response.json())[0] == "2026-11-02 09:00"
