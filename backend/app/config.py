from pydantic import model_validator
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

    @model_validator(mode="after")
    def _no_test_mode_when_deployed(self) -> "BackendSettings":
        if self.caps_test_mode and self.deployed_environment:
            raise ValueError("cap test mode cannot be enabled when DEPLOYED_ENVIRONMENT is set")
        return self
