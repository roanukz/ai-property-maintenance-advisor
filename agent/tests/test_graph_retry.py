"""The validation retry and the forced refusal (PLAN 8.4; decisions 7, 23; v1 guardrail test 13).

Each test replays a synthetic cassette (agent/tests/cassettes/synthetic)
through the real graph. The retry script in every cassette asks for three
searches, a fetch and an answer, more than the cheap retry pass allows, so a
retry that used the main caps would show in the stub call counts. Each test
names the mutation that turns it red.
"""

from __future__ import annotations

import pytest

from agent import config
from agent.graph import retry_need_usd
from agent.ledger import Ledger
from agent.nodes.synthesize import PARSE_ERROR_PREFIX
from agent.rules.observed_code import NO_MATCH
from agent.tests.helpers import assert_run_record_matches, load_case, run_case

CLEARED = ("candidates", "try_first", "upgrade_options", "maintenance_due")
RETRY = config.RESEARCH_LIMITS["retry_cheap"]


def _run(case: str, tmp_path):
    """Run a typed identity case; it pauses only when the symptom's code needs confirming."""
    cassette = load_case(f"synthetic/{case}")
    _, ctx, outcomes = run_case(cassette, tmp_path)
    assert len(outcomes) == (2 if cassette.resume is not None else 1)
    return cassette, ctx, outcomes[-1]


def _states_after(outcome, node: str) -> list[dict]:
    return [state for name, state in outcome.states if name == node]


def _assert_forced_refusal(outcome, ctx) -> dict:
    state = outcome.state
    assert_run_record_matches(ctx, state)
    assert state["status"] == "no_reliable_answer"
    assert state["refusal_origin"] == "forced"
    brief = state["brief"]
    assert brief["status"] == "no_reliable_answer"
    for key in CLEARED:
        assert brief[key] == [], key
    assert outcome.nodes.count("refuse") == 1
    assert outcome.nodes.count("research") == 2
    assert state["validation_failures"] == 2
    return brief


def _retry_calls(ctx, cassette) -> tuple[int, int, int]:
    """(searches, fetches, research model calls) made by the retry pass."""
    first_pass = sum(1 for r in cassette.data["research"]["tool_results"] if r["tool"] == "search"
                     and "retry" not in r["query"])
    first_calls = first_pass + 1  # every first pass here ends with an answer turn
    stubs = ctx.replay["stubs"]
    return (stubs["search"].calls - first_pass, stubs["fetch"].calls,
            ctx.replay["models"]["research"].cursor - first_calls)


def test_zero_match_twice_ends_forced_refusal(tmp_path) -> None:
    """Mutations: observed_zero_match_passes (the rules builder's; the first
    draft passes with COOL and ICE); sc3a_refuse_skipped (the second failure
    retries again and the script runs out)."""
    cassette, ctx, outcome = _run("zero_match_twice", tmp_path)
    validated = _states_after(outcome, "validate")
    assert len(validated) == 2
    for state in validated:
        assert state["brief"] is None
        assert any(e.startswith(NO_MATCH) for e in state["validation_errors"])
    brief = _assert_forced_refusal(outcome, ctx)
    assert brief["observed_code"] == cassette.expect["observed_code"]


def test_second_validation_failure_forces_refusal_without_candidates(tmp_path) -> None:
    """Mutations: sc3a_failures_not_counted (validate never increments, the
    loop runs until the script runs out); sc3a_refuse_skipped; retry_uses_main_caps."""
    cassette, ctx, outcome = _run("retry_fails_twice", tmp_path)
    for draft in (a["draft"] for a in cassette.data["synthesize"]):
        assert draft["candidates"], "both drafts carry candidates"
    brief = _assert_forced_refusal(outcome, ctx)
    assert _retry_calls(ctx, cassette)[:2] == (RETRY["search"], RETRY["fetch"])
    queries = [e["query"] for e in outcome.state["search_trail"] if e["tool"] == "search" and e["status"] == "ok"]
    assert len(queries) == 5 + RETRY["search"]
    assert brief["no_reliable_answer"]["searched"] == queries
    assert outcome.state["status"] != "budget_stopped"


def test_retry_taken_when_affordable(tmp_path) -> None:
    """Mutations: retry_affordability_worst_case (the need is priced at worst
    case and the retry is never taken); retry_uses_main_caps (the retry pass
    makes three searches and a fetch)."""
    cassette, ctx, outcome = _run("retry_typical", tmp_path)
    [before_gate] = _states_after(outcome, "validate")[:1]
    assert before_gate["validation_failures"] == 1
    # The state mirror of spend matches the ledger, and the first pass fits
    # the decision 23 test: remaining budget >= retry typical + synthesize reservation.
    assert outcome.state["cost_usd"] == pytest.approx(Ledger(ctx.ledger_path).run_total(ctx.run_id))
    first_pass = before_gate["cost_usd"]
    assert config.REPLAY_RUN_CAP_USD - first_pass >= retry_need_usd("replay")
    assert outcome.nodes.count("retry_gate") == 1 and outcome.nodes.count("research") == 2
    searches, fetches, model_calls = _retry_calls(ctx, cassette)
    assert (searches, fetches) == (RETRY["search"], RETRY["fetch"])
    assert model_calls <= RETRY["loop_guard"]
    assert outcome.state["research_attempts"] == 2
    assert outcome.state["status"] == "ok"
    assert outcome.state["brief"]["candidates"]


def test_unparseable_synthesis_counts_as_validation_failure(tmp_path) -> None:
    """v1 guardrail test 13: output that fails the schema goes to the retry, then
    a forced refusal, never an empty ok brief.

    Mutations: validate_no_draft_is_ok (validate treats a missing draft as an
    empty ok brief); nodes_synth_parse_failure_empty_draft."""
    _, ctx, outcome = _run("unparseable_twice", tmp_path)
    synthesized = _states_after(outcome, "synthesize")
    assert len(synthesized) == 2
    for state in synthesized:
        assert state["draft"] is None
        assert state["validation_errors"][0].startswith(PARSE_ERROR_PREFIX)
    assert [s["validation_failures"] for s in _states_after(outcome, "validate")] == [1, 2]
    _assert_forced_refusal(outcome, ctx)
    charges = [r for r in Ledger(ctx.ledger_path).rows(ctx.run_id)
               if r["kind"] == "charge" and r["node"] == "synthesize"]
    assert len(charges) == 2
