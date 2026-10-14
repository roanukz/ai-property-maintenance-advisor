"""Decision 69 in the lifecycle lookups (SC14): a declined call ends the lookup, charged, saving nothing.

A refusal is scripted into one node's ReplayChatModel, built up front on the lookup's context; the
rest comes from the synthetic XR16 cassette, whose research would otherwise verify a successor. Each
test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import config, lifecycle
from agent.ledger import Ledger
from agent.models import max_tokens_for
from agent.registry import open_registry
from agent.replay.cassettes import load_cassette
from agent.replay.replay_model import ReplayChatModel
from agent.tests.helpers import replay_ctx
from agent.tests.test_haiku55_declines_graph import REFUSAL, USAGE
from agent.tests.test_sweep import XR_CASSETTE, sweep_data  # noqa: F401  (sweep_data is a fixture)


def lookup_declining(data: Path, node: str) -> tuple[dict, object]:
    """One replayed XR16 lookup whose `node` model declines its first call."""
    appliance = open_registry(config.REGISTRY_PATH).get_appliance("appl-xr16")
    ctx = replay_ctx(data / node, load_cassette(XR_CASSETTE), run_id=f"lifecycle-declined-{node}")
    ctx.replay.setdefault("models", {})[node] = ReplayChatModel(
        responses=[dict(REFUSAL)], model=config.MODEL_FOR["replay"][node], max_tokens=max_tokens_for(node, "replay"))
    return lifecycle.lookup(appliance, ctx), ctx


def assert_declined_lookup(result: dict, ctx: object, node: str) -> None:
    assert result["status"] == lifecycle.DECLINED == "model_declined"
    assert result["stop_reason"] == f"model_declined: cyber ({node})"
    assert result["kept"] == [] and result["graph_edges"] is None
    assert not Path(ctx.graph_path).exists(), "nothing is saved after a decline"
    assert ctx.replay["models"][node].cursor == 1  # asked once, never again
    charges = [r for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["kind"] == "charge" and r["node"] == node]
    assert charges and charges[-1]["output_tokens"] == USAGE["output_tokens"]
    assert result["cost_usd"] == pytest.approx(Ledger(ctx.ledger_path).run_total(ctx.run_id))
    record = json.loads((Path(ctx.pages_dir).parent / "runs" / f"{ctx.run_id}.json").read_text(encoding="utf-8"))
    assert record["kind"] == lifecycle.RECORD_KIND and record["status"] == lifecycle.DECLINED
    line = lifecycle.result_lines(result)[0]
    assert f"the model declined (model_declined: cyber ({node})); nothing saved, and no other model was asked" in line


def test_a_declined_research_turn_ends_the_lookup_before_the_structured_call(sweep_data: Path) -> None:
    """Mutations: lifecycle_research_decline_unhandled (the decline crashes the sweep);
    lifecycle_decline_reported_as_budget_stop (the lookup says the ledger stopped it)."""
    result, ctx = lookup_declining(sweep_data, "research")
    assert_declined_lookup(result, ctx, "research")
    assert "synthesize" not in ctx.replay["models"], "no structured call after a declined research turn"
    assert result["search_trail"] == []


def test_a_declined_structured_call_saves_no_edge(sweep_data: Path) -> None:
    """Mutation lifecycle_extract_decline_unhandled: the declined structured call crashes the sweep."""
    result, ctx = lookup_declining(sweep_data, "synthesize")
    assert_declined_lookup(result, ctx, "synthesize")
    assert ctx.replay["stubs"]["search"].calls == 1, "research ran from the cassette first"
