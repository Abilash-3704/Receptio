"""Load test CLI.

    python -m loadtest standin --database-url postgresql+psycopg://...  # free: stand-in providers
    python -m loadtest real [--execute ...]       # SPENDS MONEY; a dry run unless --execute
    python -m loadtest summarise --database-url ... --since-call-id N   # timings of stored Calls

`standin` starts the backend and the web service as subprocesses (uvicorn) against a database you
name, runs one Call alone as a baseline, then N Calls at once, and prints a Markdown report. The
database is written to (Calls, Patients, Appointments): point it at a throwaway one. See
loadtest/standins.py for what is and is not real, and loadtest/real.py for the guarded real mode."""

import argparse
import os
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import ExitStack
from datetime import UTC, datetime
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.alerts import evaluate_alerts
from backend.app.config import BackendSettings
from loadtest import real
from loadtest.pgstats import PgSampler
from loadtest.report import (
    RunSummary,
    calls_since,
    float_stats,
    render_standin_report,
    summarise_calls,
    summarise_run,
)
from loadtest.runner import LoadConfig, LoadResult, run_load
from loadtest.standins import StandInLatency

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_ready(url: str, process: subprocess.Popen[bytes], seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the server for {url} exited early (code {process.returncode})")
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"{url} did not become ready")


def serve(target: str, port: int, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            target,
            "--factory",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env={**os.environ, **env},
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )


def overhead(result: LoadResult) -> dict[str, int] | None:
    values = [v for c in result.calls for v in c.overhead_ms]
    return float_stats([max(0.0, v) for v in values]) if values else None


