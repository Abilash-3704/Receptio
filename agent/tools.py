"""Tool layer: thin wrappers over the clinic backend. The LLM asks, the tools act, and the call
state machine decides what is allowed. Writes carry deterministic Idempotency-Keys, so a retried
tool call can never create a second Appointment."""

from dataclasses import dataclass, field
from typing import Any

import httpx

from agent.availability_search import AvailabilitySearch
from agent.call_state import CallState, HoldInfo
from agent.llm import ToolSpec
from agent.times import resolve, say_date
from backend.app.clock import CLINIC_TZ, Clock, system_clock

TOOL_SPECS = [
    ToolSpec(
        "check_availability",
        "Look up open times for a Service. Pass the Caller's own words for when, for example "
        "'next Tuesday afternoon'; never work out a date yourself. Only times it returns may be "
        "offered. If it asks you to confirm a date, ask the Caller and wait for a yes. Pass "
        "provider only if the Caller named one.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "when": {"type": "string"},
                "provider": {"type": "string"},
            },
            "required": ["service", "when"],
        },
    ),
    ToolSpec(
        "record_patient",
        "Record the Patient's full name and phone number (the Patient may not be the Caller).",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}, "phone": {"type": "string"}},
            "required": ["name", "phone"],
        },
    ),
    ToolSpec(
        "place_hold",
        "Hold one of the offered times, by its option number (1 is the first offered).",
        {
            "type": "object",
            "properties": {"option": {"type": "integer"}},
            "required": ["option"],
        },
    ),
    ToolSpec(
        "read_back",
        "Read the Appointment details back to the Caller and wait for their answer.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "confirm_appointment",
        "Confirm the held Appointment. Only works after the Caller agreed to the read-back.",
        {"type": "object", "properties": {}},
    ),
]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=lambda: {})
    # Said to the Caller by code, verbatim, ending the agent's turn.
    speak: str | None = None
    # True when the state machine refused to let the tool run (nothing reached the backend).
    refused: bool = False


@dataclass(frozen=True)
class ToolCall:
    """What the tools were asked, for assertions and the trace."""

    name: str
    args: dict[str, Any]
    ok: bool
    refused: bool
    data: dict[str, Any] = field(default_factory=lambda: {})  # what the tool returned


class BackendError(Exception):
    """The clinic backend refused or failed a request. `detail` is its explanation."""

    def __init__(self, detail: str, status: int = 0) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def checked(response: httpx.Response) -> httpx.Response:
    if response.is_error:
        try:
            detail = str(response.json().get("detail", response.text))
        except ValueError:
            detail = response.text
        raise BackendError(detail, response.status_code)
    return response


