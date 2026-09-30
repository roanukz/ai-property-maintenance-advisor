"""Model and tool factories: replay never builds live clients; live settings come from config."""

from __future__ import annotations

import json

import httpx2
import pytest
import typesafe_sdk
from langchain_anthropic import ChatAnthropic

from agent import config, models, prompts
from agent.ledger import Ledger
from agent.replay.replay_model import ReplayChatModel, ReplayExhausted
from agent.research.tools import ResearchTool
from agent.safety_judge import ReplayJudge, SafetyCheckError, question_hash
from agent.state import RunContext

STEP = "ab" * 32
STATE = {"appliance": "hot tub", "step": "Turn off power at the breaker", "detail": ""}


class FakeCassette:
    def responses_for(self, node):
        return [{"message": {"content": node, "tool_calls": [], "usage": {}}}]

    def tool_results_for(self, tool):
        return []


def ctx(mode, tmp_path, cassette=None):
    return RunContext(run_id="r1", mode=mode, ledger_path=tmp_path / "l", registry_path=tmp_path / "r",
                      graph_path=tmp_path / "g", pages_dir=tmp_path / "p", cassette=cassette)


def test_replay_mode_never_constructs_live_clients(tmp_path, monkeypatch):
    # Mutation: have make_model or make_tools build the live client first and
    # swap it out in replay; the raising constructors fire. Mutation
    # models_replay_builds_typesafe_client: make_safety_judge builds a
    # TypeSafeClient in replay. The live judge catches every error as a failed
    # check, so construction is recorded, not only raised.
    built = []

    def boom(*a, **k):
        built.append(k)
        raise AssertionError("live client constructed in replay")

    for name in ("ChatAnthropic", "TavilySearch", "TavilyExtract"):
        monkeypatch.setattr(models, name, boom)
    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", boom)
    run = ctx("replay", tmp_path, FakeCassette())
    for node in models.NODES:
        m = models.make_model(node, run)
        assert isinstance(m, ReplayChatModel)
        assert m.model == config.MODEL_FOR["replay"][node]
        assert m.invoke("hi").content == node  # served from this node's script
    assert [t.name for t in models.make_tools(run)] == ["search", "fetch"]
    judge = models.make_safety_judge(run)
    assert isinstance(judge, ReplayJudge)
    with pytest.raises(SafetyCheckError) as caught:  # a cassette with no safety_check key
        judge.ask(STEP, STATE)
    assert caught.value.kind == "not_recorded"
    assert built == []


def test_replay_max_tokens_match_config(tmp_path):
    # Mutation: give replay synthesize the full budget, or a fixed default.
    run = ctx("replay", tmp_path, FakeCassette())
    got = {node: models.make_model(node, run).max_tokens for node in models.NODES}
    assert got == {
        "read_plate": config.MAX_TOKENS["read_plate"],
        "classifier": config.MAX_TOKENS["classifier"],
        "research": config.MAX_TOKENS["research"],
        "synthesize": config.MAX_TOKENS["synthesize_cheap"],
    }


def test_replay_without_cassette_fails(tmp_path):
    # Mutation: fall through to the live constructor when the cassette is missing.
    with pytest.raises(ValueError):
        models.make_model("research", ctx("replay", tmp_path))
    with pytest.raises(ValueError):
        models.make_tools(ctx("replay", tmp_path))


@pytest.mark.parametrize("mode", config.LIVE_MODES)
@pytest.mark.parametrize("node", ("read_plate", "classifier", "research", "synthesize"))
def test_live_model_settings(mode, node, tmp_path, monkeypatch):
    # Construct only; no call is made and sockets stay blocked.
    # Mutations: pass temperature=0; drop max_tokens or timeout; leave
    # max_retries at the library default of 2; send reasoning_effort to every
    # synthesize (reaches Haiku in cheap) or never to Sonnet in full.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")
    m = models.make_model(node, ctx(mode, tmp_path))
    assert isinstance(m, ChatAnthropic)
    expected_model = config.MODEL_FOR[mode][node]
    key = node if node != "synthesize" else f"synthesize_{'full' if mode == 'full' else 'cheap'}"
    assert m.model == expected_model
    assert m.max_tokens == config.MAX_TOKENS[key]
    assert m.default_request_timeout == config.model_timeout_s(config.MAX_TOKENS[key])
    assert m.max_retries == config.CLIENT_MAX_RETRIES == 0
    assert m.temperature is None and m.top_p is None and m.top_k is None

    payload = m._get_request_payload([("user", "hi")])
    for param in config.NEVER_SEND_PARAMS:
        assert param not in payload
    if expected_model == config.HAIKU:
        assert m.reasoning_effort is None and m.thinking is None
        assert "thinking" not in payload and "output_config" not in payload
    else:
        assert m.reasoning_effort == config.FULL_SYNTH_EFFORT
        assert payload["output_config"]["effort"] == config.FULL_SYNTH_EFFORT


def test_live_tools_use_pinned_tavily_settings(tmp_path, monkeypatch):
    # Mutation: drop a settings dict from the constructor (search_depth
    # "advanced" or include_usage False would be left to the library).
    monkeypatch.setenv("TAVILY_API_KEY", "test-dummy-key")
    search, fetch = models.make_tools(ctx("cheap", tmp_path))
    # Live mode gets the same wrappers as replay, around the real clients.
    assert [search.name, fetch.name] == ["search", "fetch"]
    assert isinstance(search, ResearchTool) and search.handle_tool_error is True
    for k, v in config.TAVILY_SEARCH_SETTINGS.items():
        assert getattr(search.inner, k) == v
    for k, v in config.TAVILY_EXTRACT_SETTINGS.items():
        assert getattr(fetch.inner, k) == v


