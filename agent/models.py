"""Model and tool factories for every mode.

Replay builds ReplayChatModel and stub Tavily clients from the cassette and
must never construct a live client. Live modes build ChatAnthropic and Tavily
clients with every setting taken from config. In every mode the model sees
the same search and fetch wrappers (agent/research/tools.py); only the inner
client differs.

In replay each run builds one model per node and one set of tools, cached on
RunContext.replay, so a node that runs twice (a retry) continues the script
instead of starting it over, and tests can read the instances the graph used.
"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import BaseTool
from langchain_tavily import TavilyExtract, TavilySearch

from agent import config
from agent.replay.replay_model import ReplayChatModel
from agent.replay.tool_stubs import make_stub_inners
from agent.research.tools import make_research_tools
from agent.state import RunContext

NODES = ("read_plate", "classifier", "research", "synthesize")


def _check(node: str | None, mode: str) -> None:
    if mode not in config.MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {config.MODES}")
    if node is not None and node not in NODES:
        raise ValueError(f"unknown model node {node!r}; expected one of {NODES}")


def max_tokens_for(node: str, mode: str) -> int:
    """Output budget for a node; synthesize has one per tier (replay uses cheap)."""
    if node == "synthesize":
        return config.MAX_TOKENS["synthesize_full" if mode == "full" else "synthesize_cheap"]
    return config.MAX_TOKENS[node]


def live_model_kwargs(node: str, mode: str) -> dict[str, Any]:
    """The exact ChatAnthropic constructor arguments for a live call.

    Never temperature, top_p or top_k (Sonnet 5 returns 400 on them), and never
    effort or thinking to Haiku, which does not support effort.
    """
    model = config.MODEL_FOR[mode][node]
    max_tokens = max_tokens_for(node, mode)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "timeout": config.model_timeout_s(max_tokens),
        "max_retries": config.CLIENT_MAX_RETRIES,
    }
    if node == "synthesize" and mode == "full" and model != config.HAIKU:
        kwargs["reasoning_effort"] = config.FULL_SYNTH_EFFORT
    return kwargs


def make_model(node: str, ctx: RunContext) -> Any:
    """The chat model for one node in this run's mode."""
    _check(node, ctx.mode)
    if ctx.mode == "replay":
        if ctx.cassette is None:
            raise ValueError("replay mode needs a cassette on the RunContext")
        cache = ctx.replay.setdefault("models", {})
        if node not in cache:
            cache[node] = ReplayChatModel(
                responses=ctx.cassette.responses_for(node),
                model=config.MODEL_FOR["replay"][node],
                max_tokens=max_tokens_for(node, "replay"),
            )
        return cache[node]
    return ChatAnthropic(**live_model_kwargs(node, ctx.mode))


def make_tools(ctx: RunContext) -> list[BaseTool]:
    """Search and fetch tools for the research agent, wrapped the same way in every mode."""
    _check(None, ctx.mode)
    if ctx.mode == "replay":
        if ctx.cassette is None:
            raise ValueError("replay mode needs a cassette on the RunContext")
        if "tools" not in ctx.replay:
            stubs = make_stub_inners(ctx.cassette)
            ctx.replay["stubs"] = stubs
            ctx.replay["tools"] = make_research_tools(
                ctx, search_inner=stubs["search"], fetch_inner=stubs["fetch"]
            )
        return list(ctx.replay["tools"])
    return make_research_tools(
        ctx,
        search_inner=TavilySearch(**config.TAVILY_SEARCH_SETTINGS),
        fetch_inner=TavilyExtract(**config.TAVILY_EXTRACT_SETTINGS),
    )
