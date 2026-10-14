"""The live recorder names each call's node by its max_tokens; no call is ever filed under another node.

After the Claude Haiku 5.5 budgets (decision 69) every node of each live mode has its own budget,
and the replacement sweep's lifecycle lookups borrow two of them (research for its turns,
synthesize for its one extraction), which is how their cassettes file those calls. Two calls can
still meet one budget: check-schema's single structured call is held at 4,096 output tokens, the
research budget. So the recorder also checks that only research carries tools, and a budget two
nodes share names neither. Each run here is live on the HTTP fakes of test_round2_cassettes, with
the network blocked. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import cli, config, lifecycle, models
from agent.tests.test_live_path import live_env, type_line  # noqa: F401  (live_env is a fixture)
from agent.tests.test_round2_cassettes import FANOUT, FLO, Haiku55Api, capture_nodes, run


@pytest.mark.parametrize("mode", config.LIVE_MODES)
def test_every_node_has_its_own_budget_in_every_live_mode(mode: str) -> None:
    """Mutation round2_budgets_collide: the classifier is given read_plate's budget."""
    budgets = {node: models.max_tokens_for(node, mode) for node in models.NODES}
    assert len(set(budgets.values())) == len(budgets), budgets
    # The lifecycle lookups' calls are research and synthesize calls, filed as such.
    assert (lifecycle.NODE_RESEARCH, lifecycle.NODE_EXTRACT) == ("research", "synthesize")


def test_a_structured_call_at_the_research_budget_is_not_filed_as_research(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """check-schema run while a capture is open: its structured call, at the research budget, is
    "unknown" because it carries no tools.

    Mutation round2_recorder_node_by_budget_only: the recorder names a call by its budget alone."""
    monkeypatch.setattr(config, "SCHEMA_CHECK_MAX_TOKENS", models.max_tokens_for("research", "cheap"))
    Haiku55Api(FLO.data()).install(monkeypatch)
    type_line(monkeypatch, "proceed\n")
    with cli._live_capture(SimpleNamespace(mode="cheap", secrets=[]), "probe-schema", "probe"):
        code = cli.main(["check-schema", "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    assert code == cli.EXIT_OK, out + err
    assert "schema check: accepted" in out
    assert capture_nodes("probe-schema") == ["unknown"]


def test_two_nodes_sharing_a_budget_file_neither(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """The classifier given synthesize's budget: both calls are left out with a note, never filed
    as each other, and research is still research.

    Mutation round2_recorder_collision_overwrites: the budget map keeps the last node of a shared budget."""
    monkeypatch.setitem(config.MAX_TOKENS, "classifier", config.MAX_TOKENS["synthesize_cheap"])
    api = Haiku55Api(FANOUT.data()).install(monkeypatch)
    thread, out = run(monkeypatch, capsys, FANOUT)
    assert "status: ok" in out
    sent = [c["node"] for c in api.anthropic]
    filed = capture_nodes(thread)
    assert [f for f, s in zip(filed, sent) if s in ("classifier", "synthesize")] == ["unknown", "unknown"]
    assert [f for f, s in zip(filed, sent) if s == "research"] == ["research"] * sent.count("research")
    report = json.loads((config.RECORDINGS_DIR / f"live_t_{thread[2:]}.privacy.json").read_text(encoding="utf-8"))
    assert "2 model reply(ies) could not be matched to a node and were left out" in report["notes"]
