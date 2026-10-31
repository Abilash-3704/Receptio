from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Agent configuration, read from the environment (see .env.example).

    Text mode needs no provider keys and no network beyond the clinic backend.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    backend_url: str = "http://localhost:8000"
    # Applied to every request the agent makes to the clinic backend.
    backend_timeout_seconds: float = 5.0

    # The real LLM (agent/anthropic_llm.py). Never needed in text mode with the scripted LLM.
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"  # main turns
    llm_model_fast: str = "claude-haiku-5-5"  # simple turns (FAQ); not used yet
    llm_timeout_seconds: float = 15.0
