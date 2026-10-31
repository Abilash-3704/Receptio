"""Who may call which route. Default deny, decided in one place.

Two kinds of caller, two secrets, both read from the environment (see .env.example):
- the SERVICE: the agent and the voice worker. They send `X-Service-Token: <BACKEND_SERVICE_TOKEN>`
  and may use the routes in SERVICE_ROUTES (open a Call, write its turns and trace, the clinic
  tools). They can never read a transcript back.
- STAFF: people at the clinic. They use HTTP Basic, user `staff`, password DASHBOARD_PASSWORD (a
  browser prompts for it). They get every route that is neither PUBLIC nor SERVICE: the dashboard,
  the Escalation list, the Confirmation Message list, a Call's transcript, the demo override.

A route in neither set is a STAFF route, so a route added without a decision is the most
restricted kind, not an open one. tests/test_auth.py enumerates the app's routes and fails if one
answers without credentials.

Answers: 401 for no or wrong credentials; 403 for credentials that are valid for the OTHER kind of
caller; 503 while the secret this route needs is not configured (fail closed: there is no default
password, so an unconfigured backend lets nobody in). Secrets are compared in constant time."""

import base64
import binascii
import hmac

from fastapi import HTTPException, Request

# Routes that answer without credentials. Keep this list short and justified.
PUBLIC_ROUTES = {
    ("GET", "/health"),  # liveness probe, no data
    # The Caller-facing cost guard (ticket 13): the public token endpoint calls these on behalf of
    # a visitor who has no credentials. Residual risk: anything that can reach the backend can
    # also consume the daily caps, so do not expose the backend port publicly.
    ("POST", "/capacity/admissions"),
    ("POST", "/capacity/admissions/{room}/settle"),
}

# Routes the agent and the voice worker call.
SERVICE_ROUTES = {
    ("GET", "/providers"),
    ("GET", "/services"),
    ("GET", "/availability"),
    ("GET", "/faq"),
    ("GET", "/clinic-status"),
    ("POST", "/holds"),
    ("POST", "/holds/{hold_id}/release"),
    ("POST", "/holds/{hold_id}/read-back"),
    ("POST", "/holds/{hold_id}/confirm"),
    ("POST", "/appointments/{appointment_id}/read"),
    ("POST", "/appointments/lookup"),
    ("POST", "/appointments/{appointment_id}/cancel"),
    ("POST", "/appointments/{appointment_id}/reschedule"),
    ("POST", "/appointments/{appointment_id}/confirmation-message"),
    ("POST", "/calls"),
    ("POST", "/calls/{call_id}/turns"),
    ("POST", "/calls/{call_id}/tool-calls"),
    ("POST", "/calls/{call_id}/llm-calls"),
    ("POST", "/calls/{call_id}/turn-timings"),
    ("POST", "/calls/{call_id}/escalations"),
    ("POST", "/calls/{call_id}/end"),
}

STAFF_USER = "staff"
SERVICE_HEADER = "X-Service-Token"


def _same(given: str, secret: str) -> bool:
    return hmac.compare_digest(given.encode(), secret.encode())  # bytes: any text, no TypeError


def _basic(header: str | None) -> tuple[str, str] | None:
    if header is None or header[:6].lower() != "basic ":
        return None
    try:
        user, _, password = base64.b64decode(header[6:], validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return user, password


def authenticate(request: Request) -> None:
    """The app-wide dependency: let the request through or raise 401, 403 or 503."""
    route = request.scope.get("route")
    key = (request.method, getattr(route, "path", request.url.path))
    if key in PUBLIC_ROUTES:
        return
    settings = request.app.state.settings
    service_secret = (
        None
        if settings.backend_service_token is None
        else settings.backend_service_token.get_secret_value()
    )
    staff_secret = (
        None
        if settings.dashboard_password is None
        else settings.dashboard_password.get_secret_value()
    )
    needs_service = key in SERVICE_ROUTES
    needed = service_secret if needs_service else staff_secret
    if needed is None:
        raise HTTPException(status_code=503, detail="authentication is not configured")

    # Evaluate both checks every time, so the answer does not depend on which one matched first.
    token = request.headers.get(SERVICE_HEADER)
    is_service = service_secret is not None and token is not None and _same(token, service_secret)
    basic = _basic(request.headers.get("authorization"))
    is_staff = (
        staff_secret is not None
        and basic is not None
        and _same(basic[1], staff_secret) & _same(basic[0], STAFF_USER)
    )
    if is_service if needs_service else is_staff:
        return
    if is_service or is_staff:
        raise HTTPException(status_code=403, detail="these credentials may not use this route")
    challenge = {} if needs_service else {"WWW-Authenticate": 'Basic realm="Receptio staff"'}
    raise HTTPException(status_code=401, detail="authentication required", headers=challenge)
