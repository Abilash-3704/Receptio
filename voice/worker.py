# pyright: basic
"""LiveKit agent worker: joins each new room and runs a voice Call on the shared agent core.

Pipeline (livekit-agents 1.8.10): Silero VAD -> Deepgram streaming ASR -> `agent.text_mode.TextCall`
(the same core as text mode, with the real Claude LLM) -> Cartesia streaming TTS. LiveKit owns
transport, VAD, turn detection and barge-in; the agent core never sees LiveKit.

Run (needs `uv sync --extra voice` and the provider keys in the environment or .env):
    python -m voice.worker dev        # local; `start` for production-style logging

Preemptive generation is switched off: the core mutates Call state (holds, Escalations) when it
answers, so it must run once per Caller turn, never speculatively on interim transcripts."""

import asyncio
import logging
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterable, Coroutine
from pathlib import Path
from typing import Any

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    cli,
    llm,
)
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ErrorEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import anthropic as livekit_anthropic
from livekit.plugins import cartesia, deepgram, silero

from agent.anthropic_llm import make_anthropic_llm
from agent.backend_client import make_backend_client
from agent.call_limits import CallLimits
from agent.config import AgentSettings
from agent.text_mode import DISCLOSURE, open_text_call
from backend.app.redaction import install_log_redaction
from voice.admission import settle_admission
from voice.config import VoiceSettings
from voice.driver import VoiceTurnDriver, watch_call_limits
from voice.persist import storing
from voice.resilience import AsrDropoutWatch, VoiceResilience
from voice.timing import TurnTimings

logger = logging.getLogger("receptio.voice")
install_log_redaction()  # names and phone numbers never reach this process's logs


def last_user_text(chat_ctx: llm.ChatContext) -> str:
    for item in reversed(chat_ctx.items):
        if item.type == "message" and item.role == "user":
            return item.text_content or ""
    return ""


class ReceptioAgent(Agent):
    """Routes the pipeline's LLM step to the agent core and marks the timing moments."""

    def __init__(self, driver: VoiceTurnDriver, timings: TurnTimings) -> None:
        super().__init__(instructions="")  # unused: the agent core owns the prompt
        self._driver = driver
        self._timings = timings

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[str, None]:
        async for sentence in self._driver.reply(last_user_text(chat_ctx)):
            yield sentence

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        first = True
        async for frame in Agent.default.tts_node(self, text, model_settings):
            if first:
                self._timings.on_tts_first_audio(time.monotonic())
                first = False
            yield frame


def write_line(path: str):  # noqa: ANN201
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    def emit(line: str) -> None:
        logger.info(line)  # structured JSON line on stdout too
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    return emit


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


_settings = VoiceSettings()  # LiveKit's own env lookup ignores .env, so pass the values in
server = AgentServer(
    setup_fnc=prewarm,
    ws_url=_settings.livekit_url,
    api_key=_settings.livekit_api_key,
    api_secret=_settings.livekit_api_secret,
    port=_settings.worker_port,
)


