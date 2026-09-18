"""Billing at the call sites (PLAN 8.9; decision 52).

The ledger's own tests prove charge_timeout, charge and release; these prove
the callers use the right one: a call that timed out after it was sent is
charged at its reservation (marked estimated), an error carrying a billed
message is charged its usage, a model that cannot even be built releases its
hold, and no node creates the live ledger or recreates a deleted one. Each
test names the mutation that turns it red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.exceptions import ModelTimeoutError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent import config
from agent.graph import retry_affordable
from agent.ledger import Ledger, LedgerMissing
from agent.nodes import synthesize as synth_mod
from agent.nodes.research import research
from agent.nodes.synthesize import open_ledger, paid_structured_call
from agent.replay.replay_model import ReplayChatModel
from agent.research.middleware import SpendCap
from agent.schemas import BriefDraft
from agent.state import RunContext

MESSAGES = [SystemMessage("synthetic system prompt"), HumanMessage("synthetic user prompt")]
BILLED_USAGE = {"input_tokens": 1234, "output_tokens": 56, "total_tokens": 1290}


def _ctx(tmp_path: Path, *, mode: str = "replay", ledger_path: Path | None = None) -> RunContext:
    return RunContext(run_id="run-paid", mode=mode,
                      ledger_path=ledger_path or tmp_path / "ledger" / "replay_ledger.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages", cassette=None, caps=None)


class _Raising:
    """A structured output runnable whose call fails the given way."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def with_structured_output(self, *args: Any, **kwargs: Any) -> _Raising:
        return self

    def invoke(self, messages: Any) -> Any:
        raise self.exc


class _BilledError(Exception):
    """An error raised after the reply arrived, carrying the billed message."""

    def __init__(self) -> None:
        super().__init__("reply arrived but could not be used")
        self.ai_message = AIMessage("", usage_metadata=BILLED_USAGE)


def _rows(ctx: RunContext) -> list[dict]:
    return Ledger(ctx.ledger_path).rows(ctx.run_id)


def _held_usd(rows: list[dict]) -> float:
    reserved = sum(r["usd"] for r in rows if r["kind"] == "reserve")
    released = sum(r["usd"] for r in rows if r["kind"] == "release")
    return reserved - released


@pytest.mark.parametrize("node", ["synthesize", "read_plate"])
def test_timed_out_call_is_charged_at_its_reservation(node: str, tmp_path: Path, monkeypatch) -> None:
    """Mutation synth_timeout_released: the timeout branch of paid_structured_call releases."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(synth_mod, "make_model", lambda n, c: _Raising(ModelTimeoutError("timed out")))
    with pytest.raises(ModelTimeoutError):
        paid_structured_call(ctx, node, BriefDraft, MESSAGES)
    rows = _rows(ctx)
    [reserve] = [r for r in rows if r["kind"] == "reserve"]
    [charge] = [r for r in rows if r["kind"] == "charge"]
    assert charge["node"] == node and charge["estimated"] == 1
    assert charge["usd"] == pytest.approx(reserve["usd"]) and charge["usd"] > 0
    assert Ledger(ctx.ledger_path).run_total(ctx.run_id) == pytest.approx(reserve["usd"])
    assert _held_usd(rows) == pytest.approx(0.0)


def test_error_with_a_billed_message_is_charged(tmp_path: Path, monkeypatch) -> None:
    """Mutation synth_billed_error_released: the billed ai_message branch releases instead."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(synth_mod, "make_model", lambda n, c: _Raising(_BilledError()))
    with pytest.raises(_BilledError):
        paid_structured_call(ctx, "synthesize", BriefDraft, MESSAGES)
    [charge] = [r for r in _rows(ctx) if r["kind"] == "charge"]
    assert charge["estimated"] == 0
    assert (charge["input_tokens"], charge["output_tokens"]) == (1234, 56)
    assert Ledger(ctx.ledger_path).run_total(ctx.run_id) > 0


def test_model_that_fails_to_build_releases_its_hold(tmp_path: Path, monkeypatch) -> None:
    """Mutation synth_model_built_outside_try: make_model runs after reserve but outside the try."""
    ctx = _ctx(tmp_path)

    def broken(node: str, c: RunContext) -> Any:
        raise ValueError("replay mode needs a cassette on the RunContext")

    monkeypatch.setattr(synth_mod, "make_model", broken)
    with pytest.raises(ValueError, match="needs a cassette"):
        paid_structured_call(ctx, "synthesize", BriefDraft, MESSAGES)
    rows = _rows(ctx)
    assert [r["kind"] for r in rows] == ["reserve", "release"]
    assert _held_usd(rows) == pytest.approx(0.0)
    assert Ledger(ctx.ledger_path).run_total(ctx.run_id) == 0.0


