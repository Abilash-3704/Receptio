from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    """Clinic backend configuration, read from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://receptio:receptio@localhost:5432/receptio"
    # Seconds to wait for Postgres to accept a connection.
    database_connect_timeout_seconds: int = 5
    # Demo-only controls (the Clinic Hours open/closed override). Off by default; the endpoint
    # does not exist unless this is set.
    demo_controls_enabled: bool = False

    # Prices for per-Call cost (backend/app/cost.py). Configuration, never constants in the code
    # that computes cost. The defaults are PLACEHOLDERS, not provider prices: a Call's cost says
    # so while a price was not set through the environment (LLM_PRICES_PER_MTOK,
    # DEEPGRAM_USD_PER_MINUTE, CARTESIA_USD_PER_1K_CHARACTERS).
    # Claude: model name -> (input, output) USD per million tokens.
    llm_prices_per_mtok: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {"claude-sonnet-5-5": (3.0, 15.0), "claude-haiku-5-5": (1.0, 5.0)}
    )
    deepgram_usd_per_minute: float = 0.01
    cartesia_usd_per_1k_characters: float = 0.05

    # Cost guard (ticket 13). All caps are configuration; the defaults are the specified values.
    # Longest a Call may run. The agent enforces it (CALL_MAX_SECONDS is read there too); the
    # backend reserves this much of the global budget for every admitted Call.
    call_max_seconds: int = 300
    # Calls per day for one visitor (browser plus IP), and for one IP whatever the browser says:
    # a client can drop its browser id, so the IP ceiling is what bounds that.
    visitor_calls_per_day: int = 3
    ip_calls_per_day: int = 10
    # Minutes of Call time per day across everyone.
    global_minutes_per_day: int = 120
    # Lifts the three daily caps, for the load test. Off by default.
    caps_test_mode: bool = False
    # Set in any deployed environment (the public demo). Makes caps_test_mode impossible.
    deployed_environment: bool = False

    # Rate limits (ticket 20), in process; see backend/app/ratelimit.py. Abuse throttles only: the
    # daily caps above stay the cost guard.
    # Capacity admission requests per client IP per minute. The web service asks once per token
    # request and has its own, lower limit in front, so this is the backstop behind it.
    admission_requests_per_minute_per_ip: int = Field(default=30, ge=1)
    # Failed identification attempts (name + phone) allowed within the window before further
    # attempts are refused: per name, per phone, and across everyone. See identity_throttle.py.
    identity_failure_window_seconds: int = Field(default=600, ge=1)
    identity_failures_per_name: int = Field(default=5, ge=1)
    identity_failures_per_phone: int = Field(default=5, ge=1)
    identity_failures_global: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _no_test_mode_when_deployed(self) -> "BackendSettings":
        if self.caps_test_mode and self.deployed_environment:
            raise ValueError("cap test mode cannot be enabled when DEPLOYED_ENVIRONMENT is set")
        return self