def _cancel(task: "asyncio.Task[None]"):  # noqa: ANN202
    async def cancel() -> None:
        task.cancel()

    return cancel


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    voice = VoiceSettings()
    agent_settings = AgentSettings()
    if voice.deepgram_api_key is None or voice.cartesia_api_key is None:
        raise RuntimeError("DEEPGRAM_API_KEY and CARTESIA_API_KEY must be set for voice Calls")

    if agent_settings.anthropic_api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY must be set for voice Calls")
    anthropic_key = agent_settings.anthropic_api_key.get_secret_value()
    backend = make_backend_client(agent_settings)
    call = open_text_call(
        backend,
        llm=make_anthropic_llm(agent_settings),
        limits=CallLimits.from_settings(agent_settings),
        mode="voice",
    )
    started = time.monotonic()
    call_over = asyncio.Event()
    # One path: the timing line is logged and stored with the Call, like text-mode Calls.
    timings = TurnTimings(
        call.call_id, emit=storing(backend, call.call_id, write_line(voice.turn_log_path))
    )
    driver = VoiceTurnDriver(call, timings)
    asr_watch = AsrDropoutWatch()  # ticket 19: a Caller who spoke and got no text back

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # The session refuses to generate a reply without an LLM object, but ReceptioAgent.llm_node
        # never calls it: replies come from the agent core's own LLM (agent/anthropic_llm.py).
        llm=livekit_anthropic.LLM(model=agent_settings.llm_model, api_key=anthropic_key),
        stt=deepgram.STT(model=voice.asr_model, api_key=voice.deepgram_api_key.get_secret_value()),
        tts=cartesia.TTS(
            voice=voice.cartesia_voice, api_key=voice.cartesia_api_key.get_secret_value()
        ),
        turn_handling={
            "turn_detection": "vad",
            "endpointing": {"min_delay": voice.min_endpointing_delay},
            "interruption": {"enabled": True, "mode": "vad"},
            "preemptive_generation": {"enabled": False},
        },
    )

    @session.on("user_state_changed")
    def _user_state(ev: UserStateChangedEvent) -> None:  # pyright: ignore[reportUnusedFunction]
        logger.info("user %s -> %s", ev.old_state, ev.new_state)
        if ev.new_state == "speaking":
            timings.on_user_speech_start(time.monotonic())
            asr_watch.on_speech_start(time.monotonic())
        elif ev.old_state == "speaking":
            timings.on_speech_end(time.monotonic())
            asr_watch.on_speech_end(time.monotonic())

    @session.on("user_input_transcribed")
    def _asr(ev: UserInputTranscribedEvent) -> None:  # pyright: ignore[reportUnusedFunction]
        logger.info("asr final=%s chars=%d", ev.is_final, len(ev.transcript))
        if ev.is_final:
            timings.on_asr_final(time.monotonic())
            asr_watch.on_text()

    @session.on("agent_state_changed")
    def _agent_state(ev: AgentStateChangedEvent) -> None:  # pyright: ignore[reportUnusedFunction]
        logger.info("agent %s -> %s", ev.old_state, ev.new_state)

    async def finish() -> None:
        call_over.set()
        timings.close()
        try:
            driver.end()
        finally:
            # Report the Call's length so its unused minutes go back to the daily budget.
            settle_admission(backend, ctx.room.name, int(time.monotonic() - started))
            backend.close()

    ctx.add_shutdown_callback(finish)

    await session.start(ReceptioAgent(driver, timings), room=ctx.room, record=False)
    # The AI + recording disclosure is the first thing the Caller hears. It is already stored as
    # the Call's first turn by open_text_call, so it is not added to the chat context again.
    session.say(DISCLOSURE, allow_interruptions=False, add_to_chat_ctx=False)

    async def speak(text: str) -> None:
        handle = session.say(text, allow_interruptions=False, add_to_chat_ctx=False)
        await handle.wait_for_playout()

    async def close() -> None:
        # The close has been spoken in full (`speak` waits for playout). Deleting the room
        # disconnects the browser, which shows "Call ended."
        await ctx.delete_room()

    # The duration cap for a Caller who says nothing: the warning at 4:30 and the close at 5:00.
    # (A Caller who is talking hears them appended to the agent's reply instead.)
    limits_task = asyncio.create_task(watch_call_limits(driver, speak, close, stop=call_over))
    ctx.add_shutdown_callback(_cancel(limits_task))

    # Provider failures (ticket 19). UNVERIFIED against real LiveKit and providers: see
    # docs/failure-analysis.md. Only errors LiveKit has given up on (not recoverable) are handled;
    # its own retries come first.
    resilience = VoiceResilience(driver, speak, close)
    background: set[asyncio.Task[None]] = set()

    def spawn(coro: "Coroutine[Any, Any, None]") -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    @session.on("error")
    def _error(ev: ErrorEvent) -> None:  # pyright: ignore[reportUnusedFunction]
        kind = getattr(ev.error, "type", None)
        logger.warning(
            "session error %s recoverable=%s", kind, getattr(ev.error, "recoverable", None)
        )
        if getattr(ev.error, "recoverable", True):
            return
        if kind == "tts_error":
            spawn(resilience.on_tts_failure())
        elif kind == "stt_error":
            spawn(resilience.on_asr_dropout())

    async def watch_asr() -> None:
        while not call_over.is_set():
            if asr_watch.due(time.monotonic()):
                await resilience.on_asr_dropout()
            await asyncio.sleep(0.5)

    asr_task = asyncio.create_task(watch_asr())
    ctx.add_shutdown_callback(_cancel(asr_task))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("start")
    cli.run_app(server)
