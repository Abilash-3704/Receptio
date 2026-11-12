"""Run N concurrent stand-in Calls through the real stack and record what happened.

Real parts: the web token endpoint, capacity admission and settlement, the agent core (`TextCall`
with all its guards and tools), the clinic backend, the database, and the per-turn timing records
the voice worker would write (`voice.timing.TurnTimings`, fed here from this process's clock).
Stand-in parts: ASR, LLM and TTS are waits drawn from `StandInLatency` (loadtest/standins.py).

Each virtual Call is a thread; all of them start at the same moment (a barrier), each with its own
client address (an `X-Forwarded-For` the web service trusts for one hop) unless `distinct_ips` is
off. Nothing in a result is a provider measurement."""

import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from agent.text_mode import open_text_call
from loadtest.standins import Flow, StandInLatency, StandInLLM, flow_for
from voice.admission import settle_admission
from voice.persist import storing
from voice.timing import TurnTimings


@dataclass(frozen=True)
class LoadConfig:
    calls: int = 20
    latency: StandInLatency = field(default_factory=StandInLatency)
    seed: int = 1
    contention: str = "spread"  # "spread": distinct slots; "same": everyone wants one slot
    distinct_ips: bool = True  # False: every Call arrives from the same address
    index_offset: int = 0  # shifts which flow, name and address each virtual Call gets


@dataclass
class CallResult:
    index: int
    flow: str
    admitted: bool = False
    refusal: str | None = None
    token_ms: float | None = None
    call_id: int | None = None
    turn_ms: list[float] = field(default_factory=lambda: [])  # stand-in clock, per Caller turn
    overhead_ms: list[float] = field(default_factory=lambda: [])  # core time minus injected waits
    fallbacks: int = 0  # stand-in LLM steps that fell back because the script no longer fitted
    error: str | None = None


@dataclass
class LoadResult:
    calls: list[CallResult]
    wall_seconds: float
    max_in_flight: int

    @property
    def admission(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for call in self.calls:
            counts["admitted" if call.admitted else f"refused:{call.refusal}"] += 1
        return dict(counts)

    @property
    def errors(self) -> list[str]:
        return [f"call {c.index}: {c.error}" for c in self.calls if c.error is not None]


class _InFlight:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._now = 0
        self.peak = 0

    def __enter__(self) -> None:
        with self._lock:
            self._now += 1
            self.peak = max(self.peak, self._now)

    def __exit__(self, *_: object) -> None:
        with self._lock:
            self._now -= 1


def _ip(index: int) -> str:
    return f"10.{index // 250}.{index % 250}.7"


def _converse(
    backend: httpx.Client,
    flow: Flow,
    latency: StandInLatency,
    rng: random.Random,
    result: CallResult,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> float:
    """The Call itself. Returns its length in seconds."""
    llm = StandInLLM(flow.steps, latency, rng, sleep)
    call = open_text_call(backend, llm=llm, mode="voice")
    result.call_id = call.call_id
    timings = TurnTimings(call.call_id, emit=storing(backend, call.call_id, lambda _line: None))
    started = clock()
    for utterance in flow.caller:
        injected_before = llm.injected_seconds
        turn_start = clock()
        timings.on_speech_end(turn_start)
        sleep(latency.asr(rng))
        timings.on_asr_final(clock())
        sleep(latency.endpointing())
        core_start = clock()
        call.hear(utterance)
        core_seconds = clock() - core_start
        timings.on_core_turn(call.caller_turn_seq)
        timings.on_llm_first_token(clock())
        sleep(latency.tts_first_audio(rng))
        timings.on_tts_first_audio(clock())
        result.turn_ms.append((clock() - turn_start) * 1000)
        result.overhead_ms.append((core_seconds - (llm.injected_seconds - injected_before)) * 1000)
    timings.close()
    result.fallbacks = llm.fallbacks
    call.end()
    return clock() - started


def run_load(
    config: LoadConfig,
    web: httpx.Client,
    backend: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> LoadResult:
    results = [
        CallResult(i, flow_for(config.index_offset + i, config.contention).name)
        for i in range(config.calls)
    ]
    barrier = threading.Barrier(config.calls)
    in_flight = _InFlight()

    def one(index: int) -> None:
        result = results[index]
        rng = random.Random(config.seed * 100_003 + config.index_offset + index)
        barrier.wait()
        try:
            with in_flight:
                headers = (
                    {"X-Forwarded-For": _ip(config.index_offset + index)}
                    if config.distinct_ips
                    else {}
                )
                asked = clock()
                response = web.post("/token", headers=headers)
                result.token_ms = (clock() - asked) * 1000
                if response.status_code != 200:
                    body = response.json()
                    result.refusal = str(body.get("reason", response.status_code))
                    return
                result.admitted = True
                room: str = response.json()["room"]
                seconds = _converse(
                    backend,
                    flow_for(config.index_offset + index, config.contention),
                    config.latency,
                    rng,
                    result,
                    sleep,
                    clock,
                )
                settle_admission(backend, room, int(seconds))
        except Exception as error:  # noqa: BLE001 - a virtual Call failing is a finding, not a crash
            result.error = f"{type(error).__name__}: {error}"

    started = clock()
    threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(config.calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return LoadResult(results, clock() - started, in_flight.peak)
