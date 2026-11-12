from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Agent configuration, read from the environment (see .env.example).

    Text mode needs no provider keys and no network beyond the clinic backend.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    backend_url: str = "http://localhost:8000"
    # Shared secret the backend checks on every agent-facing route (BACKEND_SERVICE_TOKEN, the
    # same variable the backend reads). Environment only; no default.
    backend_service_token: SecretStr | None = None
    # Applied to every request the agent makes to the clinic backend.
    backend_timeout_seconds: float = 5.0

    # Cost guard (ticket 13): the longest a Call runs, and when the warning that offers a Callback
    # Request is given. CALL_MAX_SECONDS is also read by the backend, which reserves that much
    # of the daily global budget for each Call, so set it once for both.
    call_max_seconds: int = 300
    call_warning_seconds: int = 270

    # The real LLM (agent/anthropic_llm.py). Never needed in text mode with the scripted LLM.
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"  # main turns
    # Off by default: the fast model takes the planning steps too (which tool, what to ask next).
    # Roughly twice as fast per request, but unproven on the booking flow: see the eval in
    # docs/latency-investigation.md before turning it on.
    llm_fast_planning: bool = False
    llm_model_fast: str = "claude-haiku-5-5"  # wording a tool result (FAQ, confirm, cancel)
    llm_timeout_seconds: float = 15.0
