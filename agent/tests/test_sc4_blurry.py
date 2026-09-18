"""SC4: a blurry plate stops at the confirmation pause and spends nothing more (PLAN section 5).

Both tests run the recorded blurry case (a v1 derived cassette, skipped until
the privacy approval and failed in gate mode) and the synthetic
blurry_guessed_model variant, where the model wrote text for fields it marked
unreadable. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import pytest
from langgraph.types import Command

from agent.graph import run_until_pause_or_end
from agent.ledger import Ledger
from agent.nodes.confirm_identity import FIRST_PROMPT, MISSING_MODEL_PROMPT
from agent.tests.helpers import load_case, replay_ctx, run_case

BLURRY_CASES = ("blurry", "synthetic/blurry_guessed_model")
NULLED = ("model", "serial", "manufacture_date")


def _assert_nothing_after_read_plate(ctx) -> None:
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert rows and {r["node"] for r in rows} == {"read_plate"}
    assert [r["kind"] for r in rows].count("charge") == 1
    models = ctx.replay.get("models", {})
    assert "research" not in models and "synthesize" not in models
    assert getattr(ctx.replay.get("stubs", {}).get("search"), "calls", 0) == 0


@pytest.mark.parametrize("case", BLURRY_CASES)
def test_blurry_plate_zero_searches_and_zero_spend_after_extraction(case: str, tmp_path) -> None:
    """Mutations: nodes_read_plate_keeps_guesses (the variant's guessed model
    reaches the payload, so accepting the proposal proceeds to research);
    nodes_confirm_accepts_empty_model; sc4_confirm_loop_ends (a resume with no
    model leaves the pause)."""
    cassette = load_case(case)
    graph, ctx, [ask] = run_case(cassette, tmp_path, resume=False)
    assert ask.paused
    recorded = cassette.data["read_plate"]["extraction"]
    unreadable = [f for f in NULLED if recorded["confidence"][f] == "unreadable"]
    assert "model" in unreadable
    for field in unreadable:
        assert ask.state["extraction"][field] is None, field
        assert ask.interrupt["identity"][field] is None, field
        assert field in ask.interrupt["missing"]
    assert ask.interrupt["prompt"] == FIRST_PROMPT

    # The owner accepts the proposal as shown, then sends an empty model: the
    # run re-prompts and stays paused both times.
    for answer in ({"identity": ask.interrupt["identity"]},
                   {"identity": {**ask.interrupt["identity"], "model": "  "}}):
        again = run_until_pause_or_end(graph, Command(resume=answer), ctx, ask.thread_id)
        assert again.paused
        assert again.nodes == ["confirm_identity"]
        assert again.interrupt["prompt"] == MISSING_MODEL_PROMPT
        assert "model" in again.interrupt["missing"]
        assert again.state["identity_confirmed"] is False
    _assert_nothing_after_read_plate(ctx)


@pytest.mark.parametrize("case", BLURRY_CASES)
def test_resume_does_not_rerun_read_plate(case: str, tmp_path) -> None:
    """Resumes use a fresh RunContext, as a new process would.

    Mutation: sc4_confirm_loops_to_read_plate (the loop back goes through
    read_plate, so the vision call runs again)."""
    cassette = load_case(case)
    graph, first_ctx, [ask] = run_case(cassette, tmp_path, resume=False)
    assert first_ctx.replay["models"]["read_plate"].cursor == 1
    for _ in range(2):
        ctx = replay_ctx(tmp_path, cassette, run_id=first_ctx.run_id)
        answer = {"identity": {"manufacturer": "Sundance Spas", "model": None, "serial": None,
                               "manufacture_date": None}}
        again = run_until_pause_or_end(graph, Command(resume=answer), ctx, ask.thread_id)
        assert again.paused
        assert "read_plate" not in again.nodes
        assert "read_plate" not in ctx.replay.get("models", {})
    rows = Ledger(first_ctx.ledger_path).rows(first_ctx.run_id)
    assert [r["node"] for r in rows if r["kind"] == "charge"] == ["read_plate"]
