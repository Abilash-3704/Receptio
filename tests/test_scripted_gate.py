"""The scripted suite as a CI gate (ticket 15): deterministic, whatever day it is run."""

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from evals.__main__ import main
from evals.harness import SCENARIOS, load_scenarios, run_suite
from evals.runner import run_scenario
from tests.support import FakeClock


def test_the_suite_does_not_depend_on_the_day_it_is_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's clock must be the backend's fixed clock, not the machine's. Simulate a CI run
    on another day by making the machine clock a Tuesday in December."""
    december = FakeClock(datetime(2026, 12, 15, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    monkeypatch.setattr(run_scenario, "__defaults__", (december, None, None))

    suite = run_suite(load_scenarios(SCENARIOS))

    # Known-gap probes are expected to miss; they are not part of the gate.
    failed = {s.id: r.failures for s, r in suite.runs if not r.passed and not s.known_gap}
    assert failed == {}


def test_the_gate_exits_1_on_a_regressed_suite_and_0_on_the_committed_one(
    tmp_path: Path,
) -> None:
    """End to end, as CI runs it: `python -m evals --check-baseline` against the committed
    baseline. The regression is made by breaking one Emergency scenario's phrasing so the
    detector no longer sees it (an Emergency missed), the worst thing a change can do."""
    regressed = tmp_path / "scenarios"
    shutil.copytree(SCENARIOS, regressed)
    broken = regressed / "emergency_chest_pain.yaml"
    broken.write_text(
        broken.read_text().replace("chest pain", "a heavy feeling").replace("numb", "tingly")
    )
    reports = ["--reports-dir", str(tmp_path / "reports")]
    output: list[str] = []

    assert main(["--check-baseline", *reports], env={}, out=output.append) == 0
    code = main(
        ["--check-baseline", "--scenarios", str(regressed), *reports], env={}, out=output.append
    )

    assert code == 1
    text = "\n".join(output)
    assert "REGRESSION" in text and "emergency_recall" in text
