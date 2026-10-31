from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class VoiceSettings(BaseSettings):
    """Voice pipeline configuration, read from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LiveKit server. The defaults are LiveKit's documented `--dev` credentials: not secrets, and
    # only valid against a local dev server. Never use them in a public deployment.
    livekit_url: str = "ws://localhost:7880"
    # What the browser dials, when it differs from what the agent worker dials (Compose).
    livekit_public_url: str | None = None
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"

    # The worker's own health/metrics HTTP port (LiveKit's default is 8081 in production mode).
    worker_port: int = 8081

    deepgram_api_key: SecretStr | None = None
    cartesia_api_key: SecretStr | None = None
    cartesia_voice: str = "f786b574-daa5-4673-aa0c-cbe3e8534c02"  # the plugin's default voice
    asr_model: str = "nova-3"

    # Turn-taking. 0.5 s is LiveKit's default; lowering it trades latency for cut-offs.
    min_endpointing_delay: float = 0.5
    # One JSON line per Caller turn (voice/timing.py); summarise with scripts/latency_summary.
    turn_log_path: str = "logs/voice-turns.jsonl"

    @property
    def browser_url(self) -> str:
        return self.livekit_public_url or self.livekit_url
