from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Agent configuration, read from the environment (see .env.example).

    Text mode needs no provider keys and no network beyond the clinic backend.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    backend_url: str = "http://localhost:8000"
    # Applied to every request the agent makes to the clinic backend.
    backend_timeout_seconds: float = 5.0
