"""Decision 69: the capabilities check for the first live step, through `advisor check-capabilities --live`.

The live code is reached only through agent.cli, like every other live command
(test_offline_guard.py). The fake client, installed as models.ChatAnthropic, is ReplayChatModel
built with each call's exact live arguments, so the structured output and tool binding code a live
call uses runs, with thinking first replies. Keys are fake values in a temp .env; nothing is sent
anywhere. Each test names its mutation.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent import cli, config, models
from agent.declined import ModelDeclined
from agent.ledger import Ledger
from agent.nodes.read_plate import PlateReadingRefused, read_plate
from agent.replay.replay_model import ReplayChatModel
from agent.state import RunContext
from agent.tests.test_haiku55_thinking import EXTRACTION, PLATE, THINKING, USAGE
from agent.tests.test_live_path import (  # noqa: F401  (live_env is a fixture)
    FLO_CASSETTE,
    NRA_DRAFT,
    SYMPTOM,
    live_env,
)

REPLIES = {
    "read_plate": {"structured": EXTRACTION, "thinking": [THINKING], "usage": USAGE},
    "classifier": {"structured": {"verdict": "match"}, "thinking": [THINKING], "usage": USAGE},
    "research": {"message": {"content": [THINKING], "usage": USAGE,
                             "tool_calls": [{"name": "search", "args": {"query": "optima 880 manual"}}]}},
    "synthesize": {"structured": NRA_DRAFT, "thinking": [THINKING], "usage": USAGE},
}


class FakeClient:
    """Builds one ReplayChatModel per call from the exact live arguments, and keeps those arguments."""

    def __init__(self, mode: str = "cheap", fail: dict[str, BaseException] | None = None) -> None:
        self.mode = mode
        self.built: list[dict[str, Any]] = []
        self.fail = fail or {}

    def __call__(self, **kwargs: Any) -> Any:
        node = next(n for n in models.NODES if models.live_model_kwargs(n, self.mode) == kwargs)
        self.built.append(kwargs)
        if node in self.fail:
            raise self.fail[node]
        return ReplayChatModel(responses=[REPLIES[node]], model=kwargs["model"], max_tokens=kwargs["max_tokens"])


class _Http400(Exception):
    status_code = 400
    message = "Image input is not supported for this model (synthetic)"


DECLINED = ModelDeclined(model=config.HAIKU, category=None, ai_message=AIMessage(
    content=[], usage_metadata={"input_tokens": 9, "output_tokens": 0, "total_tokens": 9}))


def check_capabilities(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], typed: str,
                       *extra: str) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    code = cli.main(["check-capabilities", "--live", *extra])
    out, err = capsys.readouterr()
    return code, out, err


def live_photo_ask(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                   typed: str = "no\n") -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    code = cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    return code, out, err


def record_path() -> Path:
    return config.EVAL_DIR / config.CAPABILITIES_FILE


def read_record() -> dict[str, Any]:
    return json.loads(record_path().read_text(encoding="utf-8"))


def test_capabilities_preflight_then_typed_proceed_then_each_haiku_node(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation haiku55_capabilities_skips_proceed: the check runs without the typed "proceed"."""
    client = FakeClient()
    monkeypatch.setattr(models, "ChatAnthropic", client)
    code, out, err = check_capabilities(monkeypatch, capsys, "no\n", "--new-ledger")
    assert code == cli.EXIT_REFUSED, out + err
    assert client.built == [] and Ledger(config.LEDGER_PATH).rows() == [] and not record_path().exists()
    assert f"planned: 4 small calls on {config.HAIKU}" in out
    for node in models.NODES:
        kwargs = models.live_model_kwargs(node, "cheap")
        assert f"  {node}: effort {kwargs['reasoning_effort']}, max_tokens {kwargs['max_tokens']}," in out
    assert "Type proceed to spend this" in out

    code, out, err = check_capabilities(monkeypatch, capsys, "proceed\n")
    assert code == cli.EXIT_OK, out + err
    assert client.built == [models.live_model_kwargs(node, "cheap") for node in models.NODES]
    rows = Ledger(config.LEDGER_PATH).rows()
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"] * 4
    assert {(r["node"], r["model"]) for r in rows} == {(config.CAPABILITIES_NODE, config.HAIKU)}
    assert len({r["run_id"] for r in rows}) == 1 and rows[0]["run_id"].startswith("capabilities-")
    record = read_record()
    assert (record["model"], record["image_input"], record["passed"]) == (config.HAIKU, "accepted", True)
    assert [(r["node"], r["status"], r["parsed"], r["thinking_first"]) for r in record["results"]] == [
        (node, "accepted", True, True) for node in models.NODES]
    assert "capabilities check on " + config.HAIKU + ": passed" in out


