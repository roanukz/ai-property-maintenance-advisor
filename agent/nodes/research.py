"""The research node (PLAN 8.3, 8.7; decisions 10, 16, 23, 26).

Runs the research agent once per invocation and builds sources in code, only
from the successful tool artifacts this invocation added to the RunContext
collector: never from model text and never from an error ToolMessage. A tool
cap, the loop guard and the research sub budget end research with what was
collected; only the ledger's dollar caps, the credit caps and Tavily's plan
limit end the run as budget_stopped.
"""

from __future__ import annotations

import hashlib
import time
import warnings
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent import config
from agent.ledger import BudgetExceeded
from agent.models import make_model, make_tools
from agent.nodes.synthesize import open_ledger
from agent.research.agent import build_research_agent
from agent.prompts import build_research_user_prompt
from agent.research.excerpts import Terms, cut_excerpts, excerpt_terms
from agent.research.tools import make_research_tools, save_page_text
from agent.rules.tiers import host_of
from agent.schemas import HTTP_URL
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "research"

# BudgetExceeded reasons that stop the run. "research_budget" is not one of
# them: it ends research and the run goes on to synthesize (decision 26).
BUDGET_STOP_REASONS = ("run_cap", "build_cap", "run_credit_cap", "build_credit_cap", "tavily_plan_limit")

TRAIL_KEYS = ("tool", "query", "n_results", "credits", "at", "status")
SKIPPED_NON_WEB = "skipped_non_web"
REGISTRY_TRAIL_TOOL = "registry"

# Passing durability to the checkpointer-less agent (see research()) makes
# langgraph warn that it has no effect; that is the point, so the one message
# is silenced.
warnings.filterwarnings("ignore", message="`durability` has no effect when no checkpointer is present.")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def pass_kind(state: AdvisorState, mode: str) -> str:
    """Which RESEARCH_LIMITS row applies to this invocation.

    A retry after a validation failure takes the reduced pass in cheap (and in
    replay, which mirrors cheap); full retries at the route's own caps
    (PLAN 8.4). Otherwise the row route chose: "top_up" beside the graph
    (decision 46), "main" alone.
    """
    if int(state.get("validation_failures") or 0) >= 1 and mode != "full":
        return "retry_cheap"
    chosen = state.get("research_limits")
    return chosen if chosen in config.RESEARCH_LIMITS else "main"


def task_text(state: AdvisorState) -> str:
    """The research request: identity, symptom, the confirmed code and any prior validation errors."""
    return build_research_user_prompt(identity=state.get("identity"), symptom=state.get("symptom") or "",
                                      prior_errors=state.get("validation_errors") or None,
                                      observed_code=state.get("observed_code"))


def source_id(url: str) -> str:
    return "src-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def build_sources(ctx: RunContext, artifacts: list[dict[str, Any]], trail: list[dict[str, Any]],
                  known_urls: set[str], terms: Terms | None = None) -> list[dict[str, Any]]:
    """Sources from successful tool artifacts, in call order, first appearance of each URL.

    URLs already in state (an earlier pass) are skipped. A page fetched after
    a search found it gives the search entry its text hash. Each source with
    raw text carries the verbatim excerpts synthesize shows (decision 24).
    A result whose URL is not http or https (a javascript: link, or a fetch
    of a URL typed without its scheme) is never registered; each is recorded
    in the trail (tool "registry", status "skipped_non_web") instead.
    """
    terms = terms or Terms()
    when = {id(e["artifact"]): e["at"] for e in trail if e.get("artifact") is not None}
    sources: list[dict[str, Any]] = []
    by_url: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        origin = "search" if artifact["tool"] == "search" else "extract"
        retrieved_at = when.get(id(artifact), _now())
        for result in artifact["results"]:
            url = result["url"]
            if not (isinstance(url, str) and HTTP_URL.match(url)):
                # Tool "registry", so the skip is neither a search nor a fetch in
                # `searched` or in the run record's counts.
                trail.append({"tool": REGISTRY_TRAIL_TOOL, "query": str(url), "n_results": 0, "credits": 0,
                              "at": retrieved_at, "status": SKIPPED_NON_WEB, "artifact": None, "pages": {}})
                continue
            raw = result.get("raw_content")
            sha = save_page_text(ctx.pages_dir, raw) if raw else None
            if url in known_urls:
                continue
            if url in by_url:
                if by_url[url]["text_sha256"] is None and sha:
                    by_url[url]["text_sha256"] = sha
                    by_url[url]["excerpts"] = cut_excerpts(raw, terms, config.SEARCH_RESULT_MAX_CHARS)
                continue
            source = {
                "source_id": source_id(url),
                "url": url,
                "host": host_of(url),
                "title": result.get("title") or None,
                "retrieved_at": retrieved_at,
                "origin": origin,
                "text_sha256": sha,
                "snippet": result.get("content") or None,
                "excerpts": cut_excerpts(raw, terms, config.SEARCH_RESULT_MAX_CHARS),
            }
            by_url[url] = source
            sources.append(source)
    return sources


def research(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Run one research pass and return only the keys it writes."""
    started = time.monotonic()
    ctx = runtime.context
    ledger = open_ledger(ctx)
    cost_before = ledger.run_total(ctx.run_id)
    credits_before = ledger.run_credits(ctx.run_id)
    attempt = int(state.get("research_attempts") or 0) + 1
    limits = dict(config.RESEARCH_LIMITS[pass_kind(state, ctx.mode)])

    terms = excerpt_terms(state.get("identity"), state.get("observed_code"), state.get("symptom"))
    trail: list[dict[str, Any]] = []
    base = make_tools(ctx)  # replay: the run's shared stubs, so call counts span passes
    tools = make_research_tools(ctx, search_inner=base[0].inner, fetch_inner=base[1].inner,
                                terms=terms, trail=trail)
    agent = build_research_agent(make_model(NODE, ctx), tools, limits, ctx, trail)

    start = len(ctx.collector)
    out: dict[str, Any] = {}
    try:
        # The agent has no checkpointer, but it inherits the parent's
        # durability="sync" through the runnable config, and langgraph 1.2.11
        # then waits on a checkpoint write that never started. "exit" writes
        # nothing, which is what an agent without a checkpointer does anyway.
        agent.graph.invoke({"messages": [HumanMessage(task_text(state))]}, durability="exit")
    except ModelCallLimitExceededError:
        pass  # the loop guard: research ends with what was collected
    except BudgetExceeded as exc:
        if exc.reason in BUDGET_STOP_REASONS:
            out["status"] = "budget_stopped"
            out["stop_reason"] = exc.reason
        elif exc.reason != "research_budget":
            raise

    known = {s["url"] for s in state.get("sources") or []}
    out["sources"] = build_sources(ctx, ctx.collector[start:], trail, known, terms)
    out["search_trail"] = [{k: e[k] for k in TRAIL_KEYS} for e in trail]
    out["cost_usd"] = ledger.run_total(ctx.run_id) - cost_before
    out["tavily_credits"] = ledger.run_credits(ctx.run_id) - credits_before
    out["research_attempts"] = attempt
    out.update(latency_entry(state, NODE, time.monotonic() - started))
    return out
