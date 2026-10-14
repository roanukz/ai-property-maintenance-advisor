"""Decision 69: a Claude Haiku 5.5 reply can begin with thinking blocks whose text is empty.

Every reader of model output reads blocks by type: the structured call every node makes (live and
replay), the research tool loop, which must send each thinking block back unmodified and never
edit an earlier turn, and the live recorder, driven through `advisor ask/resume --live` on the live
path fakes. The other live replies here come from the real
DeclineAwareChatAnthropic with its HTTP call replaced by recorded Messages, so langchain-anthropic's
own parsing and request building run; no socket is opened. Each test names its mutation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import anthropic.types
import pytest
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent import config, models
from agent.ledger import Ledger
from agent.nodes.classify import ClassifierVerdict, classify_symptom
from agent.nodes.read_plate import read_plate
from agent.nodes.synthesize import paid_structured_call
from agent.replay.tool_stubs import StubInner
from agent.research.agent import build_research_agent
from agent.research.tools import make_research_tools
from agent.schemas import BriefDraft, Extraction
from agent.state import RunContext
from agent.tests.test_live_path import (  # noqa: F401  (live_env is a fixture)
    NRA_DRAFT,
    LiveFakes,
    flo_data,
    live_env,
    run_flo_live,
)

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-recorded-1"}
USAGE = {"input_tokens": 1_200, "output_tokens": 300}
EXTRACTION = {"manufacturer": "SUNDANCE SPAS", "model": "OPTIMA 880", "serial": None, "manufacture_date": None,
              "confidence": {"manufacturer": "high", "model": "high", "serial": "unreadable",
                             "manufacture_date": "unreadable"}}
PLATE = config.REPO_ROOT / "demo-assets" / "plate-clear.jpg"


def message(content: list[dict[str, Any]], stop_reason: str = "end_turn", **extra: Any) -> anthropic.types.Message:
    """A Messages API reply as the SDK parses it."""
    return anthropic.types.Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": config.HAIKU, "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": USAGE["input_tokens"], "output_tokens": USAGE["output_tokens"]}, **extra})


class _Raw:
    def __init__(self, reply: anthropic.types.Message) -> None:
        self.reply = reply

    def parse(self) -> anthropic.types.Message:
        return self.reply


class RecordedApi:
    """Stands in for the HTTP call of every DeclineAwareChatAnthropic: serves replies, keeps each request body."""

    def __init__(self, replies: list[anthropic.types.Message]) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> RecordedApi:
        api = self

        def _create(chat: Any, payload: dict[str, Any]) -> _Raw:
            api.bodies.append(copy.deepcopy(payload))
            return _Raw(api.replies.pop(0))

        monkeypatch.setattr(models.DeclineAwareChatAnthropic, "_create", _create)
        return self


def live_chat(node: str) -> models.DeclineAwareChatAnthropic:
    return models.ChatAnthropic(**models.live_model_kwargs(node, "cheap"), api_key="test-dummy-key")


def ctx_for(tmp_path: Path, mode: str, cassette: Any = None) -> RunContext:
    Ledger(tmp_path / "ledger.sqlite", create=True)
    return RunContext(run_id="run-thinking", mode=mode, ledger_path=tmp_path / "ledger.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages", cassette=cassette)


class Script:
    """A cassette with one scripted reply list per node."""

    def __init__(self, **responses: list[dict[str, Any]]) -> None:
        self.responses = responses

    def responses_for(self, node: str) -> list[dict[str, Any]]:
        return self.responses.get(node, [])


# ---------------------------------------------------------------------------
# The structured call: live
# ---------------------------------------------------------------------------


def test_live_thinking_first_structured_reply_parses_and_is_charged(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """A classifier call on the live path: the reply opens with an empty thinking block.

    Mutation haiku55_empty_first_block_read_as_declined: the decline check takes a reply whose first
    block has no text for a refusal."""
    api = RecordedApi([message([THINKING, {"type": "text", "text": json.dumps({"verdict": "match"})}])])
    api.install(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")
    ctx = ctx_for(tmp_path, "cheap")
    out, usd = paid_structured_call(ctx, "classifier", ClassifierVerdict, [HumanMessage("synthetic")])
    assert out["parsing_error"] is None and out["parsed"].verdict == "match"
    assert out["raw"].content[0] == THINKING  # kept, its empty text and all
    assert usd == pytest.approx((1_200 * 0.10 + 300 * 0.50) / 1_000_000)
    body = api.bodies[0]
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"]


# ---------------------------------------------------------------------------
# The structured call: replay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("node", "schema", "reply"), [
    ("read_plate", Extraction, EXTRACTION),
    ("classifier", ClassifierVerdict, {"verdict": "no_match"}),
    ("synthesize", BriefDraft, NRA_DRAFT),
])
def test_replay_serves_thinking_first_structured_replies_that_parse(node: str, schema: type, reply: dict,
                                                                    tmp_path: Path) -> None:
    """Mutation haiku55_replay_drops_thinking: ReplayChatModel leaves a recorded reply's thinking blocks
    out, so no test would see a thinking first reply."""
    ctx = ctx_for(tmp_path, "replay", Script(**{node: [{"structured": reply, "thinking": [THINKING],
                                                        "usage": USAGE}]}))
    out, usd = paid_structured_call(ctx, node, schema, [HumanMessage("synthetic")])
    assert out["parsing_error"] is None and out["parsed"].model_dump(mode="json") == schema.model_validate(
        reply).model_dump(mode="json")
    assert [b["type"] for b in out["raw"].content] == ["thinking", "text"]
    assert out["raw"].content[0] == THINKING
    assert usd == pytest.approx((1_200 * 1.00 + 300 * 5.00) / 1_000_000)  # replay's recorded card


def test_nodes_read_a_thinking_first_reply(tmp_path: Path) -> None:
    """read_plate and the classifier, end to end on replay. Mutation haiku55_read_plate_string_replies_only:
    read_plate takes only a reply whose content is one string, as Haiku 4.5's were."""
    plate_ctx = ctx_for(tmp_path / "plate", "replay", Script(read_plate=[
        {"structured": EXTRACTION, "thinking": [THINKING, dict(THINKING, signature="sig-2")], "usage": USAGE}]))
    out = read_plate({"photo": {"path": str(PLATE), "sha256": "x"}}, Runtime(context=plate_ctx))
    assert out["extraction"]["model"] == "OPTIMA 880" and out["extraction"]["serial"] is None
    assert plate_ctx.replay["models"]["read_plate"].cursor == 1
    classifier_ctx = ctx_for(tmp_path / "classifier", "replay", Script(classifier=[
        {"structured": {"verdict": "match"}, "thinking": [THINKING], "usage": USAGE}]))
    result = classify_symptom(classifier_ctx, identity={"model": "Optima 880"}, symptom="no heat", documented=[])
    assert (result.verdict, result.note) == ("match", None)


