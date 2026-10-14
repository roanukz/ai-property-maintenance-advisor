"""Review of round 2 (13 October 2026), the live path findings, offline: a decline in the retry pass
(L1), a reply model with no price (R6, L5), the sweep's warning about later routes (L3) and a registry
that differs from the seed (L4).

- L1: a run that declines in research during the retry pass made one synthesize attempt between the
  passes; its recording loads and replays to the live run's state. A first pass decline followed by
  synthesize, or a second synthesize attempt, still fails.
- R6, L5: a reply that names a model with no row in config.PRICES_PER_MTOK (a dated snapshot, say) is
  left out of the recording's recorded.models, so the recording loads and replays at config.MODEL_FOR's
  card; a cassette naming such a model fails to load. The capabilities check records each reply's
  model name and stops when one is not config.HAIKU.
- L3: the live sweep's preflight says its saved edges make later questions take the top up route.
- L4: a live step refuses while the registry differs from the seed, naming the reload.

Live runs go through `advisor ... --live` with the real clients and only their HTTP faked
(test_round2_cassettes.Haiku55Api), or with fake chat clients; sockets are blocked and every key
value is fake. Each test names, in its docstring, the planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agent import cli, config, lifecycle, models
from agent import product_recalls as pr
from agent.registry import open_registry
from agent.replay.cassettes import CassetteError, load_cassette, validate_cassette
from agent.replay.replay_model import ReplayChatModel
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR
from agent.tests.test_haiku55_capabilities import REPLIES, FakeClient, check_capabilities, read_record
from agent.tests.test_live_path import PLATE, SYMPTOM, live_env, run_record  # noqa: F401  (live_env is a fixture)
from agent.tests.test_round2_cassettes import (
    CATEGORY,
    FLO,
    Case,
    Haiku55Api,
    assert_replay_matches,
    recording,
    run,
)
from agent.tests.test_round2_model_number import _old_registry
from agent.tests.test_sweep import live_sweep, sweep_cmd, sweep_data  # noqa: F401  (fixtures)

RETRY_CASSETTE = SYNTHETIC_CASSETTE_DIR / "retry_typical.json"
RETRY_IDENTITY = {"manufacturer": "Examplecorp", "model": "EX-100", "serial": "", "manufacture_date": ""}
RETRY = Case(RETRY_CASSETTE, ["--symptom", "heater will not start", "--identity", json.dumps(RETRY_IDENTITY)])
# Research call 6 is the first turn of the retry pass: calls 0 to 5 are the first pass, which call 5 closes.
RETRY_PASS_FIRST_CALL = 6
DECLINE = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": CATEGORY},
           "usage": {"input_tokens": 900, "output_tokens": 0}}
SNAPSHOT_SUFFIX = "-20261001"  # a dated snapshot name, which config.PRICES_PER_MTOK has no row for


# ---------------------------------------------------------------------------
# L1: a decline in the retry pass
# ---------------------------------------------------------------------------


def test_a_decline_in_the_retry_pass_records_a_cassette_that_replays_to_the_live_run(
        live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """validate, retry_gate, then research declines on its first retry turn: the recording holds the
    one synthesize attempt made before the retry, loads, and replays to the same path, run record and
    brief. Mutation: cassettes_retry_pass_synthesize_refused (any synthesize response after a research
    decline fails the load, as before)."""
    data = RETRY.data()
    assert not data["research"]["script"][RETRY_PASS_FIRST_CALL - 1]["message"]["tool_calls"]
    Haiku55Api(data, decline=("research", RETRY_PASS_FIRST_CALL)).install(monkeypatch)
    live_thread, live_out = run(monkeypatch, capsys, RETRY)
    assert f"stop reason: model_declined: {CATEGORY} (research)" in live_out
    assert "retry_gate" in live_out and "loads as a cassette: yes" in live_out, live_out
    path, cassette = recording(live_env, live_thread)
    load_cassette(path)
    assert len(cassette["synthesize"]) == 1 and cassette["synthesize"][0].get("stop_reason") != "refusal"
    assert cassette["research"]["script"][-1]["stop_reason"] == "refusal"
    assert run_record(live_thread)["refusal_origin"] == "model_declined"

    replay_thread, replay_out = run(monkeypatch, capsys, RETRY, cassette=path)
    assert_replay_matches(live_thread, replay_thread, live_out, replay_out)


def _retry_variant(script_len: int, synthesize: int) -> dict[str, Any]:
    """retry_typical with research cut to `script_len` turns plus a decline, and `synthesize` attempts."""
    data = json.loads(RETRY_CASSETTE.read_text(encoding="utf-8"))
    kept = data["research"]["script"][:script_len]
    calls = [c["name"] for turn in kept for c in turn["message"]["tool_calls"]]
    results = data["research"]["tool_results"]
    data["research"]["tool_results"] = [r for tool in ("search", "fetch")
                                        for r in [r for r in results if r["tool"] == tool][:calls.count(tool)]]
    data["research"]["script"] = kept + [copy.deepcopy(DECLINE)]
    data["synthesize"] = (data["synthesize"] * 2)[:synthesize]
    return data


def test_a_first_pass_decline_or_a_second_synthesize_attempt_still_fails_to_load() -> None:
    """Only the retry pass case is allowed: a decline in the first pass (no earlier turn closed it)
    followed by synthesize fails, as do two synthesize attempts after a retry pass decline and a
    synthesize decline before it. Mutation: cassettes_retry_pass_any_synthesize (every synthesize
    response after a research decline is accepted)."""
    validate_cassette(_retry_variant(RETRY_PASS_FIRST_CALL, 1))  # the retry pass case loads
    for data in (_retry_variant(3, 1), _retry_variant(RETRY_PASS_FIRST_CALL, 2)):
        with pytest.raises(CassetteError, match="holds responses after the decline in research.script"):
            validate_cassette(data)
    declined_draft = _retry_variant(RETRY_PASS_FIRST_CALL, 0)
    declined_draft["synthesize"] = [{"attempt": 1, **copy.deepcopy(DECLINE)}]
    with pytest.raises(CassetteError, match="holds responses after the decline in research.script"):
        validate_cassette(declined_draft)


# ---------------------------------------------------------------------------
# R6, L5: a reply model with no price
# ---------------------------------------------------------------------------


class SnapshotApi(Haiku55Api):
    """Haiku55Api whose replies name a dated snapshot instead of the model the request asked for."""

    def reply(self, body: dict[str, Any]) -> dict[str, Any]:
        out = super().reply(body)
        out["model"] = body["model"] + SNAPSHOT_SUFFIX
        return out


def test_a_reply_model_with_no_price_is_left_out_and_the_recording_still_replays(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Every reply names a snapshot with no price: the recording is written and loads, records its
    mode with no models (so replay prices each node as config.MODEL_FOR names it), says why in its
    notes, and replays to the live run. A cassette that names the snapshot fails to load. Mutations:
    recorder_unpriced_model_kept (the snapshot goes into recorded.models, so the candidate does not
    load), cassettes_unpriced_model_accepted (a cassette naming it loads, and its replay would raise)."""
    snapshot = config.HAIKU + SNAPSHOT_SUFFIX
    assert snapshot not in config.PRICES_PER_MTOK
    SnapshotApi(FLO.data()).install(monkeypatch)
    live_thread, live_out = run(monkeypatch, capsys, FLO)
    assert "status: ok" in live_out and "loads as a cassette: yes" in live_out, live_out
    path, cassette = recording(live_env, live_thread)
    load_cassette(path)
    assert cassette["recorded"] == {"mode": "cheap", "models": {}}
    notes = json.loads(path.with_suffix(".privacy.json").read_text(encoding="utf-8"))["notes"]
    assert any(snapshot in n and "no price in config.PRICES_PER_MTOK" in n for n in notes), notes

    replay_thread, replay_out = run(monkeypatch, capsys, FLO, cassette=path)
    assert_replay_matches(live_thread, replay_thread, live_out, replay_out)

    planted = path.with_name("planted.json")
    named = json.loads(path.read_text(encoding="utf-8"))
    named["recorded"]["models"] = {"research": snapshot}
    planted.write_text(json.dumps(named), encoding="utf-8")
    with pytest.raises(CassetteError, match="no price in config.PRICES_PER_MTOK"):
        load_cassette(planted)


