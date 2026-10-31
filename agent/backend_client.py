import httpx

from agent.config import AgentSettings


def make_backend_client(settings: AgentSettings) -> httpx.Client:
    """The only way the agent reaches the clinic backend: configured URL, explicit timeout."""
    return httpx.Client(
        base_url=settings.backend_url,
        timeout=httpx.Timeout(settings.backend_timeout_seconds),
    )
