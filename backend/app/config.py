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
