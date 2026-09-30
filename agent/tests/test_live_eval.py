"""The LIVE evaluations, offline (PLAN section 5 SC3b and SC7b rows; decisions 33, 34, 35, 49).

The evaluators score synthetic run records. The two `advisor eval` commands
run here only with fake clients: agent.models' ChatAnthropic, TavilySearch and
TavilyExtract constructors are monkeypatched to scripted fakes, the network
is blocked, and every path the commands write is moved under tmp_path. No
test makes a paid call. Each test names the mutation that turns it red.

These tests reach agent/live only through agent.cli, the command surface
(`cli.eval_score` and `cli.main`), because test_offline_guard.py forbids a
test file that imports agent.live.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest
import typesafe_sdk

from agent import cli, config
from agent.graph import RETRY_NOT_AFFORDABLE
from agent.replay.replay_model import ReplayChatModel

# Built by concatenation so this file holds no key shaped string for the privacy scan.
FAKE_ANTHROPIC = "sk-" + "ant-" + "planted" + "Fake" + "0123456789"
FAKE_TAVILY = "tv" + "ly-" + "planted" + "Fake" + "9876543210"
FAKE_TYPESAFE = "apikey" + "_" + "planted" + "Fake" + "1357924680"
REAL_TYPESAFE_CLIENT = typesafe_sdk.TypeSafeClient


def fake_typesafe_client(**kwargs: Any) -> Any:
    """The real TypeSafeClient on a mock transport: every Jev question answered 0.5, no network."""
    def answer(request: httpx2.Request) -> httpx2.Response:
        questions = json.loads(request.content)["questions"]
        return httpx2.Response(200, json={"model": config.JEV_MODEL, "usage": {"input_tokens": 384, "output_tokens": 3},
                                          "answers": {name: {"type": "noul", "noul": 0.5} for name in questions}})

    return REAL_TYPESAFE_CLIENT(**kwargs, transport=httpx2.MockTransport(answer))
BUILD = "build-a"


# ---------------------------------------------------------------------------
# Synthetic run records
# ---------------------------------------------------------------------------


def _trail(*entries: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"tool": tool, "query": f"q{i}", "n_results": 1, "credits": 1 if status == "ok" else 0,
             "at": f"2026-09-20T00:00:{i:02d}", "status": status} for i, (tool, status) in enumerate(entries)]


def _rec(run_id: str, status: str, *, origin: str | None = None, stop: str | None = None,
         build: str = BUILD, fix: str | None = None, started: str = "2026-09-20T00:00:00",
         trail: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"kind": "run", "suite": "sc3b", "run_id": run_id, "status": status, "refusal_origin": origin,
            "stop_reason": stop, "build_id": build, "fix": fix, "started_at": started,
            "search_trail": trail if trail is not None else _trail(("search", "ok")),
            "credits": 1, "cost_usd": 0.05, "run_cap_usd": config.RUN_CAP_USD["cheap"], "latency_s": 9.5,
            **extra}


def sc3b(records: list[dict[str, Any]]) -> dict[str, Any]:
    return cli.eval_score("sc3b", records)[0]


def sc7b(records: list[dict[str, Any]], firsts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return cli.eval_score("sc7b", records, firsts)[0]


def test_sc3b_evaluator_scores_each_outcome() -> None:
    """Mutations: eval_budget_stop_counts_as_refusal (classify budget_stopped as a model
    refusal), eval_forced_counts_as_pass (a forced refusal passes), eval_unaffordable_retry_unflagged
    (the retry flag ignores the stop reason)."""
    records = [
        _rec("r-ok", "ok", started="2026-09-20T00:00:01"),
        _rec("r-stop", "budget_stopped", stop="run_cap", started="2026-09-20T00:00:02"),
        _rec("r-retry", "budget_stopped", stop=RETRY_NOT_AFFORDABLE, started="2026-09-20T00:00:03"),
        _rec("r-forced", "no_reliable_answer", origin="forced", started="2026-09-20T00:00:04"),
        _rec("r-model", "no_reliable_answer", origin="model", started="2026-09-20T00:00:05"),
    ]
    score = sc3b(records)
    assert score["headline"] == {
        "runs": 5, "model_refusals": 1, "forced_refusals": 1, "budget_stops": 2,
        "budget_stops_from_unaffordable_retry": 1, "ok": 1, "other": 0,
    }
    assert score["result"] == "1 of 5"
    assert score["passed"] is False
    outcome = {row["run_id"]: row["outcome"] for row in score["rows"]}
    assert outcome == {"r-ok": "ok", "r-stop": "budget_stopped", "r-retry": "budget_stopped",
                       "r-forced": "forced_refusal", "r-model": "model_refusal"}
    assert [r["run_id"] for r in score["rows"] if r["passed"]] == ["r-model"]
    assert {r["run_id"]: r["budget_stop_from_unaffordable_retry"] for r in score["rows"]}["r-retry"] is True
    assert {r["run_id"]: r["budget_stop_from_unaffordable_retry"] for r in score["rows"]}["r-stop"] is False
    # Each outcome alone, SC3B_RUNS times: only model refusals pass SC3b (decision 33).
    for rec, passed in zip(records, (False, False, False, False, True), strict=True):
        runs = [dict(rec, run_id=f"{rec['run_id']}-{k}") for k in range(config.SC3B_RUNS)]
        assert sc3b(runs)["passed"] is passed, rec["run_id"]
    assert sc3b([])["passed"] is False
    lines = "\n".join(cli.eval_score("sc3b", records)[1])
    assert "forced refusals 1" in lines and "budget stops 2" in lines and "MISS" in lines


def test_sc3b_evaluator_pools_unchanged_build_reruns() -> None:
    """Mutations: eval_keeps_latest_batch_only (an unchanged build's rerun replaces the first
    runs), eval_fix_not_required (a changed build without a logged fix is reported)."""
    first = [_rec(f"a{i}", "no_reliable_answer", origin="model", started=f"2026-09-20T00:00:0{i}") for i in range(3)]
    first[1] = _rec("a1", "budget_stopped", stop="run_cap", started="2026-09-20T00:00:01")
    rerun = [_rec(f"b{i}", "no_reliable_answer", origin="model", started=f"2026-09-21T00:00:0{i}") for i in range(3)]
    pooled = sc3b(first + rerun)
    assert pooled["result"] == "5 of 6"
    assert pooled["passed"] is False
    assert pooled["superseded_runs"] == []
    assert {r["run_id"] for r in pooled["rows"]} == {"a0", "a1", "a2", "b0", "b1", "b2"}

    fixed = [_rec(f"c{i}", "no_reliable_answer", origin="model", build="build-b", fix="retry gate misread the cap",
                  started=f"2026-09-22T00:00:0{i}") for i in range(3)]
    after_fix = sc3b(first + fixed)
    assert after_fix["result"] == "3 of 3"
    assert after_fix["passed"] is True
    assert after_fix["superseded_runs"] == ["a0", "a1", "a2"]
    assert after_fix["fix"] == "retry gate misread the cap"

    unlogged = [dict(r, fix=None) for r in fixed]
    no_fix = sc3b(first + unlogged)
    assert no_fix["passed"] is False
    assert no_fix["problems"] and "decision 35" in no_fix["problems"][0]

    # A pooled rerun of the fixed build carries no --fix (the build did not change): it
    # inherits the logged fix and is pooled with the fixed runs (findings P2 and T3).
    pooled_after_fix = [_rec(f"d{i}", "no_reliable_answer", origin="model", build="build-b",
                             started=f"2026-09-23T00:00:0{i}") for i in range(3)]
    both = sc3b(first + fixed + pooled_after_fix)
    assert both["problems"] == [] and both["passed"] is True
    assert both["result"] == "6 of 6" and both["fix"] == "retry gate misread the cap"
    assert both["superseded_runs"] == ["a0", "a1", "a2"]
    assert [b["build_id"] for b in both["superseded_builds"]] == [BUILD]
    # Two different fixes on one build is still no single logged fix.
    two = sc3b(first + fixed + [dict(r, fix="another defect") for r in pooled_after_fix])
    assert two["passed"] is False and "decision 35" in two["problems"][0]


def test_sc3b_needs_every_planned_run() -> None:
    """Mutation: eval_sc3b_planned_ignored (one model refusal alone passes SC3b; finding E1)."""
    one = sc3b([_rec("only", "no_reliable_answer", origin="model")])
    assert one["passed"] is False and one["planned"] == config.SC3B_RUNS
    assert any("needs at least" in p for p in one["problems"])
    lines = "\n".join(cli.eval_score("sc3b", [_rec("only", "no_reliable_answer", origin="model")])[1])
    assert f"planned at least {config.SC3B_RUNS} runs; reported 1" in lines and "MISS" in lines
    full = sc3b([_rec(f"r{i}", "no_reliable_answer", origin="model", started=f"2026-09-20T00:00:0{i}")
                 for i in range(config.SC3B_RUNS)])
    assert full["passed"] is True and full["problems"] == []


def test_sc7b_counts_searches_from_trail_not_model() -> None:
    """Mutations: eval_blocked_search_counted (blocked calls count as searches),
    eval_count_from_record_field (the count read from the record's own "searches" field,
    which a model or an older counter may have set), eval_fetch_counted_as_search,
    T_sc7b_counts_cap_refused_search (only blocked and run_credit_cap count as not reached,
    so run_cap, build_cap and build_credit_cap refusals count as searches; finding T5)."""
    from agent.ledger import STOP_REASONS

    # Every ledger refusal a Tavily call can meet before it is sent, and the tool call cap's "blocked".
    assert set(config.TRAIL_NOT_REACHED_STATUSES) == {"blocked"} | (
        set(STOP_REASONS) - {"tavily_plan_limit", "research_budget"})
    two_and_fetch = _rec("rep-1", "ok", trail=_trail(("search", "ok"), ("fetch", "ok"), ("search", "ok")),
                         role="repeat", model_key="optima880", label="vague symptom repeat",
                         route=["graph", "research"], searches=9, credits=3,
                         brief={"no_reliable_answer": None, "summary": "I ran 7 searches"})
    blocked = _rec("rep-2", "ok", trail=_trail(("search", "ok"), ("search", "ok"), ("search", "blocked"),
                                               ("search", "run_credit_cap"), ("search", "run_cap"),
                                               ("search", "build_cap"), ("search", "build_credit_cap")),
                   role="repeat", model_key="xr164ttr6036", label="Trane repeat", route=["graph", "research"],
                   searches=4, credits=2)
    first = _rec("first-1", "ok", trail=_trail(*[("search", "ok")] * 5, ("fetch", "ok")), role="first",
                 model_key="xr164ttr6036", started="2026-09-19T00:00:00")
    external = {"kind": "run", "role": "first", "model_key": "optima880", "run_id": "phase5",
                "started_at": "2026-09-18T00:00:00", "search_trail": _trail(*[("search", "ok")] * 4)}

    score = sc7b([first, two_and_fetch, blocked], [external])
    rows = {row["run_id"]: row for row in score["rows"]}
    assert set(rows) == {"rep-1", "rep-2"}
    assert rows["rep-1"]["searches"] == 2 and rows["rep-1"]["fetches"] == 1 and rows["rep-1"]["credits"] == 3
    assert rows["rep-1"]["first_lookup_searches"] == 4 and rows["rep-1"]["first_lookup_run_id"] == "phase5"
    assert rows["rep-2"]["searches"] == 2
    assert rows["rep-2"]["first_lookup_searches"] == 5
    assert [c["status"] for c in rows["rep-2"]["not_reached"]] == [
        "blocked", "run_credit_cap", "run_cap", "build_cap", "build_credit_cap"]
    assert rows["rep-2"]["route"] == ["graph", "research"]
    assert score["passed"] is True
    assert score["repeat_search_range"] == [2, 2]

    three = _rec("rep-3", "ok", trail=_trail(*[("search", "ok")] * 3), role="repeat", model_key="optima880")
    over = sc7b([first, two_and_fetch, three], [external])
    assert over["passed"] is False
    assert {r["run_id"]: r["passed"] for r in over["rows"]} == {"rep-1": True, "rep-3": False}
    assert over["repeat_search_range"] == [2, 3]
    text = "\n".join(cli.eval_score("sc7b", [first, two_and_fetch, blocked], [external])[1])
    assert "not sent: search 'q2' (blocked)" in text and "first lookup phase5 used 4" in text


def test_sc7b_repeat_that_did_not_finish_is_not_measured() -> None:
    """Mutations: eval_unfinished_repeat_passes (an errored or paused repeat with 0 searches passes);
    eval_budget_stop_measured (a budget stopped repeat counts as a measurement; finding E3)."""
    errored = _rec("rep-e", "error", trail=[], role="repeat", model_key="optima880")
    score = sc7b([errored])
    assert score["rows"][0]["measured"] is False
    assert score["passed"] is False
    assert sc7b([])["passed"] is False
    # A budget stop cuts research short: at read_plate (0 searches) or at the run cap (2).
    at_plate = _rec("rep-p", "budget_stopped", stop="run_cap", trail=[], role="repeat", model_key="optima880",
                    label="FLO repeat")
    at_cap = _rec("rep-c", "budget_stopped", stop="run_cap", trail=_trail(("search", "ok"), ("search", "ok")),
                  role="repeat", model_key="optima880", label="vague symptom repeat", started="2026-09-20T00:00:01")
    for rec in (at_plate, at_cap):
        one = sc7b([rec])
        assert one["rows"][0]["measured"] is False and one["passed"] is False, rec["run_id"]
    text = "\n".join(cli.eval_score("sc7b", [at_plate, at_cap])[1])
    assert "stop reason run_cap; not a measurement" in text and "MISS" in text


def test_sc11_post_condition_per_record() -> None:
    """Mutation: eval_sc11_compares_to_build_cap (compare cost with BUILD_CAP_USD)."""
    cap = config.RUN_CAP_USD["cheap"]
    records = [_rec("at-cap", "no_reliable_answer", origin="model", cost_usd=cap, started="2026-09-20T00:00:01"),
               _rec("over", "no_reliable_answer", origin="model", cost_usd=cap + 0.001, started="2026-09-20T00:00:02"),
               _rec("unknown", "no_reliable_answer", origin="model", cost_usd=None, started="2026-09-20T00:00:03")]
    score = sc3b(records)
    assert {row["run_id"]: row["sc11"] for row in score["rows"]} == {"at-cap": "pass", "over": "miss", "unknown": "miss"}
    assert score["sc11_misses"] == ["over", "unknown"]


# ---------------------------------------------------------------------------
# The commands, with fake clients
# ---------------------------------------------------------------------------

NODE_BY_MAX_TOKENS = {
    config.MAX_TOKENS["read_plate"]: "read_plate",
    config.MAX_TOKENS["classifier"]: "classifier",
    config.MAX_TOKENS["research"]: "research",
    config.MAX_TOKENS["synthesize_cheap"]: "synthesize",
}
USAGE = {"input_tokens": 1_000, "output_tokens": 100}
REFUSAL_DRAFT = {
    "status": "no_reliable_answer", "matched_identity": None, "warranty_caution": None, "happened_before": None,
    "try_first": [], "candidates": [], "warranty": None,
    "no_reliable_answer": {"found": ["No page in the provided sources documents this model."],
                           "why_insufficient": "Nothing provided matches the exact model."},
    "upgrade_options": [], "maintenance_due": [], "source_tiers": [],
}
SCRIPTS: dict[str, list[dict[str, Any]]] = {
    "read_plate": [{"structured": {
        "manufacturer": "SUNDANCE SPAS", "model": "OPTIMA 880", "serial": "SYN-PLATE-1", "manufacture_date": "06/2014",
        "confidence": {"manufacturer": "high", "model": "high", "serial": "high", "manufacture_date": "high"}},
        "usage": USAGE}],
    "classifier": [{"structured": {"verdict": "unsure", "confidence": 0.5, "reason": "fake"}, "usage": USAGE}],
    "research": [
        {"message": {"content": "", "tool_calls": [{"name": "search", "args": {"query": "fake manual search"}}],
                     "usage": USAGE}},
        {"message": {"content": "done", "usage": USAGE}},
    ],
    "synthesize": [{"structured": REFUSAL_DRAFT, "usage": USAGE}],
}
SEARCH_RESULTS = [
    {"url": "https://www.aquarestspas.com/owners", "title": "Owners", "content": "Synthetic snippet.",
     "raw_content": None, "score": 0.9},
    {"url": "https://example.com/fake/manual", "title": "Fake manual", "content": "Synthetic snippet.",
     "raw_content": None, "score": 0.8},
]


class Fakes:
    """Scripted stand ins for the live client constructors; they record what they saw."""

    def __init__(self) -> None:
        self.models: list[str] = []
        self.tavily: list[str] = []
        self.keys_seen: list[tuple[str | None, str | None]] = []

    def _keys(self) -> None:
        self.keys_seen.append((os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("TAVILY_API_KEY")))

    def chat(self, **kwargs: Any) -> ReplayChatModel:
        node = NODE_BY_MAX_TOKENS[kwargs["max_tokens"]]
        self.models.append(node)
        self._keys()
        return ReplayChatModel(responses=copy.deepcopy(SCRIPTS[node]), model=kwargs["model"],
                               max_tokens=kwargs["max_tokens"])

    def search(self, **kwargs: Any) -> Any:
        self.tavily.append("search")
        self._keys()
        return _FakeTavily({"results": copy.deepcopy(SEARCH_RESULTS), "usage": {"credits": 1}})

    def extract(self, **kwargs: Any) -> Any:
        self.tavily.append("extract")
        return _FakeTavily({"results": [], "failed_results": []})


class _FakeTavily:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def invoke(self, input: dict[str, Any]) -> Any:
        return copy.deepcopy(self.response)


@pytest.fixture()
def live_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    """cheap mode, every written path under tmp_path, a planted .env, fake clients, an existing ledger."""
    data = tmp_path / "data"
    for name, path in {
        "CHECKPOINT_PATH": data / "checkpoints.sqlite",
        "REPLAY_LEDGER_PATH": data / "ledger" / "replay_ledger.sqlite",
        "LEDGER_PATH": data / "ledger" / "ledger.sqlite",
        "REGISTRY_PATH": data / "registry.sqlite",
        "REPLAY_GRAPH_PATH": data / "replay_graph.json",
        "GRAPH_PATH": data / "graph.json",
        "PAGES_DIR": data / "pages",
        "LOOKUPS_DIR": data / "lookups",
        "BRIEFS_OUT_DIR": data / "briefs",
        "PROPERTY_OUT_DIR": data / "property",
        "EVAL_DIR": data / "eval",
        "ENV_FILE": tmp_path / ".env",
        "RECORDINGS_DIR": data / "recordings",
    }.items():
        monkeypatch.setattr(config, name, path)
    (tmp_path / ".env").write_text(
        f"# planted by test_live_eval\nANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nexport TAVILY_API_KEY=\"{FAKE_TAVILY}\"\n"
        f"{config.TYPESAFE_KEY_NAME}={FAKE_TYPESAFE}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    monkeypatch.delenv(config.ENV_CASSETTE, raising=False)
    monkeypatch.delenv(config.ENV_TRACING, raising=False)
    for name in config.LIVE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    fakes = Fakes()
    import agent.models as models

    monkeypatch.setattr(models, "ChatAnthropic", fakes.chat)
    monkeypatch.setattr(models, "TavilySearch", fakes.search)
    monkeypatch.setattr(models, "TavilyExtract", fakes.extract)
    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", fake_typesafe_client)
    from agent.ledger import Ledger

    Ledger(config.LEDGER_PATH, create=True)
    return fakes


def _run(argv: list[str], stdin: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return cli.main(argv)


def _spent_nothing(fakes: Fakes) -> None:
    from agent.ledger import Ledger

    assert fakes.models == [] and fakes.tavily == []
    assert Ledger(config.LEDGER_PATH).rows() == []
    assert not config.EVAL_DIR.exists() or not list(config.EVAL_DIR.glob("*.json"))


@pytest.mark.parametrize("which", ["sc3b", "sc7b"])
def test_eval_refuses_without_live(which: str, live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: eval_live_flag_not_checked (check_mode ignores --live)."""
    assert _run(["eval", which], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "--live" in captured.err
    assert "planned runs" not in captured.out
    _spent_nothing(live_env)
    assert "ANTHROPIC_API_KEY" not in os.environ


@pytest.mark.parametrize("which", ["sc3b", "sc7b"])
@pytest.mark.parametrize("mode", ["replay", "full"])
def test_eval_refuses_outside_cheap_mode(which: str, mode: str, live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: eval_mode_check_skipped (check_mode accepts any mode resolve_mode accepts)."""
    # Even with full mode approved for ask and resume, eval runs only in cheap mode (decision 49).
    monkeypatch.setattr(config, "FULL_LIVE_APPROVED", True)
    monkeypatch.setenv(config.ENV_MODE, mode)
    assert _run(["eval", which, "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "planned runs" not in capsys.readouterr().out
    _spent_nothing(live_env)


@pytest.mark.parametrize("which", ["sc3b", "sc7b"])
@pytest.mark.parametrize("typed", ["", "\n", "Proceed\n", "proceed now\n", " proceed\n", "yes\n"])
def test_eval_refuses_without_the_typed_proceed(which: str, typed: str, live_env: Fakes,
                                                monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: eval_confirmation_case_insensitive (lower() before comparing),
    eval_confirmation_prefix (startswith instead of equality), eval_confirmation_skipped."""
    assert _run(["eval", which, "--live"], typed, monkeypatch) == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "planned runs" in captured.out  # the preflight came first
    assert "proceed" in captured.err
    _spent_nothing(live_env)
    assert "ANTHROPIC_API_KEY" not in os.environ


def _typical(out: str) -> float:
    return float(re.search(r"estimated cost: typical ([0-9.]+) USD", out).group(1))


def _records(suite: str) -> list[dict[str, Any]]:
    """The run records a batch wrote, oldest first, read straight from the eval folder."""
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in config.EVAL_DIR.glob(f"{suite}-*.json")]
    return sorted((r for r in rows if r.get("kind") == "run"), key=lambda r: r["started_at"])


def _preflight(which: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    assert _run(["eval", which, "--live"], "no\n", monkeypatch) == cli.EXIT_REFUSED
    return capsys.readouterr().out


def test_eval_preflight_counts_match_the_plan(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: eval_sc3b_run_count_hard_coded (a literal 2 in place of SC3B_RUNS),
    eval_worst_case_hard_coded (worst case not from RUN_CAP_USD), eval_sc7b_spec_dropped,
    eval_first_pass_credits_ignore_fetch (fetch cap left out of the first pass credits)."""
    out = _preflight("sc3b", monkeypatch, capsys)
    assert "planned runs: 3" in out
    assert "worst case 0.4500 USD (3 runs at the 0.1500 USD per run cap)" in out
    assert "at most 24 on first passes, ceiling 30 with retries" in out
    assert "per run cap: 0.1500 USD and 10 Tavily credits" in out
    assert "build spend so far: 0.0000 USD of 5.0000 USD; remaining 5.0000 USD" in out
    assert _typical(out) == pytest.approx(0.24, abs=0.005)  # PLAN section 9: 3 x 0.08
    assert out.count("expected route research: typical") == 3

    out = _preflight("sc7b", monkeypatch, capsys)
    assert "planned runs: 4" in out
    assert "worst case 0.6000 USD (4 runs at the 0.1500 USD per run cap)" in out
    assert "at most 14 on first passes, ceiling 40 with retries" in out
    assert "at most 17 on first passes if a run falls from its expected route" in out
    assert "first lookup for optima880: none found" in out
    assert _typical(out) == pytest.approx(0.20, abs=0.01)  # PLAN section 9: 0.03 + 0.05 + 0.08 + 0.04
    planned = [line.split(". ", 1)[1].split(" (", 1)[0] for line in out.splitlines() if re.match(r"  \d\. ", line)]
    assert planned == ["FLO repeat", "vague symptom repeat", "Trane XR16 first lookup", "Trane repeat"]
    assert "expected route graph" in out and out.count("expected route top_up") == 2

    # Computed from config, not written in: a different cap and search cap move the numbers.
    monkeypatch.setattr(config, "RUN_CAP_USD", {**config.RUN_CAP_USD, "cheap": 0.2})
    monkeypatch.setattr(config, "RESEARCH_LIMITS", {**config.RESEARCH_LIMITS,
                                                    "main": {"search": 4, "fetch": 3, "loop_guard": 9}})
    out = _preflight("sc3b", monkeypatch, capsys)
    assert "worst case 0.6000 USD (3 runs at the 0.2000 USD per run cap)" in out
    assert "at most 21 on first passes" in out
    _spent_nothing(live_env)


def test_eval_preflight_names_and_prices_the_jev_calls(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """Every sc7b run goes past the pause to synthesize and safety_check, so the preflight names its
    Jev calls and counts their cost; with Jev disabled it plans none.

    Mutations: eval_estimate_drops_jev_cost (the Jev calls are listed but not priced);
    eval_preflight_drops_jev_calls (the planned paid calls line leaves Jev out)."""
    # Inflated so the Jev share shows at the preflight's four decimals.
    monkeypatch.setattr(config, "JEV_EST_INPUT_TOKENS", 100_000)
    out = _preflight("sc7b", monkeypatch, capsys)
    calls = 4 * config.JEV_PLAN_STEPS_PER_PASS
    assert f"Tavily searches and {calls} Jev calls (typesafe)" in out
    assert f"Jev calls (typesafe, {config.JEV_MODEL}): planned {calls} for one draft" in out
    with_jev = _typical(out)
    monkeypatch.setattr(config, "JEV_SAFETY_ENABLED", False)
    out = _preflight("sc7b", monkeypatch, capsys)
    assert "Tavily searches and 0 Jev calls (typesafe)" in out and f"({config.JEV_PROVIDER}, " not in out
    per_call = 100_000 * config.PRICES_PER_MTOK[config.JEV_MODEL]["input"] / 1_000_000
    assert with_jev - _typical(out) == pytest.approx(calls * per_call, abs=0.0002)
    _spent_nothing(live_env)


def test_eval_refuses_missing_key_ledger_budget_and_retired_model(
    live_env: Fakes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: eval_missing_key_allowed, eval_retirement_date_unchecked,
    eval_batch_budget_single_run (the batch check compares with one run's cap)."""
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n", encoding="utf-8")
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "TAVILY_API_KEY" in err and FAKE_ANTHROPIC not in err
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nTAVILY_API_KEY={FAKE_TAVILY}\n"
                                   f"{config.TYPESAFE_KEY_NAME}={FAKE_TYPESAFE}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_today", lambda: date.fromisoformat(config.HAIKU_RETIREMENT_EARLIEST))
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "Recheck the model list" in capsys.readouterr().err
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 9, 18))

    # 4.60 spent leaves 0.40, enough for one run but not three at the cap.
    from agent.ledger import Ledger

    ledger = Ledger(config.LEDGER_PATH)
    hold = ledger.reserve("old", mode="cheap", node="synthesize", model=config.HAIKU,
                          input_tokens_est=10, max_tokens=10)
    ledger.charge(hold, {"input_tokens": 4_600_000, "output_tokens": 0})
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "below 3 runs" in capsys.readouterr().err

    config.LEDGER_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(config.LEDGER_PATH) + suffix).unlink(missing_ok=True)
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "--new-ledger" in capsys.readouterr().err
    assert not config.LEDGER_PATH.exists()
    assert live_env.models == [] and live_env.tavily == []


