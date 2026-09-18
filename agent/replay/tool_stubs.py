"""Replay stand ins for the Tavily clients (PLAN 8.7 and 8.10).

A stub plays the part of TavilySearch or TavilyExtract, not of the tool the
model calls: it returns, raises or gives back exactly what the cassette
recorded, in order, and counts its calls. The real search and fetch wrappers
in agent/research/tools.py sit around it, so replay runs the same failure
mapping, plan limit stop, ledger credit holds and collector writes as a live
run.

Recorded result shapes (cassette "research.tool_results", tool key removed):

    {"query"|"url", "results": [...], "failed_results"?, "credits"?}
        a Tavily response; "credits" becomes {"usage": {"credits": n}}
    {"error": "Error 500: ..."}   the {"error": ...} dict Tavily's tools return
    {"string": "..."}             a bare string return
    {"raise": "timeout"}          the call never answers (the wrapper's timeout)
"""

from __future__ import annotations

import copy
import threading
from typing import Any

from langchain_core.tools import BaseTool

from agent.replay.replay_model import ReplayExhausted
from agent.research.tools import make_research_tools
from agent.state import RunContext

RAISE_KINDS = ("timeout",)


def _call_arg(input: dict[str, Any]) -> str | None:
    """The query a search was sent, or the URL a fetch was sent."""
    if "query" in input:
        return str(input["query"])
    urls = input.get("urls")
    return str(urls[0]) if isinstance(urls, list) and urls else None


class StubInner:
    """Hands out one tool's recorded results and counts calls.

    A call gets the first unused result recorded for its own query or URL, and
    falls back to the next unused result in order when none matches. Parallel
    tool calls reach the stub in no fixed order, so matching by position alone
    would hand one call another call's results (Phase 5 finding).
    """

    def __init__(self, name: str, results: list[dict[str, Any]]) -> None:
        self.name = name
        self.results = list(results)
        self.calls = 0
        self.received: list[dict[str, Any]] = []
        self._used: set[int] = set()
        self._lock = threading.Lock()

    def _take(self, input: dict[str, Any]) -> dict[str, Any]:
        arg = _call_arg(input)
        with self._lock:
            unused = [i for i in range(len(self.results)) if i not in self._used]
            if not unused:
                raise ReplayExhausted(f"no recorded {self.name} result for call {self.calls + 1}")
            key = "query" if self.name == "search" else "url"
            matching = [i for i in unused if arg is not None and self.results[i].get(key) == arg]
            index = (matching or unused)[0]
            self._used.add(index)
            self.calls += 1
            return copy.deepcopy(self.results[index])

    def invoke(self, input: dict[str, Any]) -> Any:
        recorded = self._take(input)
        self.received.append(dict(input))
        if "raise" in recorded:
            if recorded["raise"] != "timeout":
                raise ValueError(f"unknown recorded raise {recorded['raise']!r}")
            raise TimeoutError(f"recorded {self.name} timeout")
        if "string" in recorded:
            return recorded["string"]
        if "error" in recorded:
            return {"error": recorded["error"]}
        response: dict[str, Any] = {"results": recorded.get("results", [])}
        if "failed_results" in recorded:
            response["failed_results"] = recorded["failed_results"]
        if "credits" in recorded:
            response["usage"] = {"credits": recorded["credits"]}
        return response


def make_stub_inners(cassette: Any) -> dict[str, StubInner]:
    """One stub per tool, each with its own recorded results."""
    return {name: StubInner(name, cassette.tool_results_for(name)) for name in ("search", "fetch")}


def make_stub_tools(cassette: Any, ctx: RunContext) -> list[BaseTool]:
    """The replay search and fetch tools: the real wrappers around fresh stubs.

    Each returned tool exposes its stub as `.inner` and the stub's call count
    as `.calls`. Graph code gets them through models.make_tools, which builds
    them once per run.
    """
    inners = make_stub_inners(cassette)
    return make_research_tools(ctx, search_inner=inners["search"], fetch_inner=inners["fetch"])
