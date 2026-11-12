"""The real-provider load test: ~20 concurrent Calls through real Deepgram, Cartesia and Claude.

THIS SPENDS REAL MONEY, so it is built to be hard to run by accident:
- the default is a DRY RUN that prints a worst-case cost estimate and the exact command, and
  contacts nothing;
- running for real needs `--execute` AND the environment variable LOADTEST_REAL_SPEND_OK=yes;
- the estimate must be at or under `--max-cost-usd` (default 10), and that cap cannot be set above
  HARD_CAP_USD in this file whatever the flag says;
- at most MAX_CALLS Calls;
- keys are only read from the environment, never printed, never written anywhere; a missing key
  is reported by NAME.

The estimate is an upper bound under stated assumptions (see `Assumptions`), priced with the
backend's configured prices (LLM_PRICES_PER_MTOK, DEEPGRAM_USD_PER_MINUTE,
CARTESIA_USD_PER_1K_CHARACTERS). Unless those are set it uses the PLACEHOLDER defaults of
backend/app/config.py and says so: set real prices from the providers' price lists first.

What running does: it starts nothing. The stack must already be up (`docker compose up --build`:
LiveKit, the voice worker with the provider keys, the backend). It launches one synthetic Caller
(`scripts/voice_smoke.py`: Cartesia-synthesised speech published into a fresh room) per Call, all
at once, waits for them, and summarises the persisted per-turn timings of the Calls created
meanwhile. These Calls enter by LiveKit directly, not through the web token endpoint, so the cost
guard's admission is not exercised in this mode (the stand-in run covers it)."""

import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from backend.app.config import BackendSettings
from backend.app.cost import MTOK

