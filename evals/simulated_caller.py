"""An LLM-driven simulated Caller. It plays a persona with a goal against the agent and writes its
own phrasings, so Emergency recall and completion numbers do not depend on the author of the rules
also having written what the Caller says. Scripted `turns` stay available for exact reproduction.

It talks to Anthropic through a client object with `messages.create` (the real SDK client wrapped by
`evals.metering.MeteredClient`, or a fake in tests)."""

from typing import Any

from agent.text_mode import Turn
from evals.personas import Persona

HANG_UP = "[HANG UP]"

SYSTEM_TEMPLATE = """You are playing a person who has phoned a dental clinic's AI receptionist, for
a test of that receptionist. Stay in character. You are the Caller, never the receptionist.

How you talk: {style}

What you want from this call: {goal}

Rules:
- Say one short spoken utterance per reply, as you would on the phone. No stage directions, no
  quotation marks, no narration.
- Use your own words. Give personal details (name, phone number, symptoms) only as the goal says,
  and make up nothing that contradicts the goal.
- Answer what the receptionist actually asked. If it offers times, choose one that suits the goal.
- When your goal is met, or you have given up, or you would hang up, finish with {hang_up} (you
  may say a short goodbye before it)."""


class SimulatedCaller:
    def __init__(
        self, client: Any, model: str, persona: Persona, goal: str, max_turns: int
    ) -> None:
        self._client = client
        self._model = model
        self._system = SYSTEM_TEMPLATE.format(style=persona.style, goal=goal, hang_up=HANG_UP)
        # The scenario's cap and the persona's own patience, whichever is smaller.
        self._limit = min(max_turns, persona.max_turns or max_turns)
        self._said = 0
        self.hung_up = False

    def respond(self, transcript: list[Turn]) -> str | None:
        """What the Caller says next, or None if the Caller hangs up."""
        if self.hung_up or self._said >= self._limit:
            self.hung_up = True
            return None
        response = self._client.messages.create(
            model=self._model,
            max_tokens=120,
            system=self._system,
            messages=api_messages(transcript),
        )
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        self.hung_up = HANG_UP in text
        text = text.replace(HANG_UP, "").strip()
        if not text:
            return None
        self._said += 1
        return text


def api_messages(transcript: list[Turn]) -> list[dict[str, str]]:
    """The Call from the Caller's side: the agent speaks as `user`, the Caller as `assistant`.
    Consecutive turns by one side are merged, as the API needs alternating roles."""
    out: list[dict[str, str]] = []
    for turn in transcript:
        role = "assistant" if turn.speaker == "caller" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += " " + turn.text
        else:
            out.append({"role": role, "content": turn.text})
    return out