def test_research_timeout_is_charged_at_its_reservation(tmp_path: Path) -> None:
    """Mutation spendcap_timeout_released: SpendCap releases a hold whose call timed out."""
    ctx = _ctx(tmp_path)
    Ledger(ctx.ledger_path, create=True)
    cap = SpendCap(ctx, research_budget_usd=config.RESEARCH_BUDGET_USD)
    model = ReplayChatModel(responses=[], model=config.MODEL_FOR["replay"]["research"],
                            max_tokens=config.MAX_TOKENS["research"])
    request = ModelRequest(model=model, messages=[HumanMessage("find the manual")], model_settings={})

    def timed_out(req: ModelRequest) -> Any:
        raise TimeoutError("worker timed out")

    with pytest.raises(TimeoutError):
        cap.wrap_model_call(request, timed_out)
    rows = _rows(ctx)
    [reserve] = [r for r in rows if r["kind"] == "reserve"]
    [charge] = [r for r in rows if r["kind"] == "charge"]
    assert charge["node"] == "research" and charge["estimated"] == 1
    assert charge["usd"] == pytest.approx(reserve["usd"])
    assert cap.spent_usd == pytest.approx(reserve["usd"]) and cap.spent_usd > 0


def test_nodes_never_create_the_live_ledger(tmp_path: Path, monkeypatch) -> None:
    """Decision 52 and the replay rule: open_ledger creates only a replay ledger that
    is not the live one, and every caller (synthesize, research, the retry gate)
    goes through it.

    Mutation nodes_open_ledger_always_create: open_ledger always passes create=True."""
    live = tmp_path / "live" / "ledger.sqlite"
    monkeypatch.setattr(config, "LEDGER_PATH", live)

    # A live run whose ledger was deleted is refused, never recreated.
    with pytest.raises(LedgerMissing):
        open_ledger(_ctx(tmp_path, mode="cheap", ledger_path=live))
    # A replay context pointed at the live path does not create it, from any caller.
    pointed = _ctx(tmp_path, ledger_path=live)
    with pytest.raises(LedgerMissing):
        open_ledger(pointed)
    with pytest.raises(LedgerMissing):
        paid_structured_call(pointed, "synthesize", BriefDraft, MESSAGES)
    with pytest.raises(LedgerMissing):
        retry_affordable(pointed)
    with pytest.raises(LedgerMissing):
        research({"identity": None, "symptom": "not heating", "route": ["research"]}, Runtime(context=pointed))
    assert not live.exists()

    # Any other replay ledger is created on demand.
    own = _ctx(tmp_path)
    assert open_ledger(own).path.is_file()


class _Replying:
    """A structured output runnable whose reply reports no usage (a fake, or a future streaming path)."""

    def with_structured_output(self, *args: Any, **kwargs: Any) -> _Replying:
        return self

    def invoke(self, messages: Any) -> Any:
        return {"raw": AIMessage("{}"), "parsed": None, "parsing_error": ValueError("not a draft")}


class _UnbilledReplyError(Exception):
    """An error raised after a reply arrived, carrying the message but no usage on it."""

    def __init__(self) -> None:
        super().__init__("reply arrived without usage")
        self.ai_message = AIMessage("")


@pytest.mark.parametrize("failure", ["reply", "error"])
def test_reply_without_usage_is_charged_its_reservation(failure: str, tmp_path: Path, monkeypatch) -> None:
    """Mutations synth_no_usage_charged_free (a reply with no usage is charged at 0);
    synth_unbilled_reply_error_released (an error carrying a reply without usage releases its hold).
    Finding M4: a call that reached the API is never recorded as free."""
    ctx = _ctx(tmp_path, mode="cheap", ledger_path=tmp_path / "ledger" / "ledger.sqlite")
    Ledger(ctx.ledger_path, create=True)
    fake = _Replying() if failure == "reply" else _Raising(_UnbilledReplyError())
    monkeypatch.setattr(synth_mod, "make_model", lambda n, c: fake)
    if failure == "reply":
        paid_structured_call(ctx, "synthesize", BriefDraft, MESSAGES)
    else:
        with pytest.raises(_UnbilledReplyError):
            paid_structured_call(ctx, "synthesize", BriefDraft, MESSAGES)
    rows = _rows(ctx)
    [reserve] = [r for r in rows if r["kind"] == "reserve"]
    [charge] = [r for r in rows if r["kind"] == "charge"]
    assert charge["usd"] == pytest.approx(reserve["usd"]) and charge["usd"] > 0
    assert charge["estimated"] == 1
    assert Ledger(ctx.ledger_path).build_total() == pytest.approx(reserve["usd"])
    assert _held_usd(rows) == pytest.approx(0.0)


def test_paid_call_errors_leave_with_the_keys_redacted(tmp_path: Path, monkeypatch) -> None:
    """Mutation synth_error_not_scrubbed: paid_structured_call re-raises an error that echoes a key
    (findings M5 and T1: langgraph keeps a failed task's repr in the checkpoint)."""
    secret = "planted-" + "secret-" + "value-0123456789"
    ctx = _ctx(tmp_path)
    ctx.secrets = [secret]
    monkeypatch.setattr(synth_mod, "make_model", lambda n, c: _Raising(RuntimeError(f"401 bad key {secret}")))
    with pytest.raises(RuntimeError) as err:
        paid_structured_call(ctx, "synthesize", BriefDraft, MESSAGES)
    assert secret not in str(err.value) and secret not in repr(err.value)
    assert "[redacted]" in str(err.value)
    assert [r["kind"] for r in _rows(ctx)] == ["reserve", "release"]  # the billing branch is unchanged
