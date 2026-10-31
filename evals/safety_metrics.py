"""Emergency and Urgent Case recall and false-positive rate over the scenarios (spec: Testing
Decisions, docs/metrics.md).

Truth is each scenario's `safety` field. What the agent did is judged from outside, by the runner
(`ScenarioResult.observed_safety`). Recall for a level is the share of that level's scenarios where
the right thing happened AND every assertion of the scenario held (script delivered or not,
Escalation recorded, no scheduling, Hold released). A scenario that crashes the agent (for example
by consulting the LLM after the safety turn) counts as a miss. Scenarios marked `known_gap` are
probes of what the detector cannot see: reported, never counted toward the targets."""

from dataclasses import dataclass
from pathlib import Path

from evals.harness import FreshBackend
from evals.runner import Scenario, ScenarioResult, load_scenario, run_scenario

Run = tuple[Scenario, ScenarioResult]


def run_all(directory: Path, fresh_backend: FreshBackend) -> list[Run]:
    """Every scenario in the directory, each on its own fresh backend and database, as a scenario
    is written to run: one scenario's Holds and Appointments must not shape the next one."""
    runs: list[Run] = []
    for path in sorted(directory.glob("*.yaml")):
        scenario = load_scenario(path)
        try:
            with fresh_backend() as (backend, session_factory, clock):
                result = run_scenario(scenario, backend, session_factory, clock)
        except AssertionError as exc:  # e.g. the scripted LLM was consulted after the safety turn
            result = ScenarioResult(scenario.id, [f"agent error: {exc}"], "none")
        runs.append((scenario, result))
    return runs


@dataclass(frozen=True)
class SafetyMetrics:
    runs: list[Run]

    def _core(self, truth: str) -> list[Run]:
        return [(s, r) for s, r in self.runs if s.safety == truth and not s.known_gap]

    def _hits(self, truth: str) -> int:
        return sum(1 for _, r in self._core(truth) if r.observed_safety == truth and r.passed)

    @property
    def emergency_total(self) -> int:
        return len(self._core("emergency"))

    @property
    def urgent_total(self) -> int:
        return len(self._core("urgent"))

    @property
    def none_total(self) -> int:
        return len(self._core("none"))

    @property
    def emergency_recall(self) -> float:
        return self._hits("emergency") / max(1, self.emergency_total)

    @property
    def urgent_recall(self) -> float:
        return self._hits("urgent") / max(1, self.urgent_total)

    @property
    def urgent_called_emergency(self) -> int:
        return sum(1 for _, r in self._core("urgent") if r.observed_safety == "emergency")

    @property
    def false_positives(self) -> int:
        return len(self.flagged_wrongly())

    def flagged_wrongly(self) -> list[str]:
        return [
            f"{s.id} -> {r.observed_safety}"
            for s, r in self._core("none")
            if r.observed_safety != "none"
        ]

    def missed(self, truth: str) -> list[str]:
        return [
            f"{s.id}: observed {r.observed_safety}; {r.failures}"
            for s, r in self._core(truth)
            if not (r.observed_safety == truth and r.passed)
        ]

    def failed_scenarios(self) -> dict[str, list[str]]:
        return {s.id: r.failures for s, r in self.runs if not s.known_gap and not r.passed}

    @property
    def probe_total(self) -> int:
        return sum(1 for s, _ in self.runs if s.known_gap)

    @property
    def probes_handled_correctly(self) -> list[str]:
        return [
            s.id for s, r in self.runs if s.known_gap and r.observed_safety == s.safety and r.passed
        ]

    def render(self) -> str:
        def pct(part: int, whole: int) -> str:
            return f"{part}/{whole} = {100 * part / max(1, whole):.0f}%"

        probes = [(s, r) for s, r in self.runs if s.known_gap]
        lines = [
            "Safety metrics (scripted LLM, scenarios in evals/scenarios)",
            f"  Emergency recall:    {pct(self._hits('emergency'), self.emergency_total)}",
            f"  Urgent Case recall:  {pct(self._hits('urgent'), self.urgent_total)}",
            f"  Urgent given the Emergency script: {self.urgent_called_emergency}",
            f"  False positives:     {pct(self.false_positives, self.none_total)} of negative "
            "scenarios flagged",
            f"  Probes of known gaps: {len(probes)} written, "
            f"{len(self.probes_handled_correctly)} handled correctly",
        ]
        lines += [
            f"    probe {s.id} (truth {s.safety}): observed {r.observed_safety}" for s, r in probes
        ]
        return "\n".join(lines)


def compute(runs: list[Run]) -> SafetyMetrics:
    return SafetyMetrics(runs)
