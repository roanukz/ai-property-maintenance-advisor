"""The search and fetch wrappers (PLAN 8.7) around replay stubs and hung fakes, offline.

Replay stubs sit inside the same wrappers a live run uses, so these tests
exercise the live failure mapping. Each test names the mutation that turns it
red.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, ToolMessage

from agent import config
from agent.ledger import BudgetExceeded, Ledger
from agent.replay.replay_model import ReplayChatModel
from agent.replay.tool_stubs import make_stub_tools
from agent.research.tools import make_research_tools
from agent.state import RunContext

USAGE = {"input_tokens": 1000, "output_tokens": 100}


class FakeCassette:
    def __init__(self, search=(), fetch=()):
        self._tools = {"search": list(search), "fetch": list(fetch)}

    def tool_results_for(self, tool):
        return list(self._tools[tool])


def run_ctx(tmp_path: Path, caps: dict | None = None) -> RunContext:
    Ledger(tmp_path / "ledger.sqlite", create=True)
    return RunContext(run_id="r1", mode="replay", ledger_path=tmp_path / "ledger.sqlite",
                      registry_path=tmp_path / "r", graph_path=tmp_path / "g",
                      pages_dir=tmp_path / "p", caps=caps)


def call(tool, **args):
    return {"message": {"content": "", "tool_calls": [{"name": tool, "args": args}], "usage": USAGE}}


def answer(text):
    return {"message": {"content": text, "tool_calls": [], "usage": USAGE}}


def found(n: int) -> dict:
    return {"results": [{"url": f"https://example.com/{n}", "title": f"Page {n}", "content": "c",
                         "raw_content": "r", "score": 0.5}], "credits": 1}


def agent_for(responses):
    m = ReplayChatModel(responses=responses, model=config.HAIKU, max_tokens=config.MAX_TOKENS["research"])
    return lambda tools: create_agent(m, tools).invoke({"messages": [HumanMessage("find it")]})


def open_holds(ledger: Ledger) -> int:
    rows = ledger.rows()
    return sum(r["kind"] == "reserve" for r in rows) - sum(r["kind"] == "release" for r in rows)


# Each non budget failure: (recorded result, credits charged for it).
FAILURES = {
    "error_500": ({"error": "Error 500: upstream"}, 0),
    "string_return": ({"string": "No search results found for 'q'."}, config.TAVILY_CREDITS["search_basic"]),
    "timeout": ({"raise": "timeout"}, config.TAVILY_TIMEOUT_CREDITS),
    "empty_results": ({"results": [], "credits": 1}, 1),
}


PLAN_LIMIT = {"error": "Error 432: plan usage limit exceeded"}


@pytest.mark.parametrize("shape", [*FAILURES, "plan_limit_432"])
def test_tool_failure_shapes_end_cleanly(shape: str, tmp_path: Path) -> None:
    """Mutations: build the wrappers with handle_tool_error=False (the agent
    crashes); drop the empty results check (an artifact is recorded); release
    the hold instead of charging a timeout (credits come out 0). At the
    research node (Phase 2): tools_plan_limit_is_ordinary_error (432 reaches
    synthesize); research_failure_status_lost (the wrapper records every
    failure as "error", so the trail status is wrong)."""
    _failure_through_research_node(shape, tmp_path / "graph")
    if shape == "plan_limit_432":
        return  # the wrapper level plan limit test is test_tavily_plan_limit_stops_the_run
    recorded, credits = FAILURES[shape]
    ctx = run_ctx(tmp_path)
    tools = make_stub_tools(FakeCassette(search=[recorded, found(2)]), ctx)
    out = agent_for([call("search", query="q"), call("search", query="again"), answer("done")])(tools)

    first, second = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert first.status == "error" and first.artifact is None
    assert second.status == "success"
    assert [a["query"] for a in ctx.collector] == ["again"]
    assert out["messages"][-1].content == "done"
    ledger = Ledger(ctx.ledger_path)
    assert ledger.run_credits("r1") == credits + 1
    assert open_holds(ledger) == 0


TRAIL_STATUS = {"error_500": "error", "string_return": "error", "timeout": "timeout",
                "empty_results": "empty", "plan_limit_432": "tavily_plan_limit"}


def _failure_through_research_node(shape: str, tmp_path: Path) -> None:
    """The same failure inside the research node and a small graph: a clean terminal status."""
    from agent.tests.test_research_limits import FakeCassette as NodeCassette
    from agent.tests.test_research_limits import answer as node_answer
    from agent.tests.test_research_limits import call as node_call
    from agent.tests.test_research_limits import make_ctx, run_graph

    tmp_path.mkdir()
    recorded = PLAN_LIMIT if shape == "plan_limit_432" else FAILURES[shape][0]
    script = [node_call("search", query="q"), node_call("search", query="again"), node_answer("done")]
    ctx = make_ctx(tmp_path, NodeCassette(script, search=[recorded, found(2)]))

    final = run_graph(ctx)

    first = final["search_trail"][0]
    assert first["status"] == TRAIL_STATUS[shape] and first["n_results"] == 0
    if shape == "plan_limit_432":
        assert final["status"] == "budget_stopped" and final["stop_reason"] == "tavily_plan_limit"
        assert final["_visited"] == ["render"] and final["sources"] == []
        return
    assert final["status"] == "ok" and final.get("stop_reason") is None
    assert final["_visited"] == ["synthesize", "render"]
    assert [s["url"] for s in final["sources"]] == ["https://example.com/2"]
    assert [e["status"] for e in final["search_trail"]] == [TRAIL_STATUS[shape], "ok"]


@pytest.mark.parametrize("marker", config.TAVILY_PLAN_LIMIT_MARKERS)
def test_tavily_plan_limit_stops_the_run(marker: str, tmp_path: Path) -> None:
    """Mutation: drop the plan limit branch in guarded_call, so 432 is an
    ordinary tool error and the agent carries on."""
    ctx = run_ctx(tmp_path)
    tools = make_stub_tools(FakeCassette(search=[{"error": f"{marker}: plan limit reached"}]), ctx)
    with pytest.raises(BudgetExceeded) as err:
        agent_for([call("search", query="q"), answer("done")])(tools)
    assert err.value.reason == "tavily_plan_limit"
    ledger = Ledger(ctx.ledger_path)
    stops = [r for r in ledger.rows("r1") if r["kind"] == "stop"]
    assert [r["note"] for r in stops] == ["tavily_plan_limit"]
    assert ledger.run_credits("r1") == 0 and open_holds(ledger) == 0


def test_cassette_credit_cap_stops_the_run_and_keeps_sources(tmp_path: Path) -> None:
    """Mutation: stop passing the RunContext caps to reserve_credits (the
    configured cap of 10 lets the third search through)."""
    ctx = run_ctx(tmp_path, caps={"run_credit_cap": 2})
    tools = make_stub_tools(FakeCassette(search=[found(1), found(2), found(3)]), ctx)
    script = [call("search", query=q) for q in ("a", "b", "c")] + [answer("done")]
    with pytest.raises(BudgetExceeded) as err:
        agent_for(script)(tools)
    assert err.value.reason == "run_credit_cap"
    assert tools[0].calls == 2  # the third call was refused before Tavily saw it
    assert [a["query"] for a in ctx.collector] == ["a", "b"]  # sources survive the stop


class HungSearch:
    """Answers only after `delay` seconds, far past the wrapper's timeout."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.release = threading.Event()

    def invoke(self, input):
        self.release.wait(self.delay)
        return found(1)


