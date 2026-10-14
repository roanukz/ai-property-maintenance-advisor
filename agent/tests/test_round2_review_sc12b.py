"""Review of round 2 (13 October 2026), finding L2: SC12b reads an input recording filed under a superseded
build's folder; and finding L4 for the live evaluation batches.

Decision 67 moved both SC12B_INPUT_RUNS recordings to data/superseded/<build>/recordings/, so the
SC12b re-measure planned for the live batch refused before any spend. `advisor eval sc12b --live`
now reads a recording from config.RECORDINGS_DIR when it is there, else from the one superseded
folder that holds it, names that file in the preflight, and refuses when none or more than one does.
The live evaluation batches (sc3b, sc7b and plates through eval_sc3b, and sc12b) read the registry, so
each refuses while the registry differs from the seed (L4).

Reached through agent.cli only (test_offline_guard.py), on test_safety_eval's synthetic locked set
and test_live_eval's fakes; every path is under tmp_path. Each test names, in its docstring, the
planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent import cli, config
from agent.tests.test_live_eval import Fakes, _run, live_env  # noqa: F401  (live_env is a fixture)
from agent.tests.test_round2_model_number import _old_registry
from agent.tests.test_safety_eval import _sc12b, _sc12b_ready

NAME = "live_t_5678.json"


def _file_away(*builds: str) -> list[Path]:
    """Move the input recording into each named superseded build's recordings/; returns the copies."""
    original = config.RECORDINGS_DIR / NAME
    copies = []
    for build in builds:
        folder = config.RECORDINGS_DIR.parent / "superseded" / build / "recordings"
        folder.mkdir(parents=True)
        copies.append(Path(shutil.copy(original, folder / NAME)))
    original.unlink()
    return copies


def test_sc12b_reads_an_input_filed_under_one_superseded_build_and_names_it(
        live_env: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """With the recording only under data/superseded/<build>/recordings/, the preflight is printed,
    names the file it read, and the batch waits for the typed "proceed" (here "no", so nothing is
    spent). Mutations: sc12b_superseded_fallback_dropped (the command refuses, as before),
    sc12b_preflight_hides_input_folder (the preflight does not say which file it read)."""
    _sc12b_ready(monkeypatch)
    [copy] = _file_away("2026-09-30-build-839b1854a0aaf25e")
    assert _sc12b(monkeypatch, "no\n") == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert f"input of t-5678: read from {copy}" in captured.out
    assert "Type proceed" in captured.out and "the typed confirmation was not exactly" in captured.err
    assert "is not at" not in captured.err
    assert live_env.models == [] and live_env.tavily == []


def test_sc12b_refuses_an_input_two_superseded_builds_hold(
        live_env: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Two superseded builds holding the same recording leave the input unclear, so the command
    refuses, naming both, before any ledger is read. Mutation: sc12b_two_superseded_inputs_accepted."""
    _sc12b_ready(monkeypatch)
    copies = _file_away("2026-09-18-build-a", "2026-09-30-build-b")
    config.LEDGER_PATH.unlink()
    assert _sc12b(monkeypatch, "proceed\n") == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "more than one superseded build holds it" in err and all(str(p) in err for p in copies)
    assert "Nothing was spent" in err
    assert not config.LEDGER_PATH.exists() and live_env.models == []


def test_the_live_evaluation_batches_refuse_a_stale_registry(
        live_env: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """On a registry made before the model number and warranty coverage, `advisor eval sc3b --live` and
    `advisor eval sc12b --live` refuse before their preflight, naming the reload ("no" is typed, so
    nothing would be spent were the guard gone). Mutations: eval_sc3b_session_without_registry_check,
    sc12b_session_without_registry_check."""
    _sc12b_ready(monkeypatch)
    config.REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.REGISTRY_PATH.unlink(missing_ok=True)
    _old_registry(config.REGISTRY_PATH)
    for run in (lambda: _run(["eval", "sc3b", "--live"], "no\n", monkeypatch), lambda: _sc12b(monkeypatch, "no\n")):
        assert run() == cli.EXIT_REFUSED
        captured = capsys.readouterr()
        assert f"the registry at {config.REGISTRY_PATH} differs from the seed" in captured.err
        assert "open_registry(config.REGISTRY_PATH)" in captured.err
        assert "Type proceed" not in captured.out
    assert live_env.models == [] and live_env.tavily == []
