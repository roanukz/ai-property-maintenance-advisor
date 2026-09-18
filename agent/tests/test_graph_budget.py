"""SC11, the graph half: a budget stop is never a NO RELIABLE ANSWER (PLAN 8.4, section 5).

Each scenario replays a synthetic cassette with a stated `caps` block, so the
ledger refuses a reservation before the call is made, or the retry after a
failed first pass at the caps cannot be afforded (decision 23). Every one must
end `budget_stopped`, reach render through validate (which writes the one
budget stop brief) and never pass through refuse.
"""

from __future__ import annotations

import copy
import json

import pytest

from agent.graph import RETRY_NOT_AFFORDABLE, retry_need_usd
from agent.ledger import Ledger
from agent.replay.cassettes import cassette_from_dict
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR, assert_run_record_matches, run_case

CLEARED = ("candidates", "try_first", "upgrade_options", "maintenance_due")

PLAN_LIMIT_ERROR = "Error 432: plan usage limit exceeded (synthetic)"

# scenario: (synthetic cassette, caps override or None, stop reason, node whose call is
# refused, index of a search result replaced by a Tavily 432 error or None)
SCENARIOS = {
    "read_plate_refused": ("flo_three_candidates", {"run_cap_usd": 0.001}, "run_cap", "read_plate", None),
    "research_refused_midway": ("retry_typical", {"run_cap_usd": 0.02}, "run_cap", "research", None),
    "synthesize_refused": ("retry_typical", {"run_cap_usd": 0.07}, "run_cap", "synthesize", None),
    "retry_unaffordable_at_caps": ("at_caps_first_pass", None, RETRY_NOT_AFFORDABLE, None, None),
    # Tool stops take the same research -> gather -> validate -> render path as a dollar cap.
    "research_credit_cap": ("retry_typical", {"run_credit_cap": 3}, "run_credit_cap", "research", None),
    "research_tavily_plan_limit": ("retry_typical", None, "tavily_plan_limit", "research", 1),
}


def _cassette(name: str, caps: dict | None, plan_limit_at: int | None = None):
    data = copy.deepcopy(json.loads((SYNTHETIC_CASSETTE_DIR / f"{name}.json").read_text(encoding="utf-8")))
    if caps is not None:
        data["caps"] = caps
    if plan_limit_at is not None:
        result = data["research"]["tool_results"][plan_limit_at]
        data["research"]["tool_results"][plan_limit_at] = {
            "tool": result["tool"], "query": result["query"], "error": PLAN_LIMIT_ERROR}
    return cassette_from_dict(data, f"{name} with caps {caps}, 432 at {plan_limit_at}")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_budget_stop_is_never_no_reliable_answer(scenario: str, tmp_path) -> None:
    """Mutations: budget_stop_routed_to_refuse (after validate a budget stop goes
    to refuse); retry_gate_always_affordable (the at caps run retries);
    read_plate_budget_stop_pauses (a refused read_plate still pauses);
    nodes_synth_no_budget_catch (the nodes builder's); nodes_gather_ignores_budget_stop
    (a research stop goes on to synthesize); research_credit_cap_not_a_stop and
    tools_plan_limit_is_ordinary_error (the tool stops through the real graph);
    persist_status_from_brief_missing (the run record says ok)."""
    name, caps, reason, refused_node, plan_limit_at = SCENARIOS[scenario]
    cassette = _cassette(name, caps, plan_limit_at)
    _, ctx, outcomes = run_case(cassette, tmp_path)
    outcome = outcomes[-1]
    state = outcome.state
    assert not outcome.paused
    assert state["status"] == "budget_stopped"
    assert state["status"] != "no_reliable_answer"
    assert state["stop_reason"] == reason
    assert state.get("refusal_origin") is None
    ran = [n for o in outcomes for n in o.nodes]
    assert "refuse" not in ran
    # Phase 3: persist is the node after render on every path to END.
    assert ran[-3:] == ["validate", "render", "persist"] or ran[-3:] == ["retry_gate", "render", "persist"]
    brief = state["brief"]
    assert brief["status"] == "budget_stopped"
    for key in CLEARED:
        assert brief[key] == [], key
    assert (tmp_path / "brief.html").is_file()
    assert_run_record_matches(ctx, state)

    models = ctx.replay.get("models", {})
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    if refused_node is not None:
        # Refused before the call: the model was never asked (SC11).
        stops = [r for r in rows if r["kind"] == "stop"]
        assert [r["node"] for r in stops] == [refused_node]
        assert stops[0]["note"].startswith(reason)
        if refused_node == "research":
            assert ran.count("research") == 1 and "synthesize" not in ran
        model = models.get(refused_node)
        charges = sum(1 for r in rows if r["kind"] == "charge" and r["node"] == refused_node
                      and r["provider"] == "anthropic")
        assert model is None or len(model.received) == charges
        assert "synthesize" not in models  # never reached, or refused before the model was built
    else:
        # The first pass hit the caps: its spend leaves less than a retry needs,
        # so the retry is skipped and nothing more is spent.
        [gate] = [s for n, s in outcome.states if n == "retry_gate"]
        assert gate["validation_failures"] == 1
        cap = Ledger.run_cap("replay", (cassette.caps or {}).get("run_cap_usd"))
        remaining = cap - Ledger(ctx.ledger_path).run_total(ctx.run_id)
        assert remaining < retry_need_usd("replay")
        assert ran.count("research") == 1 and ran.count("synthesize") == 1
        assert len(models["synthesize"].received) == 1
        assert models["research"].cursor == 8  # 5 searches and 3 fetches, then ToolBudgetDone


def test_research_budget_ends_research_and_synthesize_runs(tmp_path) -> None:
    """The research sub budget (decision 26) through the real graph: research ends
    early, the run is not budget_stopped, and synthesize drafts from what was
    collected.

    Mutation research_budget_is_budget_stop ("research_budget" added to
    BUDGET_STOP_REASONS; the run goes to validate as a budget stop)."""
    cassette = _cassette("retry_typical", {"research_budget_usd": 0.01})
    _, ctx, outcomes = run_case(cassette, tmp_path)
    state = outcomes[-1].state
    ran = [n for o in outcomes for n in o.nodes]
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert [r["note"] for r in rows if r["kind"] == "stop"][0].startswith("research_budget")
    assert ctx.replay["stubs"]["search"].calls < 5  # research ended before the full allowance
    assert ran.index("synthesize") == ran.index("gather") + 1
    assert state["status"] != "budget_stopped" and state.get("stop_reason") is None
    assert_run_record_matches(ctx, state)
