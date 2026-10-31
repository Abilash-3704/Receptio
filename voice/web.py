"""Web client server: serves the static page and mints a LiveKit token per Call.

The token is the only secret-bearing step, so it lives here and never in the browser. Each Call
gets a fresh room (the agent worker is dispatched into every new room) and a token that can join
that one room only. Run: `uvicorn voice.web:create_app --factory --port 8002`.

The endpoint is unauthenticated and a token starts a Call that spends provider money, so the cost
guard sits in front of token issuance (server side, not in the page): the clinic backend admits
or refuses each Call against per-visitor, per-IP and global daily caps (backend/app/capacity.py).

The visitor is a browser id plus an IP, neither of which the client can simply declare:
- the browser id is a random cookie this server issues (HttpOnly, 1 year); a client that drops it
  just gets a fresh one, which is why the IP is also capped on its own;
- the IP is the connection's own address. X-Forwarded-For is ignored unless
  `trusted_proxy_hops` says how many proxies of ours append to it, and then only the entry our
  outermost proxy added is used, never what the client wrote further left.
Limits, stated plainly: this is a cost guard, not identity. Someone who changes IP and clears
cookies can still start Calls; the global daily cap is what bounds total spend. Many people behind
one shared address (an office) share that address's ceiling."""

import math
import re
import secrets
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from livekit import api

from backend.app.ratelimit import SlidingWindowLimiter
from voice.admission import admit
from voice.config import VoiceSettings

PAGE = Path(__file__).parent / "web" / "index.html"
TOKEN_TTL_SECONDS = 10 * 60  # long enough to join; the Call itself is not bounded by it
BROWSER_COOKIE = "receptio_browser"
BROWSER_COOKIE_MAX_AGE = 365 * 24 * 3600
RATE_LIMITED_MESSAGE = "Too many requests. Please wait a moment and try again."
_BROWSER_ID = re.compile(r"[0-9a-f]{32}")


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    forwarded = [
        p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
    ]
    if len(forwarded) < trusted_proxy_hops:
        return peer  # not what we expect from our own proxies: do not trust it
    return forwarded[-trusted_proxy_hops]


def create_app(
    settings: VoiceSettings | None = None,
    backend: httpx.Client | None = None,
    now: Callable[[], float] = time.monotonic,
) -> FastAPI:
    settings = settings or VoiceSettings()
    backend = backend or httpx.Client(
        base_url=settings.backend_url, timeout=httpx.Timeout(settings.backend_timeout_seconds)
    )
    app = FastAPI(title="Receptio web client")
    limiter = SlidingWindowLimiter(settings.token_requests_per_minute_per_ip, 60, now)

    @app.get("/")
    def index() -> FileResponse:  # pyright: ignore[reportUnusedFunction]
        return FileResponse(PAGE, media_type="text/html")

    @app.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    @app.post("/token", response_model=None)
    def token(request: Request) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        ip = client_ip(request, settings.trusted_proxy_hops)
        wait = limiter.hit(ip)
        if wait is not None:
            # Before the cost guard is asked: nothing here is counted against anyone's allowance.
            return JSONResponse(
                {"reason": "rate_limited", "message": RATE_LIMITED_MESSAGE},
                status_code=429,
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            )
        browser = request.cookies.get(BROWSER_COOKIE, "")
        issued_cookie = _BROWSER_ID.fullmatch(browser) is None
        if issued_cookie:
            browser = secrets.token_hex(16)
        room = f"call-{secrets.token_hex(6)}"
        admission = admit(backend, room, browser, ip)
        if admission.admitted:
            identity = f"caller-{secrets.token_hex(4)}"
            jwt = (
                api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
                .with_identity(identity)
                .with_ttl(timedelta(seconds=TOKEN_TTL_SECONDS))
                .with_grants(api.VideoGrants(room_join=True, room=room))
                .to_jwt()
            )
            response = JSONResponse({"url": settings.browser_url, "room": room, "token": jwt})
        else:
            response = JSONResponse(
                {"reason": admission.reason, "message": admission.message},
                status_code=admission.status_code,
            )
        if issued_cookie:
            response.set_cookie(
                BROWSER_COOKIE,
                browser,
                max_age=BROWSER_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
            )
        return response

    return app
