"""SC7a: a question the graph already answers takes the graph route (PLAN section 5, 8.4 row 1).

The synthetic graph fixture (fixtures/graphs/optima_flo.json, every edge a
verbatim span of a synthetic page on an example.com URL) holds a verified
HAS_CODE edge for FLO on the Optima 880's 880 Series family. The synthetic
cassette flo_graph_repeat has an empty research script, so a research call
would fail the run. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.kg import KnowledgeGraph
from agent.ledger import Ledger
from agent.models import make_model, make_tools
from agent.nodes.persist import run_record_path
from agent.rules.observed_code import normalize_code
from agent.tests.helpers import load_case, replay_ctx
from agent.tests.test_sc8_history import FIXTURE_GRAPH, final_state, run_phase3_case, synth_request

MANUAL_URL = "https://example.com/synthetic/sundance/optima-880-manual"


def test_graph_route_makes_no_search_calls(tmp_path: Path) -> None:
    """Mutations: sc7a_route_row1_off (the router ignores the graph's code
    coverage and tops up with research); sc7a_graph_source_retrieved_now (the
    graph source loses its original retrieved_at); fanout_join_waits_for_all
    (gather waits for all three branches, so a graph only route never reaches
    synthesize); persist_graph_source_readded (the repeat writes the graph's
    own edge back under this run); synth_prompt_hides_graph_sources (the
    graph source reaches synthesize with no text, so the brief is not built
    from the graph's evidence)."""
    cassette = load_case("synthetic/flo_graph_repeat")
    assert cassette.data["research"] == {"script": [], "tool_results": []}
    fixture = KnowledgeGraph.load(FIXTURE_GRAPH)
    original = fixture.source_for(MANUAL_URL)["retrieved_at"]

    # Build the run's research model and search stubs up front, so "called 0
    # times" is read from the very objects the graph would use.
    probe = replay_ctx(tmp_path, cassette, run_id="thread-1")
    make_model("research", probe)
    make_tools(probe)
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path, graph=FIXTURE_GRAPH, pages=True, ctx=probe)
    assert ctx is probe
    before = Path(FIXTURE_GRAPH).read_bytes()
    state = final_state(outcomes)

    assert state["route"] == ["graph"]
    assert state["route_reason"].startswith("Route table row 1:")
    assert "FLO" in state["route_reason"]
    assert state.get("research_limits") is None
    ran = [n for o in outcomes for n in o.nodes]
    assert "research" not in ran and "graph_lookup" in ran
    assert ctx.replay["models"]["research"].received == []
    assert ctx.replay["stubs"]["search"].calls == 0 and ctx.replay["stubs"]["fetch"].calls == 0
    assert state["search_trail"] == []
    assert {r["node"] for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["kind"] == "charge"} == {"synthesize"}

    assert state["status"] == "ok"
    brief = state["brief"]
    assert [s["url"] for s in brief["sources"]] == [MANUAL_URL]
    [source] = brief["sources"]
    assert source["origin"] == "graph"
    assert source["retrieved_at"] == original
    [candidate] = brief["candidates"]
    assert normalize_code(candidate["code"]) == "FLO" and candidate["confirmed"] is True
    assert brief["candidates"][0]["source_index"] == 0
    assert state["graph_hits"] and all(h["source_url"] == MANUAL_URL for h in state["graph_hits"])
    # synthesize saw the graph's verified evidence under the graph source's header.
    [hit] = [h for h in state["graph_hits"] if h["kind"] == "HAS_CODE"]
    sent = synth_request(ctx)
    assert hit["evidence"] in sent
    assert f"[0] {fixture.source_for(MANUAL_URL)['title']}" in sent

    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["route"] == ["graph"] and record["searches"] == 0
    assert record["route_reason"] == state["route_reason"]
    # The repeat adds nothing: its only source came from the graph.
    assert record["graph_edges"]["written"] == 0
    # Skipped outright, not merely found already present (same evidence, same URL).
    assert record["graph_edges"]["already_present"] == 0
    assert Path(ctx.graph_path).read_bytes() == before