def test_worker_timeout_abandons_a_hung_call(tmp_path: Path) -> None:
    """Mutation: join the worker thread with no timeout; the slow answer then
    arrives and is recorded as a success."""
    ctx = run_ctx(tmp_path)
    hung = HungSearch(delay=3.0)
    search, _ = make_research_tools(ctx, search_inner=hung, fetch_inner=hung, timeout_s=0.1)
    start = time.monotonic()
    msg = search.invoke({"name": "search", "args": {"query": "q"}, "id": "c1", "type": "tool_call"})
    elapsed = time.monotonic() - start
    hung.release.set()
    assert msg.status == "error" and "timed out" in msg.content
    assert elapsed < 2.0
    assert ctx.collector == []
    assert Ledger(ctx.ledger_path).run_credits("r1") == config.TAVILY_TIMEOUT_CREDITS


def test_parallel_searches_log_their_own_credits(tmp_path: Path) -> None:
    """Mutation tools_log_credits_from_ledger_delta: each lookup log entry records the
    run's ledger total before and after the call, so searches running in
    parallel pick up each other's credits (the Phase 5 live run logged 1, 2, 3
    for three parallel searches that each spent 1)."""
    import json
    from concurrent.futures import ThreadPoolExecutor

    from agent.replay.tool_stubs import StubInner
    from agent.research.tools import lookup_log_path

    ctx = run_ctx(tmp_path)
    gate = threading.Barrier(3)

    class SlowInner(StubInner):
        def invoke(self, input):  # every call waits until all three have started
            gate.wait(timeout=10)
            response = super().invoke(input)
            time.sleep(0.05 * (1 + self.calls % 3))
            return response

    search_inner = SlowInner("search", [found(n) for n in range(3)])
    trail: list[dict] = []
    search_tool, _ = make_research_tools(ctx, search_inner=search_inner,
                                         fetch_inner=StubInner("fetch", []), trail=trail)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda q: search_tool.func(query=q), ["a", "b", "c"]))
    assert sorted(e["credits"] for e in trail) == [1, 1, 1]
    logged = [json.loads(line) for line in lookup_log_path(ctx).read_text(encoding="utf-8").splitlines()]
    assert sorted(line["credits"] for line in logged) == [1, 1, 1]
    assert sum(e["credits"] for e in trail) == Ledger(ctx.ledger_path).run_credits("r1") == 3
