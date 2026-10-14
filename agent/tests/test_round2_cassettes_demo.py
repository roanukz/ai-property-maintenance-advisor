"""The demo builder and check_demo accept runs recorded on Claude Haiku 5.5 (decision 69).

The published demo was recorded on Claude Haiku 4.5 and stays byte identical until it is
re-recorded (test_build_demo.py checks the installed site). Here the builder's synthetic data
folder (fixtures/demo) is turned into a Haiku 5.5 recording: each Anthropic ledger row names
Claude Haiku 5.5, its output tokens include thinking tokens and its cost is that usage on Claude
Haiku 5.5's card, as a live run charges it; the run records carry those totals; the recordings
hold content blocks with thinking markers and name the models they were recorded on. The page
must build, pass check_demo and show the recorded model, its cost lines and the thinking
inclusive output tokens. Then a real recording, made by the live recorder from Haiku 5.5 shaped
replies (test_round2_cassettes.Haiku55Api), goes through the builder's own readers and checks for
its role. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent import config
from agent.ledger import price_usage
from agent.replay import build_demo, check_demo
from agent.replay.cassettes import THINKING_MARKERS
from agent.tests.test_build_demo import Env, env  # noqa: F401  (env is a fixture)
from agent.tests.test_live_path import live_env  # noqa: F401  (live_env is a fixture)
from agent.tests.test_round2_cassettes import FLO, THINKING_TOKENS, Haiku55Api, recording, run

DEMO_THINKING_TOKENS = 240


def _marker() -> dict[str, Any]:
    return dict(THINKING_MARKERS["thinking"])


def to_haiku_5_5(e: Env) -> dict[str, float]:
    """Turn the synthetic data folder into a Claude Haiku 5.5 recording; returns each run's new total."""
    conn = sqlite3.connect(e.data / "ledger" / "ledger.sqlite")
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT * FROM entries WHERE provider = 'anthropic' AND kind = 'charge'").fetchall():
        output = int(row["output_tokens"]) + DEMO_THINKING_TOKENS
        usage = {"input_tokens": int(row["input_tokens"]) + int(row["cache_read"]) + int(row["cache_write"]),
                 "output_tokens": output,
                 "input_token_details": {"cache_read": int(row["cache_read"]), "cache_creation": int(row["cache_write"])}}
        conn.execute("UPDATE entries SET model = ?, output_tokens = ?, usd = ? WHERE id = ?",
                     (config.HAIKU, output, price_usage(config.HAIKU, usage), row["id"]))
    conn.commit()
    totals = {r[0]: float(r[1]) for r in conn.execute(
        "SELECT run_id, SUM(usd) FROM entries WHERE kind = 'charge' GROUP BY run_id")}
    conn.close()
    for path in sorted((e.data / "runs").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        record["cost_usd"] = totals.get(record["run_id"], 0.0)
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    for path in sorted((e.data / "eval").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        run_id = path.stem.split("-", 1)[1]
        if record.get("cost_usd") is not None:
            record["cost_usd"] = totals.get(run_id, 0.0)
            path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    for path in sorted((e.data / "recordings").glob("live_t_*.json")):
        if path.name.count(".") != 1:
            continue
        cassette = json.loads(path.read_text(encoding="utf-8"))
        nodes = set()
        for i, step in enumerate((cassette.get("research") or {}).get("script") or []):
            message = step["message"]
            calls = message.get("tool_calls") or []
            for j, call in enumerate(calls):
                call["id"] = f"toolu_demo_{i}_{j}"
            text = message.get("content") or ""
            message["content"] = [_marker(), *([{"type": "text", "text": text}] if text else []),
                                  *({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                                    for c in calls)]
            nodes.add("research")
        for key, node in (("classifier", "classifier"), ("synthesize", "synthesize")):
            for entry in cassette.get(key) or []:
                entry["thinking"] = [_marker()]
                nodes.add(node)
        if cassette.get("read_plate"):
            cassette["read_plate"]["thinking"] = [_marker()]
            nodes.add("read_plate")
        cassette["recorded"] = {"mode": "cheap", "models": dict.fromkeys(sorted(nodes), config.HAIKU)}
        path.write_text(json.dumps(cassette, indent=1), encoding="utf-8")
    return totals


def test_a_demo_recorded_on_haiku_5_5_builds_and_passes_check_demo(env: Env) -> None:  # noqa: F811
    """Mutation round2_demo_by_node_drops_model: the numbers panel loses each call's model, so the
    page could not say which model, and so which prices, a cost line belongs to."""
    totals = to_haiku_5_5(env)
    assert env.build() == 0
    assert check_demo.check(env.out, env.denylist()) == []
    demo = env.fixtures()
    conn = sqlite3.connect(env.data / "ledger" / "ledger.sqlite")
    conn.row_factory = sqlite3.Row
    for case in demo["cases"]:
        numbers = case["numbers"]
        assert numbers["cost_usd"] == build_demo.usd6(totals[case["run_id"]])
        rows = [r for r in numbers["by_node"] if r["provider"] == "anthropic"]
        assert rows, case["role"]
        for row in rows:
            ledger = conn.execute(
                "SELECT COUNT(*), SUM(output_tokens), SUM(usd) FROM entries WHERE run_id = ? AND node = ? "
                "AND provider = 'anthropic' AND kind = 'charge'", (case["run_id"], row["node"])).fetchone()
            assert row["model"] == config.HAIKU
            # Thinking tokens are output tokens; the cost is the ledger's, charged on the recorded model's card.
            assert row["output_tokens"] == ledger[1] and row["output_tokens"] >= DEMO_THINKING_TOKENS * ledger[0]
            assert row["usd"] == build_demo.usd6(ledger[2])
    conn.close()


def test_a_recording_whose_ledger_names_another_model_is_refused(env: Env,  # noqa: F811
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation round2_demo_recorded_models_unchecked: the builder never compares the models a
    recording names with the ledger's, so a cost line could carry another model's prices."""
    to_haiku_5_5(env)
    path = env.data / env.cassette_rel("t-5a00000000000001")
    cassette = json.loads(path.read_text(encoding="utf-8"))
    cassette["recorded"]["models"]["synthesize"] = config.SONNET
    path.write_text(json.dumps(cassette), encoding="utf-8")
    assert env.build() != 0
    err = capsys.readouterr().err
    assert f"t-5a00000000000001's ledger charged synthesize on {config.HAIKU}, but its recording names " \
        f"{config.SONNET}" in err
    assert not (env.out / "src" / "fixtures.js").exists()


def test_the_live_recorders_haiku_5_5_recording_passes_the_builders_checks_for_its_role(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    """A plate photo run (ask, then resume) recorded by the live recorder from Haiku 5.5 shaped replies
    is read by the builder as the first hot tub question: no role check fails, its trail matches its
    run record, the plate read comes back from the capture, and each model line is the ledger's.

    Mutation round2_capture_content_is_blocks: the capture keeps a reply's content list as its
    content, so the builder cannot read the plate back from it."""
    Haiku55Api(FLO.data()).install(monkeypatch)
    thread, out = run(monkeypatch, capsys, FLO)
    assert "status: ok" in out
    _, cassette = recording(live_env, thread)
    rf = build_demo.RunFiles(thread, config.DATA_DIR)
    notes = build_demo.Notes(stand_in=False)
    ledger = build_demo.Ledger(config.LEDGER_PATH)
    build_demo.check_role("hot_tub_code_first", rf, notes, ledger)
    assert notes.errors == [] and notes.warnings == []

    trail, check = build_demo.trail_for("hot_tub_code_first", rf, notes)
    record = rf.run or {}
    assert sum(1 for t in trail if t["tool"] == "search" and t["status"] == "sent") == record["searches"] > 0
    assert check["blocked_in_script"] == check["blocked_recorder_count"]
    assert rf.capture_read_plate() == cassette["read_plate"]["extraction"]

    led = build_demo.ledger_numbers(ledger.charges(thread))
    build_demo.check_recorded_models("hot_tub_code_first", rf, led["by_node"], notes)
    assert notes.errors == []
    anthropic = [r for r in led["by_node"] if r["provider"] == "anthropic"]
    assert {r["model"] for r in anthropic} == {config.HAIKU}
    assert all(r["output_tokens"] >= THINKING_TOKENS * r["calls"] for r in anthropic)
    assert led["total_usd"] == build_demo.usd6(record["cost_usd"])
