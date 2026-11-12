"""The worker's LLM node: each piece the core hands over is its own speech segment, so speech
synthesis starts on it at once. No LiveKit server, no network: the node is driven directly."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from livekit.agents import FlushSentinel, llm

from voice.worker import ReceptioAgent


class SegmentsDriver:
    def __init__(self, segments: list[list[str]]) -> None:
        self._segments = segments
        self.heard: list[str] = []

    async def segments(self, caller_text: str) -> AsyncIterator[list[str]]:
        self.heard.append(caller_text)
        for segment in self._segments:
            yield segment


def test_every_segment_is_followed_by_a_flush() -> None:
    driver = SegmentsDriver(
        [["Let me look that up."], ["We open at nine.", "Shall I book you in?"]]
    )
    agent = ReceptioAgent(driver, timings=None)  # type: ignore[arg-type]
    chat = llm.ChatContext()
    chat.add_message(role="user", content="When do you open?")

    async def run() -> list[Any]:
        return [chunk async for chunk in agent.llm_node(chat, [], None)]  # type: ignore[arg-type]

    out = asyncio.run(run())

    kinds = ["flush" if isinstance(c, FlushSentinel) else c for c in out]
    assert kinds == [
        "Let me look that up.",
        "flush",
        "We open at nine.",
        "Shall I book you in?",
        "flush",
    ]
    assert driver.heard == ["When do you open?"]
