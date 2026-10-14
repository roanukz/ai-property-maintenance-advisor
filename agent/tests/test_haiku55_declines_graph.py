"""Decision 69 in the graph: a declined call in any model node ends the run at render on the decline brief.

read_plate, route's classifier, research and synthesize each catch ModelDeclined and write the code
written decline brief (agent.declined); the edge after each goes straight to render, so nothing the
model wrote is validated, retried, checked by Jev or shown. The replayed runs here script a refusal
into one node's ReplayChatModel, built up front on the run's context, and the rest of each run comes
from its synthetic cassette. Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent import config
from agent.declined import REFUSAL_ORIGIN
from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end
from agent.ledger import Ledger
from agent.models import max_tokens_for
from agent.replay.replay_model import ReplayChatModel
from agent.schemas import Brief, RunResult
from agent.tests.helpers import assert_run_record_matches, cassette_input, load_case, replay_ctx
from agent.tests.test_sc8_history import FIXTURE_GRAPH, OPTIMA, final_state, run_phase3_case

USAGE = {"input_tokens": 1_200, "output_tokens": 300}
REFUSAL = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
           "usage": USAGE}
# Nodes a declined run must never reach.
NEVER_AFTER_DECLINE = ("validate", "safety_check", "upgrade_check", "refuse", "retry_gate")


def declining(node: str) -> ReplayChatModel:
    """The node's replay model, scripted with one refusal and nothing after it."""
    return ReplayChatModel(responses=[dict(REFUSAL)], model=config.MODEL_FOR["replay"][node],
                           max_tokens=max_tokens_for(node, "replay"))


def run_fanout_declining(tmp_path: Path, node: str) -> tuple[Any, list[str], dict[str, Any]]:
    """fanout_top_up (no confirmed code, the classifier asked, all three branches) with `node` declining."""
    cassette = load_case("synthetic/fanout_top_up")
    ctx = replay_ctx(tmp_path, cassette)
    ctx.replay.setdefault("models", {})[node] = declining(node)
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path, appliance_id=OPTIMA, graph=FIXTURE_GRAPH, ctx=ctx)
    return ctx, [n for o in outcomes for n in o.nodes], final_state(outcomes)


def assert_declined_run(ctx: Any, ran: list[str], state: dict[str, Any], node: str) -> None:
    """The run ended on the decline brief: charged once, recorded, rendered, never retried or validated."""
    assert state["status"] == "no_reliable_answer"
    assert state["refusal_origin"] == REFUSAL_ORIGIN
    assert state["stop_reason"] == f"model_declined: cyber ({node})"
    brief = state["brief"]
    assert brief == Brief.model_validate(brief).model_dump(mode="json")
    assert brief["status"] == "no_reliable_answer" and brief["sources"] == [] and brief["candidates"] == []
    assert brief["try_first"] == [] and brief["no_reliable_answer"]["found"] == []
    assert brief["no_reliable_answer"]["why_insufficient"].startswith("The model declined this request (safety "
                                                                      "category: cyber)")
    assert not set(NEVER_AFTER_DECLINE) & set(ran), ran
    assert ran.count("render") == 1 and ran[-2:] == ["render", "persist"]
    assert ctx.replay["models"][node].cursor == 1  # asked once, never again
    ledger = Ledger(ctx.ledger_path)
    charges = [r for r in ledger.rows(ctx.run_id) if r["kind"] == "charge" and r["node"] == node]
    assert len(charges) == 1 and charges[0]["output_tokens"] == USAGE["output_tokens"]
    assert state["cost_usd"] == pytest.approx(ledger.run_total(ctx.run_id))
    record = assert_run_record_matches(ctx, state)
    assert record["brief"] == brief
    page = Path(state["html_path"]).read_text(encoding="utf-8")
    assert "The model declined this request (safety category: cyber)" in page


def test_a_declined_plate_read_goes_straight_to_render(tmp_path: Path) -> None:
    """Mutation graph_read_plate_decline_not_routed: after_read_plate sends a declined run to
    confirm_identity, which pauses for an identity the run will never use."""
    cassette = load_case("synthetic/flo_three_candidates")
    ctx = replay_ctx(tmp_path, cassette)
    ctx.replay.setdefault("models", {})["read_plate"] = declining("read_plate")
    graph = build_graph(open_checkpointer(tmp_path / "checkpoints.sqlite"))
    outcome = run_until_pause_or_end(graph, cassette_input(cassette, tmp_path), ctx, "run-1")
    assert not outcome.paused
    assert outcome.nodes == ["intake", "read_plate", "render", "persist"]
    assert_declined_run(ctx, outcome.nodes, outcome.state, "read_plate")


def test_a_declined_classifier_routes_nowhere(tmp_path: Path) -> None:
    """Counting the decline as unsure would send the request on to research. Mutations:
    route_decline_unhandled (route lets ModelDeclined crash the run); graph_route_decline_not_routed
    (after_route refuses the empty route instead of going to render)."""
    ctx, ran, state = run_fanout_declining(tmp_path, "classifier")
    assert ran == ["intake", "read_plate", "confirm_identity", "route", "render", "persist"]
    assert state["route"] == [] and state["route_reason"] == "the classifier model declined the request"
    assert_declined_run(ctx, ran, state, "classifier")
    assert ctx.replay.get("stubs") is None or ctx.replay["stubs"]["search"].calls == 0


def test_a_declined_research_turn_skips_synthesize(tmp_path: Path) -> None:
    """Mutations: research_decline_unhandled (the research node lets ModelDeclined crash the run);
    gather_decline_not_routed (gather sends the declined run on to synthesize)."""
    ctx, ran, state = run_fanout_declining(tmp_path, "research")
    assert {"history", "graph_lookup", "research"} <= set(ran) and "synthesize" not in ran
    assert ran[ran.index("gather") + 1] == "render"
    assert_declined_run(ctx, ran, state, "research")
    assert ctx.replay["stubs"]["search"].calls == 0


def test_a_declined_synthesize_call_skips_the_safety_check_and_validate(tmp_path: Path) -> None:
    """Mutations: synthesize_decline_unhandled (synthesize lets ModelDeclined crash the run);
    graph_synthesize_decline_not_routed (the declined run goes on to safety_check and validate);
    synthesize_decline_cost_not_mirrored (the state's spend leaves out the declined call)."""
    ctx, ran, state = run_fanout_declining(tmp_path, "synthesize")
    assert ran.index("synthesize") == len(ran) - 3 and ran[-2:] == ["render", "persist"]
    assert_declined_run(ctx, ran, state, "synthesize")
    assert state["draft"] is None


def test_the_run_result_schema_names_a_decline() -> None:
    """Mutation schemas_run_result_no_decline: RunResult refuses refusal_origin "model_declined"."""
    brief = {"status": "no_reliable_answer", "sources": [], "matched_identity": None, "observed_code": None,
             "warranty_caution": None, "happened_before": None, "try_first": [], "candidates": [],
             "warranty": None, "upgrade_options": [], "maintenance_due": [],
             "no_reliable_answer": {"searched": [], "found": [], "why_insufficient": "synthetic"}}
    result = RunResult(brief=Brief.model_validate(brief), thread_id="t1", mode="replay", generated_at="2026-10-01",
                       refusal_origin=REFUSAL_ORIGIN, stop_reason="model_declined: cyber (synthesize)")
    assert result.refusal_origin == "model_declined"
