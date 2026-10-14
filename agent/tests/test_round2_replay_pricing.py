"""A Claude Haiku 5.5 recording replays at the prices, budgets and cap it was recorded under.

The cassettes recorded on Haiku 4.5 replay on that model's frozen card (config.REPLAY_PRICES and
the other REPLAY_ settings), so their goldens and the published demo stay as recorded. A recording
the live recorder writes names its live mode and each node's model ("recorded", decision 69), and
ledger.price_basis holds its replay to that mode instead: the model card, the output budgets, the
estimate settings and the run cap the live run had, so a replay reserves and charges each call as
the live run did and stops where it stopped. Replay rows are still replay rows, outside the build
total. A replay sweep is priced the same way, and a live sweep now records each lookup.

Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent import cli, config, graph, lifecycle
from agent.ledger import Ledger, basis_model, price_basis, price_usage
from agent.replay.cassettes import load_cassette
from agent.state import RunContext
from agent.tests.test_round2_cassettes import Haiku55Api
from agent.tests.test_round2_cassettes import live_env  # noqa: F401  (a fixture)
from agent.tests.test_round2_sweep_cap import lookup_lines, optima_cassette, twice
from agent.tests.test_sweep import (  # noqa: F401  (sweep_data is a fixture)
    RHEEM_CASSETTE,
    SUCCESSOR,
    XR_CASSETTE,
    sweep_cmd,
    sweep_data,
)

CHEAP = {"mode": "cheap", "models": {"research": config.HAIKU, "synthesize": config.HAIKU}}


def recording(recorded: dict[str, Any] | None) -> SimpleNamespace:
    """A stand in for a loaded cassette: price_basis reads only its "recorded" block."""
    return SimpleNamespace(recorded=recorded, caps=None)


def recorded_copy(path: Path, folder: Path) -> Path:
    """A synthetic sweep cassette with a "recorded" block, as the live recorder writes one."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["recorded"] = CHEAP
    out = folder / f"recorded_{path.name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data), encoding="utf-8")
    load_cassette(out)
    return out


# ---------------------------------------------------------------------------
# The price basis
# ---------------------------------------------------------------------------


def test_the_price_basis_is_the_recorded_live_mode_in_replay_and_nowhere_else() -> None:
    """Replay of a recording takes its live mode and its nodes' models; replay of a Haiku 4.5
    cassette keeps the frozen replay settings; a live run is never moved by a cassette.
    Mutations: ledger_price_basis_ignores_recorded, ledger_price_basis_moves_live_runs,
    ledger_basis_model_ignores_recording."""
    full = recording({"mode": "full", "models": {"synthesize": config.SONNET, "research": config.HAIKU}})
    assert price_basis("replay", None) == "replay"
    assert price_basis("replay", recording(None)) == "replay"
    assert price_basis("replay", recording(CHEAP)) == "cheap"
    assert price_basis("replay", full) == "full"
    assert price_basis("cheap", full) == "cheap"
    assert basis_model("full", "synthesize", full) == config.SONNET
    assert basis_model("cheap", "synthesize", recording({"mode": "cheap", "models": {"synthesize": "claude-x"}})) \
        == "claude-x"
    assert basis_model("cheap", "classifier", full) == config.MODEL_FOR["cheap"]["classifier"]
    assert basis_model("replay", "synthesize", full) == config.MODEL_FOR["replay"]["synthesize"]


