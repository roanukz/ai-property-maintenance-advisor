"""One run cap for the whole sweep (Roanuk, 13 October 2026): the lifecycle lookups share it.

All of a sweep's lookups together spend at most config.RUN_CAP_USD for the mode. Each lookup is
its own ledger run under what is left of that cap (lifecycle.SweepBudget), and a lookup whose worst
case what is left cannot cover is not started, is reported as not looked up because the sweep's
run cap was reached, and saves nothing. The preflight and the replay plan show the sweep as one
plan whose worst case is the cap.

Replay tests use the hand built synthetic cassettes (and copies of the XR16 one retargeted to the
Optima 880); live tests run on fake clients with the network blocked and fake key values in a temp
.env. Every page, URL and successor here is synthetic. Each test names the mutations in
mutations.toml that turn it red.
"""

from __future__ import annotations

import copy
import io
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from agent import cli, config, lifecycle, sweep
from agent.kg import KnowledgeGraph
from agent.ledger import Ledger, call_prices, price_usage
from agent.registry import open_registry
from agent.tests.test_sweep import (  # noqa: F401  (sweep_data and live_sweep are fixtures)
    OPTIMA,
    RHEEM_CASSETTE,
    SUCCESSOR,
    XR16,
    XR_CASSETTE,
    entry,
    install_fakes,
    live_sweep,
    page_of,
    sweep_cmd,
    sweep_data,
)

XR_MODEL = "XR16 4TTR6036"


def optima_cassette(folder: Path) -> Path:
    """The XR16 cassette retargeted to the Optima 880: its identity, query, pages and quotes name it."""
    text = XR_CASSETTE.read_text(encoding="utf-8").replace(XR_MODEL, OPTIMA[1])
    data = json.loads(text)
    data["case"] = "sweep_optima_successor_synthetic"
    data["input"]["identity"]["manufacturer"] = OPTIMA[0]
    path = folder / "sweep_optima_successor_synthetic.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def lookup_lines(out: str) -> dict[str, str]:
    """Each looked up unit's appliance ID and its lookup's run ID, from the sweep's output."""
    found = {}
    for line in out.splitlines():
        if ": lookup lifecycle-" in line:
            found[line.split()[0]] = line.split("lookup ", 1)[1].split(":", 1)[0]
    return found


