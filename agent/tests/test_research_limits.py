"""Research limits (PLAN section 5 row "Research limits"; decisions 10, 16, 26).

Every test drives the research node with a ReplayChatModel script and the
replay stub inners inside the real wrappers, through a small StateGraph that
stands in for the rest of the graph: research, then a fake synthesize, or a
fake render on budget_stopped (the real gather rule of PLAN 8.3, whose
validate label the fake render stands in for). test_graph_budget runs the
same stops through the real graph. Each test names
the mutation that turns it red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent import config
from agent.ledger import Ledger
from agent.nodes.gather import next_after_gather
from agent.nodes.research import research
from agent.state import AdvisorState, RunContext, check_json_native

SMALL_USAGE = {"input_tokens": 1_000, "output_tokens": 100}
IDENTITY = {"manufacturer": "Aquarest", "model": "AR-500", "serial": "", "manufacture_date": ""}


# ---------------------------------------------------------------------------
# Shared fakes (also imported by test_research_sources.py and test_research_tools.py)
# ---------------------------------------------------------------------------


class FakeCassette:
    """The two cassette methods models.py and the stubs read."""

    def __init__(self, script: list[dict], search: list[dict] = (), fetch: list[dict] = ()) -> None:
        self._script = list(script)
        self._tools = {"search": list(search), "fetch": list(fetch)}

    def responses_for(self, node: str) -> list[dict]:
        return list(self._script) if node == "research" else []

    def tool_results_for(self, tool: str) -> list[dict]:
        return list(self._tools[tool])


def make_ctx(tmp_path: Path, cassette: FakeCassette, caps: dict | None = None) -> RunContext:
    return RunContext(run_id="run-1", mode="replay", ledger_path=tmp_path / "ledger" / "replay.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages", cassette=cassette, caps=caps)


def call(tool: str, usage: dict | None = None, **args: Any) -> dict:
    return {"message": {"content": "", "tool_calls": [{"name": tool, "args": args}],
                        "usage": usage or SMALL_USAGE}}


def answer(text: str, usage: dict | None = None) -> dict:
    return {"message": {"content": text, "tool_calls": [], "usage": usage or SMALL_USAGE}}


def found(n: int, credits: int = 1, raw: str | None = None) -> dict:
    return {"results": [{"url": f"https://example.com/doc{n}", "title": f"Doc {n}", "content": f"snippet {n}",
                         "raw_content": raw if raw is not None else f"AR-500 manual page {n}", "score": 0.5}],
            "credits": credits}


def page(url: str) -> dict:
    return {"results": [{"url": url, "raw_content": f"Full text of {url} for the AR-500."}], "credits": 1}


def base_state(**extra: Any) -> dict:
    state = {"run_id": "run-1", "mode": "replay", "symptom": "heater will not start", "identity": IDENTITY,
             "observed_code": None, "route": ["research"], "sources": [], "search_trail": [],
             "validation_errors": [], "validation_failures": 0, "research_attempts": 0,
             "status": "running", "cost_usd": 0.0, "tavily_credits": 0, "latency": {}}
    state.update(extra)
    return state


def run_node(ctx: RunContext, **state: Any) -> dict:
    """Call the research node directly and check its delta is JSON native."""
    out = research(base_state(**state), Runtime(context=ctx))
    check_json_native(out, "delta")
    return out


def run_graph(ctx: RunContext, **state: Any) -> dict:
    """research, then fake synthesize, or fake render when budget_stopped; returns final state."""
    visited: list[str] = []

    def synthesize(s: AdvisorState, runtime: Runtime[RunContext]) -> dict:
        visited.append("synthesize")
        return {"status": "ok", "latency": {"synthesize": 0.0}}

    def render(s: AdvisorState, runtime: Runtime[RunContext]) -> dict:
        visited.append("render")
        return {"latency": {"render": 0.0}}

    builder = StateGraph(AdvisorState, context_schema=RunContext)
    builder.add_node("research", research)
    builder.add_node("synthesize", synthesize)
    builder.add_node("render", render)
    builder.add_edge(START, "research")
    # The real gather rule; its "validate" (the budget stop brief) stands in as render here.
    builder.add_conditional_edges("research", next_after_gather, {"synthesize": "synthesize", "validate": "render"})
    builder.add_edge("synthesize", "render")
    builder.add_edge("render", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    result = graph.invoke(base_state(**state),
                          {"configurable": {"thread_id": "t1"}, "recursion_limit": config.RECURSION_LIMIT},
                          context=ctx, durability="sync", version="v2")
    final = result.value if hasattr(result, "value") else result
    check_json_native(final, "state")
    final["_visited"] = visited
    return final


def research_model(ctx: RunContext):
    return ctx.replay["models"]["research"]


def stub(ctx: RunContext, tool: str):
    return ctx.replay["stubs"][tool]


def trail_statuses(trail: list[dict]) -> list[tuple[str, str]]:
    return [(e["tool"], e["status"]) for e in trail]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sixth_search_is_blocked_and_run_continues(tmp_path: Path) -> None:
    """Mutation research_search_cap_off_by_one: run_limit=limits["search"] + 1 for
    the search ToolCallLimitMiddleware (the stub is called 6 times). Mutation
    research_search_cap_errors: exit_behavior="error" for search (the node
    raises ToolCallLimitExceededError)."""
    searches = 7
    script = [call("search", query=f"q{i}") for i in range(1, searches + 1)] + [answer("done")]
    cassette = FakeCassette(script, search=[found(i) for i in range(1, searches + 1)])
    ctx = make_ctx(tmp_path, cassette)

    out = run_node(ctx)

    assert stub(ctx, "search").calls == config.RESEARCH_LIMITS["main"]["search"] == 5
    assert "status" not in out and "stop_reason" not in out
    assert [s["url"] for s in out["sources"]] == [f"https://example.com/doc{i}" for i in range(1, 6)]
    assert trail_statuses(out["search_trail"]) == [("search", "ok")] * 5 + [("search", "blocked")] * 2
    assert [e["query"] for e in out["search_trail"]][5:] == ["q6", "q7"]
    assert len(research_model(ctx).received) == searches + 1  # the model carried on to its answer
    assert out["tavily_credits"] == 5


def test_research_full_allowance_is_not_budget_stopped(tmp_path: Path) -> None:
    """Mutation research_loop_guard_below_tools: ModelCallLimitMiddleware
    run_limit=limits["search"] + limits["fetch"] - 1 (the eighth call trips
    the guard, so the third fetch never runs). Mutation
    research_tool_budget_done_never_ends: the ToolBudgetDone check never fires
    (the model is called a ninth time)."""
    limits = config.RESEARCH_LIMITS["main"]
    urls = [f"https://example.com/doc{i}" for i in range(1, 6)]
    script = ([call("search", query=f"q{i}") for i in range(1, 6)]
              + [call("fetch", url=u) for u in urls[:3]]
              + [answer("should never be requested")])
    cassette = FakeCassette(script, search=[found(i) for i in range(1, 6)], fetch=[page(u) for u in urls[:3]])
    ctx = make_ctx(tmp_path, cassette)

    final = run_graph(ctx)

    assert stub(ctx, "search").calls == limits["search"] and stub(ctx, "fetch").calls == limits["fetch"]
    assert len(research_model(ctx).received) == limits["search"] + limits["fetch"]
    assert final["status"] == "ok" and final.get("stop_reason") is None
    assert final["_visited"] == ["synthesize", "render"]
    assert [s["url"] for s in final["sources"]] == urls
    assert all(e["status"] == "ok" for e in final["search_trail"]) and len(final["search_trail"]) == 8
    assert final["research_attempts"] == 1 and "research" in final["latency"]


def test_loop_guard_keeps_collected_sources(tmp_path: Path) -> None:
    """Mutation research_loop_guard_is_budget_stop: the node maps
    ModelCallLimitExceededError to status budget_stopped (the run renders
    without synthesize). Mutation research_loop_guard_uncaught: the except
    clause for ModelCallLimitExceededError is removed (the run raises)."""
    guard = config.RESEARCH_LIMITS["main"]["loop_guard"]
    # Five searches, then the model keeps asking for more (each is blocked)
    # and never fetches, so only the loop guard can stop it.
    script = [call("search", query=f"q{i}") for i in range(1, guard + 4)]
    cassette = FakeCassette(script, search=[found(i) for i in range(1, 6)])
    ctx = make_ctx(tmp_path, cassette)

    final = run_graph(ctx)

    assert len(research_model(ctx).received) == guard
    assert [s["url"] for s in final["sources"]] == [f"https://example.com/doc{i}" for i in range(1, 6)]
    assert final["status"] == "ok" and final.get("stop_reason") is None
    assert final["status"] != "no_reliable_answer"
    assert final["_visited"] == ["synthesize", "render"]
    assert [e["status"] for e in final["search_trail"]].count("blocked") == guard - 5


@pytest.mark.parametrize("caps, expected_searches", [(None, 4), ({"research_budget_usd": 0.03}, 2)],
                         ids=["config_budget", "cassette_budget"])
def test_research_budget_ends_research_not_run(tmp_path: Path, caps: dict | None,
                                               expected_searches: int) -> None:
    """Mutation research_budget_is_budget_stop: "research_budget" added to
    BUDGET_STOP_REASONS (the run renders without synthesize). Mutation
    research_budget_check_off: SpendCap never compares against the research
    budget (research runs on until the run cap stops the run). Mutation
    research_budget_ignores_cassette: the caps override is dropped (the
    cassette case makes 4 searches)."""
    heavy = {"input_tokens": 20_000, "output_tokens": 250}  # $0.02125 a call at replay prices
    script = [call("search", usage=heavy, query=f"q{i}") for i in range(1, 11)]
    cassette = FakeCassette(script, search=[found(i) for i in range(1, 11)])
    ctx = make_ctx(tmp_path, cassette, caps=caps)

    final = run_graph(ctx)

    assert stub(ctx, "search").calls == expected_searches
    assert final["status"] == "ok" and final.get("stop_reason") is None
    assert final["_visited"] == ["synthesize", "render"]
    assert len(final["sources"]) == expected_searches
    ledger = Ledger(ctx.ledger_path)
    assert [r["note"] for r in ledger.rows("run-1") if r["kind"] == "stop"] == ["research_budget"]
    # Like the run cap, the check is made before each call on its reservation,
    # so the last call starts under the budget and may end above it.
    budget = (caps or {}).get("research_budget_usd", config.RESEARCH_BUDGET_USD)
    last_call = heavy["input_tokens"] / 1e6 * config.REPLAY_PRICES["input"] + \
        heavy["output_tokens"] / 1e6 * config.REPLAY_PRICES["output"]
    assert ledger.run_total("run-1") - last_call < budget
    assert final["cost_usd"] == pytest.approx(ledger.run_total("run-1"))


@pytest.mark.parametrize("kind", ["credit_cap", "http_432"])
def test_credit_cap_and_http_432_stop_the_run(tmp_path: Path, kind: str) -> None:
    """Mutation tools_plan_limit_is_ordinary_error (existing): 432 becomes an
    ordinary tool error and the run synthesizes. Mutation
    research_credit_cap_not_a_stop: "run_credit_cap" removed from
    BUDGET_STOP_REASONS (the node re-raises)."""
    if kind == "credit_cap":
        # Five searches Tavily bills at 2 credits each reach the run cap of 10;
        # the fetch that follows cannot reserve its credit.
        script = [call("search", query=f"q{i}") for i in range(1, 6)] + [
            call("fetch", url="https://example.com/doc1"), answer("done")]
        cassette = FakeCassette(script, search=[found(i, credits=2) for i in range(1, 6)],
                                fetch=[page("https://example.com/doc1")])
        reason, kept, credits = "run_credit_cap", 5, config.RUN_CREDIT_CAP
    else:
        script = [call("search", query="q1"), call("search", query="q2"), answer("done")]
        cassette = FakeCassette(script, search=[found(1), {"error": "Error 432: plan usage limit exceeded"}])
        reason, kept, credits = "tavily_plan_limit", 1, 1
    ctx = make_ctx(tmp_path, cassette)

    final = run_graph(ctx)

    assert final["status"] == "budget_stopped" and final["stop_reason"] == reason
    assert final["_visited"] == ["render"]
    assert len(final["sources"]) == kept  # what was collected before the stop survives
    assert final["tavily_credits"] == credits
    assert final["search_trail"][-1]["status"] == reason
    ledger = Ledger(ctx.ledger_path)
    assert [r["note"] for r in ledger.rows("run-1") if r["kind"] == "stop"][-1].startswith(reason)


def test_retry_task_carries_prior_errors(tmp_path: Path) -> None:
    """A retry pass searches knowing why the last draft failed (contract: research
    reads validation_errors).

    Mutation research_no_prior_errors: task_text passes prior_errors=None."""
    from agent.prompts import PRIOR_ERRORS_HEADING

    error = "observed_code_no_match: no candidate documents the observed code 'FLO'"
    cassette = FakeCassette([answer("done"), answer("done")])
    ctx = make_ctx(tmp_path, cassette)
    run_node(ctx)
    run_node(ctx, validation_errors=[error], validation_failures=1, research_attempts=1)
    first, retry = ("\n".join(str(m.content) for m in msgs) for msgs in research_model(ctx).received)
    assert PRIOR_ERRORS_HEADING not in first
    assert PRIOR_ERRORS_HEADING in retry and f"- {error}" in retry
