"""The suite must not depend on a developer's local `.env`."""

from pathlib import Path

import pytest

from backend.app.config import BackendSettings


def test_settings_ignore_a_dotenv_file_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("CAPS_TEST_MODE=true\nDEMO_CONTROLS_ENABLED=true\n")
    monkeypatch.delenv("CAPS_TEST_MODE", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = BackendSettings()

    assert settings.caps_test_mode is False
    assert settings.demo_controls_enabled is False
