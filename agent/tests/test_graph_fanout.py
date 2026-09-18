"""Parallel branches: history, graph_lookup and research in one superstep (PLAN 8.2, 8.3; section 5).

The synthetic cassette fanout_top_up runs on appliance appl-optima880 against
the synthetic graph fixture with no confirmed code; the classifier says
no_match, so route table row 4 sends the run to all three branches, research
with the top up limits (decision 46). The research branch charges the ledger
and its tool wrappers write the lookup log from their own thread while the
other branches run. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import config
from agent.graph import thread_config
from agent.ledger import Ledger
from agent.nodes.persist import run_record_path
from agent.research.tools import lookup_log_path
from agent.tests.helpers import load_case
from agent.tests.test_graph_latency import expected_latency_keys
from agent.tests.test_sc8_history import (
    FIXTURE_GRAPH, OPTIMA, SEEDED_RECORD, final_state, run_phase3_case, synth_request,
)

BRANCHES = {"history", "graph_lookup", "research"}
MANUAL_URL = "https://example.com/synthetic/sundance/optima-880-manual"
LUKEWARM_URL = "https://example.com/synthetic/spa/lukewarm-water"


def run_fanout(tmp_path: Path):
    return run_phase3_case(load_case("synthetic/fanout_top_up"), tmp_path, appliance_id=OPTIMA,
                           graph=FIXTURE_GRAPH)


def test_three_branch_fanout_single_superstep(tmp_path: Path) -> None:
    """Mutations: fanout_only_first_branch (route's other branches never run);
    fanout_branches_chained (the branches run one after another, one superstep
    each); state_sources_dedupe_off (a URL both branches found is registered
    twice); research_limits_ignored (research runs with the main limits beside
    the graph); checkpointer_same_thread_only (the SQLite checkpointer refuses
    writes from the parallel branches' threads); synth_prompt_hides_graph_sources
    (the graph branch's source reaches synthesize with no text)."""
    compiled, ctx, outcomes = run_fanout(tmp_path)
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    assert state["route"] == ["history", "graph", "research"]
    assert state["route_reason"].startswith("Route table row 4:")
    assert state["research_limits"] == "top_up"

    # One checkpoint schedules exactly the three branches together: one superstep.
    history = list(compiled.get_state_history(thread_config("thread-1")))
    scheduled = [set(snap.next) for snap in history if set(snap.next) & BRANCHES]
    assert scheduled == [BRANCHES]
    ran = [n for o in outcomes for n in o.nodes]
    for node in (*BRANCHES, "gather", "synthesize", "validate", "persist"):
        assert ran.count(node) == 1, node
    assert ran.index("gather") > max(ran.index(b) for b in BRANCHES)

    # The top up allows 2 searches; the script asked for 3.
    assert config.RESEARCH_LIMITS["top_up"]["search"] == 2
    assert ctx.replay["stubs"]["search"].calls == 2
    assert [e["status"] for e in state["search_trail"] if e["tool"] == "search"].count("ok") == 2

    # Each branch's writes arrived: history, graph hits, and sources from both.
    assert SEEDED_RECORD in [h["record_id"] for h in state["history_hits"]]
    assert state["graph_hits"] and all(h["source_url"] == MANUAL_URL for h in state["graph_hits"])
    urls = [s["url"] for s in state["sources"]]
    assert len(urls) == len(set(urls)), "a URL is registered once"
    by_url = {s["url"]: s for s in state["sources"]}
    assert by_url[MANUAL_URL]["origin"] == "graph" and by_url[LUKEWARM_URL]["origin"] == "search"
    assert [s["url"] for s in state["brief"]["sources"]] == [MANUAL_URL, LUKEWARM_URL]
    # synthesize saw the graph branch's evidence, not just its URL.
    sent = synth_request(ctx)
    for hit in state["graph_hits"]:
        assert hit["evidence"] in sent

    # The ledger (route's classifier, then the research branch) and the lookup
    # log (written from the tool thread) both took their writes.
    charges = [r["node"] for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["kind"] == "charge"]
    assert charges.count("classifier") == 1 and "research" in charges and charges.count("synthesize") == 1
    log_lines = lookup_log_path(ctx).read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 2


def test_latency_keeps_every_node_entry(tmp_path: Path) -> None:
    """Mutations: latency_reducer_keeps_last_write (latency keeps only the
    newest write, so parallel and earlier entries are lost);
    persist_latency_not_in_record (the run record's latency misses the persist
    node's own entry)."""
    _, ctx, outcomes = run_fanout(tmp_path)
    state = final_state(outcomes)
    ran = [n for o in outcomes for n in o.nodes]
    latency = state["latency"]
    assert set(latency) == expected_latency_keys(ran)
    assert BRANCHES <= set(latency) and {"route", "gather", "synthesize", "render", "persist"} <= set(latency)
    assert all(isinstance(v, float) and v >= 0 for v in latency.values())
    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["latency"] == latency
    assert record["latency_s"] == pytest.approx(sum(latency.values()))
