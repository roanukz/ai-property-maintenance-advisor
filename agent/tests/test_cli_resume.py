"""`advisor ask` and `advisor resume` flag handling (PLAN 8.13, SC5).

SC5 proves a thread resumes in a new process; these tests prove the flags
reach the graph: the resume value built from the paused proposal and the
flags, --code on ask, --html on resume, and the mode check. The in process
runs point every config path at tmp_path and replay a synthetic cassette.
Each test names the mutation that turns it red.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent import cli, config
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR

CASSETTE = SYNTHETIC_CASSETTE_DIR / "flo_three_candidates.json"
PLATE = config.REPO_ROOT / "demo-assets" / "plate-clear.jpg"
PROPOSED = {"manufacturer": "SUNDANCE® SPAS", "model": "OPTIMA 880", "serial": "100915742",
            "manufacture_date": "06/2014"}


def _resume_args(**flags: str | None) -> argparse.Namespace:
    values = {name: None for name in ("manufacturer", "model", "serial", "date", "code")}
    values.update(flags)
    return argparse.Namespace(**values)


def test_resume_value_applies_flags_over_the_proposal() -> None:
    """Mutations: cli_resume_ignores_identity_flags (flags dropped);
    cli_resume_empty_flag_ignored (--serial "" keeps the plate's serial);
    cli_resume_code_ignored (--code dropped)."""
    value = cli.resume_value(_resume_args(manufacturer="Sundance Spas", model="Optima 880", serial=""), PROPOSED)
    assert value == {"identity": {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "",
                                  "manufacture_date": "06/2014"}}
    assert "observed_code" not in value  # leaving --code out confirms the shown code
    assert cli.resume_value(_resume_args(code="FLO"), PROPOSED)["observed_code"] == "FLO"
    assert cli.resume_value(_resume_args(code=""), PROPOSED)["observed_code"] == ""
    assert cli.resume_value(_resume_args(), None)["identity"] == dict.fromkeys(PROPOSED)


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every path the CLI writes, moved under tmp_path; replay mode."""
    data = tmp_path / "data"
    for name, path in {
        "CHECKPOINT_PATH": data / "checkpoints.sqlite",
        "REPLAY_LEDGER_PATH": data / "ledger" / "replay_ledger.sqlite",
        "LEDGER_PATH": data / "ledger" / "ledger.sqlite",
        "REGISTRY_PATH": data / "registry.sqlite",
        "REPLAY_GRAPH_PATH": data / "replay_graph.json",
        "GRAPH_PATH": data / "graph.json",
        "PAGES_DIR": data / "pages",
        "BRIEFS_OUT_DIR": data / "briefs",
    }.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.delenv(config.ENV_MODE, raising=False)
    monkeypatch.delenv(config.ENV_CASSETTE, raising=False)
    return data


def _thread_values(thread_id: str) -> dict:
    from agent.graph import build_graph, open_checkpointer, thread_config

    return dict(build_graph(open_checkpointer(config.CHECKPOINT_PATH)).get_state(thread_config(thread_id)).values)


def _ask(capsys: pytest.CaptureFixture[str], *extra: str) -> str:
    assert cli.main(["ask", "--symptom", "hot tub not heating", "--photo", str(PLATE),
                     "--cassette", str(CASSETTE), *extra]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "paused at confirm_identity" in out
    return out.split("advisor ask: thread ", 1)[1].split(" ", 1)[0]


def test_ask_code_and_resume_flags_reach_the_graph(data_dir: Path, tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: cli_ask_code_dropped (ask passes code=None, so the pause shows
    no code); cli_resume_ignores_identity_flags (the plate's text is kept);
    cli_resume_html_ignored (update=update removed from the Command)."""
    thread_id = _ask(capsys, "--code", "FLO")
    paused = _thread_values(thread_id)
    assert paused["observed_code"] == "FLO"  # --code confirms it up front

    html = tmp_path / "out" / "brief.html"
    assert cli.main(["resume", thread_id, "--manufacturer", "Sundance Spas", "--model", "Optima 880",
                     "--serial", "", "--cassette", str(CASSETTE), "--html", str(html)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "status: ok" in out
    final = _thread_values(thread_id)
    assert final["identity"] == {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": None,
                                 "manufacture_date": None}
    assert final["observed_code"] == "FLO"
    assert final["html_path"] == str(html) and html.is_file()
    assert not config.LEDGER_PATH.exists()


def test_resume_refuses_a_thread_from_another_mode(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation cli_resume_mode_check_off: a replay thread is resumed as a live one."""
    thread_id = _ask(capsys)
    args = cli.build_parser().parse_args(["resume", thread_id, "--model", "Optima 880"])
    with pytest.raises(cli.CliRefusal, match="ran in 'replay' mode"):
        cli.cmd_resume(args, "cheap", CASSETTE)
    assert _thread_values(thread_id).get("identity_confirmed") is False
