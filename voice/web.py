"""Web client server: serves the static page and mints a LiveKit token per Call.

The token is the only secret-bearing step, so it lives here and never in the browser. Each Call
gets a fresh room (the agent worker is dispatched into every new room) and a token that can join
that one room only. Run: `uvicorn voice.web:create_app --factory --port 8002`."""

import secrets
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from livekit import api

from voice.config import VoiceSettings

PAGE = Path(__file__).parent / "web" / "index.html"
TOKEN_TTL_SECONDS = 10 * 60  # long enough to join; the Call itself is not bounded by it


def create_app(settings: VoiceSettings | None = None) -> FastAPI:
    settings = settings or VoiceSettings()
    app = FastAPI(title="Receptio web client")

    @app.get("/")
    def index() -> FileResponse:  # pyright: ignore[reportUnusedFunction]
        return FileResponse(PAGE, media_type="text/html")

    @app.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    @app.post("/token")
    def token() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        room = f"call-{secrets.token_hex(6)}"
        identity = f"caller-{secrets.token_hex(4)}"
        jwt = (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(identity)
            .with_ttl(timedelta(seconds=TOKEN_TTL_SECONDS))
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .to_jwt()
        )
        return {"url": settings.browser_url, "room": room, "token": jwt}

    return app