def test_a_replay_row_held_at_a_live_price_mode_is_reserved_capped_and_charged_at_that_mode(
        tmp_path: Path) -> None:
    """A replay call held at the cheap price mode reserves and is charged on Claude Haiku 5.5's card,
    under the cheap run cap, in a row that stays a replay row and outside the build total.
    Mutations: ledger_reserve_ignores_price_mode, ledger_charge_ignores_price_mode."""
    ledger = Ledger(tmp_path / "ledger.sqlite", create=True)
    hold = ledger.reserve("run-1", mode="replay", node="synthesize", model=config.HAIKU, input_tokens_est=10_000,
                          max_tokens=16_000, price_mode="cheap")
    assert hold.usd == pytest.approx(price_usage(config.HAIKU, {"input_tokens": 10_000, "output_tokens": 16_000}))
    usage = {"input_tokens": 9_000, "output_tokens": 2_000}
    assert ledger.charge(hold, usage) == pytest.approx(price_usage(config.HAIKU, usage))
    assert {r["mode"] for r in ledger.rows("run-1")} == {"replay"}
    assert ledger.build_total() == 0
    # The cheap run cap holds it: a reservation past 0.15 USD at the cheap card is refused.
    big = int(config.RUN_CAP_USD["cheap"] / (config.PRICES_PER_MTOK[config.HAIKU]["output"] / 1e6)) + 1
    with pytest.raises(Exception, match="run cap"):
        ledger.reserve("run-1", mode="replay", node="synthesize", model=config.HAIKU, input_tokens_est=0,
                       max_tokens=big, price_mode="cheap")


def replay_context(tmp_path: Path, cassette: Any) -> RunContext:
    return RunContext(run_id="run-r", mode="replay", ledger_path=tmp_path / "replay_ledger.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages", cassette=cassette)


def test_the_research_budget_is_judged_at_the_recorded_card(tmp_path: Path) -> None:
    """The research sub budget (decision 26) weighs a replayed research call at the card its recording
    names; a Haiku 4.5 era cassette keeps the frozen replay card. Mutation:
    middleware_budget_check_frozen_card."""
    from agent.research.middleware import SpendCap

    usage = {"input_tokens": 8_000, "output_tokens": 4_096}
    recorded = SpendCap(replay_context(tmp_path, recording(CHEAP)), config.RESEARCH_BUDGET_USD)
    assert recorded._reservation_usd(config.HAIKU, 8_000, 4_096) == pytest.approx(price_usage(config.HAIKU, usage))
    frozen = SpendCap(replay_context(tmp_path, recording(None)), config.RESEARCH_BUDGET_USD)
    assert frozen._reservation_usd(config.HAIKU, 8_000, 4_096) == pytest.approx(
        price_usage(config.REPLAY_PRICES, usage))


def test_a_replayed_retry_is_judged_at_the_recorded_mode(tmp_path: Path) -> None:
    """The retry gate (decision 23) of a recording's replay asks what a cheap retry needs, under the
    cheap run cap, not the frozen replay reservation. Mutation: graph_retry_basis_ignored."""
    ctx = replay_context(tmp_path, recording(CHEAP))
    affordable, remaining, need = graph.retry_affordable(ctx)
    assert need == pytest.approx(graph.retry_need_usd("cheap"))
    assert need != pytest.approx(graph.retry_need_usd("replay"))
    assert remaining == pytest.approx(config.RUN_CAP_USD["cheap"]) and affordable


# ---------------------------------------------------------------------------
# The replay sweep
# ---------------------------------------------------------------------------