def test_full_mode_checks_only_its_haiku_nodes(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation haiku55_capabilities_checks_every_node: full mode's Sonnet synthesize is checked too."""
    monkeypatch.setenv(config.ENV_MODE, "replay")
    assert cli.main(["check-capabilities"]) == cli.EXIT_REFUSED
    assert "check-capabilities makes paid calls" in capsys.readouterr().err
    assert not config.LEDGER_PATH.exists()

    monkeypatch.setattr(config, "FULL_LIVE_APPROVED", True)  # this test's own approval, never config's
    monkeypatch.setenv(config.ENV_MODE, "full")
    client = FakeClient("full")
    monkeypatch.setattr(models, "ChatAnthropic", client)
    code, out, err = check_capabilities(monkeypatch, capsys, "proceed\n", "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    assert client.built == [models.live_model_kwargs(n, "full") for n in ("read_plate", "classifier", "research")]
    assert [r["node"] for r in read_record()["results"]] == ["read_plate", "classifier", "research"]


@pytest.mark.parametrize("failure", [_Http400(), DECLINED], ids=["http_400", "declined"])
def test_a_refused_plate_photo_stops_the_check_and_turns_plate_reading_off(
        failure: BaseException, live_env: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: haiku55_capabilities_keeps_going (the check makes the other calls after a failure);
    haiku55_plate_refusal_ignored (plate_refusal never refuses); haiku55_read_plate_ignores_refusal
    (read_plate calls the model anyway); cli_ask_ignores_plate_refusal (a live photo ask reaches its
    preflight and opens the ledger)."""
    client = FakeClient(fail={"read_plate": failure})
    monkeypatch.setattr(models, "ChatAnthropic", client)
    code, out, err = check_capabilities(monkeypatch, capsys, "proceed\n", "--new-ledger")
    assert code == cli.EXIT_LIVE_ERROR, out + err
    record = read_record()
    assert record["image_input"] == "refused" and not record["passed"]
    assert [r["node"] for r in record["results"] if r["status"] != "not run"] == ["read_plate"]
    assert len(client.built) == 1
    assert "refused the plate photo" in out

    # A live photo ask now refuses, in every live mode, before its preflight and before the ledger is
    # touched, and never moves to another model.
    rows = Ledger(config.LEDGER_PATH).rows()
    for mode in config.LIVE_MODES:
        monkeypatch.setenv(config.ENV_MODE, mode)
        code, out, err = live_photo_ask(monkeypatch, capsys, "proceed\n")
        assert code == cli.EXIT_REFUSED, out + err
        assert config.HAIKU in err and "no other model" in err and "preflight" not in out
    assert Ledger(config.LEDGER_PATH).rows() == rows and len(client.built) == 1

    # read_plate refuses too, before any reservation.
    ctx = RunContext(run_id="r", mode="cheap", ledger_path=config.LEDGER_PATH, registry_path=live_env / "g",
                     graph_path=live_env / "g.json", pages_dir=live_env / "p")
    with pytest.raises(PlateReadingRefused, match="no other model"):
        read_plate({"photo": {"path": str(PLATE), "sha256": "x"}}, Runtime(context=ctx))
    assert Ledger(config.LEDGER_PATH).rows("r") == []

    # Replay never reads the record: a replayed photo ask reads its recorded plate and pauses as before.
    monkeypatch.setenv(config.ENV_MODE, "replay")
    assert cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--cassette", str(FLO_CASSETTE)]) == 0
    assert "paused at confirm_identity" in capsys.readouterr().out


def test_a_record_for_another_model_or_no_record_is_unchecked(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing record, one written for another model, or one that does not parse leaves plate
    reading on: the live photo ask reaches its preflight. Mutation haiku55_capabilities_any_model: a
    record written for another model counts."""
    record_path().parent.mkdir(parents=True, exist_ok=True)
    for text in (None, json.dumps({"model": "an-older-model", "image_input": "refused"}), "not json"):
        if text is not None:
            record_path().write_text(text, encoding="utf-8")
        code, out, err = live_photo_ask(monkeypatch, capsys)
        assert code == cli.EXIT_REFUSED, out + err
        assert 'the typed confirmation was not exactly "proceed"' in err and "no other model" not in err
        assert "preflight" in out