class ToolBox:
    def __init__(
        self, backend: httpx.Client, call_id: int, state: CallState, clock: Clock = system_clock
    ) -> None:
        self._clock = clock
        self._backend = backend
        self._call_id = call_id
        self._state = state
        self._counters: dict[str, int] = {}

    def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolResult(False, {"error": f"unknown tool {name}"})
        try:
            return handler(**args)
        except BackendError as exc:
            return ToolResult(False, {"error": exc.detail})
        except TypeError as exc:
            return ToolResult(False, {"error": f"bad arguments: {exc}"})

    def _key(self, operation: str) -> str:
        """Same logical operation and attempt number, same key: retries are safe."""
        attempt = self._counters.get(operation, 0) + 1
        self._counters[operation] = attempt
        return f"call{self._call_id}-{operation}-{attempt}"

    def _post(self, path: str, key: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._backend.post(path, headers={"Idempotency-Key": key}, json=json)
        return checked(response).json()

    def _release_live_hold(self) -> None:
        """The Caller changed their mind: free the old Hold before a new one is placed."""
        state = self._state
        if state.hold is not None:
            try:
                self._post(f"/holds/{state.hold.id}/release", self._key("release"))
            except BackendError:
                pass  # already expired or gone: the time is free either way
            state.hold = None
        state.awaiting_read_back_answer = False
        state.read_back_agreed = False

    def _tool_check_availability(
        self,
        service: str,
        when: str | None = None,
        provider: str | None = None,
        date: str | None = None,  # older spelling of `when`
    ) -> ToolResult:
        state = self._state
        services: list[dict[str, Any]] = self._backend.get("/services").json()
        match = next((s for s in services if s["name"].casefold() == service.casefold()), None)
        if match is None:
            names = [s["name"] for s in services]
            return ToolResult(False, {"error": f"unknown service; choose from {names}"})
        providers: list[dict[str, Any]] = self._backend.get("/providers").json()
        names_by_id = {p["id"]: p["name"] for p in providers}
        wanted: int | None = None
        if provider:
            wanted = next(
                (i for i, n in names_by_id.items() if provider.casefold() in n.casefold()), None
            )
            if wanted is None:
                return ToolResult(
                    False, {"error": f"unknown provider; choose from {list(names_by_id.values())}"}
                )

        now = self._clock().astimezone(CLINIC_TZ)
        resolution = resolve(when or date or "", now)
        if resolution.unparsed:
            return ToolResult(False, {"error": "not a time I can read: ask the Caller for a day"})
        if resolution.needs_day:
            return ToolResult(False, {"error": "ask the Caller which day they mean"})
        proposed = resolution.dates[0]
        if resolution.needs_confirmation and proposed not in state.confirmed_dates:
            # The date is only searched once the Caller has agreed to it aloud.
            state.pending_date = proposed
            return ToolResult(
                True,
                {
                    "confirm_date_with_caller": say_date(proposed),
                    "other_possibility": [say_date(d) for d in resolution.alternatives],
                },
                refused=True,
            )
        state.pending_date = None
        self._release_live_hold()  # a new search means the Caller has left the old time
        state.service_id, state.service = match["id"], match["name"]

        search = AvailabilitySearch(self._backend, match["id"], names_by_id, wanted, now).run(
            resolution
        )
        state.offers = search.offers
        state.ever_offered |= {o.start for o in search.offers}
        if not search.ok:
            return ToolResult(False, search.facts)
        return ToolResult(True, {**search.facts, "offered": [o.label for o in search.offers]})

    def _tool_record_patient(self, name: str, phone: str) -> ToolResult:
        self._state.patient_name = name.strip()
        self._state.patient_phone = phone.strip()
        return ToolResult(True)

    def _tool_place_hold(self, option: int) -> ToolResult:
        offers = self._state.offers
        if not 1 <= option <= len(offers) or self._state.service_id is None:
            return ToolResult(False, {"error": "that is not one of the offered times"})
        offer = offers[option - 1]
        self._release_live_hold()  # a correction: the previous Hold goes before a new one is placed
        body = self._post(
            "/holds",
            self._key("hold"),
            {
                "service_id": self._state.service_id,
                "provider_id": offer.provider_id,
                "start": offer.start.isoformat(),
            },
        )
        self._state.hold = HoldInfo(body["id"], offer)
        self._state.hold_lost = None
        return ToolResult(True, {"hold": offer.label})

    def _tool_read_back(self) -> ToolResult:
        state = self._state
        if state.hold is None or state.service is None:
            return ToolResult(False, {"error": "there is no Hold to read back"})
        if state.patient_name is None or state.patient_phone is None:
            return ToolResult(False, {"error": "the Patient's name and phone are missing"})
        state.awaiting_read_back_answer = True
        speech = (
            f"Let me read that back. A {state.service} for {state.patient_name}, "
            f"{state.hold.offer.label}. We'll message {state.patient_phone}. "
            "Is that all correct?"
        )
        return ToolResult(True, speak=speech)

    def _tool_confirm_appointment(self) -> ToolResult:
        state = self._state
        if state.hold is None or state.patient_name is None or state.patient_phone is None:
            return ToolResult(False, {"error": "there is no Hold to confirm"})
        if not state.read_back_agreed:
            # ADR-0001: only the state machine records the "yes". Without it the backend is
            # not even asked.
            return ToolResult(
                False,
                {"error": "the Caller has not agreed to the read-back; read it back and wait"},
                refused=True,
            )
        try:
            appointment = self._post(
                f"/holds/{state.hold.id}/confirm",
                self._key("confirm"),
                {"patient_name": state.patient_name, "patient_phone": state.patient_phone},
            )
        except BackendError as exc:
            if exc.status == 409:  # the Hold expired or the time was taken: nothing is confirmed
                self._lose_hold(exc.detail)
                return ToolResult(
                    False, {"error": exc.detail, "next": "check Availability again, then read back"}
                )
            raise
        state.appointment_id = appointment["id"]
        self._post(f"/appointments/{appointment['id']}/confirmation-message", self._key("message"))
        return ToolResult(True, {"confirmed": state.hold.offer.label})

    def record_read_back_agreement(self) -> None:
        """Called by the state machine, never by the LLM, when the Caller says yes."""
        state = self._state
        assert state.hold is not None
        try:
            self._post(f"/holds/{state.hold.id}/read-back", self._key("read-back"))
        except BackendError as exc:
            if exc.status != 409:
                raise
            self._lose_hold(exc.detail)  # the Hold expired while the Caller was thinking
            return
        state.awaiting_read_back_answer = False
        state.read_back_agreed = True

    def _lose_hold(self, reason: str) -> None:
        """The Hold is gone: the Caller's yes (if any) was for a time that is no longer held."""
        state = self._state
        state.hold = None
        state.awaiting_read_back_answer = False
        state.read_back_agreed = False
        state.hold_lost = reason
