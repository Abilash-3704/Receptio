"""A deterministic LLM for tests, CI and scenario runs: replays a fixed list of steps."""

from agent.llm import LLMTurn, Say, Step, UseTool


class ScriptExhausted(AssertionError):
    pass


class ScriptedLLM:
    """Returns the next scripted step each time it is asked. A Say's text may refer to the state
    summary, for example "{offered[0]}", so a script can quote what Availability returned."""

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self._next = 0

    def next_step(self, turn: LLMTurn) -> Step:
        if self._next >= len(self._steps):
            raise ScriptExhausted("the scripted LLM was asked for more steps than it has")
        step = self._steps[self._next]
        self._next += 1
        if isinstance(step, Say):
            return Say(step.text.format(**turn.summary))
        return UseTool(step.name, dict(step.args))
