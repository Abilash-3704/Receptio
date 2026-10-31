"""The one eval command: `uv run python -m evals`.

Default is the scripted suite: deterministic, free, no network, exactly reproducible. It is a
regression gate. `--mode simulated` replaces the scripted Caller turns with an LLM playing a
persona, and the scripted agent with the real model. That spends real money, so it is opt-in: it
needs ANTHROPIC_API_KEY in the environment, enforces a hard request and cost cap, and `--dry-run`
prints the worst-case spend without calling anything.

Exit codes: 0 ok; 1 the baseline check found regressions; 2 usage error or missing key; 3 the run
was aborted by a spend cap (a partial report is still written)."""

import argparse
import json
import math
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent.config import AgentSettings
from evals.baseline import baseline_from, compare_to_baseline
from evals.config import EvalSettings
from evals.harness import SCENARIOS, Suite, load_scenarios, run_suite
from evals.metering import Estimate, Ledger, MeteredClient, estimate_run
from evals.personas import DEFAULT_PERSONAS, load_personas
from evals.report import build_report, render_markdown, write_report
from evals.runner import Scenario, caller_goal
from evals.simulated_caller import SimulatedCaller

EVALS = Path(__file__).parent
REPORTS = EVALS / "reports"
BASELINES = EVALS / "baselines"
KEY = "ANTHROPIC_API_KEY"


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m evals", description=(__doc__ or "").split("\n\n")[0]
    )
    p.add_argument("--mode", choices=["scripted", "simulated"], default="scripted")
    p.add_argument("--persona", default="patient", help="simulated mode: which Caller persona")
    p.add_argument("--personas", type=Path, default=DEFAULT_PERSONAS, help="personas YAML file")
    p.add_argument("--scenarios", type=Path, default=SCENARIOS)
    p.add_argument("--only", help="comma-separated scenario ids")
    p.add_argument("--limit", type=int, help="run at most this many scenarios")
    p.add_argument(
        "--dry-run", action="store_true", help="simulated: print the spend, call nothing"
    )
    p.add_argument("--max-requests", type=int, help="simulated: hard cap on API requests")
    p.add_argument("--max-cost-usd", type=float, help="simulated: hard cap on spend")
    p.add_argument("--reports-dir", type=Path, default=REPORTS)
    p.add_argument("--baseline-dir", type=Path, default=BASELINES)
    p.add_argument("--write-baseline", action="store_true", help="save this run as the baseline")
    p.add_argument("--check-baseline", action="store_true", help="exit 1 if worse than baseline")
    p.add_argument("--tolerance", type=float, help="slack for --check-baseline (simulated: 0.05)")
    return p.parse_args(argv)


def default_client(api_key: str) -> Any:
    import anthropic  # the optional `llm` extra

    return anthropic.Anthropic(api_key=api_key, max_retries=1)


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] = os.environ,
    client_factory: Callable[[str], Any] = default_client,
    out: Callable[[str], None] = print,
) -> int:
    args = parse(argv)
    settings, agent = EvalSettings(), AgentSettings()
    scenarios = load_scenarios(args.scenarios, args.only.split(",") if args.only else None)
    if args.limit is not None:
        scenarios = scenarios[: args.limit]

    ledger = Ledger()
    prices = settings.price_book(agent)
    persona_name: str | None = None
    if args.mode == "scripted":
        suite = run_suite(scenarios)
    else:
        personas = load_personas(args.personas)
        if args.persona not in personas:
            raise SystemExit(f"unknown persona {args.persona!r}; known: {sorted(personas)}")
        persona_name = args.persona
        max_requests = args.max_requests or settings.max_requests
        max_cost = args.max_cost_usd or settings.max_cost_usd
        if args.dry_run:
            out(dry_run(scenarios, settings, agent, max_requests, max_cost).describe())
            return 0
        if not env.get(KEY):
            out(
                f"Simulated runs call the real API and spend money. Set {KEY} in the environment "
                "(never commit it), or use --dry-run to see the cost first."
            )
            return 2
        suite = run_simulated(
            scenarios, personas[args.persona], settings, agent, ledger, prices,
            client_factory(env[KEY]), max_requests, max_cost,
        )  # fmt: skip

    report = build_report(
        suite.runs,
        args.mode,
        settings,
        ledger=ledger if args.mode == "simulated" else None,
        prices=prices if args.mode == "simulated" else None,
        persona=persona_name,
        aborted=suite.aborted,
    )
    markdown, as_json = write_report(report, args.reports_dir)
    out(render_markdown(report))
    out(f"Wrote {markdown} and {as_json}")

    baseline_path = args.baseline_dir / f"{args.mode}.json"
    if args.write_baseline:
        args.baseline_dir.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(baseline_from(report), indent=2) + "\n")
        out(f"Wrote baseline {baseline_path}")
    if suite.aborted:
        out(f"ABORTED: {suite.aborted}")
        return 3
    if args.check_baseline:
        tolerance = (
            args.tolerance if args.tolerance is not None else (0.05 if persona_name else 0.0)
        )
        baseline = json.loads(baseline_path.read_text())
        problems = compare_to_baseline(report, baseline, tolerance)
        for problem in problems:
            out(f"REGRESSION against baseline: {problem}")
        return 1 if problems else 0
    return 0


def dry_run(
    scenarios: list[Scenario],
    settings: EvalSettings,
    agent: AgentSettings,
    max_requests: int,
    max_cost: float,
) -> Estimate:
    turns = math.ceil(sum(s.expected.max_turns + 1 for s in scenarios) / max(1, len(scenarios)))
    return estimate_run(
        len(scenarios), turns, settings.est_requests_per_turn, settings,
        settings.price_book(agent), agent.llm_model, max_requests, max_cost,
    )  # fmt: skip


def run_simulated(
    scenarios: list[Scenario],
    persona: Any,
    settings: EvalSettings,
    agent: AgentSettings,
    ledger: Ledger,
    prices: Any,
    client: Any,
    max_requests: int,
    max_cost: float,
) -> Suite:
    from agent.anthropic_llm import AnthropicLLM  # needs the `llm` extra

    metered = MeteredClient(client, ledger, prices, max_requests, max_cost)
    llm = AnthropicLLM(metered, agent.llm_model, agent.llm_timeout_seconds)

    def caller_for(scenario: Scenario) -> SimulatedCaller:
        return SimulatedCaller(
            metered, settings.sim_model, persona, caller_goal(scenario), scenario.expected.max_turns
        )

    return run_suite(scenarios, caller_for=caller_for, llm=llm)


if __name__ == "__main__":
    raise SystemExit(main())
