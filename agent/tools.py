"""Tool layer: thin wrappers over the clinic backend. The LLM asks, the tools act, and the call
state machine decides what is allowed. Writes carry deterministic Idempotency-Keys, so a retried
tool call can never create a second Appointment."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from agent.availability_search import AvailabilitySearch
from agent.call_state import CallState, HoldInfo, KnownAppointment
from agent.escalation import TOO_SOON_REASON
from agent.faq import NO_ANSWER_REASON, NO_ANSWER_SPEECH
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
        "search_faq",
        "Look up clinic facts: hours, location, Services, price ranges, insurance accepted. "
        "The only source for such facts; say only what it returns.",
        {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
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
        "identify_patient",
        "Identify the Patient for a reschedule or cancel. Both the full name and the phone number "
        "on file must match. Say nothing about any Appointment until this succeeds.",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}, "phone": {"type": "string"}},
            "required": ["name", "phone"],
        },
    ),
    ToolSpec(
        "select_appointment",
        "After identification, choose which of the Patient's Appointments is meant, by number "
        "(1 is the first listed). Ask the Caller first when there is more than one.",
        {
            "type": "object",
            "properties": {"option": {"type": "integer"}},
            "required": ["option"],
        },
    ),
    ToolSpec(
        "request_cancel",
        "Read the selected Appointment back and ask the Caller to confirm cancelling it.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "cancel_appointment",
        "Cancel the selected Appointment. Only works after the Caller said yes to request_cancel.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "start_reschedule",
        "Begin moving the selected Appointment. Then check_availability, place_hold, read_back "
        "and confirm_appointment as for a booking; the old time stays until the new one is "
        "confirmed.",
        {"type": "object", "properties": {}},
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

    def _hold_details(self) -> dict[str, str] | None:
        """The Patient details the backend wants with a Hold's confirm. For a reschedule they are
        the identified Patient's, taken from call state."""
        state = self._state
        if state.rescheduling:
            if state.identity is None:
                return None
            name, phone = state.identity
        else:
            name, phone = state.patient_name, state.patient_phone
        if name is None or phone is None:
            return None
        return {"patient_name": name, "patient_phone": phone}

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
        if state.rescheduling and state.target is not None:
            service = state.target.service  # a reschedule moves the same Service
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
        state.callback_offer = None
        self._release_live_hold()  # a new search means the Caller has left the old time
        state.service_id, state.service = match["id"], match["name"]

        search = AvailabilitySearch(self._backend, match["id"], names_by_id, wanted, now).run(
            resolution
        )
        state.offers = search.offers
        if search.facts.get("offer_callback_request"):
            state.callback_offer = TOO_SOON_REASON
        state.ever_offered |= {o.start for o in search.offers}
        if not search.ok:
            return ToolResult(False, search.facts)
        return ToolResult(True, {**search.facts, "offered": [o.label for o in search.offers]})

    def _tool_search_faq(self, question: str) -> ToolResult:
        entries: list[dict[str, str]] = checked(
            self._backend.get("/faq", params={"q": question})
        ).json()
        if not entries:
            # Code says it, so the LLM cannot make something up in its place.
            self._state.callback_offer = NO_ANSWER_REASON.format(question=question[:200])
            return ToolResult(True, {"found": False}, speak=NO_ANSWER_SPEECH)
        facts = [e["text"] for e in entries]
        self._state.known_facts += facts
        return ToolResult(True, {"found": True, "facts": facts})

    def _tool_record_patient(self, name: str, phone: str) -> ToolResult:
        self._state.patient_name = name.strip()
        self._state.patient_phone = phone.strip()
        return ToolResult(True)

    def _tool_identify_patient(self, name: str, phone: str) -> ToolResult:
        state = self._state
        state.identity, state.known_appointments, state.target = None, [], None
        state.rescheduling = state.awaiting_cancel_answer = state.cancel_agreed = False
        response = self._backend.post(
            "/appointments/lookup", json={"patient_name": name, "patient_phone": phone}
        )
        if response.is_error:  # the backend says nothing more than "failed": neither do we
            return ToolResult(False, {"error": "identification failed"})
        state.identity = (name.strip(), phone.strip())
        state.patient_name, state.patient_phone = state.identity
        state.known_appointments = [
            KnownAppointment(
                a["id"], a["service"], a["provider"], datetime.fromisoformat(a["start"])
            )
            for a in response.json()
        ]
        state.ever_offered |= {a.start for a in state.known_appointments}
        if len(state.known_appointments) == 1:
            state.target = state.known_appointments[0]
        return ToolResult(True, {"appointments": [a.label for a in state.known_appointments]})

    def _tool_select_appointment(self, option: int) -> ToolResult:
        state = self._state
        if state.identity is None or not 1 <= option <= len(state.known_appointments):
            return ToolResult(False, {"error": "that is not one of the Patient's Appointments"})
        state.target = state.known_appointments[option - 1]
        state.rescheduling = state.awaiting_cancel_answer = state.cancel_agreed = False
        return ToolResult(True, {"selected": state.target.label})

    def _tool_request_cancel(self) -> ToolResult:
        state = self._state
        if state.identity is None or state.target is None:
            return ToolResult(False, {"error": "identify the Patient and choose an Appointment"})
        state.rescheduling = False
        state.awaiting_cancel_answer = True
        state.cancel_agreed = False
        speech = f"Just to check: you'd like to cancel your {state.target.label}. Is that right?"
        return ToolResult(True, speak=speech)

    def _tool_cancel_appointment(self) -> ToolResult:
        state = self._state
        if state.identity is None or state.target is None:
            return ToolResult(False, {"error": "identify the Patient and choose an Appointment"})
        if not state.cancel_agreed:
            # Same rule as the booking read-back: only the state machine records the "yes".
            return ToolResult(
                False,
                {"error": "the Caller has not agreed to the cancellation; ask and wait"},
                refused=True,
            )
        name, phone = state.identity
        self._post(
            f"/appointments/{state.target.id}/cancel",
            self._key("cancel"),
            {"patient_name": name, "patient_phone": phone},
        )
        state.appointment_cancelled = True
        state.cancel_agreed = False
        done, state.target = state.target, None
        state.known_appointments = [a for a in state.known_appointments if a.id != done.id]
        return ToolResult(True, {"cancelled": done.label})

    def _tool_start_reschedule(self) -> ToolResult:
        state = self._state
        if state.identity is None or state.target is None:
            return ToolResult(False, {"error": "identify the Patient and choose an Appointment"})
        state.rescheduling = True
        state.awaiting_cancel_answer = state.cancel_agreed = False
        return ToolResult(True, {"moving": state.target.label})

    def _tool_place_hold(self, option: int) -> ToolResult:
        offers = self._state.offers
        if not 1 <= option <= len(offers) or self._state.service_id is None:
            return ToolResult(False, {"error": "that is not one of the offered times"})
        offer = offers[option - 1]
        self._release_live_hold()  # a correction: the previous Hold goes before a new one is placed
        hold = {
            "service_id": self._state.service_id,
            "provider_id": offer.provider_id,
            "start": offer.start.isoformat(),
        }
        if self._state.rescheduling and self._state.target and self._state.identity:
            name, phone = self._state.identity
            body = self._post(
                f"/appointments/{self._state.target.id}/reschedule",
                self._key("reschedule-hold"),
                {**hold, "patient_name": name, "patient_phone": phone},
            )
        else:
            body = self._post("/holds", self._key("hold"), hold)
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
        if state.rescheduling and state.target is not None:
            speech = (
                f"Let me read that back. I'll move your {state.target.label} to "
                f"{state.hold.offer.label}. Is that all correct?"
            )
        else:
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
        # A reschedule Hold keeps the original Patient, and the backend still wants that Patient
        # identified: the details come from call state, never from the LLM.
        details = self._hold_details()
        if details is None:
            return ToolResult(False, {"error": "the Patient's name and phone are missing"})
        try:
            appointment = self._post(
                f"/holds/{state.hold.id}/confirm", self._key("confirm"), details
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

    def release_hold(self) -> None:
        """Called by the state machine when any Escalation ends scheduling. A failed
        release is not fatal: the Hold then expires on its own."""
        hold = self._state.hold
        if hold is not None:
            try:
                self._post(f"/holds/{hold.id}/release", self._key("release"))
            except (BackendError, httpx.HTTPError):
                pass
        self._state.abandon_scheduling()

    def record_read_back_agreement(self) -> None:
        """Called by the state machine, never by the LLM, when the Caller says yes. If the Hold
        has gone (expired), nothing is recorded and a later confirm is refused."""
        state = self._state
        assert state.hold is not None
        try:
            # Only a reschedule Hold's read-back carries the identified Patient's details.
            details = self._hold_details() if state.rescheduling else None
            self._post(f"/holds/{state.hold.id}/read-back", self._key("read-back"), details)
        except BackendError as exc:
            if exc.status != 409:
                raise
            self._lose_hold(exc.detail)  # the Hold expired while the Caller was thinking
            return
        state.awaiting_read_back_answer = False
        state.read_back_agreed = True

    def record_cancel_answer(self, said_yes: bool) -> None:
        """Called by the state machine, never by the LLM. Any answer ends the question: only an
        explicit yes to the read-back counts, and a "no" cannot be revived by a later yes."""
        self._state.awaiting_cancel_answer = False
        self._state.cancel_agreed = said_yes

    def _lose_hold(self, reason: str) -> None:
        """The Hold is gone: the Caller's yes (if any) was for a time that is no longer held."""
        state = self._state
        state.hold = None
        state.awaiting_read_back_answer = False
        state.read_back_agreed = False
        state.hold_lost = reason
