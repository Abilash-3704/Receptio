"""Finding times to offer for what the Caller asked, and explaining when there are none.

The backend alone enforces the booking window and Clinic Hours (it never returns a time outside
them). This module only decides what to look up, which of the returned times to offer, and why
nothing was offered: the request was too soon, too far ahead, or simply fully booked."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx

from agent.call_state import Offer
from agent.times import Resolution, say_date
from backend.app.clock import CLINIC_TZ
from backend.app.scheduling import MAX_AHEAD, MIN_NOTICE

MAX_OFFERS = 3
# How far ahead to look for alternatives when the asked-for time has none (backend maximum: 31).
ALTERNATIVES_DAYS = 14


@dataclass
class Search:
    offers: list[Offer] = field(default_factory=lambda: [])
    # What the LLM is told, besides the offered times.
    facts: dict[str, Any] = field(default_factory=lambda: {})
    ok: bool = True


class AvailabilitySearch:
    def __init__(
        self,
        backend: httpx.Client,
        service_id: int,
        provider_names: dict[int, str],
        provider_id: int | None,
        now: datetime,
    ) -> None:
        self._backend = backend
        self._service_id = service_id
        self._provider_names = provider_names
        self._provider_id = provider_id
        self._now = now.astimezone(CLINIC_TZ)

    def run(self, resolution: Resolution) -> Search:
        now = self._now
        days = [d for d in resolution.dates if d >= now.date()]
        if not days:
            return Search(ok=False, facts={"error": "that day has already passed"})
        latest = now + MAX_AHEAD
        if days[0] > latest.date():
            return Search(
                ok=False,
                facts={"reason": "too_far", "latest_bookable_day": say_date(latest.date())},
            )

        slots = self._wanted(days, resolution, self._provider_id)
        facts: dict[str, Any] = {}
        if self._provider_id is not None:
            facts["preferred_provider_available"] = bool(slots)
            if not slots:  # not forced: show what the other Providers have
                slots = self._wanted(days, resolution, None)
        offers = self._spread(slots, resolution)

        if self._too_soon(resolution, days[0], bool(offers)):
            # The flag tells the LLM to offer a Callback Request; the state machine records it
            # (ticket 07's Escalation) only if the Caller's next answer is a plain yes.
            facts["reason"] = "too_soon"
            facts["offer_callback_request"] = True
            facts["earliest_bookable"] = say_date((now + MIN_NOTICE).date())
            offers = self._alternatives(days[0])
            facts["alternatives"] = True
        elif not offers:
            facts["reason"] = "no_availability"
            offers = self._alternatives(days[0])
            facts["alternatives"] = True
        return Search(offers, facts)

    def _slots(
        self, start: datetime, end: datetime, provider_id: int | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "service_id": self._service_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if provider_id is not None:
            params["provider_id"] = provider_id
        response = self._backend.get("/availability", params=params)
        response.raise_for_status()
        slots: list[dict[str, Any]] = response.json()
        return sorted(slots, key=lambda s: (s["start"], s["provider_id"]))

    def _wanted(
        self, days: list[date], resolution: Resolution, provider_id: int | None
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for day in days:
            start = datetime.combine(day, time(0), tzinfo=CLINIC_TZ)
            for slot in self._slots(start, start + timedelta(hours=24), provider_id):
                local = datetime.fromisoformat(slot["start"]).astimezone(CLINIC_TZ).time()
                if resolution.window and not resolution.window[0] <= local < resolution.window[1]:
                    continue
                if resolution.exact and local < resolution.exact:
                    continue
                if resolution.moment and datetime.fromisoformat(slot["start"]) < resolution.moment:
                    continue
                found.append(slot)
        return found

    def _offer(self, slot: dict[str, Any]) -> Offer:
        return Offer(
            slot["provider_id"],
            self._provider_names[slot["provider_id"]],
            datetime.fromisoformat(slot["start"]),
        )

    def _spread(self, slots: list[dict[str, Any]], resolution: Resolution) -> list[Offer]:
        seen: set[datetime] = set()
        offers: list[Offer] = []
        for slot in slots:
            offer = self._offer(slot)
            if offer.start not in seen:
                seen.add(offer.start)
                offers.append(offer)
        if resolution.exact is not None or resolution.moment is not None:
            return offers[:MAX_OFFERS]  # the nearest times to the one asked for
        # Spread the offers over the day (or days) rather than listing the first three.
        step = max(1, len(offers) // MAX_OFFERS)
        return offers[::step][:MAX_OFFERS]

    def _too_soon(self, resolution: Resolution, day: date, any_offered: bool) -> bool:
        earliest = self._now + MIN_NOTICE
        if resolution.moment is not None:
            return resolution.moment < earliest
        if day != self._now.date():
            return False
        if resolution.exact is not None:
            return datetime.combine(day, resolution.exact, tzinfo=CLINIC_TZ) < earliest
        return not any_offered  # "today", with nothing left that is 2 hours away

    def _alternatives(self, first_day: date) -> list[Offer]:
        """The first open time on each of the next few days that have one."""
        start = datetime.combine(max(first_day, self._now.date()), time(0), tzinfo=CLINIC_TZ)
        slots = self._slots(start, start + timedelta(days=ALTERNATIVES_DAYS), self._provider_id)
        by_day: dict[date, Offer] = {}
        for slot in slots:
            offer = self._offer(slot)
            by_day.setdefault(offer.start.astimezone(CLINIC_TZ).date(), offer)
        return list(by_day.values())[:MAX_OFFERS]