MAX_CALLS = 20
HARD_CAP_USD = 25.0
DEFAULT_CAP_USD = 10.0
CONFIRM_VARIABLE = "LOADTEST_REAL_SPEND_OK"
KEYS = ("DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "ANTHROPIC_API_KEY")
ROOT = Path(__file__).resolve().parent.parent

PHRASES = [
    "What are your opening hours?",
    "I'd like to book a cleaning for next Tuesday afternoon.",
    "My name is Priya Sharma and my number is nine eight seven six five four three two one zero.",
]


@dataclass(frozen=True)
class Prices:
    claude_input_per_mtok: float
    claude_output_per_mtok: float
    deepgram_per_minute: float
    cartesia_per_1k_chars: float
    placeholder: bool

    @staticmethod
    def from_environment(model: str = "claude-sonnet-5-5") -> "Prices":
        settings = BackendSettings()
        configured = settings.model_fields_set
        price_in, price_out = settings.llm_prices_per_mtok.get(model, (0.0, 0.0))
        needed = {
            "llm_prices_per_mtok",
            "deepgram_usd_per_minute",
            "cartesia_usd_per_1k_characters",
        }
        return Prices(
            price_in,
            price_out,
            settings.deepgram_usd_per_minute,
            settings.cartesia_usd_per_1k_characters,
            placeholder=not needed <= configured,
        )


@dataclass(frozen=True)
class Assumptions:
    """Per Call, deliberately pessimistic. Raise them to cover a longer script."""

    turns_per_call: int = len(PHRASES)
    llm_steps_per_turn: int = 3  # a tool turn is two; allow one more
    input_tokens_per_step: int = 6000  # system prompt, tools and the growing conversation
    output_tokens_per_step: int = 300
    chars_per_reply: int = 500  # agent speech per turn, synthesised by Cartesia
    caller_chars_per_turn: int = 60  # the synthetic Caller's own Cartesia use (voice_smoke.py)
    call_max_seconds: int = 300  # Deepgram is billed for the Call's audio: assume the full cap


@dataclass(frozen=True)
class Estimate:
    calls: int
    deepgram_usd: float
    cartesia_usd: float
    claude_usd: float
    placeholder_prices: bool

    @property
    def total_usd(self) -> float:
        return self.deepgram_usd + self.cartesia_usd + self.claude_usd


def estimate(calls: int, a: Assumptions, prices: Prices) -> Estimate:
    steps = a.turns_per_call * a.llm_steps_per_turn
    claude = steps * (
        a.input_tokens_per_step * prices.claude_input_per_mtok
        + a.output_tokens_per_step * prices.claude_output_per_mtok
    )
    chars = a.turns_per_call * (a.chars_per_reply + a.caller_chars_per_turn)
    return Estimate(
        calls,
        calls * a.call_max_seconds / 60 * prices.deepgram_per_minute,
        calls * chars / 1000 * prices.cartesia_per_1k_chars,
        calls * claude / MTOK,
        prices.placeholder,
    )


def command(calls: int, cap: float) -> str:
    return (
        f"{CONFIRM_VARIABLE}=yes python -m loadtest real --calls {calls} "
        f"--max-cost-usd {cap:g} --execute"
    )


def execute_with_voice_smoke(calls: int, turns: int) -> None:
    """One synthetic Caller process per Call, all at once. Not run by any test or by CI."""
    phrases = PHRASES[:turns]
    script = str(ROOT / "scripts" / "voice_smoke.py")
    deadline = time.monotonic() + Assumptions().call_max_seconds + 90
    processes = [
        subprocess.Popen(
            [sys.executable, script, *phrases],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(calls)
    ]
    for process in processes:
        try:
            process.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()


def main(
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
    execute_real: Callable[[int, int], None] = execute_with_voice_smoke,
    prices: Prices | None = None,
) -> int:
    import argparse

    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="python -m loadtest real")
    parser.add_argument("--calls", type=int, default=MAX_CALLS)
    parser.add_argument("--turns", type=int, default=len(PHRASES))
    parser.add_argument("--max-cost-usd", type=float, default=DEFAULT_CAP_USD)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(list(argv))

    if not 1 <= args.calls <= MAX_CALLS:
        out(f"REFUSED: --calls must be between 1 and {MAX_CALLS} (got {args.calls}).")
        return 2
    if not 1 <= args.turns <= len(PHRASES):
        out(f"REFUSED: --turns must be between 1 and {len(PHRASES)}.")
        return 2
    assumptions = Assumptions(turns_per_call=args.turns)
    result = estimate(args.calls, assumptions, prices or Prices.from_environment())

    out(f"Real-provider load test: {args.calls} concurrent Calls, {args.turns} Caller turns each.")
    out(
        f"Worst-case cost estimate: ${result.total_usd:.2f} "
        f"(Deepgram ${result.deepgram_usd:.2f}, Cartesia ${result.cartesia_usd:.2f}, "
        f"Claude ${result.claude_usd:.2f})."
    )
    out(
        f"Assumed per Call: Deepgram for the full {assumptions.call_max_seconds} s cap; "
        f"{assumptions.turns_per_call} turns x {assumptions.llm_steps_per_turn} LLM steps of "
        f"{assumptions.input_tokens_per_step} input and {assumptions.output_tokens_per_step} "
        f"output tokens; {assumptions.chars_per_reply} agent characters per turn; "
        f"{assumptions.caller_chars_per_turn} Caller characters per turn."
    )
    if result.placeholder_prices:
        out(
            "WARNING: prices are the PLACEHOLDER defaults, not provider prices. Set "
            "LLM_PRICES_PER_MTOK, DEEPGRAM_USD_PER_MINUTE and CARTESIA_USD_PER_1K_CHARACTERS "
            "from the providers' price lists before relying on this number."
        )
    out(f"Spend cap: ${args.max_cost_usd:g} (hard ceiling ${HARD_CAP_USD:g}).")

    if not args.execute:
        out("DRY RUN: nothing was started and no provider was contacted.")
        out(f"To run for real: {command(args.calls, args.max_cost_usd)}")
        return 0

    problems: list[str] = []
    if args.max_cost_usd > HARD_CAP_USD:
        problems.append(f"--max-cost-usd exceeds the hard ceiling of ${HARD_CAP_USD:g}")
    if result.total_usd > args.max_cost_usd:
        problems.append(
            f"the worst-case estimate ${result.total_usd:.2f} exceeds the cap "
            f"${args.max_cost_usd:g}: lower --calls/--turns or raise the cap knowingly"
        )
    if env.get(CONFIRM_VARIABLE) != "yes":
        problems.append(f"set {CONFIRM_VARIABLE}=yes in the environment to confirm the spend")
    missing = [name for name in KEYS if not env.get(name)]
    if missing:
        problems.append("missing provider keys (names only): " + ", ".join(missing))
    if problems:
        for problem in problems:
            out(f"REFUSED: {problem}.")
        return 2
    out("Launching. Keys are read from the environment and are not printed.")
    execute_real(args.calls, args.turns)
    return 0
