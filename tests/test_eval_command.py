"""The one eval command (`python -m evals`). The real API is never called here: simulated runs
use a fake client handed in through `client_factory`."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.__main__ import main
from tests.fake_anthropic import FakeAnthropic, replies

CHEST_PAIN = "Please hurry, my dad has terrible chest pain and his left arm is numb"


class Run:
    def __init__(self, tmp_path: Path, env: dict[str, str] | None = None) -> None:
        self.reports = tmp_path / "reports"
        self.baselines = tmp_path / "baselines"
        self.env = env or {}
        self.output: list[str] = []
        self.clients: list[FakeAnthropic] = []

    def factory(self, responder: Any) -> Any:
        def make(api_key: str) -> FakeAnthropic:
            assert api_key == self.env["ANTHROPIC_API_KEY"]
            client = FakeAnthropic(responder)
            self.clients.append(client)
            return client

        return make

    def __call__(self, *args: str, responder: Any = None) -> int:
        return main(
            [*args, "--reports-dir", str(self.reports), "--baseline-dir", str(self.baselines)],
            env=self.env,
            client_factory=self.factory(responder or replies()),
            out=self.output.append,
        )

    @property
    def text(self) -> str:
        return "\n".join(self.output)


def test_the_default_run_is_scripted_and_writes_markdown_and_json(tmp_path: Path) -> None:
    run = Run(tmp_path)

    assert run() == 0

    report = json.loads((run.reports / "metrics-scripted.json").read_text())
    assert (run.reports / "metrics-scripted.md").exists()
    assert report["mode"] == "scripted"
    assert report["metrics"]["emergency_recall"]["value"] == 1.0
    assert report["metrics"]["completion_rate"]["value"] == 1.0
    assert report["metrics"]["wrong_time_rate"]["value"] == 0.0
    assert report["known_gaps"]["probes"], "probes are reported apart from the targets"
    assert "Task completion rate" in run.text
    assert not run.clients  # no client, no API


def test_a_simulated_run_without_a_key_refuses_and_calls_nothing(tmp_path: Path) -> None:
    run = Run(tmp_path, env={})

    assert run("--mode", "simulated") == 2

    assert "ANTHROPIC_API_KEY" in run.text
    assert not run.clients and not run.reports.exists()


def test_a_dry_run_prints_the_spend_and_needs_neither_key_nor_client(tmp_path: Path) -> None:
    run = Run(tmp_path, env={})

    assert run("--mode", "simulated", "--dry-run") == 0

    assert "Dry run" in run.text and "USD" in run.text and "PLACEHOLDER" in run.text
    assert "Hard caps" in run.text
    assert not run.clients and not run.reports.exists()


def test_a_simulated_run_uses_the_fake_client_and_reports_cost_and_persona(tmp_path: Path) -> None:
    run = Run(tmp_path, env={"ANTHROPIC_API_KEY": "sk-test-not-real"})

    code = run(
        "--mode",
        "simulated",
        "--only",
        "emergency_chest_pain",
        "--persona",
        "hostile",
        responder=replies(CHEST_PAIN),
    )

    assert code == 0, run.text
    report = json.loads((run.reports / "metrics-simulated.json").read_text())
    assert report["persona"] == "hostile"
    assert report["metrics"]["emergency_recall"]["value"] == 1.0
    assert report["cost"]["status"] == "measured"
    assert report["cost"]["requests"] == 1  # the scenario allows one Caller turn
    assert report["cost"]["prices_are_placeholders"] is True
    assert report["scenarios"][0]["transcript"][1]["text"] == CHEST_PAIN
    # the persona's style reached the model
    assert "angry" in run.clients[0].requests[0]["system"]
    assert "sk-test-not-real" not in (run.reports / "metrics-simulated.json").read_text()


def test_the_request_cap_aborts_the_run_and_the_report_says_so(tmp_path: Path) -> None:
    run = Run(tmp_path, env={"ANTHROPIC_API_KEY": "sk-test-not-real"})

    code = run(
        "--mode",
        "simulated",
        "--only",
        "emergency_chest_pain,emergency_car_accident",
        "--max-requests",
        "1",
        responder=replies(CHEST_PAIN),
    )

    assert code == 3
    report = json.loads((run.reports / "metrics-simulated.json").read_text())
    assert "request cap" in report["aborted"]
    assert report["scenario_counts"]["run"] == 1
    assert len(run.clients[0].requests) == 1  # the scenario after the cap never started
    assert "ABORTED" in (run.reports / "metrics-simulated.md").read_text()


def test_the_limit_caps_how_many_scenarios_run(tmp_path: Path) -> None:
    run = Run(tmp_path)

    assert run("--limit", "3") == 0

    report = json.loads((run.reports / "metrics-scripted.json").read_text())
    assert report["scenario_counts"]["run"] == 3


def test_a_baseline_can_be_written_and_then_checked(tmp_path: Path) -> None:
    run = Run(tmp_path)

    assert run("--write-baseline") == 0
    baseline = json.loads((run.baselines / "scripted.json").read_text())
    assert baseline["metrics"]["emergency_recall"] == 1.0

    assert run("--check-baseline") == 0

    baseline["metrics"]["completion_rate"] = 1.0
    baseline["metrics"]["escalation_rate"] = 0.0
    (run.baselines / "scripted.json").write_text(json.dumps(baseline))
    assert run("--limit", "0", "--check-baseline") == 1  # nothing measured is a regression
    assert "baseline" in run.text.lower()


def test_an_unknown_persona_is_an_error(tmp_path: Path) -> None:
    run = Run(tmp_path, env={"ANTHROPIC_API_KEY": "k"})

    with pytest.raises(SystemExit):
        run("--mode", "simulated", "--persona", "nonexistent", "--dry-run")


def test_the_committed_scripted_baseline_still_holds(tmp_path: Path) -> None:
    # The baseline file ticket 15's CI gate compares against. Regenerate deliberately with
    # `python -m evals --write-baseline` when a metric legitimately changes.
    output: list[str] = []

    code = main(["--check-baseline", "--reports-dir", str(tmp_path)], env={}, out=output.append)

    assert code == 0, "\n".join(output[-5:])
