"""AdvisorState is JSON native, RunContext stays out of it, and the reducers merge."""

from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

import pytest
from langgraph.graph import END, START, StateGraph

from agent.state import AdvisorState, RunContext, check_json_native, merge_dicts


def representative_state() -> dict:
    return {
        "run_id": "r1",
        "mode": "replay",
        "property_id": "p1",
        "appliance_id": None,
        "symptom": "Spa shows FLO",
        "photo": {"path": "demo-assets/plate.jpg", "sha256": "ab" * 32},
        "extraction": {"brand": "Sundance Spas", "model": None},
        "identity": {"brand": "Sundance Spas", "model": "Optima"},
        "identity_confirmed": True,
        "observed_code": "FLO",
        "observed_code_candidate": None,
        "confirm_prompt": None,
        "route": ["research", "history"],
        "route_reason": "no graph edge",
        "research_limits": "main",
        "sources": [{"source_id": 1, "url": "https://example.com/a", "host": "example.com", "title": "A",
                     "retrieved_at": "2026-09-17T00:00:00Z", "origin": "search", "text_sha256": "cd" * 32,
                     "snippet": "text"}],
        "search_trail": [{"query": "flo", "n_results": 1, "credits": 1, "at": "2026-09-17T00:00:00Z",
                          "status": "ok"}],
        "graph_hits": [],
        "history_hits": [],
        "draft": None,
        "validation_errors": [],
        "validation_failures": 0,
        "research_attempts": 1,
        "status": "running",
        "refusal_origin": None,
        "stop_reason": None,
        "brief": None,
        "grounding": [{"candidate": 0, "code": "FLO", "source_url": "https://example.com/a",
                       "status": "verified", "reason": "code in the cited source's raw text"}],
        "grounding_status": "verified",
        "cost_usd": 0.0123,
        "tavily_credits": 1,
        "latency": {"intake": 0.01},
        "html_path": None,
        "persist_report": None,
    }


def test_state_json_round_trips_and_excludes_run_context():
    # Mutation: add a `ctx: RunContext` key to AdvisorState, or let
    # check_json_native accept tuples or Paths.
    state = representative_state()
    assert set(state) == set(AdvisorState.__annotations__)
    check_json_native(state)
    assert json.loads(json.dumps(state)) == state

    context_fields = {f.name for f in fields(RunContext)}
    assert not context_fields & (set(AdvisorState.__annotations__) - {"run_id", "mode"})
    for bad in ({"route": ("a",)}, {"photo": {"path": Path("x")}}, {"latency": {1: 0.1}},
                {"ctx": RunContext("r", "replay", Path(), Path(), Path(), Path())}):
        with pytest.raises(TypeError):
            check_json_native(bad)


def test_run_context_collector_is_per_run():
    # Mutation: default the collector, or the replay cache, to a shared module
    # level object (one run would then replay another run's script).
    a = RunContext("a", "replay", Path(), Path(), Path(), Path())
    b = RunContext("b", "replay", Path(), Path(), Path(), Path())
    a.collector.append({"tool": "search"})
    a.replay["models"] = {"synthesize": object()}
    assert b.collector == [] and a.cassette is None and a.caps is None
    assert b.replay == {}


def test_reducers_merge_parallel_branch_writes():
    # Mutation: remove the reducer from any of these keys (LangGraph raises
    # INVALID_CONCURRENT_GRAPH_UPDATE), or make merge_dicts return only the
    # right side (latency loses a node's entry).
    def research(state):
        return {"sources": [{"url": "r"}], "search_trail": [{"query": "q"}], "cost_usd": 0.01,
                "tavily_credits": 2, "latency": {"research": 1.5}}

    def history(state):
        return {"sources": [{"url": "h"}], "search_trail": [], "cost_usd": 0.02,
                "tavily_credits": 1, "latency": {"history": 0.2}}

    g = StateGraph(AdvisorState)
    g.add_node("research", research)
    g.add_node("history", history)
    g.add_edge(START, "research")
    g.add_edge(START, "history")
    g.add_edge("research", END)
    g.add_edge("history", END)
    out = g.compile().invoke({"run_id": "r1", "sources": [{"url": "intake"}], "cost_usd": 0.1,
                              "latency": {"intake": 0.01}})

    assert sorted(s["url"] for s in out["sources"]) == ["h", "intake", "r"]
    assert out["sources"][0] == {"url": "intake"}
    assert out["search_trail"] == [{"query": "q"}]
    assert out["cost_usd"] == pytest.approx(0.13)
    assert out["tavily_credits"] == 3
    assert out["latency"] == {"intake": 0.01, "research": 1.5, "history": 0.2}
    check_json_native(out)


def test_merge_dicts_right_wins_on_same_key():
    # Mutation: {**right, **left} keeps the stale value.
    assert merge_dicts({"a": 1.0, "b": 2.0}, {"b": 3.0}) == {"a": 1.0, "b": 3.0}
    assert merge_dicts(None, {"a": 1.0}) == {"a": 1.0}


@pytest.mark.parametrize("case", ["synthetic/flo_three_candidates", "synthetic/retry_typical", "flo"])
def test_state_json_round_trips_after_every_node(case, tmp_path):
    # Mutation: state_draft_is_pydantic (synthesize writes the BriefDraft
    # model into state instead of its JSON dump). The recorded flo case skips
    # until its privacy approval (a failure in gate mode); the synthetic cases
    # cover the pause, the resume and the retry loop either way.
    from agent.tests.helpers import load_case, run_case

    cassette = load_case(case)
    _, _, outcomes = run_case(cassette, tmp_path)
    ran = [name for outcome in outcomes for name in outcome.nodes]
    seen = [name for outcome in outcomes for name, _ in outcome.states]
    assert seen == ran and "synthesize" in ran and "render" in ran
    for outcome in outcomes:
        for name, state in outcome.states:
            check_json_native(state, f"state after {name}")
            assert json.loads(json.dumps(state)) == state, name
