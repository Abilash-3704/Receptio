"""`docker compose up` with BACKEND_SERVICE_TOKEN unset must stop at once with a clear message,
not start a backend that answers 503 and a worker that is silent to the Caller."""

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

COMPOSE = Path(__file__).parents[1] / "docker-compose.yml"
NEEDS_THE_TOKEN = ["backend", "agent", "voice-agent"]


def environment_of(service: str) -> dict[str, str]:
    compose = cast(dict[str, Any], yaml.safe_load(COMPOSE.read_text()))
    return cast(dict[str, str], compose["services"][service]["environment"])


@pytest.mark.parametrize("service", NEEDS_THE_TOKEN)
def test_a_service_that_needs_the_service_token_refuses_to_start_without_it(service: str) -> None:
    value = environment_of(service)["BACKEND_SERVICE_TOKEN"]

    assert value.startswith("${BACKEND_SERVICE_TOKEN:?"), value
    assert ".env" in value  # the message says where to set it
