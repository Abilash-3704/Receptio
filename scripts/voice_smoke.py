"""Synthetic-Caller smoke test for the voice loop. Spends a little real provider money.

Starts nothing itself: LiveKit server and the voice worker (`python -m voice.worker start`) must
already be running, with the provider keys in the environment or .env. This script:
  1. synthesises each phrase with Cartesia (and reports Cartesia's time to first audio),
  2. joins a fresh room as a Caller and publishes that audio as the microphone,
  3. measures, on its own clock, the time from the end of the published speech to the first
     audible agent audio (an outside view to compare with the worker's turn log).

    python scripts/voice_smoke.py "What are your opening hours?" "Do you do teeth whitening?"

It never prints key values. The first agent audio heard is the disclosure."""

import asyncio
import secrets
import sys
import time
from pathlib import Path

import numpy as np
from livekit import api, rtc
from livekit.agents.utils import http_context
from livekit.plugins import cartesia

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.config import VoiceSettings  # noqa: E402

SAMPLE_RATE = 24000
AUDIBLE_RMS = 300  # int16 RMS above which a frame counts as sound
QUIET_SECONDS = 1.5  # this long without sound = the agent has finished speaking
TURN_TIMEOUT = 30.0
PEAK = [0.0]


async def synthesise(settings: VoiceSettings, text: str) -> tuple[bytes, float]:
    assert settings.cartesia_api_key is not None
    tts = cartesia.TTS(
        voice=settings.cartesia_voice,
        api_key=settings.cartesia_api_key.get_secret_value(),
        sample_rate=SAMPLE_RATE,
    )
    start = time.monotonic()
    first: float | None = None
    pcm = bytearray()
    async with tts.synthesize(text) as stream:
        async for audio in stream:
            if first is None:
                first = time.monotonic() - start
            pcm += bytes(audio.frame.data)
    await tts.aclose()
    assert first is not None
    return bytes(pcm), first


def rms(frame: rtc.AudioFrame) -> float:
    samples = np.frombuffer(frame.data, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


async def main(phrases: list[str]) -> None:
    settings = VoiceSettings()
    room_name = f"smoke-{secrets.token_hex(4)}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"smoke-{secrets.token_hex(3)}")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    spoken_pcm: list[tuple[str, bytes, float]] = []
    for phrase in phrases:
        pcm, first = await synthesise(settings, phrase)
        spoken_pcm.append((phrase, pcm, first))
        print(f"cartesia first audio {first * 1000:.0f} ms for {len(phrase)} chars")

    room = rtc.Room()
    last_sound = [0.0]
    first_sound = [0.0]  # monotonic time of the first audible agent frame since last reset
    heard_any = asyncio.Event()

    async def listen(track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1):
            PEAK[0] = max(PEAK[0], rms(ev.frame))
            if rms(ev.frame) > AUDIBLE_RMS:
                now = time.monotonic()
                if first_sound[0] == 0.0:
                    first_sound[0] = now
                last_sound[0] = now
                heard_any.set()

    tasks: list[asyncio.Task[None]] = []

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, *_: object) -> None:
        print("subscribed to a remote track:", track.kind, flush=True)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(listen(track)))

    await room.connect(settings.livekit_url, token)
    source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=100)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )

    async def wait_for_agent_to_finish() -> None:
        await asyncio.wait_for(heard_any.wait(), TURN_TIMEOUT)
        while time.monotonic() - last_sound[0] < QUIET_SECONDS:
            await asyncio.sleep(0.1)

    try:
        try:
            await wait_for_agent_to_finish()
        except TimeoutError:
            print(f"no agent audio; peak rms heard {PEAK[0]:.0f}", flush=True)
            raise
        print("disclosure heard and finished")
        for phrase, pcm, _ in spoken_pcm:
            heard_any.clear()
            first_sound[0] = 0.0
            frame_bytes = SAMPLE_RATE // 100 * 2  # 10 ms of int16 mono
            for i in range(0, len(pcm), frame_bytes):
                chunk = pcm[i : i + frame_bytes].ljust(frame_bytes, b"\0")
                await source.capture_frame(rtc.AudioFrame(chunk, SAMPLE_RATE, 1, frame_bytes // 2))
            silence = rtc.AudioFrame(b"\0" * frame_bytes, SAMPLE_RATE, 1, frame_bytes // 2)
            speech_end = time.monotonic()  # capture_frame paces to real time
            for _ in range(150):  # 1.5 s of silence so the VAD sees the end of the turn
                await source.capture_frame(silence)
            await asyncio.wait_for(heard_any.wait(), TURN_TIMEOUT)
            print(
                f"turn {phrase!r}: first agent audio "
                f"{(first_sound[0] - speech_end) * 1000:.0f} ms after the end of speech"
                " (outside view; negative if the agent answered during the trailing silence)"
            )
            await wait_for_agent_to_finish()
    finally:
        await room.disconnect()
        for t in tasks:
            t.cancel()


if __name__ == "__main__":

    async def run() -> None:
        async with http_context.open():  # plugins outside the worker need an http session
            await main(sys.argv[1:] or ["What are your opening hours?"])

    asyncio.run(run())