def test_a_replay_sweep_of_recordings_is_priced_and_capped_as_the_live_sweep_was(
        sweep_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Three units whose cassettes are recordings of a cheap sweep, under a cheap cap with room for
    three of the cheap mode's lookup worst cases (0.033 USD each at Claude Haiku 5.5's card): all three
    start, as they would live. Judged on the frozen card, whose worst case (0.13 USD) is above that
    cap, none would. The plan says which prices and output budgets it used.
    Mutations: cli_sweep_replay_price_mode_ignored, cli_sweep_replay_plan_price_mode_dropped,
    cli_sweep_replay_plan_frozen_budgets."""
    worst = lifecycle.lookup_worst_usd("cheap")
    monkeypatch.setitem(config.RUN_CAP_USD, "cheap", 3 * worst + 0.001)
    assert lifecycle.lookup_worst_usd("replay") > config.RUN_CAP_USD["cheap"]
    folder = sweep_data / "cassettes"
    paths = [recorded_copy(p, folder) for p in (optima_cassette(folder), XR_CASSETTE, RHEEM_CASSETTE)]
    args = [a for p in paths for a in ("--cassette", str(p))]
    code, out, err = sweep_cmd(capsys, "--all", "--research", *args)
    assert code == cli.EXIT_OK, out + err
    assert list(lookup_lines(out)) == ["appl-optima880", "appl-xr16", "appl-waterheater"], out
    assert "not looked up because the sweep's run cap was reached" not in out
    assert f"verified: discontinued, successor {SUCCESSOR}" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("run cap "))
    assert f"its worst case of {lifecycle.lookup_worst_usd('cheap'):.4f} USD at cheap mode's prices, as recorded" in line
    assert f"{config.MAX_TOKENS['research']} output tokens per call" in out


def test_cassettes_recorded_under_different_prices_are_refused_together(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A recording of a cheap sweep with a Haiku 4.5 era cassette: one sweep has one cap, so the
    replay refuses before any lookup and the replay ledger gets no row.
    Mutation: lifecycle_sweep_mixed_bases_accepted."""
    folder = sweep_data / "cassettes"
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(recorded_copy(XR_CASSETTE, folder)),
                               "--cassette", str(optima_cassette(folder)))
    assert code == cli.EXIT_REFUSED, out + err
    assert "recorded under different prices (cheap, replay)" in err
    assert lookup_lines(out) == {}
    assert not config.REPLAY_LEDGER_PATH.exists() or Ledger(config.REPLAY_LEDGER_PATH).rows() == []


# ---------------------------------------------------------------------------
# The live sweep records each lookup
# ---------------------------------------------------------------------------


def test_a_live_sweep_records_each_lookup_and_its_recording_replays_through_the_sweep(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`advisor property sweep --research --live` on the real clients, only their HTTP faked in
    Claude Haiku 5.5's shapes: each lookup's replies are captured and a recording candidate is
    written after it, naming its unit and its live mode. The XR16's replays through the sweep to
    the same verified successor and the same run record.
    Mutations: cli_sweep_live_lookup_not_captured, cli_sweep_live_lookup_not_recorded."""
    Haiku55Api(twice(json.loads(XR_CASSETTE.read_text(encoding="utf-8")))).install(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    code = cli.main(["property", "sweep", "prop-a", "--research", "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    assert code == cli.EXIT_OK, out + err
    runs = lookup_lines(out)
    assert list(runs) == ["appl-optima880", "appl-xr16"], out
    assert out.count("loads as a cassette: yes") == 2, out
    paths = {unit: config.RECORDINGS_DIR / f"live_{run_id.replace('-', '_')}.json" for unit, run_id in runs.items()}
    cassettes = {unit: load_cassette(path) for unit, path in paths.items()}
    for unit, cassette in cassettes.items():
        assert cassette.recorded == CHEAP, unit
    assert cassettes["appl-xr16"].input["identity"]["model"] == "XR16 4TTR6036"
    assert cassettes["appl-optima880"].input["identity"]["model"] == "Optima 880"
    live_record = json.loads((config.DATA_DIR / "runs" / f"{runs['appl-xr16']}.json").read_text(encoding="utf-8"))

    monkeypatch.setenv(config.ENV_MODE, "replay")
    code = cli.main(["property", "sweep", "prop-a", "--research", "--cassette", str(paths["appl-xr16"])])
    replay_out, replay_err = capsys.readouterr()
    assert code == cli.EXIT_OK, replay_out + replay_err
    assert f"verified: discontinued, successor {SUCCESSOR}" in replay_out
    replay_run = lookup_lines(replay_out)["appl-xr16"]
    replay_record = json.loads((config.DATA_DIR / "runs" / f"{replay_run}.json").read_text(encoding="utf-8"))
    for field in ("status", "kept", "dropped", "unit", "appliance_id"):
        assert replay_record[field] == live_record[field], field
    assert replay_record["cost_usd"] == pytest.approx(live_record["cost_usd"], rel=1e-12)
