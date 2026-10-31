from pydantic import Field
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
