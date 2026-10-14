"""Decision 69: a Claude Haiku 5.5 decline (HTTP 200, stop_reason "refusal") is charged, named and final.

It is never retried, on this model or another; the run record names it with its category; the run
ends on a brief code writes. Live replies come from the real DeclineAwareChatAnthropic with its HTTP
call replaced (see test_haiku55_thinking.RecordedApi). Each test names its mutation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent import config
from agent.declined import NO_CATEGORY, REFUSAL_ORIGIN, ModelDeclined, declined_update, is_declined
from agent.ledger import Ledger
from agent.nodes.classify import ClassifierVerdict, classify_symptom
from agent.nodes.read_plate import read_plate
from agent.nodes.synthesize import paid_structured_call
from agent.replay.replay_model import ReplayChatModel
from agent.replay.tool_stubs import StubInner
from agent.research.agent import build_research_agent
from agent.research.tools import make_research_tools
from agent.schemas import Brief
from agent.tests.test_haiku55_thinking import PLATE, THINKING, USAGE, RecordedApi, Script, ctx_for, live_chat, message

REFUSAL = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
           "usage": USAGE}


def test_live_refusal_raises_at_the_model_boundary_with_its_category_and_usage(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation haiku55_live_refusal_unchecked: DeclineAwareChatAnthropic hands a refusal on as a reply."""
    RecordedApi([
        message([], stop_reason="refusal", stop_details={"type": "refusal", "category": "bio", "explanation": None}),
        message([], stop_reason="refusal", stop_details=None),
        message([THINKING, {"type": "text", "text": "fine"}]),
    ]).install(monkeypatch)
    chat = live_chat("synthesize")
    with pytest.raises(ModelDeclined) as caught:
        chat.invoke([HumanMessage("synthetic")])
    assert (caught.value.model, caught.value.category) == (config.HAIKU, "bio")
    assert caught.value.ai_message.usage_metadata["input_tokens"] == USAGE["input_tokens"]
    with pytest.raises(ModelDeclined) as caught:  # stop_details can be null on a refusal
        chat.invoke([HumanMessage("synthetic")])
    assert caught.value.category is None and NO_CATEGORY in str(caught.value)
    assert chat.invoke([HumanMessage("synthetic")]).text == "fine"


def test_replay_serves_a_recorded_refusal(tmp_path: Path) -> None:
    """Mutation haiku55_replay_refusal_served_as_reply: ReplayChatModel serves a recorded refusal as an
    empty reply instead of raising."""
    model = ReplayChatModel(responses=[REFUSAL, {"structured": {"verdict": "match"}, "usage": USAGE}],
                            model=config.HAIKU, max_tokens=64)
    with pytest.raises(ModelDeclined) as caught:
        model.invoke("synthetic")
    assert caught.value.category == "cyber" and caught.value.ai_message.content == []
    assert caught.value.ai_message.usage_metadata["output_tokens"] == USAGE["output_tokens"]
    assert model.cursor == 1 and model.invoke("again").content == '{"verdict": "match"}'


def test_a_decline_is_charged_its_usage_and_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On the live path, through the structured call every node makes. Mutation haiku55_decline_released:
    a decline carries no billed message, so paid_structured_call releases its hold instead of charging it."""
    api = RecordedApi([message([], stop_reason="refusal",
                               stop_details={"type": "refusal", "category": "general_harms"})]).install(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")
    ctx = ctx_for(tmp_path, "cheap")
    with pytest.raises(ModelDeclined):
        paid_structured_call(ctx, "synthesize", ClassifierVerdict, [HumanMessage("synthetic")])
    assert len(api.bodies) == 1 and "fallbacks" not in api.bodies[0]
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"]
    charge = rows[1]
    assert (charge["model"], charge["input_tokens"], charge["output_tokens"]) == (config.HAIKU, 1_200, 300)
    assert charge["usd"] == pytest.approx((1_200 * 0.10 + 300 * 0.50) / 1_000_000) and charge["estimated"] == 0


def test_read_plate_ends_the_run_on_the_decline_brief(tmp_path: Path) -> None:
    """Mutation haiku55_read_plate_decline_unhandled: read_plate lets the decline crash the run."""
    ctx = ctx_for(tmp_path, "replay", Script(read_plate=[REFUSAL]))
    state = {"photo": {"path": str(PLATE), "sha256": "x"}, "observed_code": None,
             "search_trail": [{"tool": "search", "query": "optima 880 manual"}]}
    out = read_plate(state, Runtime(context=ctx))
    assert out["status"] == "no_reliable_answer" and out["refusal_origin"] == REFUSAL_ORIGIN
    assert out["stop_reason"] == "model_declined: cyber (read_plate)"
    assert is_declined(out) and out["draft"] is None and "extraction" not in out
    brief = out["brief"]
    assert brief == Brief.model_validate(brief).model_dump(mode="json")
    assert brief["status"] == "no_reliable_answer" and brief["sources"] == [] and brief["try_first"] == []
    block = brief["no_reliable_answer"]
    assert block["searched"] == ["optima 880 manual"] and block["found"] == []
    assert block["why_insufficient"] == ("The model declined this request (safety category: cyber), so there is no "
                                         "answer. The request was not sent to another model.")
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"]
    assert ctx.replay["models"]["read_plate"].cursor == 1


def test_the_classifier_does_not_turn_a_decline_into_unsure(tmp_path: Path) -> None:
    """Unsure would send the same request on to research, a retry. Mutation
    haiku55_classifier_decline_as_unsure: classify_symptom catches the decline as unsure."""
    ctx = ctx_for(tmp_path, "replay", Script(classifier=[REFUSAL]))
    with pytest.raises(ModelDeclined) as caught:
        classify_symptom(ctx, identity={"model": "Optima 880"}, symptom="no heat", documented=[])
    update = declined_update({}, caught.value, "classifier")
    assert update["stop_reason"] == "model_declined: cyber (classifier)"
    assert [r["kind"] for r in Ledger(ctx.ledger_path).rows(ctx.run_id)] == ["reserve", "charge", "release"]


def test_a_research_decline_is_charged_and_never_retried(tmp_path: Path) -> None:
    """Mutations: haiku55_research_decline_released (SpendCap releases a declined research call);
    haiku55_decline_retryable (a decline is one of the errors the research loop retries)."""
    ctx = ctx_for(tmp_path, "replay")
    model = ReplayChatModel(responses=[REFUSAL, {"message": {"content": "unused", "tool_calls": [], "usage": USAGE}}],
                            model=config.HAIKU, max_tokens=config.REPLAY_MAX_TOKENS["research"])
    tools = make_research_tools(ctx, search_inner=StubInner("search", []), fetch_inner=StubInner("fetch", []))
    agent = build_research_agent(model, tools, config.RESEARCH_LIMITS["main"], ctx)
    with pytest.raises(ModelDeclined):
        agent.graph.invoke({"messages": [HumanMessage("Find the manual.")]})
    assert model.cursor == 1  # ModelRetryMiddleware did not try again
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"]
    assert rows[1]["usd"] == pytest.approx((1_200 * 1.00 + 300 * 5.00) / 1_000_000)
    assert agent.spend_cap.spent_usd == pytest.approx(rows[1]["usd"])
