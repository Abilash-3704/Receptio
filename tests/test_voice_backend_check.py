"""The voice worker must say plainly when the backend refuses it (a missing or wrong service
token), instead of dying on a bare traceback while the Caller hears silence. No LiveKit."""

import logging

import httpx
import pytest

from voice.admission import open_call_or_explain, settle_admission


def backend_answering(status: int) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": "x"})),
        base_url="http://backend",
    )


def test_an_unconfigured_backend_is_reported_in_one_clear_line_and_no_call_is_opened(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="receptio.voice")

    call = open_call_or_explain(backend_answering(503))

    assert call is None
    [record] = caplog.records
    assert "BACKEND_SERVICE_TOKEN" in record.getMessage()
    assert "backend" in record.getMessage().lower()
    assert record.exc_info is None  # a line, not a traceback


def test_a_rejected_token_is_reported_as_a_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="receptio.voice")

    assert open_call_or_explain(backend_answering(401)) is None

    [record] = caplog.records
    assert "BACKEND_SERVICE_TOKEN" in record.getMessage()
    assert "match" in record.getMessage().lower()


def test_settling_with_a_refused_token_says_why_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="receptio.voice")

    settle_admission(backend_answering(401), "room-1", 30)

    [record] = caplog.records
    assert "BACKEND_SERVICE_TOKEN" in record.getMessage()
