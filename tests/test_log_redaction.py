"""Patient names and phone numbers must not reach log output.

Seams: `redact_text` (what is recognised), the logging system (captured output of real loggers,
including exception tracebacks and third-party loggers), a whole Call through the agent and
backend with all logging captured, and the eval report.

Not covered, by design or by limit (also in README): the database and the trace keep everything in
full; a name nobody told the process about is not recognised in free text; a number spoken as
words ("nine eight four five...") is not a digit run."""

import logging
from typing import Any

import httpx
import pytest

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import Turn, open_text_call
from backend.app import redaction
from backend.app.config import BackendSettings
from backend.app.redaction import redact_text, remember_name
from evals.report import _scenario_entry  # pyright: ignore[reportPrivateUsage]
from evals.runner import Scenario, ScenarioResult
from tests.test_confirm_cancel_reschedule import booked

NAME, FIRST, LAST = "Asha Menon", "Asha", "Menon"
PHONE = "+91 98450 12345"
LEAKS = (NAME, FIRST, LAST, "98450", "12345", "9845012345")


@pytest.fixture(autouse=True)
def fresh_registry() -> None:
    redaction.forget_names()
    redaction.install_log_redaction()


def assert_clean(text: str) -> None:
    for secret in LEAKS:
        assert secret.lower() not in text.lower(), f"{secret!r} leaked: {text!r}"


@pytest.mark.parametrize(
    "number",
    [
        "+91 98450 12345",
        "9845012345",
        "+919845012345",
        "(984) 501-2345",
        "98450-12345",
        "0471 234567",
    ],
)
def test_phone_numbers_in_any_common_format_are_redacted(number: str) -> None:
    assert redact_text(f"caller rang from {number}, thanks") == "caller rang from [phone], thanks"


def test_things_that_are_not_phone_numbers_are_left_alone() -> None:
    line = (
        '{"call": 12, "at": "2026-11-02T08:00:00+05:30", "day": "2026-11-02", "ms": 1400, '
        '"cost": 0.028571, "tokens": 12345, "caller_turn": 3}'
    )
    assert redact_text(line) == line


def test_a_remembered_name_is_redacted_whole_and_by_part_in_any_case() -> None:
    remember_name(NAME)
    text = "booking for Asha Menon; asha called; MENON confirmed; Menonville is a town"
    assert (
        redact_text(text)
        == "booking for [name]; [name] called; [name] confirmed; Menonville is a town"
    )


def test_a_name_nobody_remembered_is_not_recognised() -> None:
    assert redact_text("Priya Nair called") == "Priya Nair called"  # the documented limit


def test_the_registry_is_bounded() -> None:
    for i in range(redaction.MAX_NAMES + 50):
        remember_name(f"Person Number{'x' * (i % 5)}{chr(65 + i % 26)}{i}")
    assert redaction.remembered_count() <= redaction.MAX_NAMES


def test_log_output_of_any_logger_is_redacted_including_arguments_and_tracebacks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    remember_name(NAME)
    caplog.set_level(logging.DEBUG)

    logging.getLogger("receptio.agent").warning("identify failed for %s on %s", NAME, PHONE)
    logging.getLogger("httpx").info(f"POST /lookup {PHONE} {NAME}")  # noqa: G004
    logging.getLogger("livekit.agents").debug("transcript: %(n)s", {"n": f"{LAST} {PHONE}"})
    try:
        raise ValueError(f"bad identity {NAME} / {PHONE}")
    except ValueError:
        logging.getLogger("receptio.voice").exception("boom for %s", NAME)

    assert_clean(caplog.text)
    assert "[phone]" in caplog.text and "[name]" in caplog.text
    assert "Traceback" in caplog.text  # the traceback is kept, only the personal data is not
    assert_clean("\n".join(str(r.exc_text) for r in caplog.records))


def test_a_whole_call_with_a_name_and_phone_logs_none_of_it(
    backend: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)  # every logger, lowest level: whatever anything would write
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", name=NAME, phone=PHONE)
    call = open_text_call(
        backend,
        llm=ScriptedLLM(
            [
                UseTool("identify_patient", {"name": NAME, "phone": PHONE}),
                Say("Thank you, I found your appointment."),
            ]
        ),
    )
    heard = call.hear(f"I'm {NAME}, my number is {PHONE}")
    call.end()

    assert heard == ["Thank you, I found your appointment."]
    assert caplog.records, "the capture saw no logs at all, so it proves nothing"
    assert_clean(caplog.text)
    # The record itself is deliberately complete: staff need it.
    stored = backend.get(f"/dashboard/api/calls/{call.call_id}").json()
    assert NAME in str(stored) and "98450 12345" in str(stored)


def test_the_eval_report_redacts_phone_numbers_in_transcripts() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "s1",
            "description": "d",
            "persona": "p",
            "turns": ["x"],
            "expected": {"max_turns": 1},
        }
    )
    remember_name(NAME)
    result = ScenarioResult(
        scenario_id="s1",
        failures=[],
        caller_turns=1,
        transcript=(
            Turn("caller", f"my number is {PHONE}"),
            Turn("agent", "thanks"),
            Turn("agent", f"Thank you {NAME}"),
        ),
    )

    entry: dict[str, Any] = _scenario_entry(scenario, result, with_transcript=True)

    assert_clean(str(entry["transcript"]))
    assert entry["transcript"][1]["text"] == "thanks"  # the rest of the text is untouched


@pytest.mark.parametrize("service", ["backend", "agent", "web"])
def test_each_service_installs_redaction_when_it_is_created(
    service: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(redaction, "_installed", False)
    monkeypatch.setattr(logging, "_logRecordFactory", logging.LogRecord)  # as in a fresh process
    if service == "backend":
        from backend.app.main import create_app as backend_app

        backend_app(BackendSettings(database_url="sqlite+pysqlite:///:memory:"))
    elif service == "agent":
        from agent.main import create_app as agent_app

        agent_app()
    else:
        from voice.web import create_app as web_app

        web_app()
    caplog.set_level(logging.INFO)

    logging.getLogger("anything").info("rang from %s", PHONE)

    assert_clean(caplog.text)
    monkeypatch.undo()
    redaction.install_log_redaction()


def test_the_backend_remembers_every_name_it_is_given(backend: httpx.Client) -> None:
    redaction.forget_names()

    backend.post(
        "/appointments/lookup", json={"patient_name": "Priya Nair", "patient_phone": "9845099999"}
    )

    assert redact_text("Priya Nair rang") == "[name] rang"


def test_the_agent_remembers_the_name_a_caller_gives(backend: httpx.Client) -> None:
    redaction.forget_names()
    call = open_text_call(
        backend,
        llm=ScriptedLLM(
            [UseTool("record_patient", {"name": "Kavya Iyer", "phone": "9845011111"}), Say("ok")]
        ),
    )

    call.hear("Kavya Iyer, 9845011111")

    assert redact_text("Kavya Iyer rang") == "[name] rang"
