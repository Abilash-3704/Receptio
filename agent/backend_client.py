import httpx

from agent.config import AgentSettings


def make_backend_client(
    settings: AgentSettings, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """The only way the agent reaches the clinic backend: configured URL, explicit timeout, and
    the service token (BACKEND_SERVICE_TOKEN) that proves this is the agent or the voice worker.
    With no token set none is sent, and the backend answers 401: there is no default."""
    token = settings.backend_service_token
    headers = {} if token is None else {"X-Service-Token": token.get_secret_value()}
    return httpx.Client(
        base_url=settings.backend_url,
        timeout=httpx.Timeout(settings.backend_timeout_seconds),
        headers=headers,
        transport=transport,
    )