class TwoAttemptCassette(FakeCassette):
    def responses_for(self, node):
        return [{"structured": {"attempt": n}, "usage": {}} for n in (1, 2)]

    def tool_results_for(self, tool):
        return [{"results": [{"url": f"https://example.com/{n}", "title": "t", "content": "c",
                              "raw_content": None, "score": 0.5}]} for n in (1, 2)]


def test_replay_instances_are_built_once_per_run(tmp_path):
    # Mutation: drop the ctx.replay cache in make_model (a synthesize retry is
    # served attempt 1 again) or in make_tools (a second call starts the
    # stubs over, so its call count restarts at 0).
    Ledger(tmp_path / "l", create=True)
    run = ctx("replay", tmp_path, TwoAttemptCassette())
    first = models.make_model("synthesize", run).invoke("draft")
    retry = models.make_model("synthesize", run).invoke("draft again")
    assert [first.content, retry.content] == ['{"attempt": 1}', '{"attempt": 2}']
    with pytest.raises(ReplayExhausted):
        models.make_model("synthesize", run).invoke("third")
    assert run.replay["models"]["synthesize"].cursor == 2

    models.make_tools(run)[0].invoke({"query": "one"})
    again = models.make_tools(run)
    again[0].invoke({"query": "two"})
    assert again[0].calls == 2 and run.replay["stubs"]["search"].calls == 2
    assert [a["query"] for a in run.collector] == ["one", "two"]

    other = ctx("replay", tmp_path, TwoAttemptCassette())  # a new run starts its own script
    assert models.make_model("synthesize", other).invoke("x").content == '{"attempt": 1}'


def test_unknown_mode_or_node_is_rejected(tmp_path):
    # Mutation: remove _check; an unknown mode reaches MODEL_FOR with a KeyError
    # or, worse, the live branch.
    with pytest.raises(ValueError):
        models.make_model("research", ctx("live", tmp_path))
    with pytest.raises(ValueError):
        models.make_model("summarize", ctx("replay", tmp_path, FakeCassette()))


def recorded_entry(**overrides):
    noul = prompts.SAFETY_STEP_NOUL
    entry = {"step_sha256": STEP, "question_hash": question_hash(noul["instructions"], noul["criteria"]),
             "model": config.JEV_MODEL, "noul": 0.97, "input_tokens": 384, "output_tokens": 3,
             "request_id": "req-1", "error": None}
    entry.update(overrides)
    return entry


class RecordedJevCassette(FakeCassette):
    safety_check = [recorded_entry()]


def test_replay_safety_judge_serves_the_cassette_once_per_run(tmp_path):
    # Mutation models_safety_judge_not_cached: make_safety_judge builds a new
    # replay judge on every call, so a second pass is served the first answer again.
    run = ctx("replay", tmp_path, RecordedJevCassette())
    judge = models.make_safety_judge(run)
    reply = judge.ask(STEP, STATE)
    assert (reply.noul, reply.model, reply.request_id, reply.input_tokens) == (0.97, config.JEV_MODEL, "req-1", 384)
    assert reply.question_hash == judge.question_hash
    assert models.make_safety_judge(run) is judge
    with pytest.raises(SafetyCheckError) as caught:
        models.make_safety_judge(run).ask(STEP, STATE)
    assert caught.value.kind == "not_recorded"  # one recorded answer serves one call
    with pytest.raises(ValueError):
        models.make_safety_judge(ctx("replay", tmp_path))  # no cassette


def test_live_safety_judge_uses_config(tmp_path, monkeypatch):
    # Construct and call on a mock transport; sockets stay blocked.
    # Mutations: safety_judge_model_unpinned (the client and call leave the
    # model to the SDK default); safety_judge_timeout_default (no per call
    # timeout); models_live_judge_wrong_noul (the live judge asks another wording).
    seen = {}
    real = typesafe_sdk.TypeSafeClient

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions.get("timeout")
        return httpx2.Response(200, json={"model": config.JEV_MODEL, "usage": {"input_tokens": 384, "output_tokens": 3},
                                          "answers": {"safety_step": {"type": "noul", "noul": 0.9}}})

    def factory(**kwargs):
        seen["client"] = dict(kwargs)
        return real(**kwargs, api_key="offline-test", transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", factory)
    Ledger(tmp_path / "l", create=True)
    run = ctx("cheap", tmp_path)
    judge = models.make_safety_judge(run)
    assert judge.question_hash == question_hash(prompts.SAFETY_STEP_NOUL["instructions"],
                                                prompts.SAFETY_STEP_NOUL["criteria"])
    assert judge.ask(STEP, STATE).noul == 0.9
    assert seen["client"]["model"] == config.JEV_MODEL and seen["client"]["timeout"] == config.JEV_TIMEOUT_S
    assert seen["body"]["model"] == config.JEV_MODEL and seen["body"]["state"] == STATE
    assert seen["body"]["questions"] == {"safety_step": {"type": "noul",
                                                         "instructions": prompts.SAFETY_STEP_NOUL["instructions"],
                                                         "criteria": prompts.SAFETY_STEP_NOUL["criteria"]}}
    assert seen["timeout"]["read"] == config.JEV_TIMEOUT_S
    rows = Ledger(tmp_path / "l").rows("r1")
    assert [(r["kind"], r["provider"], r["model"]) for r in rows] == [
        ("reserve", config.JEV_PROVIDER, config.JEV_MODEL), ("charge", config.JEV_PROVIDER, config.JEV_MODEL),
        ("release", config.JEV_PROVIDER, config.JEV_MODEL)]
