"""Text-mode Call: the seam used by tests and evals. Never imports LiveKit."""

from dataclasses import dataclass

import httpx

DISCLOSURE = (
    "Hello, you've reached the clinic's receptionist. I'm an AI assistant, "
    "and this call may be recorded. How can I help you today?"
)


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


class TextCall:
    def __init__(self, backend: httpx.Client, call_id: int) -> None:
        self._backend = backend
        self.call_id = call_id
        self.transcript: list[Turn] = []

    def say(self, speaker: str, text: str) -> None:
        self._backend.post(
            f"/calls/{self.call_id}/turns", json={"speaker": speaker, "text": text}
        ).raise_for_status()
        self.transcript.append(Turn(speaker, text))

    def end(self) -> None:
        self._backend.post(f"/calls/{self.call_id}/end").raise_for_status()


def open_text_call(backend: httpx.Client) -> TextCall:
    response = backend.post("/calls")
    response.raise_for_status()
    call = TextCall(backend, int(response.json()["id"]))
    call.say("agent", DISCLOSURE)
    return call
