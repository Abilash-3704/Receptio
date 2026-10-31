"""Tracer: a text-mode Call through the real agent core and the real clinic backend."""

from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.text_mode import open_text_call
from backend.app.config import BackendSettings
from backend.app.main import create_app


@pytest.fixture
def backend() -> Iterator[httpx.Client]:
    settings = BackendSettings(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(create_app(settings), base_url="http://backend") as client:
        yield cast(httpx.Client, client)  # see tests/conftest.py


def test_text_call_opens_with_disclosure_and_ends_cleanly(backend: httpx.Client) -> None:
    call = open_text_call(backend)

    first = call.transcript[0]
    assert first.speaker == "agent"
    assert "AI" in first.text
    assert "recorded" in first.text

    call.end()

    record: dict[str, Any] = backend.get(f"/calls/{call.call_id}").json()
    assert record["status"] == "ended"
    assert record["ended_at"] is not None
    assert [t["speaker"] for t in record["transcript"]][0] == "agent"
