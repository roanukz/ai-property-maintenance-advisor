"""ReplayChatModel, and the replay stubs inside the real search and fetch wrappers, offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel

from langchain_anthropic import ChatAnthropic
from langchain_anthropic.chat_models import _convert_to_anthropic_output_config_format
from langchain_core.utils.function_calling import convert_to_json_schema

from agent import config
from agent.ledger import Ledger
from agent.replay.replay_model import ReplayChatModel, ReplayExhausted
from agent.replay.tool_stubs import make_stub_tools
from agent.schemas import BriefDraft
from agent.state import RunContext


class FakeCassette:
    """The two cassette methods the model and stubs use."""

    def __init__(self, responses=None, tools=None):
        self._responses = responses or {}
        self._tools = tools or {}

    def responses_for(self, node):
        return list(self._responses.get(node, []))

    def tool_results_for(self, tool):
        return list(self._tools.get(tool, []))


def ctx_for(cassette, tmp_path: Path) -> RunContext:
    Ledger(tmp_path / "ledger.sqlite", create=True)  # the wrappers hold credits in it
    return RunContext(
        run_id="r1",
        mode="replay",
        ledger_path=tmp_path / "ledger.sqlite",
        registry_path=tmp_path / "registry.sqlite",
        graph_path=tmp_path / "graph.json",
        pages_dir=tmp_path / "pages",
        cassette=cassette,
    )


USAGE = {"input_tokens": 1000, "output_tokens": 100}


def call(tool, **args):
    return {"message": {"content": "", "tool_calls": [{"name": tool, "args": args}], "usage": USAGE}}


def answer(text):
    return {"message": {"content": text, "tool_calls": [], "usage": USAGE}}


def search_result(n):
    return {
        "results": [
            {"url": f"https://example.com/{n}", "title": f"Page {n}", "content": f"snippet {n}",
             "raw_content": f"raw {n}", "score": 0.5}
        ],
        "credits": 1,
    }


def model(responses):
    return ReplayChatModel(responses=responses, model=config.HAIKU, max_tokens=config.MAX_TOKENS["research"])


def test_agent_runs_two_searches_and_collects_artifacts_in_order(tmp_path):
    # Mutation: drop ctx.collector.append in the search wrapper, or return a
    # plain string instead of (content, artifact): the collector check fails.
    # Mutation: set raw_content (or title, content, score) to None in the
    # search artifact: the full results comparison fails.
    cassette = FakeCassette(tools={"search": [search_result(1), search_result(2)]})
    ctx = ctx_for(cassette, tmp_path)
    tools = make_stub_tools(cassette, ctx)
    m = model([call("search", query="flo code"), call("search", query="flo manual"), answer("done")])

    out = create_agent(m, tools).invoke({"messages": [HumanMessage("find it")]})

    assert [a["query"] for a in ctx.collector] == ["flo code", "flo manual"]
    assert [a["results"] for a in ctx.collector] == [search_result(1)["results"], search_result(2)["results"]]
    assert all(a["tool"] == "search" and a["credits"] == 1 for a in ctx.collector)
    tool_msgs = [x for x in out["messages"] if isinstance(x, ToolMessage)]
    assert [t.artifact for t in tool_msgs] == ctx.collector
    # Each ToolMessage answers its own call: Phase 2 pairs them by tool_call_id.
    call_ids = [m.tool_calls[0]["id"] for m in out["messages"] if isinstance(m, AIMessage) and m.tool_calls]
    assert [t.tool_call_id for t in tool_msgs] == call_ids and len(set(call_ids)) == 2
    assert out["messages"][-1].content == "done"
    assert tools[0].calls == 2 and tools[1].calls == 0
    assert len(m.received) == 3


def test_recorded_error_gives_error_tool_message_and_run_continues(tmp_path):
    # Mutation: raise RuntimeError instead of ToolException for a recorded
    # error (the agent crashes), or append to the collector before raising
    # (the collector gains an artifact for the failed call).
    cassette = FakeCassette(tools={"search": [{"error": "Error 500: upstream"}, search_result(2)]})
    ctx = ctx_for(cassette, tmp_path)
    tools = make_stub_tools(cassette, ctx)
    m = model([call("search", query="a"), call("search", query="b"), answer("done")])

    out = create_agent(m, tools).invoke({"messages": [HumanMessage("find it")]})

    first, second = [x for x in out["messages"] if isinstance(x, ToolMessage)]
    assert first.status == "error" and first.artifact is None
    assert "Error 500" in first.content
    assert second.status == "success"
    assert [a["query"] for a in ctx.collector] == ["b"]
    assert out["messages"][-1].content == "done"


def test_fetch_stub_artifact_shape(tmp_path):
    # Mutation: drop failed_results or use the recorded url instead of the
    # argument; the shape assertion fails.
    recorded = {"results": [{"url": "https://example.com/p", "raw_content": "page text"}],
                "failed_results": [], "credits": 1}
    cassette = FakeCassette(tools={"fetch": [recorded]})
    ctx = ctx_for(cassette, tmp_path)
    fetch = make_stub_tools(cassette, ctx)[1]
    msg = fetch.invoke({"name": "fetch", "args": {"url": "https://example.com/p"}, "id": "c1", "type": "tool_call"})
    assert msg.artifact == {"tool": "fetch", "url": "https://example.com/p", "results": recorded["results"],
                            "failed_results": [], "credits": 1}
    assert "page text" in msg.content
    assert ctx.collector == [msg.artifact]


def test_model_raises_when_script_runs_out():
    # Mutation: wrap around with cursor % len(responses); the second call
    # returns the first answer again instead of raising.
    m = model([answer("only")])
    assert m.invoke("hi").content == "only"
    with pytest.raises(ReplayExhausted):
        m.invoke("again")


def test_stub_raises_when_recorded_results_run_out(tmp_path):
    # Mutation: return an empty result instead of raising when calls exceed
    # the recording.
    cassette = FakeCassette(tools={"search": [search_result(1)]})
    search = make_stub_tools(cassette, ctx_for(cassette, tmp_path))[0]
    search.invoke({"query": "one"})
    with pytest.raises(ReplayExhausted):
        search.invoke({"query": "two"})


def test_message_ids_are_unique_and_usage_is_attached():
    # Mutation: a fixed id such as f"replay-{self.model}", or dropping
    # usage_metadata from the AIMessage, or a fixed default tool call id.
    m = model([answer("a"), call("search", query="q"), answer("c"), call("search", query="r")])
    msgs = [m.invoke("x") for _ in range(4)]
    ids = [x.id for x in msgs]
    assert all(ids) and len(set(ids)) == 4
    for x in msgs:
        assert x.usage_metadata == {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}
    assert msgs[1].tool_calls[0]["name"] == "search"
    assert msgs[1].tool_calls[0]["args"] == {"query": "q"}
    call_ids = [msgs[1].tool_calls[0]["id"], msgs[3].tool_calls[0]["id"]]
    assert all(call_ids) and call_ids[0] != call_ids[1]


def test_received_records_every_request_through_bound_copies():
    # Mutation: skip self.received.append in _generate, or have bind_tools
    # return a fresh model with its own script (the bound call is not
    # recorded on the original).
    m = model([answer("a"), answer("b")])
    m.invoke([HumanMessage("first")])
    m.bind_tools([]).invoke([HumanMessage("second")])
    assert [[x.content for x in req] for req in m.received] == [["first"], ["second"]]
    assert m.received_kwargs[1]["tools"] == []
    assert m.model == config.HAIKU and m.max_tokens == config.MAX_TOKENS["research"]


class Draft(BaseModel):
    title: str
    steps: list[str]


def test_structured_output_include_raw_reports_parsing_error_for_invalid_draft():
    # Mutation: drop the with_fallbacks wrapper (the invalid draft raises), or
    # set parsed without parsing the raw text (parsed is not None).
    m = ReplayChatModel(
        responses=[{"structured": {"title": "no steps"}, "usage": USAGE},
                   {"structured": {"title": "ok", "steps": ["a"]}, "usage": USAGE}],
        model=config.HAIKU,
        max_tokens=config.MAX_TOKENS["synthesize_cheap"],
    )
    runnable = m.with_structured_output(Draft, method="json_schema", include_raw=True)

    bad = runnable.invoke("write it")
    assert bad["parsed"] is None
    assert isinstance(bad["parsing_error"], OutputParserException)
    assert isinstance(bad["raw"], AIMessage) and bad["raw"].usage_metadata["output_tokens"] == 100

    good = runnable.invoke("write it again")
    assert good["parsed"] == Draft(title="ok", steps=["a"])
    assert good["parsing_error"] is None
    assert m.received_kwargs[0]["output_config"]["format"]["type"] == "json_schema"


def test_structured_output_binds_the_schema_a_live_call_sends(monkeypatch):
    # Mutation: bind {"type": "json_schema", "schema": convert_to_json_schema(schema)}
    # again; that is not the anthropic transform_schema output a live call sends.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")
    live = ChatAnthropic(model=config.HAIKU, max_tokens=config.MAX_TOKENS["synthesize_cheap"])
    live_format = live.with_structured_output(BriefDraft, method="json_schema").first.kwargs["output_config"]
    m = ReplayChatModel(responses=[{"structured": {}, "usage": USAGE}], model=config.HAIKU,
                        max_tokens=config.MAX_TOKENS["synthesize_cheap"])
    m.with_structured_output(BriefDraft, method="json_schema", include_raw=True).invoke("x")
    bound = m.received_kwargs[0]["output_config"]
    assert bound == live_format == {"format": _convert_to_anthropic_output_config_format(BriefDraft)}
    assert bound["format"]["schema"] != convert_to_json_schema(BriefDraft)  # the two really differ


def test_structured_output_without_raw_raises_on_invalid_draft():
    # Mutation: wrap the include_raw=False path in a fallback that returns
    # None; the invalid draft no longer raises.
    m = ReplayChatModel(responses=[{"structured": {"title": 1}, "usage": USAGE}],
                        model=config.HAIKU, max_tokens=config.MAX_TOKENS["synthesize_cheap"])
    with pytest.raises(OutputParserException):
        m.with_structured_output(Draft).invoke("x")



def test_stub_matches_parallel_calls_to_their_own_results() -> None:
    """Mutation stubs_match_by_position: parallel calls reach the stub in any order,
    and a stub serving by position hands a call another call's results."""
    from agent.replay.tool_stubs import StubInner

    recorded = [{"query": q, "results": [{"url": f"https://example.com/{q}"}]} for q in ("a", "b", "c")]
    stub = StubInner("search", recorded)
    got = {q: stub.invoke({"query": q})["results"][0]["url"] for q in ("c", "a", "b")}
    assert got == {q: f"https://example.com/{q}" for q in ("a", "b", "c")}
    fetch = StubInner("fetch", [{"url": u, "results": []} for u in ("https://example.com/1", "https://example.com/2")])
    fetch.invoke({"urls": ["https://example.com/2"]})
    assert fetch._used == {1}