def standin(args: argparse.Namespace) -> int:
    latency = StandInLatency(
        args.asr_ms, args.endpointing_ms, args.llm_step_ms, args.tts_ms, args.sigma
    )
    backend_port, web_port = free_port(), free_port()
    quiet = "1000000"  # the rate limits are off the table: this run is about everything behind them
    backend_env = {
        "DATABASE_URL": args.database_url,
        "CAPS_TEST_MODE": "false" if args.caps else "true",
        "DEPLOYED_ENVIRONMENT": "false",
        "ADMISSION_REQUESTS_PER_MINUTE_PER_IP": quiet,
    }
    web_env = {
        "BACKEND_URL": f"http://127.0.0.1:{backend_port}",
        "LIVEKIT_API_KEY": "loadtest-not-a-secret",
        "LIVEKIT_API_SECRET": "x" * 40,
        "TRUSTED_PROXY_HOPS": "1",
        "TOKEN_REQUESTS_PER_MINUTE_PER_IP": quiet,
    }
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(args.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    with ExitStack() as stack:
        backend_process = serve(
            "backend.app.main:create_app", backend_port, backend_env, log_dir / "backend.log"
        )
        stack.callback(backend_process.terminate)
        wait_ready(f"http://127.0.0.1:{backend_port}", backend_process)
        web_process = serve("voice.web:create_app", web_port, web_env, log_dir / "web.log")
        stack.callback(web_process.terminate)
        wait_ready(f"http://127.0.0.1:{web_port}", web_process)

        limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
        backend = stack.enter_context(
            httpx.Client(base_url=f"http://127.0.0.1:{backend_port}", timeout=60, limits=limits)
        )
        web = stack.enter_context(
            httpx.Client(base_url=f"http://127.0.0.1:{web_port}", timeout=60, limits=limits)
        )
        # Each virtual Call is its own browser: never keep the visitor cookie between requests.
        web.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        started_at = datetime.now(UTC)
        # Five Calls one after another (different flows), so the baseline has more than a few turns.
        baseline = LoadResult([], 0.0, 1)
        for k in range(5):
            alone = run_load(
                LoadConfig(1, latency, args.seed, args.contention, index_offset=1000 + k),
                web,
                backend,
            )
            baseline.calls += alone.calls
            baseline.wall_seconds += alone.wall_seconds
        # A fresh set of Callers for the loaded run; the baseline's stay in the database.
        with PgSampler(engine) as sampler:
            result = run_load(
                LoadConfig(args.calls, latency, args.seed + 1, args.contention, not args.one_ip),
                web,
                backend,
            )
        load = summarise_run(factory, result)
        base = summarise_run(factory, baseline)
        settings = BackendSettings(database_url=args.database_url).model_copy(
            update={"alert_window_minutes": 60, "alert_min_turns": 10}
        )
        with factory() as session:
            alerts = evaluate_alerts(session, settings, datetime.now(UTC))
        with engine.connect() as connection:
            version = str(connection.execute(text("show server_version")).scalar())
    conditions = {
        "Date": started_at.strftime("%Y-%m-%d"),
        "Machine": f"{os.uname().sysname} {os.uname().machine}, all processes on one host",
        "Database": f"Postgres {version}, SQLAlchemy pool defaults (5 + 10 overflow)",
        "Servers": "backend and web under uvicorn, one process each, over real HTTP on loopback",
        "Cost guard": "daily caps ON" if args.caps else "CAPS_TEST_MODE=true (daily caps lifted)",
        "Rate limits": "lifted (1,000,000 per minute) so they do not shape this run",
        "Concurrent Calls": f"{args.calls} (after 5 baseline Calls run one at a time)",
        "Contention": args.contention,
        "Caller addresses": "one shared address" if args.one_ip else "a distinct address per Call",
        "Seed": str(args.seed),
    }
    text_out = render_standin_report(
        args.title,
        conditions,
        latency,
        result.admission,
        load,
        base,
        {
            label: stats
            for label, stats in (
                ("load", overhead(result)),
                ("Calls alone", overhead(baseline)),
            )
            if stats is not None
        },
        float_stats([c.token_ms for c in result.calls if c.token_ms is not None])
        if any(c.token_ms is not None for c in result.calls)
        else None,
        sampler.stats,
        result.errors,
        sum(c.fallbacks for c in result.calls),
        alerts,
    )
    print(text_out)
    if args.out:
        Path(args.out).write_text(text_out)
    return 1 if result.errors else 0


def summarise(args: argparse.Namespace) -> int:
    engine = create_engine(args.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    ids = calls_since(factory, args.since_call_id)
    summary: RunSummary = summarise_calls(factory, ids)
    print(
        f"Calls after id {args.since_call_id}: {summary.calls}; outcomes {summary.calls_by_outcome}"
    )
    print(
        f"Turn timings stored: {summary.timing_samples} (interrupted {summary.interrupted_turns})"
    )
    print("stage                       n    p50    p95    p99    max")
    for stage, s in summary.stages.items():
        print(f"{stage:<24}{s['n']:>5}{s['p50']:>7}{s['p95']:>7}{s['p99']:>7}{s['max']:>7}")
    print(
        f"Tool calls {summary.tool_calls}, failed {summary.tool_failures} "
        f"{summary.tool_failures_by_name}, refused {summary.tool_refusals}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["real"]:
        return real.main(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m loadtest")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("standin", help="run with stand-in providers (free)")
    s.add_argument("--database-url", required=True, help="a throwaway Postgres database")
    s.add_argument("--calls", type=int, default=20)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--contention", choices=["spread", "same"], default="spread")
    s.add_argument(
        "--caps", action="store_true", help="keep the daily caps on (default: test mode)"
    )
    s.add_argument("--one-ip", action="store_true", help="all Calls arrive from one address")
    s.add_argument("--asr-ms", type=float, default=StandInLatency.asr_ms)
    s.add_argument("--endpointing-ms", type=float, default=StandInLatency.endpointing_ms)
    s.add_argument("--llm-step-ms", type=float, default=StandInLatency.llm_step_ms)
    s.add_argument("--tts-ms", type=float, default=StandInLatency.tts_first_audio_ms)
    s.add_argument("--sigma", type=float, default=StandInLatency.sigma)
    s.add_argument("--title", default="Stand-in load run")
    s.add_argument("--out", help="also write the report here")
    s.add_argument("--log-dir", default="logs/loadtest")
    m = sub.add_parser("summarise", help="persisted timings of Calls after an id")
    m.add_argument("--database-url", required=True)
    m.add_argument("--since-call-id", type=int, required=True)
    args = parser.parse_args(argv)
    return standin(args) if args.command == "standin" else summarise(args)


if __name__ == "__main__":
    raise SystemExit(main())
