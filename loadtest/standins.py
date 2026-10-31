"""Stand-in providers for the load test: a fake ASR/LLM/TTS with configurable latencies.

NOTHING HERE IS A PROVIDER AND NOTHING HERE MEASURES ONE. The latencies are numbers chosen by
whoever runs the test; they make a Call take a realistic shape in time so the parts that ARE
real (the web token endpoint, capacity admission, the backend, the database, the agent core and
its tools, persistence of turn timings) are exercised under concurrency. A percentile computed
from a stand-in run is the configured distribution plus our own overhead, and says nothing about
Deepgram, Cartesia or Claude under load.

Latencies are drawn log-normally (a long right tail, like real service latency) around the
configured median, from a seeded generator, so a run can be repeated. `sigma=0` removes the
spread."""

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from agent.llm import LLMTurn, LLMUsage, Say, Step, UseTool
from agent.scripted_llm import ScriptedLLM, ScriptExhausted

MODEL = "stand-in-llm"


@dataclass(frozen=True)
class StandInLatency:
    """Medians in milliseconds. Defaults are ASSUMPTIONS in the region seen in
    docs/latency-budget.md's two-turn smoke run (a tool turn costs two LLM steps of roughly 1.7 s
    there), rounded down to something a faster model could do. Change them with the CLI flags."""

    asr_ms: float = 120.0  # speech end to ASR final
    endpointing_ms: float = 500.0  # wait for the turn detector (MIN_ENDPOINTING_DELAY)
    llm_step_ms: float = 1200.0  # one LLM request (a turn with a tool takes two)
    tts_first_audio_ms: float = 180.0  # first agent text to first audio
    sigma: float = 0.35  # log-normal spread

    def _draw(self, rng: random.Random, median_ms: float) -> float:
        if self.sigma == 0:
            return median_ms / 1000
        return median_ms / 1000 * math.exp(rng.gauss(0, self.sigma))

    def asr(self, rng: random.Random) -> float:
        return self._draw(rng, self.asr_ms)

    def endpointing(self) -> float:
        return self.endpointing_ms / 1000  # a configured delay, not a service

    def llm_step(self, rng: random.Random) -> float:
        return self._draw(rng, self.llm_step_ms)

    def tts_first_audio(self, rng: random.Random) -> float:
        return self._draw(rng, self.tts_first_audio_ms)


class StandInLLM:
    """Replays a script like ScriptedLLM, after a drawn delay per step. `injected_seconds` is the
    total delay it added, so a run can tell its own waiting from the system's overhead."""

    def __init__(
        self,
        steps: list[Step],
        latency: StandInLatency,
        rng: random.Random,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._script = ScriptedLLM(steps)
        self._latency = latency
        self._rng = rng
        self._sleep = sleep
        self.injected_seconds = 0.0
        # Times the script no longer fitted the Call (it ran out, or quoted an offer that was not
        # there, usually because a slot was taken). The stand-in then just asks the Caller to
        # repeat, so a load run degrades instead of crashing; the count is reported.
        self.fallbacks = 0
        self.last_usage: LLMUsage | None = None

    def next_step(self, turn: LLMTurn) -> Step:
        delay = self._latency.llm_step(self._rng)
        self._sleep(delay)
        self.injected_seconds += delay
        try:
            step = self._script.next_step(turn)
        except (ScriptExhausted, IndexError, KeyError):
            self.fallbacks += 1
            step = Say("Sorry, could you say that again?")
        # Token counts are made up (a plausible prompt size); the model has no price, so the cost
        # stored for these Calls is null rather than a number that looks real.
        self.last_usage = LLMUsage(MODEL, 1500 + 40 * len(turn.messages), 60, round(delay * 1000))
        return step


@dataclass(frozen=True)
class Flow:
    """What one stand-in Caller says, and the steps the stand-in LLM takes in answer."""

    name: str
    caller: list[str]
    steps: list[Step]


# Each entry: a Service and a phrase for when. Spread across them so that 20 Callers do not all
# fight for one slot; `contention="same"` makes them all want the first (worst case for holds).
BOOKINGS = [
    ("Cleaning", "tomorrow afternoon"),
    ("Check-up", "the day after tomorrow in the morning"),
    ("Cleaning", "in three days in the morning"),
    ("Check-up", "in four days in the afternoon"),
    ("Filling", "in five days"),
    ("Filling", "in four days in the morning"),
]


def _letters(n: int) -> str:
    return chr(65 + n // 26 % 26) + chr(65 + n % 26)


def flow_for(index: int, contention: str = "spread") -> Flow:
    """Every fourth Caller only asks a question; the rest book an Appointment."""
    if index % 4 == 3:
        return Flow(
            "faq",
            ["What are your opening hours?"],
            [UseTool("search_faq", {"question": "opening hours"}), Say("Happy to help with that.")],
        )
    same = contention == "same"
    service, when = BOOKINGS[0] if same else BOOKINGS[index % len(BOOKINGS)]
    # Callers sharing a (Service, when) pair take different options, so they want different slots.
    option = 1 if same else 1 + index // len(BOOKINGS) % 3
    name = f"Loadtest Caller{_letters(index)}"
    phone = f"+91 7{index:04d} 55555"
    return Flow(
        "book",
        [
            f"Hi, I'd like to book a {service.lower()} {when}",
            f"Option {option} please. The name is {name} and the number is {phone}",
            "Yes, that's right",
        ],
        [
            UseTool("check_availability", {"service": service, "when": when}),
            Say("I can do {offered[0]}. Which suits you?"),
            UseTool("record_patient", {"name": name, "phone": phone}),
            UseTool("place_hold", {"option": option}),
            UseTool("read_back", {}),
            UseTool("confirm_appointment", {}),
            Say("You're all booked, and a confirmation message is on its way."),
        ],
    )
