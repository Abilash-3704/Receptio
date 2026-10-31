"""Failure injection (ticket 19): fake wrappers that make one dependency fail on cue, so each
failure mode is deterministic and costs nothing. No provider is ever called: `FlakyBackend`
wraps the clinic backend client (in tests, the in-process app) and `FailingLLM` wraps any `LLM`.
The voice-layer equivalents (speech recognition, speech synthesis, disconnect) are in
voice/failure_injection.py.

Nothing here is imported by production code. The agent core sees only the interfaces it already
has, so what is exercised is the real error handling."""

import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from agent.llm import LLM, LLMError, LLMTurn, Step

BackendMode = Literal[
    "timeout",  # the request never reaches the backend: the client gives up (a slow backend)
    "timeout_after_commit",  # the backend does the work, then the reply is lost: client times out
    "unavailable",  # the backend answers 503
    "connect_error",  # the backend cannot be reached at all
]


@dataclass
class _Fault:
    method: str
    path: re.Pattern[str]
    mode: BackendMode
    remaining: int | None  # None: every matching request fails until `heal`


class FlakyBackend:
    """Stands in for the backend `httpx.Client`. Requests pass through untouched unless a fault
    matches (HTTP method and a regular expression over the URL path). `requests` lists every
    request that was made, whether it failed or not."""

    def __init__(self, inner: httpx.Client) -> None:
        self._inner = inner
        self._faults: list[_Fault] = []
        self.requests: list[tuple[str, str]] = []
        self.injected = 0

    def fail(
        self, method: str, path: str, mode: BackendMode, times: int | None = 1
    ) -> "FlakyBackend":
        """Make the next `times` matching requests fail (`None`: until `heal`)."""
        self._faults.append(_Fault(method.upper(), re.compile(path), mode, times))
        return self

    def heal(self) -> None:
        self._faults.clear()

    def count(self, method: str, path: str) -> int:
        pattern = re.compile(path)
        return sum(1 for m, p in self.requests if m == method.upper() and pattern.search(p))

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        method = method.upper()
        self.requests.append((method, url))
        fault = next((f for f in self._faults if f.method == method and f.path.search(url)), None)
        if fault is None:
            return self._inner.request(method, url, **kwargs)
        if fault.remaining is not None:
            fault.remaining -= 1
            if fault.remaining <= 0:
                self._faults.remove(fault)
        self.injected += 1
        if fault.mode == "timeout":
            raise httpx.ReadTimeout(f"injected: {method} {url} timed out")
        if fault.mode == "connect_error":
            raise httpx.ConnectError(f"injected: {method} {url} unreachable")
        if fault.mode == "timeout_after_commit":
            self._inner.request(method, url, **kwargs)  # committed; the answer is lost
            raise httpx.ReadTimeout(f"injected: {method} {url} timed out after the work was done")
        return httpx.Response(
            503, json={"detail": "injected: unavailable"}, request=httpx.Request(method, url)
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)


class FailingLLM:
    """Wraps an `LLM`: the first `fail_times` requests raise `LLMError` (`None`: always), then the
    wrapped LLM answers. A failed request does not consume a scripted step."""

    def __init__(self, inner: LLM | None = None, fail_times: int | None = 1) -> None:
        self._inner = inner
        self._remaining = fail_times
        self.requests = 0
        self.failures = 0

    def next_step(self, turn: LLMTurn) -> Step:
        self.requests += 1
        if self._remaining is None or self._remaining > 0:
            if self._remaining is not None:
                self._remaining -= 1
            self.failures += 1
            raise LLMError("injected: the LLM provider failed")
        assert self._inner is not None, "FailingLLM has no LLM to fall back to"
        return self._inner.next_step(turn)