class SnapshotClient(FakeClient):
    """FakeClient whose replies name a dated snapshot of the model each call asked for."""

    def __call__(self, **kwargs: Any) -> Any:
        node = next(n for n in models.NODES if models.live_model_kwargs(n, self.mode) == kwargs)
        self.built.append(kwargs)
        return ReplayChatModel(responses=[REPLIES[node]], model=kwargs["model"] + SNAPSHOT_SUFFIX,
                               max_tokens=kwargs["max_tokens"])


def test_the_capabilities_check_records_each_reply_model_and_stops_on_another(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Each call's record names the model its reply carried. Replies naming config.HAIKU pass; replies
    naming a snapshot stop the check with a STOP line naming it, though every call parsed, so the
    mismatch shows at the batch's first live step. Mutations: capabilities_reply_model_not_compared,
    capabilities_reply_model_not_recorded."""
    monkeypatch.setattr(models, "ChatAnthropic", FakeClient())
    code, out, err = check_capabilities(monkeypatch, capsys, "proceed\n", "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    record = read_record()
    assert record["passed"] and [r["reply_model"] for r in record["results"]] == [config.HAIKU] * 4
    assert "STOP" not in out

    snapshot = config.HAIKU + SNAPSHOT_SUFFIX
    monkeypatch.setattr(models, "ChatAnthropic", SnapshotClient())
    code, out, err = check_capabilities(monkeypatch, capsys, "proceed\n")
    assert code == cli.EXIT_LIVE_ERROR, out + err
    record = read_record()
    assert not record["passed"] and [r["reply_model"] for r in record["results"]] == [snapshot] * 4
    assert all(r["status"] == "accepted" and r["parsed"] for r in record["results"])
    assert f"capabilities check on {config.HAIKU}: STOPPED" in out
    assert f"  STOP: replies named {snapshot}, not {config.HAIKU}." in out
    assert "a call was not accepted or did not parse" not in out


# ---------------------------------------------------------------------------
# L3: the live sweep warns about later routes
# ---------------------------------------------------------------------------


def test_the_live_sweep_preflight_says_saved_edges_make_later_questions_top_up(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Before the typed prompt, the live preflight names the runtime graph and says a saved successor
    or discontinued edge sends a later question about the unit to route row 4, so measured first
    lookups go first. Mutation: cli_sweep_later_routes_not_printed."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_REFUSED, out + err
    line = lifecycle.later_routes_line(config.GRAPH_PATH)
    assert str(config.GRAPH_PATH) in line and "(route row 4), not a first lookup" in line
    lines = out.splitlines()
    assert line in lines and lines.index(line) == len(lines) - 3 and lines[-1].startswith("Type proceed")


# ---------------------------------------------------------------------------
# L4: a live step refuses a registry that differs from the seed
# ---------------------------------------------------------------------------


def _stale_registry() -> None:
    """config.REGISTRY_PATH made before the model number and warranty coverage, as data/ holds it."""
    config.REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _old_registry(config.REGISTRY_PATH)


def _never_fetch(url: str, **_: Any) -> bytes:
    raise AssertionError(f"CPSC was queried: {url}")


def _refused_for_drift(code: int, out: str, err: str) -> None:
    assert code == cli.EXIT_REFUSED, out + err
    assert f"the registry at {config.REGISTRY_PATH} differs from the seed" in err
    assert "appl-xr16 (registry none, seed \"4TTR6036\")" in err
    assert "open_registry(config.REGISTRY_PATH)" in err and "Nothing was sent or spent" in err
    assert "Type proceed" not in out


def test_live_product_recalls_refuse_a_stale_registry_until_the_seed_is_loaded(
        sweep_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """On a registry made before the model number, `property recalls --live` refuses before its
    preflight, so no check is stored with a null model number; once the seed is loaded the preflight
    compares the Trane unit's model number. Mutation: cli_recalls_live_skips_registry_drift."""
    _stale_registry()
    monkeypatch.setattr(pr, "urllib_fetch", _never_fetch)
    # "no" throughout: were the guard gone, the preflight would print and nothing would be sent.
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))
    code = cli.main(["property", "recalls", "--all", "--live"])
    out, err = capsys.readouterr()
    _refused_for_drift(code, out, err)
    assert not config.RECALLS_DIR.exists()

    open_registry(config.REGISTRY_PATH)
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))
    code = cli.main(["property", "recalls", "--all", "--live"])
    out, err = capsys.readouterr()
    assert code == cli.EXIT_REFUSED and "differs from the seed" not in err
    assert 'compared exactly in each product recall: model number "4TTR6036", then model on record' in out


def test_a_live_sweep_refuses_a_stale_registry(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The live sweep reads the registry, so it refuses on a stale one (here migrated, the column
    null) before its preflight and before any call. Mutations: cli_live_session_skips_registry_drift,
    cli_sweep_session_without_registry_check."""
    _stale_registry()
    open_registry(config.REGISTRY_PATH, seed=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))  # were the guard gone, nothing would be spent
    _refused_for_drift(*sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger"))


def test_a_live_ask_refuses_a_stale_registry(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """A live ask reads the registry, so it refuses on a stale one before its preflight and before any
    call. Mutations: cli_live_session_skips_registry_drift, cli_ask_session_without_registry_check."""
    _stale_registry()
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))  # were the guard gone, nothing would be spent
    code = cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    _refused_for_drift(code, out, err)