# ---------------------------------------------------------------------------
# The research tool loop
# ---------------------------------------------------------------------------


SEARCH_RESULTS = [{"results": [{"url": "https://example.com/manual", "title": "Synthetic manual",
                                "content": "Synthetic manual text.", "raw_content": None, "score": 0.5}]}]


def test_research_loop_sends_thinking_blocks_back_unmodified_and_stays_append_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two research turns on the live path, each reply opening with an empty thinking block.

    The second request repeats the first one's system prompt, tools and messages unchanged and
    appends the first reply, its thinking block exactly as received, then the tool result.

    Mutation haiku55_research_strips_thinking: SpendCap strips earlier thinking blocks from the
    request, an edit to an earlier turn that invalidates them."""
    api = RecordedApi([
        message([THINKING, {"type": "tool_use", "id": "toolu_1", "name": "search",
                            "input": {"query": "optima 880 manual"}}], stop_reason="tool_use"),
        message([dict(THINKING, signature="sig-recorded-2"), {"type": "text", "text": "Done."}]),
    ]).install(monkeypatch)
    ctx = ctx_for(tmp_path, "cheap")
    tools = make_research_tools(ctx, search_inner=StubInner("search", SEARCH_RESULTS),
                                fetch_inner=StubInner("fetch", []))
    agent = build_research_agent(live_chat("research"), tools, config.RESEARCH_LIMITS["main"], ctx)
    agent.graph.invoke({"messages": [HumanMessage("Find the manual for a Sundance Spas Optima 880.")]})

    first, second = api.bodies
    for key in ("system", "tools", "output_config", "model", "max_tokens"):
        assert second[key] == first[key], key
    assert second["messages"][:len(first["messages"])] == first["messages"]
    assistant = second["messages"][len(first["messages"])]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == THINKING
    assert [b["type"] for b in assistant["content"]] == ["thinking", "tool_use"]
    assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert len([r for r in rows if r["kind"] == "charge" and r["provider"] == "anthropic"]) == 2
    assert agent.spend_cap.calls == 2


# ---------------------------------------------------------------------------
# The live recorder
# ---------------------------------------------------------------------------


class ThinkingFakes(LiveFakes):
    """The live path fakes, with every reply opening on an empty thinking block, as Claude Haiku 5.5's can."""

    def responses(self, node: str) -> list[dict[str, Any]]:
        out = []
        for response in copy.deepcopy(super().responses(node)):
            if "structured" in response:
                response["thinking"] = [THINKING]
            else:
                text = response["message"].get("content") or ""
                response["message"]["content"] = [THINKING, *([{"type": "text", "text": text}] if text else [])]
            out.append(response)
        return out


def test_a_live_run_of_thinking_first_replies_finishes_and_records_the_reply_text(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Every node of a live run (on fakes) reads thinking first replies, and the capture keeps each
    reply's text, never its thinking block.

    Mutation haiku55_recorder_reads_first_block: the capture takes the first block as the reply."""
    data = flo_data()
    fakes = ThinkingFakes(data).install(monkeypatch)
    _, out = run_flo_live(monkeypatch, capsys, fakes)
    assert "status: ok" in out
    events = [json.loads(line) for path in sorted((live_env / "recordings").glob("*.capture.jsonl"))
              for line in path.read_text(encoding="utf-8").splitlines()]
    models_seen = [e for e in events if e["event"] == "model"]
    research = [e["content"] for e in models_seen if e["node"] == "research"]
    assert research == [s["message"]["content"] for s in data["research"]["script"]]
    assert "Research done" in research[-1]
    for event in models_seen:
        assert "sig-recorded" not in json.dumps(event)
        if event["node"] in ("read_plate", "synthesize"):
            json.loads(event["content"])  # the structured reply's JSON text, whole
