from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A shorter secret is treated as not set. `openssl rand -hex 24` makes a good one.
MIN_SECRET_CHARS = 16


class BackendSettings(BaseSettings):
    """Clinic backend configuration, read from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Authentication (backend/app/auth.py). Both come from the environment only: there is no
    # default, and a secret that is unset, empty or shorter than MIN_SECRET_CHARS counts as not
    # configured, so the routes it protects answer 503 instead of letting anyone in.
    # BACKEND_SERVICE_TOKEN is what the agent and voice worker send; DASHBOARD_PASSWORD is the
    # staff password (user "staff") for the dashboard and every other staff route.
    backend_service_token: SecretStr | None = None
    dashboard_password: SecretStr | None = None

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

    @field_validator("backend_service_token", "dashboard_password", mode="after")
    @classmethod
    def _secret_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or len(value.get_secret_value()) < MIN_SECRET_CHARS:
            return None
        return value

    @model_validator(mode="after")
    def _no_test_mode_when_deployed(self) -> "BackendSettings":
        if self.caps_test_mode and self.deployed_environment:
            raise ValueError("cap test mode cannot be enabled when DEPLOYED_ENVIRONMENT is set")
        return self