def reserve_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Record (run ID, run_cap_usd) of every model call reservation."""
    seen: list[tuple[str, Any]] = []
    real = Ledger.reserve

    def reserve(self: Ledger, run_id: str, **kwargs: Any) -> Any:
        seen.append((run_id, kwargs.get("run_cap_usd")))
        return real(self, run_id, **kwargs)

    monkeypatch.setattr(Ledger, "reserve", reserve)
    return seen


# ---------------------------------------------------------------------------
# The worst case of one lookup, and the plan
# ---------------------------------------------------------------------------


def expected_worst(mode: str) -> float:
    """lookup_worst_usd's arithmetic, written out: the top_up row's calls at full budgets, then the
    structured call, at the prices and estimate settings the ledger uses in `mode`."""
    replay = mode == "replay"
    row = config.RESEARCH_LIMITS["top_up"]
    model = config.HAIKU
    margin = config.REPLAY_ESTIMATE_MARGIN if replay else config.ESTIMATE_MARGIN[model]
    tool = config.REPLAY_TOOL_PROMPT_TOKENS if replay else config.TOOL_PROMPT_TOKENS[model]
    out = (config.REPLAY_MAX_TOKENS if replay else config.MAX_TOKENS)["research"]
    synth_out = (config.REPLAY_MAX_TOKENS if replay else config.MAX_TOKENS)["synthesize_cheap"]
    results = row["search"] * config.RESEARCH_TOKENS_PER_SEARCH + row["fetch"] * math.ceil(
        config.FETCH_EXCERPT_MAX_CHARS / config.CHARS_PER_TOKEN_ESTIMATE)
    total = 0.0
    for k in range(row["loop_guard"]):
        tokens = math.ceil((config.RESEARCH_CALL_BASE_TOKENS + results + tool + k * out) * margin)
        total += price_usage(call_prices(mode, "anthropic", model, tokens), {"input_tokens": tokens, "output_tokens": out})
    tokens = math.ceil((config.SYNTH_SOURCE_TOKEN_BUDGET + config.SYNTH_PROMPT_TOKENS_ESTIMATE) * margin)
    total += price_usage(call_prices(mode, "anthropic", model, tokens), {"input_tokens": tokens, "output_tokens": synth_out})
    credits = row["search"] * config.TAVILY_CREDITS["search_basic"] + row["fetch"] * config.TAVILY_CREDITS[
        "extract_basic_per_5_urls"]
    return total + credits * config.tavily_credit_usd()


def test_a_lookups_worst_case_is_every_call_its_limits_allow_at_the_ledgers_prices() -> None:
    """Live and replay worst cases follow the arithmetic, each below the sweep's cap, so the first
    lookup of a sweep can always start. Mutations: lifecycle_worst_drops_extract,
    lifecycle_worst_replay_at_live_prices."""
    for mode in ("cheap", "replay"):
        assert lifecycle.lookup_worst_usd(mode) == pytest.approx(expected_worst(mode), rel=1e-12)
        assert 0 < lifecycle.lookup_worst_usd(mode) < lifecycle.sweep_cap_usd(mode)
    # Replay prices every call as recorded (config.REPLAY_PRICES), so its worst case is the higher one.
    assert lifecycle.lookup_worst_usd("replay") > lifecycle.lookup_worst_usd("cheap")


def test_the_replay_plan_is_one_plan_whose_worst_case_is_the_cap(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A replay --research sweep prints the planned lookups, their typical total and "run cap 0.15
    USD for the whole sweep" with a worst case equal to the cap, never a per lookup cap or a summed
    worst case. Mutations: lifecycle_sweep_plan_worst_summed, lifecycle_cap_line_per_lookup."""
    code, out, err = sweep_cmd(capsys, "--all", "--research")
    assert code == cli.EXIT_OK, out + err
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    eligible = [registry.get_appliance(a) for a in ("appl-optima880", "appl-xr16", "appl-waterheater")]
    plan = lifecycle.sweep_plan("replay", eligible)
    cap = config.RUN_CAP_USD["cheap"]
    assert plan.worst_usd == cap
    assert plan.typical_usd == pytest.approx(sum(lifecycle.lookup_plan("replay", a).typical_usd for a in eligible))
    assert f"planned: {plan.label}" in out
    assert f"  estimated cost if live: typical {plan.typical_usd:.4f} USD, worst case {cap:.4f} USD" in out
    for i, unit in enumerate(eligible, start=1):
        assert f"  note: lookup {i}: {lifecycle.unit_label(unit)}, typical " in out
    line = next(ln for ln in out.splitlines() if ln.startswith("run cap "))
    assert line.startswith(f"run cap 0.15 USD for the whole sweep: 3 lookup(s), typical total {plan.typical_usd:.4f} "
                           f"USD, worst case {cap:.4f} USD; ")
    assert f"its worst case of {lifecycle.lookup_worst_usd('replay'):.4f} USD at the replay ledger's prices" in line
    assert "per lookup" not in out and "total estimated cost" not in out


# ---------------------------------------------------------------------------
# Replay: the cap is shared, and a skipped unit saves nothing
# ---------------------------------------------------------------------------


