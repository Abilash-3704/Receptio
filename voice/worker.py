# pyright: basic
"""LiveKit agent worker: joins each new room and runs a voice Call on the shared agent core.

Pipeline (livekit-agents 1.8.10): Silero VAD -> Deepgram streaming ASR -> `agent.text_mode.TextCall`
(the same core as text mode, with the real Claude LLM) -> Cartesia streaming TTS. LiveKit owns
transport, VAD, turn detection and barge-in; the agent core never sees LiveKit.

Run (needs `uv sync --extra voice` and the provider keys in the environment or .env):
    python -m voice.worker dev        # local; `start` for production-style logging

Preemptive generation is switched off: the core mutates Call state (holds, Escalations) when it
answers, so it must run once per Caller turn, never speculatively on interim transcripts."""

import logging
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterable
from pathlib import Path

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
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import cartesia, deepgram, silero

from agent.anthropic_llm import make_anthropic_llm
from agent.backend_client import make_backend_client
from agent.config import AgentSettings
from agent.text_mode import DISCLOSURE, open_text_call
from voice.config import VoiceSettings
from voice.driver import VoiceTurnDriver
from voice.timing import TurnTimings

logger = logging.getLogger("receptio.voice")


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


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    voice = VoiceSettings()
    agent_settings = AgentSettings()
    if voice.deepgram_api_key is None or voice.cartesia_api_key is None:
        raise RuntimeError("DEEPGRAM_API_KEY and CARTESIA_API_KEY must be set for voice Calls")

    backend = make_backend_client(agent_settings)
    call = open_text_call(backend, llm=make_anthropic_llm(agent_settings))
    timings = TurnTimings(call.call_id, emit=write_line(voice.turn_log_path))
    driver = VoiceTurnDriver(call, timings)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
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
        if ev.new_state == "speaking":
            timings.on_user_speech_start(time.monotonic())
        elif ev.old_state == "speaking":
            timings.on_speech_end(time.monotonic())

    @session.on("user_input_transcribed")
    def _asr(ev: UserInputTranscribedEvent) -> None:  # pyright: ignore[reportUnusedFunction]
        if ev.is_final:
            timings.on_asr_final(time.monotonic())

    @session.on("agent_state_changed")
    def _agent_state(ev: AgentStateChangedEvent) -> None:  # pyright: ignore[reportUnusedFunction]
        logger.debug("agent %s -> %s", ev.old_state, ev.new_state)

    async def finish() -> None:
        timings.close()
        try:
            driver.end()
        finally:
            backend.close()

    ctx.add_shutdown_callback(finish)

    await session.start(ReceptioAgent(driver, timings), room=ctx.room, record=False)
    # The AI + recording disclosure is the first thing the Caller hears. It is already stored as
    # the Call's first turn by open_text_call, so it is not added to the chat context again.
    session.say(DISCLOSURE, allow_interruptions=False, add_to_chat_ctx=False)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("start")
    cli.run_app(server)
