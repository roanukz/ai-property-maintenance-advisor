"""Recorded Claude Haiku 5.5 replies become working cassettes (decision 69, before the live batch).

Each live run here goes through `advisor ask/resume --live` with every client real: the
anthropic SDK, langchain-anthropic's DeclineAwareChatAnthropic, langchain-tavily and the
TypeSafe SDK build every request, and only their HTTP calls are answered here, in the shapes
Claude Haiku 5.5 returns: one or more thinking blocks first (empty thinking text and a
signature), then text, tool_use blocks or a structured reply's JSON, with usage whose
output_tokens include the thinking tokens (output_tokens_details.thinking_tokens). A declined
call answers HTTP 200 with stop_reason "refusal", a stop_details category and explanation, and
some partial text. No socket is opened.

The live recorder writes the cassette candidate exactly as a live run does. Each test then
loads it, replays it through `advisor ask/resume --cassette`, and compares the replay with the
live run: the nodes that ran, the run record (status, route, stop reason, brief, safety
check, searches) and the rendered brief, byte for byte once the run IDs are set aside.

Every case covers each node: read_plate and research and synthesize on the plate photo case
(flo_three_candidates), the classifier on the typed identity case that asks it
(fanout_top_up). Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import httpx2
import pytest
import typesafe_sdk

from agent import cli, config
from agent.replay import privacy_diff
from agent.replay.cassettes import THINKING_MARKERS, load_cassette
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR
from agent.tests.test_live_path import (  # noqa: F401  (live_env is a fixture)
    FLO_CASSETTE,
    PLATE,
    REAL_TYPESAFE_CLIENT,
    RESUME_FLAGS,
    SYMPTOM,
    live_env,
    open_reservations,
    ran,
    run_record,
    thread_of,
    type_line,
)
from agent.tests.test_sc8_history import FIXTURE_GRAPH, OPTIMA

FANOUT_CASSETTE = SYNTHETIC_CASSETTE_DIR / "fanout_top_up.json"
THINKING_TOKENS = 180
PARTIAL = "PARTIAL TEXT SENTINEL written before the decline"
EXPLANATION = "EXPLANATION SENTINEL from stop_details"
CATEGORY = "cyber"
# What a run record must keep the same between the live run and its replay.
RECORD_FIELDS = ("status", "route", "route_reason", "research_limits", "stop_reason", "refusal_origin",
                 "brief", "safety", "searches", "fetches", "tavily_credits", "observed_code", "identity")


def signature(n: int) -> str:
    """An opaque signature shaped value, different for every block."""
    return f"EqQBCkYIBxgCKkB{n:06d}" + "Zm9vYmFy" * 24


class Haiku55Api:
    """Answers the real clients' HTTP calls from a synthetic cassette, in Claude Haiku 5.5's shapes.

    `decline` = (node, call number from 0) makes that call a refusal. Anthropic requests are
    answered here; Tavily's requests.post is answered from the cassette's tool results; every
    other httpx2 request (the TypeSafe SDK's) goes on to its own transport, a mock that answers
    one Noul per step.
    """

    def __init__(self, data: dict[str, Any], *, decline: tuple[str, int] | None = None) -> None:
        self.data = data
        self.decline = decline
        self.scripts = {
            "read_plate": [] if data["read_plate"] is None else [data["read_plate"]],
            "classifier": list(data.get("classifier", [])),
            "research": list(data["research"]["script"]),
            "synthesize": list(data["synthesize"]),
        }
        self.calls = dict.fromkeys(self.scripts, 0)
        self.results = {tool: iter([r for r in data["research"]["tool_results"] if r["tool"] == tool])
                        for tool in ("search", "fetch")}
        self.blocks = 0
        self.signatures: list[str] = []
        self.anthropic: list[dict[str, Any]] = []
        self.original_send = httpx2.Client.send

    def thinking(self, count: int) -> list[dict[str, Any]]:
        out = []
        for _ in range(count):
            self.blocks += 1
            sig = signature(self.blocks)
            self.signatures.append(sig)
            out.append({"type": "thinking", "thinking": "", "signature": sig})
        return out

    @staticmethod
    def node_of(body: dict[str, Any]) -> str:
        image = any(isinstance(block, dict) and block.get("type") == "image"
                    for msg in body.get("messages", []) if isinstance(msg.get("content"), list)
                    for block in msg["content"])
        schema = (((body.get("output_config") or {}).get("format") or {}).get("schema") or {})
        if body.get("tools"):
            return "research"
        if image:
            return "read_plate"
        return "classifier" if "verdict" in (schema.get("properties") or {}) else "synthesize"

    def reply(self, body: dict[str, Any]) -> dict[str, Any]:
        node = self.node_of(body)
        n = self.calls[node]
        self.calls[node] += 1
        step = self.scripts[node][n]
        thinking = self.thinking(2 if n % 2 else 1)
        stop, details = "end_turn", None
        if node == "research":
            message = step["message"]
            usage = message["usage"]
            calls = message.get("tool_calls") or []
            content = thinking + ([{"type": "text", "text": message["content"]}] if message["content"] else [])
            content += [{"type": "tool_use", "id": f"toolu_h55_{self.blocks}_{i}", "name": c["name"],
                         "input": c["args"]} for i, c in enumerate(calls)]
            stop = "tool_use" if calls else "end_turn"
        else:
            structured = {"read_plate": "extraction", "classifier": "structured", "synthesize": "draft"}[node]
            usage = step["usage"]
            content = thinking + [{"type": "text", "text": json.dumps(step[structured])}]
        if self.decline == (node, n):
            content = [{"type": "text", "text": PARTIAL}]
            stop, details = "refusal", {"type": "refusal", "category": CATEGORY, "explanation": EXPLANATION}
        self.anthropic.append({"node": node, "body": body, "stop": stop,
                               "reserved": open_reservations(config.LEDGER_PATH, "anthropic") > 0})
        return {"id": f"msg_h55_{len(self.anthropic)}", "type": "message", "role": "assistant",
                "model": body["model"], "content": content, "stop_reason": stop, "stop_sequence": None,
                "stop_details": details,
                "usage": {"input_tokens": usage["input_tokens"],
                          "output_tokens": usage["output_tokens"] + THINKING_TOKENS,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                          "output_tokens_details": {"thinking_tokens": THINKING_TOKENS}}}

    def send(self, client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        if request.url.host != "api.anthropic.com":
            return self.original_send(client, request, *args, **kwargs)
        body = json.loads(request.content.decode("utf-8"))
        return httpx2.Response(200, json=self.reply(body), request=request)

    def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
             **kwargs: Any) -> Any:
        entry = next(self.results["search" if url.endswith("/search") else "fetch"])

        class Response:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {"results": copy.deepcopy(entry.get("results", [])),
                        "usage": {"credits": entry.get("credits", 1)}}

        return Response()

    @staticmethod
    def jev_answer(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        answers = {name: {"type": "noul", "noul": 0.62} for name in body["questions"]}
        return httpx2.Response(200, json={"model": config.JEV_MODEL, "answers": answers,
                                          "usage": {"input_tokens": 384, "output_tokens": 3}},
                               headers={"x-typesafe-request-id": "req-h55"})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Haiku55Api:
        import langchain_tavily._utilities as tavily_utils

        monkeypatch.setattr(httpx2.Client, "send", lambda client, request, *a, **k: self.send(client, request, *a, **k))
        monkeypatch.setattr(tavily_utils.requests, "post", self.post)
        monkeypatch.setattr(typesafe_sdk, "TypeSafeClient",
                            lambda **kw: REAL_TYPESAFE_CLIENT(**kw, transport=httpx2.MockTransport(self.jev_answer)))
        return self


# ---------------------------------------------------------------------------
# Running a case live, then from its recording
# ---------------------------------------------------------------------------


class Case:
    """How one synthetic case is asked: its cassette, its ask flags and the graph it starts from."""

    def __init__(self, path: Path, ask: list[str], graph: Path | None = None) -> None:
        self.path = path
        self.ask = ask
        self.graph = graph

    def data(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))


FLO = Case(FLO_CASSETTE, ["--symptom", SYMPTOM, "--photo", str(PLATE)])
FANOUT = Case(FANOUT_CASSETTE, ["--symptom", "heater runs but the water stays lukewarm", "--identity",
                                json.dumps({"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "",
                                            "manufacture_date": ""}), "--appliance", OPTIMA], FIXTURE_GRAPH)


def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: Case, *,
        cassette: Path | None = None) -> tuple[str, str]:
    """One ask, and a resume when it pauses: live without `cassette`, else replay. Returns (thread, output)."""
    if cassette is None:
        monkeypatch.setenv(config.ENV_MODE, "cheap")
        extra = ["--live", "--new-ledger"]
        graph_path = config.GRAPH_PATH
    else:
        monkeypatch.setenv(config.ENV_MODE, "replay")
        extra = ["--cassette", str(cassette)]
        graph_path = config.REPLAY_GRAPH_PATH
    if case.graph is not None:
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(case.graph, graph_path)
    type_line(monkeypatch, "proceed\n")
    code = cli.main(["ask", *case.ask, *extra])
    out, err = capsys.readouterr()
    assert code == cli.EXIT_OK, out + err
    thread = thread_of(out)
    if "paused at confirm_identity" in out:
        type_line(monkeypatch, "proceed\n")
        resume_extra = ["--live"] if cassette is None else ["--cassette", str(cassette)]
        code = cli.main(["resume", thread, *RESUME_FLAGS, *resume_extra])
        more, err2 = capsys.readouterr()
        assert code == cli.EXIT_OK, more + err2
        out, err = out + more, err + err2
    return thread, out + err


def recording(data_dir: Path, thread: str) -> tuple[Path, dict[str, Any]]:
    path = data_dir / "recordings" / f"live_t_{thread[2:]}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def brief_html(thread: str) -> str:
    """The rendered brief, with its run ID set aside."""
    return (config.BRIEFS_OUT_DIR / f"{thread}.html").read_text(encoding="utf-8").replace(thread, "<run>")


def dated(value: Any) -> Any:
    """Timestamps cut to their day: a replay retrieves its pages again at its own clock time."""
    if isinstance(value, dict):
        return {k: (v[:10] if k.endswith("_at") and isinstance(v, str) else dated(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [dated(v) for v in value]
    return value


def assert_replay_matches(live_thread: str, replay_thread: str, live_out: str, replay_out: str) -> None:
    assert ran(replay_out) == ran(live_out)
    live, replay = run_record(live_thread), run_record(replay_thread)
    for field in RECORD_FIELDS:
        assert dated(replay.get(field)) == dated(live.get(field)), field
    assert brief_html(replay_thread) == brief_html(live_thread)


def staged_diff(path: Path, staging: Path) -> tuple[Any, str]:
    """The PLAN 8.10 privacy diff on the candidate, staged the way the recorder's next step says."""
    log = path.parent / privacy_diff.COPY_LOG_SUBDIR / f"{path.stem}.copies.json"
    (staging / privacy_diff.CASSETTES_SUBDIR).mkdir(parents=True)
    (staging / privacy_diff.COPY_LOG_SUBDIR).mkdir(parents=True)
    shutil.copy(path, staging / privacy_diff.CASSETTES_SUBDIR / path.name)
    shutil.copy(log, staging / privacy_diff.COPY_LOG_SUBDIR / log.name)
    (staging / privacy_diff.DENYLIST_FILE).write_text("# local names\nnobody-by-this-name\n", encoding="utf-8")
    results, text = privacy_diff.run(staging, status=lambda: "(stub)")
    [result] = [r for r in results if r.name.endswith(path.name)]
    return result, text


def files_text(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(root.rglob("*"))
                     if p.is_file() and p.suffix in (".json", ".jsonl", ".html"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [FLO, FANOUT], ids=["plate_read_research_synthesize", "classifier"])
def test_a_haiku_5_5_recording_loads_and_replays_to_the_live_runs_state_and_brief(
        case: Case, live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: round2_recorder_drops_blocks (a research turn is recorded as its text only);
    round2_recorder_keeps_signature (a thinking block is recorded whole, signature and all);
    round2_recorder_structured_thinking_dropped (a structured reply loses its thinking markers);
    round2_recorder_no_recorded_models (the cassette does not name the models it was recorded on);
    round2_recorded_no_origin (the copy log does not list the recorded model names)."""
    api = Haiku55Api(case.data()).install(monkeypatch)
    live_thread, live_out = run(monkeypatch, capsys, case)
    assert "loads as a cassette: yes" in live_out and "status: ok" in live_out
    path, cassette = recording(live_env, live_thread)
    load_cassette(path)
    assert all(call["reserved"] for call in api.anthropic)
    report = json.loads(path.with_suffix(".privacy.json").read_text(encoding="utf-8"))
    assert report["hits"] == []
    assert not [n for n in report["notes"] if "could not be matched" in n or "left out" in n], report["notes"]

    # Every reply was recorded in its blocks: thinking markers where the thinking stood, the
    # tool_use blocks repeating the tool calls, and no signature anywhere a run wrote.
    script = cassette["research"]["script"]
    assert script and all(isinstance(s["message"]["content"], list) for s in script)
    for step in script:
        content = step["message"]["content"]
        assert content[0] == THINKING_MARKERS["thinking"]
        uses = [b for b in content if b["type"] == "tool_use"]
        assert [(b["id"], b["name"], b["input"]) for b in uses] == \
            [(c["id"], c["name"], c["args"]) for c in step["message"]["tool_calls"]]
    structured = ([cassette["read_plate"]] if cassette["read_plate"] else []) + cassette.get("classifier", []) \
        + cassette["synthesize"]
    assert structured and all(entry["thinking"][0] == THINKING_MARKERS["thinking"] for entry in structured)
    written = files_text(live_env)
    assert api.signatures and not any(sig in written for sig in api.signatures)
    # Thinking tokens are output tokens, and the usage breaks them out.
    usages = [s["message"]["usage"] for s in script] + [e["usage"] for e in structured]
    assert all(u["output_token_details"] == {"reasoning": THINKING_TOKENS} for u in usages)
    nodes = {call["node"] for call in api.anthropic}
    assert cassette["recorded"] == {"mode": "cheap", "models": {node: config.HAIKU for node in nodes}}
    if case is FANOUT:
        assert cassette["classifier"][0]["structured"] == case.data()["classifier"][0]["structured"]
    # The candidate reaches the privacy diff clean: every copied string, the blocks and the recorded
    # models included, is listed with its source in the capture.
    diff, text = staged_diff(path, tmp_path / "staging_for_diff")
    assert diff.hits == [] and "Result: CLEAN" in text, diff.hits
    assert {c["value"] for c in diff.copies if c["dest"].startswith("recorded.models")} == {config.HAIKU}

    replay_thread, replay_out = run(monkeypatch, capsys, case, cassette=path)
    assert_replay_matches(live_thread, replay_thread, live_out, replay_out)


DECLINES = [
    ("read_plate", 0, FLO),
    ("classifier", 0, FANOUT),
    ("research", 1, FANOUT),
    ("synthesize", 0, FANOUT),
]


@pytest.mark.parametrize(("node", "call", "case"), DECLINES, ids=[d[0] for d in DECLINES])
def test_a_live_decline_becomes_a_replayable_cassette(node: str, call: int, case: Case, live_env: Path,
                                                      tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: round2_recorder_misses_declines (a declined call is never captured);
    round2_recorder_keeps_partial_text (the capture keeps what the model wrote before declining);
    round2_responses_for_read_plate_refusal (replay serves read_plate's recorded decline as an extraction)."""
    Haiku55Api(case.data(), decline=(node, call)).install(monkeypatch)
    live_thread, live_out = run(monkeypatch, capsys, case)
    assert f"stop reason: model_declined: {CATEGORY} ({node})" in live_out
    assert "loads as a cassette: yes" in live_out
    path, cassette = recording(live_env, live_thread)
    load_cassette(path)
    refusal = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": CATEGORY}}
    lists = {"read_plate": [cassette["read_plate"]], "classifier": cassette.get("classifier", []),
             "research": cassette["research"]["script"], "synthesize": cassette["synthesize"]}
    recorded = lists[node][-1]
    assert {k: recorded[k] for k in refusal} == refusal
    assert recorded["usage"]["input_tokens"] > 0 and recorded["usage"]["output_token_details"]
    written = files_text(live_env)
    assert PARTIAL not in written and EXPLANATION not in written
    assert run_record(live_thread)["refusal_origin"] == "model_declined"
    diff, text = staged_diff(path, tmp_path / "staging_for_diff")
    assert diff.hits == [] and "Result: CLEAN" in text, diff.hits

    replay_thread, replay_out = run(monkeypatch, capsys, case, cassette=path)
    assert_replay_matches(live_thread, replay_thread, live_out, replay_out)


# ---------------------------------------------------------------------------
# The lifecycle extraction: a lookup of the replacement sweep, recorded the same way
# ---------------------------------------------------------------------------

SWEEP_CASSETTE = SYNTHETIC_CASSETTE_DIR / "sweep_xr16_successor_synthetic.json"
XR16 = "appl-xr16"


def capture_nodes(run_id: str) -> list[str]:
    path = config.RECORDINGS_DIR / f"{run_id}.capture.jsonl"
    return [e["node"] for e in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if e["event"] == "model"]


def lifecycle_records(mode: str) -> list[dict[str, Any]]:
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((config.DATA_DIR / "runs").glob("*.json"))]
    return [r for r in records if r.get("kind") == "lifecycle_lookup" and r.get("mode") == mode]


def test_a_lifecycle_lookup_recorded_live_replays_through_the_sweep(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The lookup's research turns are filed under research and its extraction, which borrows
    synthesize's budget, under synthesize; the candidate replays through `advisor property sweep
    --research --cassette` to the same verified successor and the same dropped option.

    Mutations: round2_recorder_node_by_budget_only (a call is named by its budget alone, so a
    structured call at the research budget is filed as research); round2_lookup_state_no_identity
    (a lookup's recording carries no identity, so the sweep cannot match it to its unit)."""
    from types import SimpleNamespace

    from agent import lifecycle
    from agent.ledger import Ledger
    from agent.registry import open_registry
    from agent.replay.cassettes import lookup_recording_state

    api = Haiku55Api(json.loads(SWEEP_CASSETTE.read_text(encoding="utf-8"))).install(monkeypatch)
    for name in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.setenv(name, f"test-dummy-{name.lower()}")
    Ledger(config.LEDGER_PATH, create=True)
    cli.ensure_registry()
    [appliance] = [a for a in open_registry(config.REGISTRY_PATH).list_appliances("prop-a") if a["id"] == XR16]
    ctx = cli.run_context(lifecycle.new_run_id(), "cheap", None)
    with cli._live_capture(SimpleNamespace(mode="cheap", secrets=[]), ctx.run_id, "lookup"):
        result = lifecycle.lookup(appliance, ctx)
    assert result["status"] == lifecycle.SAVED, result
    assert [c["node"] for c in api.anthropic] == ["research", "research", "synthesize"]
    assert capture_nodes(ctx.run_id) == ["research", "research", "synthesize"]

    state = lookup_recording_state(lifecycle.identity_of(appliance), result, research_limits=lifecycle.LIMITS_ROW)
    cli.LiveSession._record(SimpleNamespace(secrets=[]), SimpleNamespace(state=state), ctx)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "loads as a cassette: yes" in out, out
    path = config.RECORDINGS_DIR / f"live_{ctx.run_id.replace('-', '_')}.json"
    cassette = load_cassette(path)
    assert cassette.recorded == {"mode": "cheap", "models": {"research": config.HAIKU, "synthesize": config.HAIKU}}
    assert cassette.data["synthesize"][0]["thinking"] == [THINKING_MARKERS["thinking"]]
    assert lifecycle.cassette_for(appliance, [cassette]) is cassette

    monkeypatch.setenv(config.ENV_MODE, "replay")
    assert cli.main(["property", "sweep", "prop-a", "--research", "--cassette", str(path)]) == cli.EXIT_OK
    replay_out = capsys.readouterr().out
    assert "verified: discontinued, successor XR99 (synthetic)" in replay_out, replay_out
    [live_record], [replay_record] = lifecycle_records("cheap"), lifecycle_records("replay")
    for field in ("status", "kept", "dropped", "unit", "appliance_id"):
        assert replay_record[field] == live_record[field], field
    assert replay_record["graph_edges"]["written"] == live_record["graph_edges"]["written"] == 1


def ledger_calls(path: Path, run_id: str) -> list[tuple[str, int, float]]:
    """(node, output budget reserved, charged USD) of each Anthropic call of one run, in call order."""
    from agent.ledger import Ledger

    rows = [r for r in Ledger(path).rows(run_id) if r["provider"] == "anthropic"]
    held = [(r["node"], r["output_tokens"]) for r in rows if r["kind"] == "reserve"]
    charged = [r["usd"] for r in rows if r["kind"] == "charge"]
    assert len(held) == len(charged), rows
    return [(node, budget, usd) for (node, budget), usd in zip(held, charged, strict=True)]


def test_a_haiku_5_5_recording_replays_at_its_recorded_cards_budgets_and_cap(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A recording that names its live mode replays under that mode (ledger.price_basis): every call
    holds the live output budget and is charged what it was charged live, at Claude Haiku 5.5's card,
    not ten times that on the frozen Haiku 4.5 replay card. A synthesize call that thought at length
    (60,000 tokens in, 20,000 out) costs about 0.016 USD live; on the frozen card it would cost about
    0.16 USD, over the replay run cap. So the recording is not flagged, and the replay matches the live
    run.

    Mutations: ledger_price_basis_ignores_recorded (replay prices every recording on the frozen card);
    ledger_charge_ignores_price_mode (a call is reserved at the recorded card but charged at the frozen
    one); models_replay_budget_not_recorded (a replayed call holds the frozen replay output budget)."""
    data = FLO.data()
    data["synthesize"][0]["usage"] = {"input_tokens": 60_000, "output_tokens": 20_000}
    Haiku55Api(data).install(monkeypatch)
    live_thread, live_out = run(monkeypatch, capsys, FLO)
    assert "status: ok" in live_out and "SC11: pass" in live_out
    path, cassette = recording(live_env, live_thread)
    assert cassette["recorded"]["mode"] == "cheap"
    report = json.loads(path.with_suffix(".privacy.json").read_text(encoding="utf-8"))
    assert not [n for n in report["notes"] if "over the" in n], report["notes"]

    replay_thread, replay_out = run(monkeypatch, capsys, FLO, cassette=path)
    assert_replay_matches(live_thread, replay_thread, live_out, replay_out)
    live = ledger_calls(config.LEDGER_PATH, live_thread)
    replay = ledger_calls(config.REPLAY_LEDGER_PATH, replay_thread)
    assert [(n, b) for n, b, _ in replay] == [(n, b) for n, b, _ in live]
    assert [usd for *_, usd in replay] == pytest.approx([usd for *_, usd in live], rel=1e-12)
    assert ("synthesize", config.MAX_TOKENS["synthesize_cheap"]) in [(n, b) for n, b, _ in replay]
    synth = next(usd for node, _, usd in replay if node == "synthesize")
    assert synth == pytest.approx((60_000 * 0.10 + (20_000 + THINKING_TOKENS) * 0.50) / 1e6, rel=1e-9)


def test_a_recording_too_dear_to_replay_at_the_recorded_card_is_flagged_when_written(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A recording is charged in replay at the card it was recorded on, so it is flagged only when its
    usage passes a cap even at that card: here a synthesize call with a 250,000 token prompt, on
    Claude Haiku 5.5's long prompt card (0.50 and 2.50 USD per million tokens), costs about 0.175 USD,
    over the cheap run cap. A replay refuses any call reserved past the cap, so the recording is
    flagged when it is written.

    Mutation round2_replay_budget_unflagged: the recorder never compares the recording's replay cost
    with the replay caps."""
    data = FLO.data()
    data["synthesize"][0]["usage"] = {"input_tokens": 250_000, "output_tokens": 20_000}
    Haiku55Api(data).install(monkeypatch)
    thread, out = run(monkeypatch, capsys, FLO)
    assert "SC11: miss" in out
    path, _ = recording(live_env, thread)
    report = json.loads(path.with_suffix(".privacy.json").read_text(encoding="utf-8"))
    flagged = [n for n in report["notes"] if "over the replay run cap" in n]
    assert len(flagged) == 1, report["notes"]
    assert flagged[0].startswith("at the recorded models' cheap mode prices this recording's model calls cost ")
    assert f"of {config.RUN_CAP_USD['cheap']:.4f} USD" in flagged[0]
    assert not [n for n in report["notes"] if "over the research budget" in n]
