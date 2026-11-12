"""The single-VM compose file must not carry a dev LiveKit or expose anything but Caddy."""

from pathlib import Path
from typing import Any, cast

import yaml

PROD = Path(__file__).parents[1] / "docker-compose.prod.yml"


def services() -> dict[str, dict[str, Any]]:
    compose = cast(dict[str, Any], yaml.safe_load(PROD.read_text()))
    return cast(dict[str, dict[str, Any]], compose["services"])


def test_there_is_no_self_hosted_livekit() -> None:
    assert "livekit" not in services()


def test_only_caddy_publishes_ports() -> None:
    assert [name for name, s in services().items() if "ports" in s] == ["caddy"]


def test_services_are_deployed_and_restart() -> None:
    for name, service in services().items():
        assert service["restart"] == "unless-stopped", name
    for name in ("backend", "voice-agent", "web"):
        assert services()[name]["environment"]["DEPLOYED_ENVIRONMENT"] == "true", name


def test_livekit_settings_come_from_env_with_no_dev_default() -> None:
    for name in ("voice-agent", "web"):
        env = services()[name]["environment"]
        for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
            assert env[key].startswith("${" + key + ":?"), (name, key)
