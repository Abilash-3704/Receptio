"""A deployed environment (DEPLOYED_ENVIRONMENT=true) refuses the development defaults: the
Postgres password `receptio`, and LiveKit's documented `devkey`/`secret`. Local runs keep them."""

import pytest
from pydantic import ValidationError

from backend.app.config import BackendSettings

pytest.importorskip("livekit.api")

from voice.config import VoiceSettings  # noqa: E402

GOOD_URL = "postgresql+psycopg://receptio:Zq8vK2mR9xT4wL7p@db.internal:5432/receptio"
GOOD_SECRET = "s" * 40


def test_local_defaults_still_work() -> None:
    assert BackendSettings().database_url.startswith("postgresql")
    assert VoiceSettings().livekit_api_key == "devkey"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://receptio:receptio@localhost:5432/receptio",  # the shipped default
        "postgresql+psycopg://receptio:postgres@db:5432/receptio",
        "postgresql+psycopg://receptio:password@db:5432/receptio",
        "postgresql+psycopg://receptio@db:5432/receptio",  # no password at all
        "postgresql+psycopg://postgres:Zq8vK2mR9xT4wL7p@db:5432/receptio",  # superuser
    ],
)
def test_a_deployed_backend_refuses_a_default_or_missing_database_password(url: str) -> None:
    with pytest.raises(ValidationError, match="DEPLOYED_ENVIRONMENT"):
        BackendSettings(database_url=url, deployed_environment=True)


def test_a_deployed_backend_accepts_a_real_password() -> None:
    assert BackendSettings(database_url=GOOD_URL, deployed_environment=True).deployed_environment


def test_a_deployed_backend_refuses_demo_controls() -> None:
    with pytest.raises(ValidationError, match="DEMO_CONTROLS_ENABLED"):
        BackendSettings(
            database_url=GOOD_URL, deployed_environment=True, demo_controls_enabled=True
        )


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("devkey", "secret"),  # LiveKit --dev
        ("devkey", GOOD_SECRET),
        ("APIrealkey123", "secret"),
        ("APIrealkey123", "short"),  # too short to be a signing secret
        ("", GOOD_SECRET),
    ],
)
def test_a_deployed_voice_service_refuses_dev_or_weak_livekit_credentials(
    key: str, secret: str
) -> None:
    with pytest.raises(ValidationError, match="LIVEKIT"):
        VoiceSettings(livekit_api_key=key, livekit_api_secret=secret, deployed_environment=True)


def test_a_deployed_voice_service_accepts_real_credentials() -> None:
    settings = VoiceSettings(
        livekit_api_key="APIk3yFromLiveKit",
        livekit_api_secret=GOOD_SECRET,
        deployed_environment=True,
    )
    assert settings.deployed_environment
