"""Agent service entrypoint.

Tracer: a small HTTP service that can run a text-mode Call against the clinic backend.
The voice worker is a separate module (voice/worker.py), run as its own service.
"""

import uvicorn
from fastapi import FastAPI

from agent.backend_client import make_backend_client
from agent.config import AgentSettings
from agent.text_mode import open_text_call
from backend.app.redaction import install_log_redaction


def create_app(settings: AgentSettings | None = None) -> FastAPI:
    install_log_redaction()
    settings = settings or AgentSettings()
    app = FastAPI(title="Receptio agent")

    @app.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    @app.post("/text-calls/tracer")
    def tracer_call() -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        """Open a text-mode Call, then end it. Proves agent -> backend -> database."""
        with make_backend_client(settings) as backend:
            call = open_text_call(backend)
            call.end()
        return {
            "call_id": call.call_id,
            "transcript": [{"speaker": t.speaker, "text": t.text} for t in call.transcript],
        }

    return app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=8001)  # noqa: S104
