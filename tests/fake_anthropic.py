"""A fake Anthropic client for the eval tests: no network, no key. It answers like
`client.messages.create` does (content blocks plus token usage) and records every request."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

Responder = Callable[[dict[str, Any]], str]


class FakeAnthropic:
    def __init__(
        self, responder: Responder, input_tokens: int = 1000, output_tokens: int = 20
    ) -> None:
        self._responder = responder
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        text = self._responder(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(
                input_tokens=self._input_tokens, output_tokens=self._output_tokens
            ),
        )


def replies(*texts: str) -> Responder:
    """A responder that gives the texts in order, then keeps hanging up."""
    queue = list(texts)
    return lambda _request: queue.pop(0) if queue else "[HANG UP]"
