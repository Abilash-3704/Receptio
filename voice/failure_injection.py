"""Stand-ins for the voice providers, to trigger their failures on cue with no LiveKit and no
spend (ticket 19). They replace what the worker passes to `VoiceResilience`: a speaker that fails
like a broken synthesis stream, and a recorder of what was spoken and whether the room was closed.

What these cannot show: how the real Deepgram, Cartesia and LiveKit report a failure. That is
listed in docs/failure-analysis.md as still to verify by hand."""

from dataclasses import dataclass, field


class SynthesisFailed(Exception):
    """Stands for any failure to turn text into audio the Caller hears."""


@dataclass
class FakeSpeaker:
    """`speak` for VoiceResilience. The first `fail_times` calls raise (`None`: always)."""

    fail_times: int | None = 0
    spoken: list[str] = field(default_factory=lambda: [])
    attempts: int = 0
    closed: int = 0

    async def speak(self, text: str) -> None:
        self.attempts += 1
        if self.fail_times is None or self.fail_times > 0:
            if self.fail_times is not None:
                self.fail_times -= 1
            raise SynthesisFailed("injected: synthesis failed")
        self.spoken.append(text)

    async def close(self) -> None:
        self.closed += 1
