"""Decision 69: replay stays as recorded on Claude Haiku 4.5 after the model switch.

The recorded cases run through the whole graph: every model call is charged its recorded usage on
the recorded model's card, and held at the budgets the cassettes were recorded under. The reviewed
v2 goldens are checked byte for byte by test_render.py. Each test names its mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import config
from agent.ledger import Ledger
from agent.replay.build_cassettes import pass_cost_usd
from agent.tests.helpers import load_case, resolve_cassette_path, run_case

BUDGET_KEY = {"read_plate": "read_plate", "classifier": "classifier", "research": "research",
              "synthesize": "synthesize_cheap"}


@pytest.mark.parametrize("case", ["flo", "hvac"])
def test_replay_charges_the_recorded_usage_on_the_recorded_card_at_the_recorded_budgets(
        case: str, tmp_path: Path) -> None:
    """Mutations: haiku55_replay_priced_at_haiku_5_5 (replay priced on Claude Haiku 5.5's card);
    haiku55_replay_takes_live_budgets (replay held at the Claude Haiku 5.5 budgets)."""
    cassette = load_case(case)
    _, ctx, outcomes = run_case(cassette, tmp_path)
    assert outcomes[-1].state["status"] == "ok"
    rows = [r for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["provider"] == "anthropic"]
    charges = [r for r in rows if r["kind"] == "charge"]
    card = config.REPLAY_PRICES
    assert card == {"input": 1.00, "output": 5.00}
    for row in charges:
        assert row["model"] == config.MODEL_FOR["replay"][row["node"]] == config.HAIKU
        assert row["usd"] == pytest.approx((row["input_tokens"] * card["input"]
                                            + row["output_tokens"] * card["output"]) / 1_000_000)
    recorded = json.loads(resolve_cassette_path(case).read_text(encoding="utf-8"))
    assert sum(r["usd"] for r in charges) == pytest.approx(pass_cost_usd(recorded))
    for row in (r for r in rows if r["kind"] == "reserve"):
        assert row["output_tokens"] == config.REPLAY_MAX_TOKENS[BUDGET_KEY[row["node"]]], row["node"]
