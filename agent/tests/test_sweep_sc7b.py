"""SC7b's first lookups never include the new run records in data/runs/ (SC14 and SC15).

A live lifecycle lookup (agent/lifecycle.py) and a live product recall run (agent/cli.py) write
their run records beside the brief runs' records, with a "kind" and no thread. SC7b reads that
folder for each model's earliest live run, so it must skip them: a lifecycle record has a live mode
and the current build but no confirmed identity. Driven through `advisor eval sc7b` on the live
path fakes, with the network blocked. The test names its mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import build_info, cli, config, lifecycle
from agent import product_recalls as pr
from agent.tests.test_live_eval import Fakes, _run, live_env  # noqa: F401  (live_env is a fixture)


def test_sc7b_never_reads_a_lifecycle_or_product_recall_record_as_a_first_lookup(
        live_env: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation eval_first_lookup_reads_other_kinds: the lifecycle record, dated before every brief
    run, is read as a first lookup and its missing identity stops the score."""
    assert _run(["eval", "sc7b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    capsys.readouterr()
    runs: Path = config.PAGES_DIR.parent / "runs"
    common = {"build_id": build_info.build_id(), "generated_at": "1999-01-01T00:00:00+00:00"}
    planted = {"lifecycle-0123456789abcdef": {"kind": lifecycle.RECORD_KIND, "mode": "cheap", "status": "saved"},
               f"{cli.RECALLS_RUN_PREFIX}0123456789abcdef": {"kind": pr.RUN_RECORD_KIND, "cost_usd": 0.0}}
    for run_id, fields in planted.items():
        (runs / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, **common, **fields}), encoding="utf-8")

    assert _run(["eval", "sc7b", "--score"], "", monkeypatch) == cli.EXIT_OK
    out = capsys.readouterr().out
    for run_id in planted:
        assert f"first lookup {run_id}" not in out