def test_three_replay_lookups_share_one_cap(sweep_data: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    """Three units, each with a cassette that costs far less than the cap: the first two run, the
    second under the cap less what the first spent, and the third is not started because what is
    left no longer covers a lookup's worst case. Under a per lookup cap all three would have run.
    Mutations: cli_sweep_replay_spend_not_recorded, cli_sweep_replay_cap_not_shared,
    lifecycle_sweep_covers_always."""
    seen = reserve_spy(monkeypatch)
    optima = optima_cassette(sweep_data / "cassettes")
    code, out, err = sweep_cmd(capsys, "--all", "--research", "--cassette", str(optima), "--cassette",
                               str(XR_CASSETTE), "--cassette", str(RHEEM_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    runs = lookup_lines(out)
    assert list(runs) == ["appl-optima880", "appl-xr16"], out
    ledger = Ledger(config.REPLAY_LEDGER_PATH)
    first, second = (ledger.run_total(runs[a]) for a in ("appl-optima880", "appl-xr16"))
    cap, worst = config.RUN_CAP_USD["cheap"], lifecycle.lookup_worst_usd("replay")
    # The premise: each lookup alone is far under the cap, and two leave less than one worst case.
    assert 0 < first < cap / 5 and 0 < second < cap / 5
    assert cap - first >= worst > cap - first - second
    assert first + second <= cap

    caps_by_run: dict[str, set[Any]] = {}
    for run_id, run_cap in seen:
        caps_by_run.setdefault(run_id, set()).add(run_cap)
    assert caps_by_run[runs["appl-optima880"]] == {cap}
    (second_cap,) = caps_by_run[runs["appl-xr16"]]
    assert second_cap == pytest.approx(cap - first)
    assert set(caps_by_run) == set(runs.values()), "the third unit reserved nothing"

    remaining = cap - first - second
    assert (f"  appl-waterheater Rheem PRO+E50 M2 RH92 CL: not looked up because the sweep's run cap was "
            f"reached: {remaining:.4f} USD of the {cap:.4f} USD cap left, below one lookup's worst case of "
            f"{worst:.4f} USD; nothing saved") in out.splitlines()


def test_a_unit_the_cap_cannot_cover_saves_nothing(sweep_data: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """With the cap set so one lookup's worst case fits once, the XR16, whose cassette saves a
    verified successor when it runs, is not looked up: no ledger row, run record or graph edge for
    it, and its page entry keeps the no discontinuation sentence. Rerun with the configured cap, the
    same cassette saves the successor. Mutations: cli_sweep_replay_skip_falls_through,
    lifecycle_sweep_covers_always."""
    worst, configured = lifecycle.lookup_worst_usd("replay"), config.RUN_CAP_USD["cheap"]
    monkeypatch.setitem(config.RUN_CAP_USD, "cheap", worst + 0.008)
    optima = optima_cassette(sweep_data / "cassettes")
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(optima), "--cassette",
                               str(XR_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    runs = lookup_lines(out)
    assert list(runs) == ["appl-optima880"]
    assert Ledger(config.REPLAY_LEDGER_PATH).run_total(runs["appl-optima880"]) > 0.008
    skipped = next(ln for ln in out.splitlines() if ln.startswith(f"  appl-xr16 Trane {XR_MODEL}: not looked up"))
    assert "because the sweep's run cap was reached" in skipped and skipped.endswith("; nothing saved")

    assert {r["run_id"] for r in Ledger(config.REPLAY_LEDGER_PATH).rows()} == {runs["appl-optima880"]}
    records = [json.loads(p.read_text(encoding="utf-8")) for p in (sweep_data / "runs").glob("*.json")]
    assert [r["appliance_id"] for r in records] == ["appl-optima880"]
    graph = KnowledgeGraph.load(config.REPLAY_GRAPH_PATH) if config.REPLAY_GRAPH_PATH.exists() else KnowledgeGraph()
    assert graph.superseded_by(*XR16) == []
    assert sweep.NONE_DOCUMENTED in entry(page_of("prop-a"), "appl-xr16")

    monkeypatch.setitem(config.RUN_CAP_USD, "cheap", configured)
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(XR_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    assert [e["successor_model"] for e in KnowledgeGraph.load(config.REPLAY_GRAPH_PATH).superseded_by(*XR16)] == [
        SUCCESSOR]


# ---------------------------------------------------------------------------
# Live, on fake clients
# ---------------------------------------------------------------------------


def twice(data: dict[str, Any]) -> dict[str, Any]:
    """The XR16 cassette's scripts served twice, so two lookups can run on the same fakes."""
    doubled = copy.deepcopy(data)
    doubled["research"]["script"] = data["research"]["script"] * 2
    doubled["research"]["tool_results"] = data["research"]["tool_results"] * 2
    doubled["synthesize"] = data["synthesize"] * 2
    return doubled


def test_live_preflight_shows_one_plan_with_the_cap_as_its_worst_case(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The live preflight prints the planned lookups, their typical total and the cap as the
    sweep's worst case, with no summed worst case, and the build check needs only the cap.
    Mutations: lifecycle_sweep_plan_worst_summed, cli_sweep_need_usd_summed."""
    needs: list[Any] = []
    real = Ledger.assert_can_start_live_run

    def check(self: Ledger, mode: str, **kwargs: Any) -> None:
        needs.append(kwargs.get("need_usd"))
        return real(self, mode, **kwargs)

    monkeypatch.setattr(Ledger, "assert_can_start_live_run", check)
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_REFUSED, out + err
    cap = config.RUN_CAP_USD["cheap"]
    assert needs == [cap]
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    plan = lifecycle.sweep_plan("cheap", [registry.get_appliance(a) for a in ("appl-optima880", "appl-xr16")])
    assert out.count("planned: ") == 1 and f"planned: {plan.label}" in out
    assert f"  estimated cost: typical {plan.typical_usd:.4f} USD, worst case {cap:.4f} USD" in out
    assert "total estimated cost" not in out
    assert f"run cap 0.15 USD for the whole sweep: 2 lookup(s), typical total {plan.typical_usd:.4f} USD" in out


def test_live_lookups_share_one_cap(live_sweep: Path, monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    """After "proceed", two live lookups on fakes: the second's every reservation carries the cap
    less what the first was charged. Mutation: cli_sweep_live_spend_not_recorded."""
    seen = reserve_spy(monkeypatch)
    install_fakes(monkeypatch, twice(json.loads(XR_CASSETTE.read_text(encoding="utf-8"))))
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    runs = lookup_lines(out)
    assert list(runs) == ["appl-optima880", "appl-xr16"], out
    ledger = Ledger(config.LEDGER_PATH)
    first = ledger.run_total(runs["appl-optima880"])
    cap = config.RUN_CAP_USD["cheap"]
    assert first > 0
    assert {c for r, c in seen if r == runs["appl-optima880"]} == {cap}
    (second_cap,) = {c for r, c in seen if r == runs["appl-xr16"]}
    assert second_cap == pytest.approx(cap - first)
    assert first + ledger.run_total(runs["appl-xr16"]) <= cap


def test_live_unit_the_cap_cannot_cover_is_not_started_and_saves_nothing(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """With room for one lookup's worst case only, the live sweep looks up the Optima and reports
    the XR16 as not looked up: no client call, ledger row or graph edge for it. Mutation:
    cli_sweep_live_covers_ignored."""
    worst = lifecycle.lookup_worst_usd("cheap")
    monkeypatch.setitem(config.RUN_CAP_USD, "cheap", worst + 0.001)
    fakes = install_fakes(monkeypatch, twice(json.loads(XR_CASSETTE.read_text(encoding="utf-8"))))
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    runs = lookup_lines(out)
    assert list(runs) == ["appl-optima880"], out
    ledger = Ledger(config.LEDGER_PATH)
    assert 0.001 < ledger.run_total(runs["appl-optima880"]) < worst
    assert any(ln.startswith(f"  appl-xr16 Trane {XR_MODEL}: not looked up because the sweep's run cap was reached")
               for ln in out.splitlines())
    assert {r["run_id"] for r in ledger.rows()} == {runs["appl-optima880"]}
    assert len(fakes["checks"]) == 4, "one lookup's three model calls and one search, no more"
    graph = KnowledgeGraph.load(config.GRAPH_PATH) if config.GRAPH_PATH.exists() else KnowledgeGraph()
    assert graph.superseded_by(*XR16) == []
