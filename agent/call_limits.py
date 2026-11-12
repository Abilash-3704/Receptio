"""The Call duration cap, decided in code: a warning near the end that offers a Callback Request,
then a spoken close. The Call is never cut off without a word.

The values are configuration (`AgentSettings.call_max_seconds`, `call_warning_seconds`); the
spec's are 5 minutes and a warning at 4:30. The same limits drive text mode and voice."""

from dataclasses import dataclass

from agent.config import AgentSettings

DURATION_REASON = "the Call reached its time limit and the caller took the offered Callback Request"


@dataclass(frozen=True)
class CallLimits:
    max_seconds: int = 300
    warning_seconds: int = 270

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> "CallLimits":
        return cls(settings.call_max_seconds, settings.call_warning_seconds)

    def warning_speech(self) -> str:
        left = max(self.max_seconds - self.warning_seconds, 0)
        return (
            f"Just so you know, this demo call has a time limit and will end in {left} seconds. "
            "If you'd like, I can have our staff call you back instead. Would you like that?"
        )


CLOSING_SPEECH = (
    "I'm sorry, we've reached the time limit for this demo call, so I have to end it here. "
    "Thank you for calling, and please call again."
)
