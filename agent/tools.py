"""Tool layer: thin wrappers over the clinic backend. The LLM asks, the tools act, and the call
state machine decides what is allowed. Writes carry deterministic Idempotency-Keys, so a retried
tool call can never create a second Appointment."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from agent.call_state import CallState, HoldInfo, Offer
from agent.faq import NO_ANSWER_REASON, NO_ANSWER_SPEECH
from agent.llm import ToolSpec

MAX_OFFERS = 3

TOOL_SPECS = [
    ToolSpec(
        "check_availability",
        "Look up open times for a Service on a date. Only times it returns may be offered.",
        {
            "type": "object",
            "properties": {"service": {"type": "string"}, "date": {"type": "string"}},
            "required": ["service", "date"],
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

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def checked(response: httpx.Response) -> httpx.Response:
    if response.is_error:
        try:
            detail = str(response.json().get("detail", response.text))
        except ValueError:
            detail = response.text
        raise BackendError(detail)
    return response


class ToolBox:
    def __init__(self, backend: httpx.Client, call_id: int, state: CallState) -> None:
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

    def _tool_check_availability(self, service: str, date: str) -> ToolResult:
        services: list[dict[str, Any]] = self._backend.get("/services").json()
        match = next((s for s in services if s["name"].casefold() == service.casefold()), None)
        if match is None:
            names = [s["name"] for s in services]
            return ToolResult(False, {"error": f"unknown service; choose from {names}"})
        providers: list[dict[str, Any]] = self._backend.get("/providers").json()
        provider_names = {p["id"]: p["name"] for p in providers}
        response = self._backend.get(
            "/availability",
            params={
                "service_id": match["id"],
                "start": f"{date}T00:00:00+05:30",
                "end": f"{date}T23:59:00+05:30",
            },
        )
        slots: list[dict[str, Any]] = checked(response).json()
        seen: set[datetime] = set()
        offers: list[Offer] = []
        for slot in sorted(slots, key=lambda s: (s["start"], s["provider_id"])):
            start = datetime.fromisoformat(slot["start"])
            if start in seen:
                continue
            seen.add(start)
            offers.append(Offer(slot["provider_id"], provider_names[slot["provider_id"]], start))
        # Spread the offers over the day rather than listing the first three back to back.
        step = max(1, len(offers) // MAX_OFFERS)
        self._state.service_id = match["id"]
        self._state.service = match["name"]
        self._state.offers = offers[::step][:MAX_OFFERS]
        self._state.ever_offered |= {o.start for o in self._state.offers}
        return ToolResult(True, {"offered": [o.label for o in self._state.offers]})

    def _tool_search_faq(self, question: str) -> ToolResult:
        entries: list[dict[str, str]] = checked(
            self._backend.get("/faq", params={"q": question})
        ).json()
        if not entries:
            # Code says it, so the LLM cannot make something up in its place.
            self._state.callback_offer_reason = NO_ANSWER_REASON.format(question=question[:200])
            return ToolResult(True, {"found": False}, speak=NO_ANSWER_SPEECH)
        facts = [e["text"] for e in entries]
        self._state.known_facts += facts
        return ToolResult(True, {"found": True, "facts": facts})

    def _tool_record_patient(self, name: str, phone: str) -> ToolResult:
        self._state.patient_name = name.strip()
        self._state.patient_phone = phone.strip()
        return ToolResult(True)

    def _tool_place_hold(self, option: int) -> ToolResult:
        offers = self._state.offers
        if not 1 <= option <= len(offers) or self._state.service_id is None:
            return ToolResult(False, {"error": "that is not one of the offered times"})
        offer = offers[option - 1]
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
        appointment = self._post(
            f"/holds/{state.hold.id}/confirm",
            self._key("confirm"),
            {"patient_name": state.patient_name, "patient_phone": state.patient_phone},
        )
        state.appointment_id = appointment["id"]
        self._post(f"/appointments/{appointment['id']}/confirmation-message", self._key("message"))
        return ToolResult(True, {"confirmed": state.hold.offer.label})

    def record_read_back_agreement(self) -> None:
        """Called by the state machine, never by the LLM, when the Caller says yes."""
        state = self._state
        assert state.hold is not None
        self._post(f"/holds/{state.hold.id}/read-back", self._key("read-back"))
        state.awaiting_read_back_answer = False
        state.read_back_agreed = True
