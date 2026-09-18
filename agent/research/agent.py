"""The research agent: create_agent with the PLAN 8.7 middleware stack.

It is built and invoked inside the research node, never added to the parent
graph, with checkpointer=False, so its transcript never reaches a parent
checkpoint and code builds the sources before anything reaches parent state.
It has no response_format: the auto strategy would pick a different path for
the replay model than for Claude.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware
from langchain_core.exceptions import ModelAPIError, ModelConnectionError, ModelRateLimitError, ModelTimeoutError
from langchain_core.tools import BaseTool

from agent import config
from agent.prompts import RESEARCH_PROMPT
from agent.research.middleware import SpendCap, ToolBudgetDone
from agent.state import RunContext

# The standard model errors worth one retry (langchain-core 1.6.3
# exceptions.py; each has is_retryable = True). Anything else, a budget stop
# included, propagates at once.
RETRY_ON = (ModelRateLimitError, ModelAPIError, ModelConnectionError, ModelTimeoutError)


@dataclass
class ResearchAgent:
    """The compiled agent and the custom middleware instances the node reads after a run."""

    graph: Any
    spend_cap: SpendCap
    budget_done: ToolBudgetDone
    limits: dict[str, int]


def research_budget_usd(ctx: RunContext) -> float:
    """The research sub budget: a cassette may set its own in replay, and only lower it live."""
    override = (ctx.caps or {}).get("research_budget_usd")
    if override is None:
        return config.RESEARCH_BUDGET_USD
    return float(override) if ctx.mode == "replay" else min(config.RESEARCH_BUDGET_USD, float(override))


def build_middleware(limits: dict[str, int], ctx: RunContext,
                     trail: list[dict[str, Any]] | None = None) -> list[Any]:
    """The PLAN 8.7 middleware list, first entry outermost."""
    return [
        ModelRetryMiddleware(max_retries=config.MODEL_RETRY_MAX, retry_on=RETRY_ON, on_failure="error"),
        ToolBudgetDone({"search": limits["search"], "fetch": limits["fetch"]}, trail),
        ModelCallLimitMiddleware(run_limit=limits["loop_guard"], exit_behavior="error"),
        ToolCallLimitMiddleware(tool_name="search", run_limit=limits["search"], exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="fetch", run_limit=limits["fetch"], exit_behavior="continue"),
        SpendCap(ctx, research_budget_usd(ctx)),
    ]


def build_research_agent(model: Any, tools: Sequence[BaseTool], limits: dict[str, int], ctx: RunContext,
                         trail: list[dict[str, Any]] | None = None) -> ResearchAgent:
    """create_agent(model, tools) with the research prompt and middleware for these limits."""
    middleware = build_middleware(limits, ctx, trail)
    graph = create_agent(
        model,
        list(tools),
        system_prompt=RESEARCH_PROMPT,
        middleware=middleware,
        checkpointer=False,
    )
    budget_done = next(m for m in middleware if isinstance(m, ToolBudgetDone))
    spend_cap = next(m for m in middleware if isinstance(m, SpendCap))
    return ResearchAgent(graph=graph, spend_cap=spend_cap, budget_done=budget_done, limits=dict(limits))