def _all_written_text(root: Path) -> bytes:
    return b"\n".join(p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name != ".env")


def test_sc3b_batch_runs_the_graph_with_fake_clients_and_never_leaks_a_key(
    live_env: Fakes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: eval_key_written_to_record (the key value added to each run record),
    eval_keys_left_in_environ (keys stay in os.environ after the batch), eval_record_trail_dropped,
    eval_record_v1_checks_dropped (the record leaves out its v1 case check; finding P4)."""
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    captured = capsys.readouterr()
    records = _records("sc3b")
    assert len(records) == config.SC3B_RUNS
    for record in records:
        assert record["status"] == "no_reliable_answer" and record["refusal_origin"] == "model"
        assert record["searches"] == 1 and record["fetches"] == 0 and record["credits"] == 1
        assert [e["tool"] for e in record["search_trail"]] == ["search"]
        assert record["reached_maker_docs"] is True
        assert record["budget_stop_from_unaffordable_retry"] is False
        assert record["sc11"] == "pass" and 0 < record["cost_usd"] <= config.RUN_CAP_USD["cheap"]
        assert record["latency_s"] > 0 and record["build_id"] == records[0]["build_id"]
    assert "SC3b: 3 of 3 runs refused by the model; PASS" in captured.out
    # PLAN 6.5: each record asserts v1 case B4 (finding P4).
    assert all(r["v1_checks"]["case"] == "B4" and r["v1_checks"]["passed"] is True for r in records)
    assert captured.out.count("v1 case B4 ") == config.SC3B_RUNS
    # The fakes saw the keys while the batch ran, so the clients would find them.
    assert live_env.keys_seen and set(live_env.keys_seen) == {(FAKE_ANTHROPIC, FAKE_TAVILY)}
    # No key anywhere the run wrote, in its output, or left in the environment.
    written = _all_written_text(tmp_path)
    for key in (FAKE_ANTHROPIC, FAKE_TAVILY, FAKE_TYPESAFE):
        assert key.encode() not in written
        assert key not in captured.out and key not in captured.err
    assert "ANTHROPIC_API_KEY" not in os.environ and "TAVILY_API_KEY" not in os.environ
    assert config.TYPESAFE_KEY_NAME not in os.environ


def test_sc7b_batch_answers_the_pause_and_records_every_run(
    live_env: Fakes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: eval_scripted_code_dropped (the scripted answer leaves out the code),
    eval_pause_not_answered (EVAL_MAX_PAUSES 0)."""
    assert _run(["eval", "sc7b", "--live"], "proceed\n", monkeypatch) in (cli.EXIT_OK,)
    captured = capsys.readouterr()
    records = {r["label"]: r for r in _records("sc7b")}
    assert list(records) == ["FLO repeat", "vague symptom repeat", "Trane XR16 first lookup", "Trane repeat"]
    flo = records["FLO repeat"]
    assert flo["pauses"] == 1 and flo["status"] != "paused"
    assert flo["answers"][0]["observed_code"] == "FLO"
    assert flo["answers"][0]["identity"]["model"] == "Optima 880"
    assert flo["answers"][0]["identity"]["serial"] == "SYN-PLATE-1"  # the proposal kept where the spec is silent
    assert "graph route caps searches at 0 by design" in flo["note"]
    assert records["vague symptom repeat"]["answers"][0]["observed_code"] is None
    assert records["Trane XR16 first lookup"]["pauses"] == 0
    assert "SC7b:" in captured.out and "first lookup" in captured.out
    written = _all_written_text(tmp_path)
    assert FAKE_ANTHROPIC.encode() not in written and FAKE_TAVILY.encode() not in written
    assert "read_plate" in live_env.models


def test_sc7b_finds_the_phase5_first_lookup_from_its_run_record(
    live_env: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: eval_first_lookup_accepts_replay (replay run records taken as first lookups);
    eval_first_lookup_counts_eval_runs (a batch's own repeat taken as its first lookup);
    eval_first_lookup_newest_wins (a later live run replaces the Phase 5 run).

    A fake batch leaves live Optima 880 runs under data/runs. Copies of one
    stand in for the Phase 5 run (the earliest live run no eval batch made)
    and for a later live run; a replay copy and the batch's own repeats are
    dated earlier still, so each wrong rule picks a different record.
    """
    assert _run(["eval", "sc7b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    capsys.readouterr()
    by_label = {r["label"]: r for r in _records("sc7b")}
    runs = config.PAGES_DIR.parent / "runs"
    logs = config.PAGES_DIR.parent / config.LOOKUPS_DIR.name
    base = json.loads((runs / f"{by_label['vague symptom repeat']['run_id']}.json").read_text(encoding="utf-8"))

    def plant(run_id: str, mode: str, generated_at: str, searches: int) -> None:
        (runs / f"{run_id}.json").write_text(
            json.dumps(dict(base, run_id=run_id, mode=mode, generated_at=generated_at)), encoding="utf-8")
        (logs / f"{run_id}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in _trail(*[("search", "ok")] * searches)) + "\n", encoding="utf-8")

    plant("t-phase5first", "cheap", "2026-09-01T00:00:00+00:00", 4)
    plant("t-laterlive", "cheap", "2026-09-02T00:00:00+00:00", 3)
    plant("t-replaycopy", "replay", "1999-01-01T00:00:00+00:00", 5)
    for label in ("FLO repeat", "vague symptom repeat"):
        path = runs / f"{by_label[label]['run_id']}.json"
        path.write_text(json.dumps(dict(json.loads(path.read_text(encoding="utf-8")),
                                        generated_at="2000-01-01T00:00:00+00:00")), encoding="utf-8")

    assert _run(["eval", "sc7b", "--score"], "", monkeypatch) == cli.EXIT_OK
    out = capsys.readouterr().out
    flo_line = next(line for line in out.splitlines() if line.lstrip().startswith("FLO repeat"))
    assert "first lookup t-phase5first used 4" in flo_line, flo_line
    for other in ("t-replaycopy", "t-laterlive", by_label["FLO repeat"]["run_id"],
                  by_label["vague symptom repeat"]["run_id"]):
        assert f"first lookup {other}" not in out


def test_eval_score_reads_records_and_spends_nothing(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: eval_score_accepts_live (--score with --live is not refused)."""
    config.EVAL_DIR.mkdir(parents=True)
    rows = [_rec("x0", "no_reliable_answer", origin="model", started="2026-09-20T00:00:00"),
            _rec("x1", "budget_stopped", stop="run_cap", started="2026-09-20T00:00:01")]
    for row in rows:
        (config.EVAL_DIR / f"sc3b-{row['run_id']}.json").write_text(json.dumps(row), encoding="utf-8")
    assert _run(["eval", "sc3b", "--score"], "", monkeypatch) == cli.EXIT_OK
    assert "SC3b: 1 of 2 runs refused by the model; MISS" in capsys.readouterr().out
    assert _run(["eval", "sc3b", "--score", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "--score only reads run records" in capsys.readouterr().err
    assert live_env.models == [] and live_env.tavily == []


def test_rerun_on_a_changed_build_needs_a_logged_fix(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: eval_rerun_check_skipped (check_rerun returns at once); eval_rerun_preflight_silent (the
    preflight of a changed build's rerun does not say what it replaces; finding E2); eval_pooled_fix_dropped
    (a pooled rerun of the fixed build carries no fix, so the fixed build can never be scored; P2)."""
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    capsys.readouterr()
    calls = len(live_env.models)
    assert _run(["eval", "sc3b", "--live", "--fix", "a defect"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "has not changed" in capsys.readouterr().err
    for path in config.EVAL_DIR.glob("sc3b-*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(dict(record, build_id="older-build")), encoding="utf-8")
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    assert "--fix" in capsys.readouterr().err
    assert len(live_env.models) == calls

    # The preflight of a rerun on a changed build says what it replaces, and with which fix.
    assert _run(["eval", "sc3b", "--live", "--fix", "retry gate misread the cap"], "no\n",
                monkeypatch) == cli.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "REPLACES the reported sc3b result; logged fix: 'retry gate misread the cap'" in out
    assert "build older-build: 3 run(s) (no_reliable_answer 3); model refusals 3" in out
    assert len(live_env.models) == calls

    assert _run(["eval", "sc3b", "--live", "--fix", "retry gate misread the cap"], "proceed\n",
                monkeypatch) == cli.EXIT_OK
    capsys.readouterr()
    summary = json.loads(sorted(config.EVAL_DIR.glob("summary-sc3b-*.json"))[-1].read_text(encoding="utf-8"))
    assert summary["rerun"]["replaces"] is True
    assert [b["build_id"] for b in summary["rerun"]["superseded"]] == ["older-build"]
    # A pooled rerun of the fixed build takes no --fix and carries the logged one (decision 35).
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "these runs are pooled with its earlier runs and carry the same fix" in out
    current = [r for r in _records("sc3b") if r["build_id"] != "older-build"]
    assert len(current) == 2 * config.SC3B_RUNS
    assert {r["fix"] for r in current} == {"retry gate misread the cap"}
    assert f"SC3b: {2 * config.SC3B_RUNS} of {2 * config.SC3B_RUNS} runs refused by the model; PASS" in out


# ---------------------------------------------------------------------------
# Review fixes: first lookups, planned runs, counts, v1 checks (findings E1, E4, E5, P4, P8)
# ---------------------------------------------------------------------------


def _sc7b_rec(run_id: str, label: str, role: str, key: str, status: str = "ok", searches: int = 1,
              started: str = "2026-09-20T00:00:00", **extra: Any) -> dict[str, Any]:
    return _rec(run_id, status, trail=_trail(*[("search", "ok")] * searches), role=role, model_key=key,
                label=label, started=started, **{"suite": "sc7b", **extra})


def test_sc7b_compares_each_repeat_with_the_earliest_first_lookup() -> None:
    """Mutation: eval_first_counts_latest_wins (a pooled rerun's second "first lookup", which found the
    graph already holding the model, replaces the true first lookup; findings E4 and P8)."""
    f1 = _sc7b_rec("f1", "Trane XR16 first lookup", "first", "xr164ttr6036", searches=5, started="2026-09-20T00:00:00")
    rep1 = _sc7b_rec("r1", "Trane repeat", "repeat", "xr164ttr6036", searches=2, started="2026-09-20T00:01:00")
    f2 = _sc7b_rec("f2", "Trane XR16 first lookup", "first", "xr164ttr6036", searches=1, started="2026-09-21T00:00:00")
    rep2 = _sc7b_rec("r2", "Trane repeat", "repeat", "xr164ttr6036", searches=1, started="2026-09-21T00:01:00")
    score = sc7b([f1, rep1, f2, rep2])
    rows = {row["run_id"]: row for row in score["rows"]}
    assert rows["r1"]["first_lookup_run_id"] == "f1" and rows["r1"]["first_lookup_searches"] == 5
    assert rows["r2"]["first_lookup_run_id"] == "f1"
    # The second first lookup is scored as a repeat, against the limit, and said to be one.
    assert rows["f2"]["searches"] == 1 and rows["f2"]["passed"] is True
    assert "graph already covered" in rows["f2"]["note"]
    assert score["passed"] is True
    over = sc7b([f1, rep1, dict(f2, search_trail=_trail(*[("search", "ok")] * 3))])
    assert over["passed"] is False  # a second first lookup that searched 3 times misses the limit


def test_sc7b_needs_every_planned_repeat_and_a_finished_first_lookup() -> None:
    """Mutations: eval_sc7b_planned_ignored (a planned repeat with no run passes); eval_failed_first_hidden
    (an errored first lookup vanishes from the result; finding E1)."""
    external = {"kind": "run", "role": "first", "model_key": "optima880", "run_id": "phase5",
                "started_at": "2026-09-18T00:00:00", "search_trail": _trail(*[("search", "ok")] * 4)}
    flo = _sc7b_rec("s1", "FLO repeat", "repeat", "optima880", searches=0)
    vague = _sc7b_rec("s2", "vague symptom repeat", "repeat", "optima880", searches=2, started="2026-09-20T00:00:01")
    trane_first = _sc7b_rec("s3", "Trane XR16 first lookup", "first", "xr164ttr6036", searches=5,
                            started="2026-09-20T00:00:02")
    trane_rep = _sc7b_rec("s4", "Trane repeat", "repeat", "xr164ttr6036", started="2026-09-20T00:00:03")

    complete = cli.eval_score("sc7b", [flo, vague, trane_first, trane_rep], [external], planned=True)[0]
    assert complete["passed"] is True and complete["problems"] == []
    missing = cli.eval_score("sc7b", [flo, vague, trane_first], [external], planned=True)
    assert missing[0]["passed"] is False
    assert any("planned repeat 'Trane repeat' has no finished run" in p for p in missing[0]["problems"])
    assert "problem: planned repeat 'Trane repeat'" in "\n".join(missing[1])

    errored_first = dict(trane_first, status="error", search_trail=[])
    failed = cli.eval_score("sc7b", [flo, vague, errored_first, trane_rep], [external], planned=True)[0]
    assert failed["passed"] is False
    assert any("Trane XR16 first lookup (s3) did not finish: status error" in p for p in failed["problems"])


def test_persisted_run_record_and_eval_record_count_searches_alike() -> None:
    """Mutation: persist_counts_ledger_refusals (the run record counts searches the ledger refused
    before Tavily was called; finding E5)."""
    from agent.nodes.persist import _count

    trail = _trail(("search", "ok"), ("search", "run_credit_cap"), ("search", "blocked"), ("search", "build_cap"),
                   ("fetch", "ok"), ("fetch", "run_cap"))
    row = sc7b([_rec("rep", "ok", trail=trail, role="repeat", model_key="optima880")])["rows"][0]
    assert _count(trail, "search") == row["searches"] == 1
    assert _count(trail, "fetch") == row["fetches"] == 1


def _state(status: str, **brief: Any) -> dict[str, Any]:
    return {"status": status, "brief": {"status": status, "sources": [], "candidates": [], "try_first": [], **brief}}


MAKER = {"url": "https://example.com/maker", "tier": "manufacturer"}
FORUM = {"url": "https://example.com/forum", "tier": "forum"}


@pytest.mark.parametrize("case,state,raw,passed", [
    ("B1", _state("ok", sources=[MAKER], candidates=[{"code": "FLO", "source_index": 0}],
                  try_first=[{"step": "Check the filter", "detail": "and the water level"}]), None, True),
    ("B1", _state("ok", sources=[FORUM], candidates=[{"code": "FLO", "source_index": 0}]), None, False),
    ("B1", _state("ok", sources=[MAKER], candidates=[{"code": "OH", "source_index": 0}]), None, False),
    ("B2", _state("ok", observed_code="FLO", candidates=[{"code": "FLO", "confirmed": True}]), None, True),
    ("B2", _state("ok", observed_code="FLO", candidates=[{"code": "FLO", "confirmed": True},
                                                       {"code": "FL1", "confirmed": True}]), None, False),
    ("B2", _state("ok", candidates=[{"code": "FLO", "confirmed": True}]), None, False),
    ("B2", {**_state("budget_stopped"), "stop_reason": "run_cap"}, None, False),
    ("B3", _state("ok", sources=[MAKER], candidates=[{"code": "E5", "source_index": 0}]), None, True),
    ("B3", _state("ok", sources=[MAKER], candidates=[{"code": "E5", "source_index": 3}]), None, False),
    ("B3", _state("no_reliable_answer", no_reliable_answer={"searched": ["q"], "found": [],
                                                            "why_insufficient": "none"}), None, True),
    ("B3", _state("budget_stopped"), None, None),
    ("B4", _state("no_reliable_answer", no_reliable_answer={"searched": ["q"]}), None, True),
    ("B4", _state("no_reliable_answer", no_reliable_answer={"searched": []}), None, False),
    ("B4", _state("ok", candidates=[{"code": "X"}]), None, False),
    ("E1", {"extraction": {"manufacturer": "SUNDANCE SPAS", "model": "OPTIMA 880", "serial": config.E1_PLATE_SERIAL}},
     {"model": "OPTIMA 880"}, True),
    ("E1", {"extraction": {"manufacturer": "SUNDANCE SPAS", "model": "OPTIMA 880", "serial": "100915743"}},
     None, False),
    ("E2", {"extraction": {"model": None, "serial": None, "manufacture_date": None}},
     {"model": None, "serial": None, "manufacture_date": None,
      "confidence": {"model": "unreadable", "serial": "unreadable", "manufacture_date": "unreadable"}}, True),
    # The model guessed a model and marked it low: the final is null only because code nulled nothing.
    ("E2", {"extraction": {"model": None, "serial": None, "manufacture_date": None}},
     {"model": "OPT 88", "serial": None, "manufacture_date": None,
      "confidence": {"model": "low", "serial": "unreadable", "manufacture_date": "unreadable"}}, False),
    ("E2", {"extraction": {"model": None, "serial": None, "manufacture_date": None}}, None, False),
])
def test_v1_case_checks(case: str, state: dict[str, Any], raw: dict[str, Any] | None, passed: bool | None) -> None:
    """Mutations: v1_b2_confirmed_at_least_one (B2 accepts 2 confirmed candidates); v1_e2_final_only
    (E2 reads only the final extraction, so a guess code nulled passes); v1_b1_tier_ignored (B1 passes
    with only forum citations). Finding P4: PLAN 6.5's checks, asserted per case."""
    result, lines = cli.v1_case_checks(case, state, raw)
    assert result["case"] == case and result["passed"] is passed, result
    assert lines[0].startswith(f"  v1 case {case}: ")


def test_sc3b_batches_on_one_build_are_pooled(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: T_batch_scores_only_this_batch (run_batch scores this batch's records alone, so a rerun
    substitutes for the first runs instead of pooling; finding T4)."""
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    first = {r["run_id"] for r in _records("sc3b")}
    capsys.readouterr()
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    out = capsys.readouterr().out
    runs = 2 * config.SC3B_RUNS
    assert f"SC3b: {runs} of {runs} runs refused by the model; PASS" in out
    summaries = sorted(config.EVAL_DIR.glob("summary-sc3b-*.json"))
    latest = json.loads(summaries[-1].read_text(encoding="utf-8"))
    scored = {row["run_id"] for row in latest["score"]["rows"]}
    assert first < scored and len(scored) == runs
    assert set(latest["this_batch"]) == scored - first


def test_batch_cut_short_leaves_a_record_for_every_planned_run(
    live_env: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: eval_not_started_unrecorded (a batch the build budget stops between runs writes nothing
    for the runs it skipped, so the result reads "1 of 1"; finding E1)."""
    from agent.ledger import Ledger

    ledger = Ledger(config.LEDGER_PATH)
    hold = ledger.reserve("old", mode="cheap", node="synthesize", model=config.HAIKU, input_tokens_est=10, max_tokens=10)
    ledger.charge(hold, {"input_tokens": 4_540_000, "output_tokens": 0})  # 0.46 left: three runs at the cap fit
    # The first run's synthesize reply bills 0.40 (charged in full, above its reservation).
    monkeypatch.setitem(SCRIPTS, "synthesize", [{"structured": REFUSAL_DRAFT,
                                                 "usage": {"input_tokens": 400_000, "output_tokens": 100}}])
    assert _run(["eval", "sc3b", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    out = capsys.readouterr().out
    records = _records("sc3b")
    assert [r["status"] for r in records] == ["no_reliable_answer", "not_started", "not_started"]
    assert all("not started" in r["error"] for r in records[1:])
    assert "stopped before run 2" in out
    assert "SC3b: 1 of 3 runs refused by the model; MISS" in out


PLATE_REPLIES = {
    "E1": {"manufacturer": "SUNDANCE SPAS", "model": "OPTIMA 880", "serial": config.E1_PLATE_SERIAL,
           "manufacture_date": "06/2014",
           "confidence": {"manufacturer": "high", "model": "high", "serial": "high", "manufacture_date": "high"}},
    "E2": {"manufacturer": "SUNDANCE SPAS", "model": None, "serial": None, "manufacture_date": None,
           "confidence": {"manufacturer": "low", "model": "unreadable", "serial": "unreadable",
                          "manufacture_date": "unreadable"}},
}


def test_plate_runs_read_each_plate_and_stop_at_the_pause(
    live_env: Fakes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: plates_pause_answered (the plate runs answer the pause and go on to research);
    plates_worst_case_at_run_cap (the preflight's worst case is not the read_plate reservation);
    plates_raw_extraction_dropped (the record keeps only the final extraction). Finding P3: PLAN
    section 9's "6, plates" row and section 6.5's E1 and E2."""
    from agent.ledger import estimate_input_tokens, price_usage
    from agent.models import max_tokens_for
    from agent.nodes.read_plate import plate_messages

    replies = [PLATE_REPLIES["E1"], PLATE_REPLIES["E2"]]
    built: list[str] = []

    def chat(**kwargs: Any) -> ReplayChatModel:
        built.append(NODE_BY_MAX_TOKENS[kwargs["max_tokens"]])
        return ReplayChatModel(responses=[{"structured": replies.pop(0), "usage": USAGE}], model=kwargs["model"],
                               max_tokens=kwargs["max_tokens"])

    import agent.models as models

    monkeypatch.setattr(models, "ChatAnthropic", chat)
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n", encoding="utf-8")  # no Tavily key needed

    assert _run(["eval", "plates", "--live"], "no\n", monkeypatch) == cli.EXIT_REFUSED
    out = capsys.readouterr().out
    model = config.MODEL_FOR["cheap"]["read_plate"]
    typical = price_usage(config.PRICES_PER_MTOK[model], {"input_tokens": config.READ_PLATE_TOKENS_TYPICAL["input"],
                                                         "output_tokens": config.READ_PLATE_TOKENS_TYPICAL["output"]})
    worst = 0.0
    for name in ("plate-clear.jpg", "plate-blurry.jpg"):
        _, est = plate_messages(str(config.REPO_ROOT / "demo-assets" / name))
        worst += price_usage(config.PRICES_PER_MTOK[model], {
            "input_tokens": estimate_input_tokens(est, model=model, images=1),
            "output_tokens": max_tokens_for("read_plate", "cheap")})
    assert "planned runs: 2" in out and "read_plate only, then the pause" in out
    assert f"estimated cost: typical {2 * typical:.4f} USD; worst case {worst:.4f} USD" in out
    assert "Tavily credits planned: typical 0, at most 0 on first passes, ceiling 0" in out
    assert built == []

    assert _run(["eval", "plates", "--live"], "proceed\n", monkeypatch) == cli.EXIT_OK
    out = capsys.readouterr().out
    records = {r["case"]: r for r in _records("plates")}
    assert set(records) == {"E1", "E2"} and built == ["read_plate", "read_plate"]
    assert live_env.tavily == []
    for case, record in records.items():
        assert record["status"] == "paused" and record["pauses"] == 0 and record["answers"] == []
        assert record["v1_checks"]["passed"] is True, record["v1_checks"]
        assert record["extraction"]["raw"] == PLATE_REPLIES[case]
        assert record["sc11"] == "pass" and 0 < record["cost_usd"] <= config.RUN_CAP_USD["cheap"]
    assert records["E2"]["extraction"]["final"]["model"] is None
    assert "plates: PASS" in out and "v1 case E1 " in out and "v1 case E2 " in out
    assert FAKE_ANTHROPIC.encode() not in _all_written_text(tmp_path)
