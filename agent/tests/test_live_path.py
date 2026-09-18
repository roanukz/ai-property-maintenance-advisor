"""The live path, run end to end on fake clients with the network blocked (Phase 5).

No test here makes a paid call. pytest-socket blocks the network, conftest
scrubs every key at import, and each test replaces the live constructors in
agent.models (ChatAnthropic, TavilySearch, TavilyExtract) with fakes: the
fake chat model is a ReplayChatModel that serves a synthetic cassette's
script, and the fake Tavily clients are the replay stubs. Both check, at the
moment each call arrives, that the ledger holds an open reservation for it.

These tests reach the live code only through agent.cli, the command surface
(test_offline_guard.py forbids a test file that imports agent.live).

Every test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import Field

from agent import cli, config, models
from agent.ledger import Ledger, LedgerMissing
from agent.replay.replay_model import ReplayChatModel
from agent.replay.tool_stubs import StubInner
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR

FLO_CASSETTE = SYNTHETIC_CASSETTE_DIR / "flo_three_candidates.json"
PLATE = config.REPO_ROOT / "demo-assets" / "plate-clear.jpg"
SYMPTOM = "panel shows FLO"
RESUME_FLAGS = ["--manufacturer", "Sundance Spas", "--model", "Optima 880", "--code", "FLO"]
KEY_NAMES = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY")


def fake_key(prefix: str, seed: str) -> str:
    """A key shaped value built at run time, so no source file holds one."""
    return prefix + hashlib.sha256(seed.encode()).hexdigest()[:40]


FAKE_ANTHROPIC = fake_key("sk" + "-ant-" + "api03-", "anthropic")
FAKE_TAVILY = fake_key("tvly" + "-" + "dev-", "tavily")


def open_reservations(ledger_path: Path, provider: str) -> int:
    """Reservations for this provider that no charge or release has settled yet."""
    rows = Ledger(ledger_path).rows()
    reserved = {r["id"] for r in rows if r["kind"] == "reserve" and r["provider"] == provider}
    settled = {int(r["note"].split()[1]) for r in rows if r["kind"] == "release"}
    return len(reserved - settled)


class CheckedChat(ReplayChatModel):
    """A fake ChatAnthropic: the replay script, plus a reservation check on every call.

    `fail_with` makes every call raise that text instead, as a client error
    that echoes a key would.
    """

    ledger_path: str = ""
    checks: list = Field(default_factory=list, exclude=True)
    fail_with: str | None = None

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        self.checks.append(open_reservations(Path(self.ledger_path), "anthropic") > 0)
        if self.fail_with is not None:
            self.received.append(list(messages))
            raise RuntimeError(self.fail_with)
        return super()._generate(messages, stop, run_manager, **kwargs)


class CheckedStub(StubInner):
    """A fake Tavily client: the recorded results, plus a reservation check on every call.

    `failure` makes the first call raise that text ("raise") or answer
    {"error": text} ("error"), as a Tavily error that echoes a key would.
    """

    def __init__(self, name: str, results: list[dict[str, Any]],
                 failure: tuple[str, str] | None = None) -> None:
        super().__init__(name, results)
        self.checks: list[bool] = []
        self.failure = failure

    def invoke(self, input: dict[str, Any]) -> Any:
        self.checks.append(open_reservations(config.LEDGER_PATH, "tavily") > 0)
        if self.failure is not None:
            how, text = self.failure
            self.failure = None
            if how == "raise":
                raise RuntimeError(text)
            return {"error": text}
        return super().invoke(input)


@dataclass
class LiveFakes:
    """The fake live clients one test installs, and what they saw."""

    data: dict[str, Any]
    mode: str = "cheap"
    constructed: list[dict[str, Any]] = field(default_factory=list)
    keys_seen: list[dict[str, str | None]] = field(default_factory=list)
    chats: dict[str, CheckedChat] = field(default_factory=dict)
    stubs: dict[str, CheckedStub] = field(default_factory=dict)
    echo_key: bool = False  # the fake prints the key it sees, as a leaky client library might
    chat_failures: dict[str, str] = field(default_factory=dict)  # node -> error text its calls raise
    search_failure: tuple[str, str] | None = None  # ("raise" or "error", text) for the first search

    def responses(self, node: str) -> list[dict[str, Any]]:
        if node == "read_plate":
            rp = self.data["read_plate"]
            return [] if rp is None else [{"structured": rp["extraction"], "usage": rp["usage"]}]
        if node == "classifier":
            return list(self.data.get("classifier", []))
        if node == "research":
            return list(self.data["research"]["script"])
        return [{"structured": a["draft"], "usage": a["usage"]} for a in self.data["synthesize"]]

    def chat(self, **kwargs: Any) -> CheckedChat:
        self.constructed.append(dict(kwargs))
        self.keys_seen.append({"anthropic": os.environ.get("ANTHROPIC_API_KEY")})
        if self.echo_key:
            print(f"debug client key {os.environ.get('ANTHROPIC_API_KEY')}")
            print(f"debug client key on stderr {os.environ.get('ANTHROPIC_API_KEY')}", file=sys.stderr)
        node = {models.max_tokens_for(n, self.mode): n for n in models.NODES}[kwargs["max_tokens"]]
        if node not in self.chats:
            self.chats[node] = CheckedChat(responses=self.responses(node), model=kwargs["model"],
                                           max_tokens=kwargs["max_tokens"], ledger_path=str(config.LEDGER_PATH),
                                           fail_with=self.chat_failures.get(node))
        return self.chats[node]

    def tool(self, name: str, **kwargs: Any) -> CheckedStub:
        self.keys_seen.append({"tavily": os.environ.get("TAVILY_API_KEY")})
        if name not in self.stubs:
            results = [{k: v for k, v in e.items() if k != "tool"}
                       for e in self.data["research"]["tool_results"] if e["tool"] == name]
            self.stubs[name] = CheckedStub(name, results, self.search_failure if name == "search" else None)
        return self.stubs[name]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> LiveFakes:
        monkeypatch.setattr(models, "ChatAnthropic", self.chat)
        monkeypatch.setattr(models, "TavilySearch", lambda **kw: self.tool("search", **kw))
        monkeypatch.setattr(models, "TavilyExtract", lambda **kw: self.tool("fetch", **kw))
        return self

    def all_checks(self) -> list[bool]:
        return [c for chat in self.chats.values() for c in chat.checks] + \
               [c for stub in self.stubs.values() for c in stub.checks]


def flo_data() -> dict[str, Any]:
    return json.loads(FLO_CASSETTE.read_text(encoding="utf-8"))


@pytest.fixture
def live_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every path a live command writes under tmp_path, cheap mode, keys only in a temp .env."""
    data = tmp_path / "data"
    for name, path in {
        "DATA_DIR": data,
        "CHECKPOINT_PATH": data / "checkpoints.sqlite",
        "LEDGER_PATH": data / "ledger" / "ledger.sqlite",
        "REPLAY_LEDGER_PATH": data / "ledger" / "replay_ledger.sqlite",
        "REGISTRY_PATH": data / "registry.sqlite",
        "GRAPH_PATH": data / "graph.json",
        "REPLAY_GRAPH_PATH": data / "replay_graph.json",
        "PAGES_DIR": data / "pages",
        "LOOKUPS_DIR": data / "lookups",
        "BRIEFS_OUT_DIR": data / "briefs",
        "PROPERTY_OUT_DIR": data / "property",
        "EVAL_DIR": data / "eval",
        "STAGING_DIR": data / "staging",
        "RECORDINGS_DIR": data / "recordings",
        "ENV_FILE": tmp_path / ".env",
    }.items():
        monkeypatch.setattr(config, name, path)
    for name in (*KEY_NAMES, config.ENV_CASSETTE, config.ENV_TRACING):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    monkeypatch.chdir(config.REPO_ROOT)
    (tmp_path / ".env").write_text(
        f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nTAVILY_API_KEY={FAKE_TAVILY}\n", encoding="utf-8")
    return data


def type_line(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def live_ask(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *extra: str,
             typed: str = "proceed\n") -> tuple[int, str, str]:
    type_line(monkeypatch, typed)
    code = cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--live", *extra])
    out, err = capsys.readouterr()
    return code, out, err


def live_resume(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], thread_id: str,
                typed: str = "proceed\n") -> tuple[int, str, str]:
    type_line(monkeypatch, typed)
    code = cli.main(["resume", thread_id, *RESUME_FLAGS, "--live"])
    out, err = capsys.readouterr()
    return code, out, err


def thread_of(out: str) -> str:
    return out.split("advisor ask: thread ", 1)[1].split(" ", 1)[0]


def run_flo_live(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                 fakes: LiveFakes) -> tuple[str, str]:
    """A live ask that pauses, then a live resume that finishes; returns (thread ID, all output)."""
    code, out1, err1 = live_ask(monkeypatch, capsys, "--new-ledger")
    assert code == cli.EXIT_OK, out1 + err1
    assert "paused at confirm_identity" in out1
    thread_id = thread_of(out1)
    code, out2, err2 = live_resume(monkeypatch, capsys, thread_id)
    assert code == cli.EXIT_OK, out2 + err2
    return thread_id, out1 + err1 + out2 + err2


def ran(out: str) -> list[str]:
    return [line.split("ran: ", 1)[1] for line in out.splitlines() if line.startswith("  ran: ")]


def run_record(thread_id: str) -> dict[str, Any]:
    return json.loads((config.DATA_DIR / "runs" / f"{thread_id}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_live_ask_and_resume_end_to_end_on_fakes(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: live_model_call_before_reservation (synthesize builds and calls the model before
    ledger.reserve); live_tavily_call_before_reservation (the wrapper calls Tavily before
    reserve_credits); live_run_uses_replay_ledger (run_context gives a live run the replay ledger);
    live_resume_skips_finish (no SC11 record after the run ends)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    thread_id, out = run_flo_live(monkeypatch, capsys, fakes)

    # The same graph and nodes as replay: the node sequence equals a replay run of the same script.
    monkeypatch.setenv(config.ENV_MODE, "replay")
    assert cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--cassette", str(FLO_CASSETTE)]) == 0
    replay_out = capsys.readouterr().out
    assert cli.main(["resume", thread_of(replay_out), *RESUME_FLAGS, "--cassette", str(FLO_CASSETTE)]) == 0
    replay_out += capsys.readouterr().out
    assert ran(out) == ran(replay_out)
    assert "status: ok" in out

    # Every live call was preceded by a ledger reservation, and all the fakes were used.
    checks = fakes.all_checks()
    assert set(fakes.chats) == {"read_plate", "research", "synthesize"}
    assert set(fakes.stubs) == {"search", "fetch"} and fakes.stubs["search"].calls == 2
    assert checks and all(checks), checks
    # ChatAnthropic got config's settings and never a key argument; the key came from the environment.
    assert all("api_key" not in kw and kw["max_retries"] == config.CLIENT_MAX_RETRIES for kw in fakes.constructed)
    assert {seen.get("anthropic") for seen in fakes.keys_seen if "anthropic" in seen} == {FAKE_ANTHROPIC}

    # The live ledger holds every charge, in cheap mode; the replay ledger holds none of them.
    rows = Ledger(config.LEDGER_PATH).rows(thread_id)
    charges = [r for r in rows if r["kind"] == "charge"]
    assert {r["mode"] for r in charges} == {"cheap"}
    assert {r["node"] for r in charges if r["provider"] == "anthropic"} == {"read_plate", "research", "synthesize"}
    assert Ledger(config.LEDGER_PATH).build_total() > 0

    # The run record carries the SC11 post condition and latency for every node that ran.
    record = run_record(thread_id)
    total = Ledger(config.LEDGER_PATH).run_total(thread_id)
    assert record["sc11"] == {"rule": cli.SC11_RULE, "run_total_usd": pytest.approx(total),
                              "run_cap_usd": config.RUN_CAP_USD["cheap"], "result": "pass"}
    assert {"read_plate", "research", "synthesize", "persist"} <= set(record["latency"])
    assert "SC11: pass" in out and "latency synthesize" in out
    # The keys are only in the environment for the command: afterwards they are gone again.
    assert all(name not in os.environ for name in KEY_NAMES)


def test_sc11_miss_is_reported_not_hidden(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: live_sc11_always_pass (the post condition ignores the total)."""
    data = flo_data()
    heavy = copy.deepcopy(data["synthesize"][0])
    heavy["usage"] = {"input_tokens": 13_000, "output_tokens": 40_000, "total_tokens": 53_000}
    data["synthesize"] = [heavy]
    fakes = LiveFakes(data).install(monkeypatch)
    thread_id, out = run_flo_live(monkeypatch, capsys, fakes)
    total = Ledger(config.LEDGER_PATH).run_total(thread_id)
    assert total > config.RUN_CAP_USD["cheap"]  # usage above the reservation is charged in full
    record = run_record(thread_id)
    assert record["sc11"]["result"] == "miss"
    assert "SC11: miss" in out


# ---------------------------------------------------------------------------
# The real clients: ChatAnthropic, the anthropic SDK and langchain-tavily build
# every request; only their HTTP transports are fakes
# ---------------------------------------------------------------------------


class FakeTransports:
    """Answers the real clients' HTTP requests from the FLO script and records each request.

    httpx2.Client.send is the anthropic SDK's transport and requests.post is
    langchain-tavily's; both are replaced, so no socket is opened (pytest-socket
    blocks sockets as well). The key checks compare against the planted fakes.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.research = iter(data["research"]["script"])
        self.synth = iter(data["synthesize"])
        self.results = {tool: iter([r for r in data["research"]["tool_results"] if r["tool"] == tool])
                        for tool in ("search", "fetch")}
        self.anthropic: list[dict[str, Any]] = []
        self.tavily: list[dict[str, Any]] = []
        self.ids = iter(range(1, 1_000))

    def _reply(self, body: dict[str, Any], content: list[dict[str, Any]], usage: dict[str, int], stop: str) -> dict:
        return {"id": f"msg_fake_{next(self.ids)}", "type": "message", "role": "assistant", "model": body["model"],
                "content": content, "stop_reason": stop, "stop_sequence": None,
                "usage": {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}

    def send(self, request: Any) -> Any:
        import httpx2

        body = json.loads(request.content.decode("utf-8"))
        image = any(isinstance(block, dict) and block.get("type") == "image"
                    for msg in body.get("messages", []) if isinstance(msg.get("content"), list)
                    for block in msg["content"])
        if body.get("tools"):
            node, step = "research", next(self.research)["message"]
            calls = step.get("tool_calls") or []
            content = [{"type": "tool_use", "id": f"toolu_fake_{next(self.ids)}", "name": c["name"],
                        "input": c["args"]} for c in calls] or [{"type": "text", "text": step["content"]}]
            reply = self._reply(body, content, step["usage"], "tool_use" if calls else "end_turn")
        elif image:
            node, rp = "read_plate", self.data["read_plate"]
            reply = self._reply(body, [{"type": "text", "text": json.dumps(rp["extraction"])}], rp["usage"], "end_turn")
        else:
            node, attempt = "synthesize", next(self.synth)
            reply = self._reply(body, [{"type": "text", "text": json.dumps(attempt["draft"])}], attempt["usage"],
                                "end_turn")
        self.anthropic.append({"node": node, "body": body, "image": image, "url": str(request.url),
                               "headers": dict(request.headers), "timeout": request.extensions.get("timeout"),
                               "reserved": open_reservations(config.LEDGER_PATH, "anthropic") > 0})
        return httpx2.Response(200, json=reply, request=request)

    def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
             **kwargs: Any) -> Any:
        tool = "search" if url.endswith("/search") else "fetch"
        entry = next(self.results[tool])
        self.tavily.append({"tool": tool, "url": url, "params": dict(json or {}), "headers": dict(headers or {}),
                            "reserved": open_reservations(config.LEDGER_PATH, "tavily") > 0})

        class Response:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {"results": copy.deepcopy(entry.get("results", [])),
                        "usage": {"credits": entry.get("credits", 1)}}

        return Response()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeTransports:
        import httpx2
        import langchain_tavily._utilities as tavily_utils

        monkeypatch.setattr(httpx2.Client, "send", lambda client, request, *a, **k: self.send(request))
        monkeypatch.setattr(tavily_utils.requests, "post", self.post)
        return self


def test_live_requests_built_by_the_real_clients(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: live_temperature_sent (temperature added to the ChatAnthropic arguments);
    live_search_depth_advanced (the Tavily search built at advanced depth); live_timeout_dropped
    (no client timeout, so the SDK's 600 second default applies)."""
    fake = FakeTransports(flo_data()).install(monkeypatch)
    thread_id, out = run_flo_live(monkeypatch, capsys, fake)  # type: ignore[arg-type]
    assert "status: ok" in out and "SC11: pass" in out

    assert [r["node"] for r in fake.anthropic] == ["read_plate", "research", "research", "research", "synthesize"]
    for req in fake.anthropic:
        body, node = req["body"], req["node"]
        assert req["url"].endswith("/v1/messages") and req["reserved"], req["node"]
        assert body["model"] == config.MODEL_FOR["cheap"][node]
        assert body["max_tokens"] == models.max_tokens_for(node, "cheap")
        assert not {"temperature", "top_p", "top_k", "thinking"} & set(body), sorted(body)
        assert "effort" not in (body.get("output_config") or {})  # never sent to Haiku
        assert req["headers"]["x-api-key"] == FAKE_ANTHROPIC  # read from the environment the .env filled
        assert req["headers"]["x-stainless-retry-count"] == "0"
        assert req["timeout"]["read"] == config.model_timeout_s(body["max_tokens"])
        structured = (body.get("output_config") or {}).get("format", {}).get("type")
        assert structured == (None if node == "research" else "json_schema")
        assert req["image"] == (node == "read_plate")
        assert ({t["name"] for t in body.get("tools") or []} == {"search", "fetch"}) == (node == "research")

    assert [r["tool"] for r in fake.tavily] == ["search", "search"]
    for req in fake.tavily:
        assert req["url"].endswith("/search") and req["reserved"]
        assert req["headers"]["Authorization"] == f"Bearer {FAKE_TAVILY}"
        for name, value in config.TAVILY_SEARCH_SETTINGS.items():
            assert req["params"][name] == value, name

    # The SDK parsed each reply's usage, and the ledger charged exactly that.
    charges = [r for r in Ledger(config.LEDGER_PATH).rows(thread_id) if r["kind"] == "charge"]
    anthropic = [r for r in charges if r["provider"] == "anthropic"]
    sent = [fake.data["read_plate"]["usage"], *(s["message"]["usage"] for s in fake.data["research"]["script"]),
            fake.data["synthesize"][0]["usage"]]
    assert [(r["input_tokens"], r["output_tokens"]) for r in anthropic] == \
        [(u["input_tokens"], u["output_tokens"]) for u in sent]
    assert sum(r["tavily_credits"] or 0 for r in charges if r["provider"] == "tavily") == 2
    written = b"\n".join(p.read_bytes() for p in live_env.rglob("*") if p.is_file())
    assert FAKE_ANTHROPIC.encode() not in written and FAKE_TAVILY.encode() not in written
    assert FAKE_ANTHROPIC not in out and FAKE_TAVILY not in out


# ---------------------------------------------------------------------------
# Refusals: nothing is built, nothing is reserved
# ---------------------------------------------------------------------------


def _nothing_spent(fakes: LiveFakes) -> None:
    assert fakes.constructed == [] and fakes.stubs == {}
    if config.LEDGER_PATH.exists():
        assert [r for r in Ledger(config.LEDGER_PATH).rows() if r["kind"] != "stop"] == []


REFUSALS = ("replay_with_live", "live_flag_missing", "ledger_missing", "ledger_empty", "budget_below_run_cap",
            "anthropic_key_missing", "tavily_key_missing", "haiku_retirement", "haiku_retirement_full",
            "full_mode", "typed_yes")


@pytest.mark.parametrize("case", REFUSALS)
def test_live_ask_refusals(case: str, live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                           capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: live_key_check_skipped (missing keys pass); live_haiku_date_ignored (the
    retirement date is never checked); live_cancel_continues (a wrong typed line still runs);
    cli_live_ledger_check_removed_phase5 (no ledger or budget check before a live run);
    ledger_empty_file_accepted (an empty ledger file passes as a ledger, finding M1);
    cli_full_mode_allowed (full mode runs live on the typed proceed alone, findings M2 and P5);
    T_retirement_check_synth_only (the Haiku check reads only the synthesize model, so full mode,
    whose other steps run on Haiku, loses it; finding T6)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    argv = ["--new-ledger"]
    expect = {
        "replay_with_live": "--live needs", "live_flag_missing": "needs --live", "ledger_missing": "no ledger file",
        "budget_below_run_cap": "build budget", "anthropic_key_missing": "missing ANTHROPIC_API_KEY",
        "tavily_key_missing": "missing TAVILY_API_KEY", "haiku_retirement": "Recheck the model list",
        "typed_yes": 'not exactly "proceed"',
    }.get(case, "")
    typed = "proceed\n"
    if case == "replay_with_live":
        monkeypatch.setenv(config.ENV_MODE, "replay")
    elif case == "live_flag_missing":
        type_line(monkeypatch, typed)
        assert cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--new-ledger"]) == cli.EXIT_REFUSED
        assert expect in capsys.readouterr().err
        _nothing_spent(fakes)
        return
    elif case in ("ledger_missing", "ledger_empty"):
        argv = []
        if case == "ledger_empty":
            config.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            config.LEDGER_PATH.touch()
            expect = "has no entries table"
    elif case == "budget_below_run_cap":
        Ledger(config.LEDGER_PATH, create=True)
        with sqlite3.connect(config.LEDGER_PATH) as conn:
            conn.execute("INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd) VALUES "
                         "('2026-09-01T00:00:00+00:00', 'earlier', 'cheap', 'synthesize', 'anthropic', 'charge', ?)",
                         (config.BUILD_CAP_USD - config.RUN_CAP_USD["cheap"] / 2,))
    elif case == "anthropic_key_missing":
        (tmp_path / ".env").write_text(f"TAVILY_API_KEY={FAKE_TAVILY}\n", encoding="utf-8")
    elif case == "tavily_key_missing":
        (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n", encoding="utf-8")
    elif case == "haiku_retirement":
        monkeypatch.setattr(config, "HAIKU_RETIREMENT_EARLIEST", cli._today().isoformat())
    elif case == "haiku_retirement_full":
        # Full mode reads plates, classifies and researches on Haiku: the retirement check applies.
        monkeypatch.setattr(config, "FULL_LIVE_APPROVED", True)
        monkeypatch.setenv(config.ENV_MODE, "full")
        monkeypatch.setattr(config, "HAIKU_RETIREMENT_EARLIEST", cli._today().isoformat())
        expect = "Recheck the model list"
    elif case == "full_mode":
        monkeypatch.setenv(config.ENV_MODE, "full")
        expect = "decision 18"
    elif case == "typed_yes":
        typed = "yes\n"
    def rows() -> list[dict[str, Any]]:
        return Ledger(config.LEDGER_PATH).rows() if config.LEDGER_PATH.stat().st_size else []

    before = rows() if config.LEDGER_PATH.exists() else []
    code, out, err = live_ask(monkeypatch, capsys, *argv, typed=typed)
    assert code == cli.EXIT_REFUSED, out + err
    assert expect in err, err
    assert "advisor ask: thread" not in out
    assert fakes.constructed == [] and fakes.stubs == {}
    if case == "full_mode":
        assert not config.LEDGER_PATH.exists()  # refused before the ledger was even opened
    if case == "ledger_empty":
        with pytest.raises(LedgerMissing):
            Ledger(config.LEDGER_PATH)  # still no ledger: nothing was spent against the empty file
        return
    after = rows() if config.LEDGER_PATH.exists() else []
    assert after == before


def test_live_resume_confirms_again_and_refuses_on_a_wrong_line(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: live_resume_skips_preflight (resume runs research without its own typed proceed)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    code, out, err = live_ask(monkeypatch, capsys, "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    thread_id = thread_of(out)
    built = len(fakes.constructed)
    code, out, err = live_resume(monkeypatch, capsys, thread_id, typed="proceed now\n")
    assert code == cli.EXIT_REFUSED and 'not exactly "proceed"' in err
    assert "the rest of one cheap run" in out  # the resume preflight was shown
    assert len(fakes.constructed) == built and fakes.stubs == {}
    code, out, err = live_resume(monkeypatch, capsys, thread_id)
    assert code == cli.EXIT_OK and "status: ok" in out, out + err


# ---------------------------------------------------------------------------
# check-schema (decision 22)
# ---------------------------------------------------------------------------


class _Raises:
    """A fake ChatAnthropic whose structured call raises the given error."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def with_structured_output(self, *args: Any, **kwargs: Any) -> Any:
        from langchain_core.runnables import RunnableLambda

        def fail(_: Any) -> Any:
            raise self.exc

        return RunnableLambda(fail)


def bad_request(message: str) -> BaseException:
    """An anthropic 400 built offline, as langchain-anthropic raises it."""
    import httpx2
    from langchain_anthropic.chat_models import AnthropicInvalidRequestError

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(400, request=request)
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return AnthropicInvalidRequestError(message=message, response=response, body=body)


def schema_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], chat: Any,
                 typed: str = "proceed\n") -> tuple[int, str, str, list[dict[str, Any]]]:
    built: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> Any:
        built.append(kwargs)
        return chat

    monkeypatch.setattr(models, "ChatAnthropic", factory)
    type_line(monkeypatch, typed)
    code = cli.main(["check-schema", "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    return code, out, err, built


NRA_DRAFT = {
    "status": "no_reliable_answer", "matched_identity": None, "warranty_caution": None, "happened_before": None,
    "try_first": [], "candidates": [], "warranty": None,
    "no_reliable_answer": {"found": [], "why_insufficient": "No documentation was provided."},
    "upgrade_options": [], "maintenance_due": [], "source_tiers": [],
}


def test_check_schema_accepted_is_reserved_and_charged(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: schema_check_max_tokens_from_synthesize (the call keeps synthesize's 6,000);
    schema_check_not_charged (the reply's usage is released, not charged); schema_check_plain_output
    (method json_schema dropped)."""
    usage = {"input_tokens": 1_900, "output_tokens": 120, "total_tokens": 2_020}
    chat = CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": usage}], model=config.HAIKU,
                       max_tokens=config.SCHEMA_CHECK_MAX_TOKENS, ledger_path=str(config.LEDGER_PATH))
    code, out, err, built = schema_check(monkeypatch, capsys, chat)
    assert code == cli.EXIT_OK, out + err
    assert "schema check: accepted" in out and "draft status: no_reliable_answer" in out
    assert built[0]["max_tokens"] == config.SCHEMA_CHECK_MAX_TOKENS and built[0]["model"] == config.HAIKU
    assert chat.checks == [True]
    assert chat.received_kwargs[0]["output_config"]["format"]["type"] == "json_schema"
    rows = Ledger(config.LEDGER_PATH).rows()
    reserve = [r for r in rows if r["kind"] == "reserve"]
    charge = [r for r in rows if r["kind"] == "charge"]
    assert [r["node"] for r in reserve] == [config.SCHEMA_CHECK_NODE] == [r["node"] for r in charge]
    assert reserve[0]["output_tokens"] == config.SCHEMA_CHECK_MAX_TOKENS
    assert charge[0]["input_tokens"] == 1_900 and charge[0]["output_tokens"] == 120 and charge[0]["mode"] == "cheap"


def test_check_schema_400_is_the_stop_condition(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: schema_check_400_reported_as_error (a 400 is not the stop condition);
    schema_check_400_exit_ok (exit 0 on a rejected schema)."""
    message = "output_config.format.schema: Schema is too complex for compilation"
    code, out, err, _ = schema_check(monkeypatch, capsys, _Raises(bad_request(message)))
    assert code == cli.EXIT_SCHEMA_REJECTED, out + err
    assert "schema check: rejected" in out and "HTTP 400" in out and message in out
    assert "Phase 5 stop condition" in out
    rows = Ledger(config.LEDGER_PATH).rows()
    assert [r["kind"] for r in rows] == ["reserve", "release"]  # a 400 carries no usage: nothing billed


def test_check_schema_charges_a_billed_error(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: schema_check_billed_error_released (an error carrying usage is released, not charged)."""
    from langchain_core.messages import AIMessage

    exc = RuntimeError("the reply could not be used")
    exc.ai_message = AIMessage(content="{", usage_metadata={"input_tokens": 2_000, "output_tokens": 512,
                                                            "total_tokens": 2_512})
    code, out, err, _ = schema_check(monkeypatch, capsys, _Raises(exc))
    assert code == cli.EXIT_LIVE_ERROR, out + err
    assert "schema check: error" in out and "ledger: charged, " in out
    charge = [r for r in Ledger(config.LEDGER_PATH).rows() if r["kind"] == "charge"]
    assert len(charge) == 1 and charge[0]["output_tokens"] == 512 and charge[0]["usd"] > 0
    assert charge[0]["input_tokens"] == 2_000 and charge[0]["estimated"] == 0  # its usage, not the reservation


def test_check_schema_refusals(live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: schema_check_needs_tavily (check-schema refuses without a Tavily key);
    schema_check_replay_allowed (replay mode reaches the call)."""
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n", encoding="utf-8")
    chat = CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": {"input_tokens": 1, "output_tokens": 1}}],
                       model=config.HAIKU, max_tokens=config.SCHEMA_CHECK_MAX_TOKENS,
                       ledger_path=str(config.LEDGER_PATH))
    code, out, err, built = schema_check(monkeypatch, capsys, chat)
    assert code == cli.EXIT_OK, out + err  # no Tavily key needed
    monkeypatch.setenv(config.ENV_MODE, "replay")
    code, out, err, built = schema_check(monkeypatch, capsys, chat)
    assert code == cli.EXIT_REFUSED and built == []
    type_line(monkeypatch, "proceed\n")
    assert cli.main(["check-schema"]) == cli.EXIT_REFUSED
    assert "makes a paid call" in capsys.readouterr().err


def test_check_schema_refuses_full_mode(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: cli_full_mode_allowed (check-schema makes the Sonnet 5 call on the typed proceed alone;
    decision 18 and the unconfirmed full synthesize timeout, findings M2 and P5)."""
    monkeypatch.setenv(config.ENV_MODE, "full")
    chat = CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": {"input_tokens": 1, "output_tokens": 1}}],
                       model=config.SONNET, max_tokens=config.SCHEMA_CHECK_MAX_TOKENS,
                       ledger_path=str(config.LEDGER_PATH))
    code, out, err, built = schema_check(monkeypatch, capsys, chat)
    assert code == cli.EXIT_REFUSED and "decision 18" in err, out + err
    assert built == [] and chat.checks == [] and not config.LEDGER_PATH.exists()
    assert "preflight" not in out


class _NoUsage:
    """A fake ChatAnthropic whose structured reply reports no usage."""

    def with_structured_output(self, *args: Any, **kwargs: Any) -> Any:
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda

        from agent.schemas import BriefDraft

        return RunnableLambda(lambda _: {"raw": AIMessage("{}"), "parsed": BriefDraft.model_validate(NRA_DRAFT),
                                         "parsing_error": None})


def test_check_schema_reply_without_usage_is_charged_its_reservation(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: schema_check_no_usage_free (a reply with no usage is charged at 0; finding M4)."""
    code, out, err, _ = schema_check(monkeypatch, capsys, _NoUsage())
    assert code == cli.EXIT_OK, out + err
    assert "ledger: charged_estimated" in out
    rows = Ledger(config.LEDGER_PATH).rows()
    [reserve] = [r for r in rows if r["kind"] == "reserve"]
    [charge] = [r for r in rows if r["kind"] == "charge"]
    assert charge["usd"] == pytest.approx(reserve["usd"]) and charge["estimated"] == 1
    assert reserve["usd"] > 0 and Ledger(config.LEDGER_PATH).build_total() == pytest.approx(reserve["usd"])


def test_live_resume_refuses_a_new_ledger(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: cli_resume_new_ledger_allowed (a live resume starts on a new, empty ledger and forgets
    the ask segment's spend; finding M3)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    code, out, err = live_ask(monkeypatch, capsys, "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    thread_id = thread_of(out)
    assert Ledger(config.LEDGER_PATH).run_total(thread_id) > 0  # read_plate spent money
    for suffix in ("", "-wal", "-shm"):
        Path(f"{config.LEDGER_PATH}{suffix}").unlink(missing_ok=True)
    built = len(fakes.constructed)
    type_line(monkeypatch, "proceed\n")
    code = cli.main(["resume", thread_id, *RESUME_FLAGS, "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    assert code == cli.EXIT_REFUSED, out + err
    assert "--new-ledger is refused with a live resume" in err
    assert not config.LEDGER_PATH.exists() and len(fakes.constructed) == built


def _files_holding(root: Path, secret: str) -> list[str]:
    """Every file under root, .env aside, whose bytes hold the secret (checkpoints.sqlite* included)."""
    return [str(p) for p in root.rglob("*") if p.is_file() and p.name != ".env" and secret.encode() in p.read_bytes()]


def _model_inputs(fakes: LiveFakes) -> str:
    return "\n".join(str(m.content) for chat in fakes.chats.values() for call in chat.received for m in call)


@pytest.mark.parametrize("how", ["raise", "error"])
def test_tavily_error_echoing_a_key_is_redacted_everywhere(
        how: str, live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: tools_error_not_redacted (the lookup log keeps a Tavily error verbatim);
    tools_tool_message_not_redacted (the error text goes back to the research model, key and all);
    tools_raised_error_not_scrubbed (a raised client error reaches the checkpoint);
    cli_scrub_only_after_success (no backstop scrub runs after a failed run).
    Findings M5 and T1: "raise" fails the run (exit 4), "error" becomes a ToolMessage the model reads."""
    text = f"401 unauthorized for key {FAKE_TAVILY}"
    data = flo_data()
    # The model's first search meets the error; the script then searches as recorded.
    data["research"]["script"].insert(0, copy.deepcopy(data["research"]["script"][0]))
    fakes = LiveFakes(data, search_failure=(how, text)).install(monkeypatch)
    code, out1, err1 = live_ask(monkeypatch, capsys, "--new-ledger")
    assert code == cli.EXIT_OK, out1 + err1
    code, out2, err2 = live_resume(monkeypatch, capsys, thread_of(out1))
    output = out1 + err1 + out2 + err2
    if how == "raise":
        assert code == cli.EXIT_LIVE_ERROR, output
        assert "the live call failed" in err2 and "[redacted]" in err2
    else:
        assert code == cli.EXIT_OK, output
        assert "[redacted]" in _model_inputs(fakes)  # the model saw the error, redacted
    for secret in (FAKE_TAVILY, FAKE_ANTHROPIC):
        assert secret not in output
        assert _files_holding(tmp_path, secret) == []
        assert secret not in _model_inputs(fakes)
    lookups = (live_env / "lookups").glob("*.jsonl")
    assert any("[redacted]" in p.read_text(encoding="utf-8") for p in lookups)
    assert "warning: a key value was found" not in output  # redacted at the source, not by the backstop


def test_live_failure_is_redacted_and_reported(live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: T_stderr_not_redacted (only stdout is redacted); T_live_failure_escapes_redaction
    (run_live re-raises, so a traceback prints after redaction is off); spendcap_error_not_scrubbed
    (a research model error that echoes a key reaches the checkpoint). Findings T1 and T2."""
    fakes = LiveFakes(flo_data(), chat_failures={"research": f"401 bad key {FAKE_ANTHROPIC}"},
                      echo_key=True).install(monkeypatch)
    code, out1, err1 = live_ask(monkeypatch, capsys, "--new-ledger")
    assert code == cli.EXIT_OK, out1 + err1
    code, out2, err2 = live_resume(monkeypatch, capsys, thread_of(out1))
    assert code == cli.EXIT_LIVE_ERROR, out2 + err2
    assert "the live call failed" in err2 and "[redacted]" in err2
    assert "debug client key on stderr [redacted]" in err1 + err2
    assert "Traceback" not in out2 + err2
    assert "warning: a key value was found" not in err2  # redacted at the source, not by the backstop
    for secret in (FAKE_ANTHROPIC, FAKE_TAVILY):
        assert secret not in out1 + err1 + out2 + err2
        assert _files_holding(tmp_path, secret) == []
    # The failed call's hold was released, not left open or charged.
    rows = Ledger(config.LEDGER_PATH).rows()
    assert open_reservations(config.LEDGER_PATH, "anthropic") == 0 and fakes.chats["research"].checks
    assert all(secret not in str(row) for row in rows for secret in (FAKE_ANTHROPIC, FAKE_TAVILY))


def test_checkpoint_scrub_masks_a_key_that_got_through(live_env: Path, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: env_sqlite_scrub_off (the backstop leaves a key in the checkpoint database).

    The source redaction is switched off here, so the key reaches the
    checkpoint the way it would through a path nobody foresaw; the backstop
    scrub must still leave no file holding it, and say so.
    """
    import agent.research.middleware as middleware

    monkeypatch.setattr(middleware, "scrub_exception", lambda exc, secrets: exc)
    LiveFakes(flo_data(), chat_failures={"research": f"401 bad key {FAKE_ANTHROPIC}"}).install(monkeypatch)
    code, out1, err1 = live_ask(monkeypatch, capsys, "--new-ledger")
    code, out2, err2 = live_resume(monkeypatch, capsys, thread_of(out1))
    assert code == cli.EXIT_LIVE_ERROR, out2 + err2
    assert f"a key value was found in {config.CHECKPOINT_PATH}" in err2
    assert _files_holding(tmp_path, FAKE_ANTHROPIC) == []
    assert FAKE_ANTHROPIC not in out2 + err2
    # The masked checkpoint still loads: the thread's state is readable.
    from agent.graph import build_graph, open_checkpointer, thread_config

    state = build_graph(open_checkpointer(config.CHECKPOINT_PATH)).get_state(thread_config(thread_of(out1)))
    assert state.values.get("run_id")
