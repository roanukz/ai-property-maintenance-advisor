"""The Claude judge arm: Claude Haiku 5.5 asked Jev's question on the SC12a held out half.

Offline throughout: the chat model is a fake built here, no Anthropic or
TypeSafe client is ever constructed, the network is blocked and the repo's
.env is never read (conftest points config.ENV_FILE elsewhere; the tests
that need a key plant a fake one in a temporary .env). Tests that need the
real held out half copy data/eval/sc12a and the committed labels, item ids
and wordings into tmp_path first, so nothing here can write a real SC12a
file; they skip, with the reason, when data/ is absent (the mutation gate
names the repo's own data folder in ADVISOR_PHASE5_DATA_DIR, read only).
Each test names the planted bug that turns it red.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from agent import build_info, cli, config
from agent.declined import ModelDeclined
from agent.ledger import Ledger, call_prices, estimate_input_tokens, price_usage
from agent.prompts import SAFETY_STEP_NOUL
from agent.safety_eval import claude_judge as cj
from agent.safety_eval import harness, sc12c, stats

REAL_DATA = Path(os.environ.get("ADVISOR_PHASE5_DATA_DIR") or config.REPO_ROOT / "data")
REAL_SC12A = REAL_DATA / "eval" / "sc12a"
NO_DATA = "needs the SC12a held out half under data/eval/sc12a (gitignored, absent here)"
SAFETY_EVAL_DIR = config.REPO_ROOT / "agent" / "safety_eval"
COMMITTED_SC12A = ("labels.json", "item_ids.json", "wordings.json")
FAKE_KEY = "test-anthropic-key-not-real-0001"
QHASH = "34fb2f3b18541662"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tree_hashes(*roots: Path, skip: Path | None = None) -> dict[str, str]:
    out = {}
    for root in roots:
        paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in paths:
            if skip is not None and path.is_relative_to(skip):
                continue
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real SC12a files, copied, with a fresh ledger and every written path under tmp_path."""
    if not (REAL_SC12A / "items.json").is_file() or not (REAL_SC12A / "lock.json").is_file():
        pytest.skip(NO_DATA)
    eval_dir = tmp_path / "eval"
    shutil.copytree(REAL_SC12A, eval_dir / "sc12a", ignore=shutil.ignore_patterns(cj.SUITE))
    committed_sc12a = tmp_path / "committed_sc12a"
    committed_sc12a.mkdir()
    for name in COMMITTED_SC12A:
        shutil.copy2(SAFETY_EVAL_DIR / name, committed_sc12a / name)
    monkeypatch.setattr(config, "EVAL_DIR", eval_dir)
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger" / "ledger.sqlite")
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed_sc12a)
    monkeypatch.setattr(cj, "COMMITTED_DIR", tmp_path / "committed")
    # No SC12c labels unless a test copies them in: the real committed ones are never read in place.
    monkeypatch.setattr(sc12c, "COMMITTED_DIR", tmp_path / "committed_sc12c")
    return tmp_path


@pytest.fixture
def live(real: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fake Anthropic key in a planted .env (read by the project's env loader) and an existing ledger."""
    env_file = real / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    Ledger(config.LEDGER_PATH, create=True)
    return {}


class Fakes:
    """A scripted stand in for models.ChatAnthropic; it records what it was built with and sent.

    `script(index, item_id)` returns the answer (True or False) for one call, or raises."""

    def __init__(self, script: Callable[[int, str], bool] | None = None,
                 on_invoke: Callable[[str], None] | None = None) -> None:
        self.script = script or (lambda n, item_id: int(item_id[:2], 16) % 3 == 0)
        self.on_invoke = on_invoke
        self.kwargs: list[dict[str, Any]] = []
        self.structured: list[tuple[Any, dict[str, Any]]] = []
        self.sent: list[tuple[str, list[Any]]] = []

    def factory(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        return _Chat(self)


class _Chat:
    def __init__(self, fakes: Fakes) -> None:
        self.fakes = fakes

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        self.fakes.structured.append((schema, kwargs))
        return _Runnable(self.fakes, schema)


def _reply(**meta: Any) -> AIMessage:
    return AIMessage(content="", usage_metadata={"input_tokens": 600, "output_tokens": 40, "total_tokens": 640},
                     response_metadata={"model_name": config.HAIKU, **meta})


class _Runnable:
    def __init__(self, fakes: Fakes, schema: Any) -> None:
        self.fakes, self.schema = fakes, schema

    def invoke(self, messages: list[Any]) -> dict[str, Any]:
        step = json.loads(messages[1].content.split("\n\nStatement:\n")[0].removeprefix("Step:\n"))
        item_id = ITEM_BY_STEP[(step["appliance"], step["step"], step["detail"])]
        if self.fakes.on_invoke is not None:
            self.fakes.on_invoke(item_id)
        index = len(self.fakes.sent)
        self.fakes.sent.append((item_id, messages))
        flag = self.fakes.script(index, item_id)
        return {"raw": _reply(stop_reason="end_turn"), "parsed": self.schema(involves_the_hazard=flag),
                "parsing_error": None}


ITEM_BY_STEP: dict[tuple[str, str, str], str] = {}


def _index_items() -> list[dict[str, Any]]:
    items = harness.load_items()["items"]
    ITEM_BY_STEP.clear()
    ITEM_BY_STEP.update({(i["appliance"], i["step"], i["detail"]): i["id"] for i in items})
    return items


def _run(fakes: Fakes, environ: dict[str, str], typed: str = "proceed\n") -> int:
    _index_items()
    return cj.run(environ=environ, stdin=io.StringIO(typed), chat_factory=fakes.factory)


def _replies() -> list[dict[str, Any]]:
    path = cj.replies_path()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def _rows(**where: Any) -> list[dict[str, Any]]:
    rows = Ledger(config.LEDGER_PATH).rows()
    return [r for r in rows if all(r[k] == v for k, v in where.items())]


def _held_ids() -> list[str]:
    return sorted(i["id"] for i in harness.load_items()["items"] if i["half"] == "heldout")


# ---------------------------------------------------------------------------
# What Claude is sent
# ---------------------------------------------------------------------------


def test_payload_is_the_jev_state_and_the_exact_jev_question(real: Path) -> None:
    """Each held out item becomes one system message and one user message holding only its appliance, step
    and detail and Jev's question, word for word; any other key on the item never reaches the payload.

    Mutation: claude_judge_payload_leaks_the_half (the step JSON carries the item's half)."""
    question = cj.jev_question()
    assert question == {"instructions": SAFETY_STEP_NOUL["instructions"], "criteria": SAFETY_STEP_NOUL["criteria"]}
    held = [i for i in harness.load_items()["items"] if i["half"] == "heldout"]
    labels = harness.load_labels()
    assert len(held) == 175
    for item in held:
        messages = cj.messages_for(item, question)
        assert [type(m).__name__ for m in messages] == ["SystemMessage", "HumanMessage"]
        assert messages[0].content == cj.SYSTEM_PROMPT
        text = messages[1].content
        head, statement = text.split("\n\nStatement:\n")
        state = json.loads(head.removeprefix("Step:\n"))
        assert list(state) == ["appliance", "step", "detail"]
        assert state == {"appliance": item["appliance"], "step": item["step"], "detail": item["detail"]}
        assert json.loads(statement) == {"instructions": SAFETY_STEP_NOUL["instructions"],
                                         "criteria": SAFETY_STEP_NOUL["criteria"]}
        assert text == (f"Step:\n{json.dumps(state, ensure_ascii=False)}\n\nStatement:\n"
                        f"{cj.statement_json(SAFETY_STEP_NOUL)}")
        # The labels, Jev's answer and the word rules never reach it, whatever the item carries.
        loaded = {**item, "label": labels[item["id"]]["label"], "hazard": labels[item["id"]]["hazard"],
                  "votes": labels[item["id"]]["votes"], "noul": 0.97, "word_published": True, "word_tuned": True}
        assert [m.content for m in cj.messages_for(loaded, question)] == [m.content for m in messages]
        for leaked in ('"label"', '"hazard"', '"votes"', '"noul"', '"half"', '"id"', item["id"]):
            assert leaked not in text, leaked


def test_question_and_state_are_what_the_sc12a_held_out_pass_sent_jev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """A synthetic set is locked and its held out half asked through `advisor eval sc12a --live --heldout`
    with a fake Jev judge; what that pass sent Jev for each item (its Noul and its state) is exactly what
    this arm sends Claude.

    Mutation: claude_judge_state_reads_the_reader_keys (the state is the reader packet's keys, id included)."""
    from agent import safety_judge
    from agent.tests.test_safety_eval import _write_set

    monkeypatch.setattr(config, "EVAL_DIR", tmp_path / "eval")
    committed = tmp_path / "committed"
    committed.mkdir()
    shutil.copy2(SAFETY_EVAL_DIR / "wordings.json", committed / "wordings.json")
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed)
    monkeypatch.setattr(config, "SAFETY_LAYERS", ("word",))
    monkeypatch.setattr(config, "JEV_THRESHOLD", None)
    items = _write_set(replies=False)
    qhash = harness.question_hash_of(harness.pick_wording(None))
    assert qhash == QHASH
    for item in items:
        if item["half"] == "tune":
            harness.append_reply(qhash, {"item_id": item["id"], "step_sha256": item["id"], "question_hash": qhash,
                                         "model": config.JEV_MODEL, "noul": 0.9, "error": None})
    harness.write_lock()
    sent: dict[str, dict[str, Any]] = {}
    nouls: list[dict[str, Any]] = []

    class FakeJudge:
        question_hash = qhash

        def ask(self, step_sha256: str, state: dict) -> safety_judge.JudgeReply:
            sent[step_sha256] = dict(state)
            return safety_judge.JudgeReply(step_sha256=step_sha256, noul=0.5, model=config.JEV_MODEL,
                                           request_id=None, input_tokens=400, output_tokens=0, question_hash=qhash)

    def fake_build(**kwargs: Any) -> FakeJudge:
        nouls.append({"instructions": kwargs["instructions"], "criteria": kwargs["criteria"]})
        return FakeJudge()

    monkeypatch.setattr(safety_judge, "build_live_judge", fake_build)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{config.TYPESAFE_KEY_NAME}=test-typesafe-key-not-real\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger" / "ledger.sqlite")
    Ledger(config.LEDGER_PATH, create=True)
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    assert cli.main(["eval", "sc12a", "--live", "--heldout"]) == cli.EXIT_OK
    held = [i for i in items if i["half"] == "heldout"]
    assert sorted(sent) == sorted(i["id"] for i in held) and len(nouls) == 1
    question = cj.jev_question()
    assert nouls[0] == question
    assert json.loads(cj.statement_json(question)) == nouls[0]
    for item in held:
        assert cj.jev_state(item) == sent[item["id"]]
        head = cj.user_text(item, question).split("\n\nStatement:\n")[0]
        assert json.loads(head.removeprefix("Step:\n")) == sent[item["id"]]


def test_the_tune_half_is_never_sent(real: Path, live: dict[str, str]) -> None:
    """The run asks exactly the 175 held out items, once each, in item id order, with exactly the payload
    messages_for builds; no tune item's step reaches the model.

    Mutations: claude_judge_items_include_tune (every item is asked), claude_judge_items_unsorted (the
    items keep the file's order)."""
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_OK
    items = harness.load_items()["items"]
    held = _held_ids()
    assert [item_id for item_id, _ in fakes.sent] == held
    by_id = {i["id"]: i for i in items}
    question = cj.jev_question()
    for item_id, messages in fakes.sent:
        assert [m.content for m in messages] == [m.content for m in cj.messages_for(by_id[item_id], question)]
    sent_text = "\n".join(m.content for _, messages in fakes.sent for m in messages)
    tune_steps = {i["step"] for i in items if i["half"] == "tune"} - {by_id[k]["step"] for k in held}
    assert tune_steps and not [s for s in tune_steps if json.dumps(s, ensure_ascii=False)[1:-1] in sent_text]
    assert [r["item_id"] for r in _replies()] == held


# ---------------------------------------------------------------------------
# The frozen settings
# ---------------------------------------------------------------------------


def test_settings_are_frozen_and_their_hash_moves_with_every_setting(real: Path) -> None:
    """SETTINGS is one read only block of immutable values; the hash covers every key; the command line
    takes no option that could change a setting; the run folder is named by the hash.

    Mutation: claude_judge_hash_skips_the_system_prompt (the hash leaves one setting out)."""
    assert isinstance(cj.SETTINGS, MappingProxyType)
    with pytest.raises(TypeError):
        cj.SETTINGS["max_tokens"] = 2048  # type: ignore[index]
    for key, value in cj.SETTINGS.items():
        assert isinstance(value, str | int | float | tuple), key
        assert not isinstance(value, tuple) or all(isinstance(v, str) for v in value), key
    digest = cj.settings_hash()
    assert len(digest) == 16 and int(digest, 16) >= 0
    assert cj.settings_hash(dict(cj.SETTINGS)) == digest
    for key, value in cj.SETTINGS.items():
        changed = {**cj.SETTINGS, key: (value + 1) if isinstance(value, int | float) else (
            (*value, "x") if isinstance(value, tuple) else value + " ")}
        assert cj.settings_hash(changed) != digest, key
    assert cj.settings_hash({**cj.SETTINGS, "extra": "x"}) != digest
    assert cj.REGISTERED_SETTINGS_HASH == digest and cj.SETTINGS["supersedes"] == ()
    assert cj.run_dir().name == digest and cj.run_dir().parent == config.EVAL_DIR / "sc12a" / "claude_judge"
    for argv in (["run", "--max-tokens", "5"], ["plan", "--effort", "high"], ["run", "--model", "x"],
                 ["score", "--threshold", "0.4"], ["run", "--force"]):
        with pytest.raises(SystemExit):
            cj.parser().parse_args(argv)
    assert cj.SETTINGS["question_hash"] == QHASH and cj.SETTINGS["wording"] == 3
    assert cj.SETTINGS["half"] == "heldout" and cj.SETTINGS["max_tokens"] == 1024
    assert cj.SETTINGS["effort"] == "low" and cj.SETTINGS["model"] == config.HAIKU
    assert cj.SETTINGS["run_cap_usd"] == config.RUN_CAP_USD["cheap"]


def test_model_is_built_as_the_cheap_classifier_and_refused_when_config_moves(
        real: Path, live: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """One model, built from models.live_model_kwargs("classifier", "cheap") exactly (model, effort low,
    max_tokens 1024, no client retries, no thinking, sampling or fallbacks), asked for json_schema
    structured output with one boolean field. A config that moved since the pre-registration is refused
    before any key is read.

    Mutations: claude_judge_sends_temperature (the model is built with a sampling setting),
    claude_judge_settings_check_skipped (a moved config is not refused)."""
    from agent import models

    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_OK
    assert fakes.kwargs == [models.live_model_kwargs("classifier", "cheap")] == [cj.expected_model_kwargs()]
    assert fakes.kwargs[0]["reasoning_effort"] == "low" and fakes.kwargs[0]["max_tokens"] == 1024
    assert fakes.kwargs[0]["model"] == config.HAIKU and fakes.kwargs[0]["max_retries"] == 0
    assert not set(fakes.kwargs[0]) & {"thinking", "budget_tokens", "fallbacks", "temperature", "top_p", "top_k"}
    (schema, kwargs), = fakes.structured
    assert kwargs == {"method": "json_schema", "include_raw": True}
    assert schema.model_json_schema()["properties"] == {
        "involves_the_hazard": {"description": cj.OUTPUT_DESCRIPTION, "title": "Involves The Hazard",
                                "type": "boolean"}}
    assert schema.model_json_schema()["required"] == ["involves_the_hazard"]
    assert models.ChatAnthropic is models.DeclineAwareChatAnthropic
    monkeypatch.setattr(config, "HAIKU_EFFORT", {**config.HAIKU_EFFORT, "classifier": "medium"})
    capsys.readouterr()
    assert cj.main(["plan"]) == cli.EXIT_REFUSED
    assert "moved since this arm was pre-registered" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def test_plan_prints_counts_and_cost_with_no_key_and_no_network(real: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Offline, with no key anywhere: the item count, every setting and its hash, the planned calls, the
    typical and worst case cost from config prices and the ledger's own estimate of the real prompts, the run
    cap and the build spend read from the ledger without writing it. Nothing is written.

    Mutation: claude_judge_plan_forgets_the_retry (at most 175 calls, not 350)."""
    assert "ANTHROPIC_API_KEY" not in os.environ
    ledger = Ledger(config.LEDGER_PATH, create=True)
    reservation = ledger.reserve("earlier", mode="cheap", node="synthesize", model=config.HAIKU,
                                 input_tokens_est=10_000, max_tokens=1_000)
    spent = ledger.charge(reservation, {"input_tokens": 12_000, "output_tokens": 900})
    # A sqlite reader of a WAL ledger may create the shared memory index beside it; the ledger itself and
    # every other file stay byte for byte, and no write lands in the write ahead log.
    sidecars = ("-wal", "-shm")
    before = {k: v for k, v in _tree_hashes(real).items() if not k.endswith(sidecars)}
    assert cj.main(["plan"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert {k: v for k, v in _tree_hashes(real).items() if not k.endswith(sidecars)} == before
    wal = Path(f"{config.LEDGER_PATH}-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    question = cj.jev_question()
    held = [i for i in harness.load_items()["items"] if i["half"] == "heldout"]
    from langchain_core.messages import HumanMessage

    schema = HumanMessage(json.dumps(cj.step_judgment_schema().model_json_schema()))
    est = [estimate_input_tokens([*cj.messages_for(i, question), schema], model=config.HAIKU) for i in held]
    typical_out = config.typical_tokens(config.HAIKU, config.CLASSIFIER_TOKENS_TYPICAL["output"])
    typical = sum(price_usage(config.HAIKU, {"input_tokens": e, "output_tokens": typical_out}) for e in est)
    reserved = 2 * sum(price_usage(call_prices("cheap", "anthropic", config.HAIKU, e),
                                   {"input_tokens": e, "output_tokens": 1024}) for e in est)
    worst = min(reserved, config.RUN_CAP_USD["cheap"])
    assert "items: 175 held out items" in out
    assert "planned calls: typical 175, at most 350" in out
    assert f"input tokens by the ledger's estimator: {sum(est)} for the 175 prompts" in out
    assert f"estimated cost: typical {typical:.4f} USD" in out
    assert f"worst case {worst:.4f} USD" in out and f"retried once: {reserved:.4f} USD" in out
    assert f"run cap (cheap): {config.RUN_CAP_USD['cheap']:.4f} USD" in out
    assert f"build spend so far (live runs, read only): {spent:.4f} USD of {config.BUILD_CAP_USD:.4f} USD" in out
    assert (f"settings (pre-registered, frozen; hash {cj.settings_hash()}, registered "
            f"{cj.REGISTERED_SETTINGS_HASH})") in out
    assert "a new run asks all 175" in out and "run refuses" not in out
    for key in cj.SETTINGS:
        assert f"\n  {key}: " in out, key
    assert "ANTHROPIC_API_KEY" not in os.environ


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_refuses_without_the_key_or_a_typed_proceed(real: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    """With no key the run is refused before the plan; with the key, the plan prints and anything but
    exactly "proceed" sends nothing, builds no model and writes no ledger row and no reply.

    Mutation: claude_judge_skips_proceed (the run goes on without the typed "proceed")."""
    Ledger(config.LEDGER_PATH, create=True)
    fakes = Fakes()
    assert _run(fakes, {}) == cli.EXIT_REFUSED
    assert "missing ANTHROPIC_API_KEY" in capsys.readouterr().err
    env_file = real / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    for typed in ("yes\n", "Proceed\n", "proceed now\n", ""):
        assert _run(fakes, {}, typed) == cli.EXIT_REFUSED
        out = capsys.readouterr().out
        assert "planned calls: typical 175, at most 350" in out and "canceled" in out
        assert FAKE_KEY not in out
    assert fakes.kwargs == [] and fakes.sent == []
    assert Ledger(config.LEDGER_PATH).rows() == []
    assert not (config.EVAL_DIR / "sc12a" / "claude_judge").exists()


def test_run_records_each_reply_with_its_fields_and_prints_progress(real: Path, live: dict[str, str],
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """One line per item: item id, flag, declined, category, usage, latency, the reply's model name and the
    charge, and no step text; the run record says completed; progress prints every 25 items; every charge is
    in one ledger run named claude-judge-<12 hex> under provider anthropic and node claude_judge.

    Mutation: claude_judge_progress_every_item (progress prints after every item)."""
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_OK
    out = capsys.readouterr().out
    progress = [line for line in out.splitlines() if line.startswith("  ") and " of 175 items; run spend" in line]
    assert [line.split()[0] for line in progress] == ["25", "50", "75", "100", "125", "150", "175"]
    replies = _replies()
    assert len(replies) == 175
    record = harness.read_json(cj.run_record_path())
    run_id = record["run_id"]
    assert run_id.startswith("claude-judge-") and len(run_id) == len("claude-judge-") + 12
    int(run_id.removeprefix("claude-judge-"), 16)
    assert record["status"] == "completed" and record["asked"] == 175 and record["stop_reason"] is None
    assert record["settings_hash"] == cj.settings_hash() and record["build_id"] == build_info.build_id()
    texts = {t for i in harness.load_items()["items"] for t in (i["step"], i["detail"]) if t}
    raw = cj.replies_path().read_text(encoding="utf-8") + cj.run_record_path().read_text(encoding="utf-8")
    assert not [t for t in texts if json.dumps(t, ensure_ascii=False)[1:-1] in raw]
    for reply in replies:
        assert {"item_id", "flag", "declined", "category", "usage", "latency_s", "model", "usd"} <= set(reply)
        assert reply["model"] == config.HAIKU and reply["usage"]["input_tokens"] == 600
        assert reply["answered"] and not reply["declined"] and reply["attempts"] == 1
        assert reply["flag"] == (int(reply["item_id"][:2], 16) % 3 == 0)
        assert reply["latency_s"] >= 0
    charges = _rows(run_id=run_id, kind="charge")
    assert len(charges) == 175 and {(r["provider"], r["node"], r["model"], r["mode"]) for r in charges} == {
        ("anthropic", "claude_judge", config.HAIKU, "cheap")}
    assert record["spent_usd"] == pytest.approx(sum(r["usd"] for r in charges))
    assert {r["run_id"] for r in Ledger(config.LEDGER_PATH).rows()} == {run_id}


def test_run_refuses_a_second_run(real: Path, live: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    """Once the run completed, run refuses before any key, plan or call; a reply file with no run record is
    refused the same way. (A stopped run is resumed instead: see the resume test.)

    Mutation: claude_judge_second_run_allowed (the run's state is not checked before the plan)."""
    assert _run(Fakes(), live) == cli.EXIT_OK
    rows = len(Ledger(config.LEDGER_PATH).rows())
    replies = cj.replies_path().read_bytes()
    capsys.readouterr()
    again = Fakes()
    assert _run(again, live) == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "already exists (status completed" in captured.err and "planned calls" not in captured.out
    cj.run_record_path().unlink()
    assert _run(again, live) == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "exists with no run record" in captured.err and "planned calls" not in captured.out
    assert again.kwargs == [] and again.sent == []
    assert len(Ledger(config.LEDGER_PATH).rows()) == rows and cj.replies_path().read_bytes() == replies


def test_run_stops_at_the_cap(real: Path, live: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A call the run cap cannot cover is not sent: the run stops, records why, and sends nothing after.

    Mutation: claude_judge_cap_refusal_skips_the_item (the run goes on to the next item after a refusal)."""
    monkeypatch.setattr(config, "RUN_CAP_USD", {**config.RUN_CAP_USD, "cheap": 0.001})
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_LIVE_ERROR
    record = harness.read_json(cj.run_record_path())
    assert record["status"] == "stopped" and record["stop_reason"] == "run_cap"
    sent = len(fakes.sent)
    assert 0 < sent < 175 and record["asked"] == sent == len(_replies())
    run_id = record["run_id"]
    assert len(_rows(run_id=run_id, kind="charge")) == sent
    assert len(_rows(run_id=run_id, kind="reserve")) == sent
    stops = _rows(run_id=run_id, kind="stop")
    assert len(stops) == 1 and stops[0]["note"].startswith("run_cap")
    assert Ledger(config.LEDGER_PATH).run_total(run_id) <= 0.001


def _open_reservations(run_prefix: str = "claude-judge-") -> list[dict[str, Any]]:
    rows = [r for r in Ledger(config.LEDGER_PATH).rows() if r["run_id"].startswith(run_prefix)]
    settled = {int(r["note"].split()[1].rstrip(";")) for r in rows if r["kind"] == "release"}
    return [r for r in rows if r["kind"] == "reserve" and r["id"] not in settled]


def test_run_reserves_before_every_call(real: Path, live: dict[str, str]) -> None:
    """At every call, retries included, exactly one reservation is open for it: node claude_judge, the model,
    the item's own input estimate and the full 1024 output tokens.

    Mutation: claude_judge_reserves_once_per_item (a retry is sent on the first attempt's settled hold)."""
    question = cj.jev_question()
    by_id = {i["id"]: i for i in harness.load_items()["items"]}
    seen: list[dict[str, Any]] = []

    def check(item_id: str) -> None:
        open_rows = _open_reservations()
        assert len(open_rows) == 1, item_id
        row = open_rows[0]
        assert (row["node"], row["model"], row["provider"], row["output_tokens"]) == (
            "claude_judge", config.HAIKU, "anthropic", 1024)
        assert row["input_tokens"] == cj.estimate_tokens(by_id[item_id], question)
        seen.append(row)

    def script(n: int, item_id: str) -> bool:
        if n in (3, 10):
            raise TimeoutError("connection lost")
        return False

    fakes = Fakes(script, on_invoke=check)
    assert _run(fakes, live) == cli.EXIT_OK
    assert len(seen) == len(fakes.sent) == 177 and len({r["id"] for r in seen}) == 177


def test_a_decline_counts_as_not_flagged_and_keeps_its_category(real: Path, live: dict[str, str]) -> None:
    """A stop_reason refusal is charged, never retried, recorded as declined with its category (or none) and
    not flagged, and the run goes on.

    Mutation: claude_judge_decline_category_dropped (the category is not recorded)."""
    held = _held_ids()

    def script(n: int, item_id: str) -> bool:
        if item_id == held[0]:
            raise ModelDeclined(model=config.HAIKU, category="cyber", ai_message=_reply(stop_reason="refusal"))
        if item_id == held[1]:
            raise ModelDeclined(model=config.HAIKU, category=None, ai_message=_reply(stop_reason="refusal"))
        return True

    fakes = Fakes(script)
    assert _run(fakes, live) == cli.EXIT_OK
    replies = {r["item_id"]: r for r in _replies()}
    assert len(replies) == 175 and [k for k, _ in fakes.sent].count(held[0]) == 1
    first, second = replies[held[0]], replies[held[1]]
    assert (first["declined"], first["flag"], first["category"], first["answered"], first["attempts"]) == (
        True, False, "cyber", False, 1)
    assert (second["declined"], second["flag"], second["category"]) == (True, False, None)
    assert first["stop_reason"] == "refusal" and first["usage"]["output_tokens"] == 40 and first["usd"] > 0
    assert all(replies[k]["flag"] and not replies[k]["declined"] for k in held[2:])
    record = harness.read_json(cj.run_record_path())
    assert record["status"] == "completed"
    assert len(_rows(run_id=record["run_id"], kind="charge")) == 175


def test_only_a_transport_error_is_retried_and_only_once(real: Path, live: dict[str, str]) -> None:
    """A transport error is charged at its reservation and retried once; a second one leaves the item
    unflagged with the error recorded; a "no" answer is never asked again; any other error is not retried
    and stops the run.

    Mutation: claude_judge_retries_any_error (every exception is retried)."""
    held = _held_ids()
    attempts: dict[str, int] = {}

    def script(n: int, item_id: str) -> bool:
        attempts[item_id] = attempts.get(item_id, 0) + 1
        if item_id == held[0] and attempts[item_id] == 1:
            raise TimeoutError("connection lost")
        if item_id == held[1]:
            raise TimeoutError("connection lost")
        if item_id == held[3]:
            raise ValueError("400 invalid request")
        return item_id == held[0]

    fakes = Fakes(script)
    assert _run(fakes, live) == cli.EXIT_LIVE_ERROR
    replies = {r["item_id"]: r for r in _replies()}
    assert attempts == {held[0]: 2, held[1]: 2, held[2]: 1, held[3]: 1}
    assert (replies[held[0]]["attempts"], replies[held[0]]["flag"], replies[held[0]]["error"]) == (2, True, None)
    assert (replies[held[1]]["attempts"], replies[held[1]]["flag"], replies[held[1]]["error"]) == (
        2, False, "transport_error")
    assert (replies[held[2]]["attempts"], replies[held[2]]["flag"]) == (1, False)
    assert (replies[held[3]]["attempts"], replies[held[3]]["error"]) == (1, "ValueError")
    record = harness.read_json(cj.run_record_path())
    assert record["status"] == "stopped" and record["stop_reason"] == "api_error" and record["asked"] == 4
    charges = _rows(run_id=record["run_id"], kind="charge")
    assert sum(r["estimated"] for r in charges) == 3 and len(charges) == 5
    assert len(_rows(run_id=record["run_id"], kind="release")) == 6


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _write_claude_run(flags: dict[str, bool], *, extra: dict[str, dict[str, Any]] | None = None,
                      run_id: str = "claude-judge-0123456789ab", spent: float = 0.0,
                      status: str = "completed") -> None:
    for item_id, flag in flags.items():
        entry = {"item_id": item_id, "run_id": run_id, "settings_hash": cj.settings_hash(), "attempts": 1,
                 "flag": flag, "answered": True, "declined": False, "category": None, "error": None,
                 "usage": {"input_tokens": 600, "output_tokens": 40}, "usd": 0.0, "model": config.HAIKU,
                 "stop_reason": "end_turn", "latency_s": 1.0, **(extra or {}).get(item_id, {})}
        cj._append(cj.replies_path(), entry)
    harness.write_json(cj.run_record_path(), {"run_id": run_id, "settings_hash": cj.settings_hash(),
                                              "status": status, "items": len(flags), "asked": len(flags),
                                              "spent_usd": spent})


def test_score_reproduces_the_published_jev_and_word_list_numbers(real: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Against the AI majority, with the code sc12c scores with: Jev 30 of 31 at 0.732, the published list
    20 of 31 at 0.714, the tuned list 22 of 31 at 0.647; a changed Jev reply makes score refuse.

    Mutation: claude_judge_score_skips_the_reproduction_check."""
    capsys.readouterr()
    assert cj.main(["score"]) == cli.EXIT_REFUSED
    assert "no run under settings" in capsys.readouterr().err
    labels = harness.load_labels()
    held = _held_ids()
    _write_claude_run({k: labels[k]["positive"] for k in held})
    result = cj.score()
    arms = result["arms"]
    expected = {cj.JEV_ARM: (30, 31, 41, 0.968, 0.732), cj.PUBLISHED_ARM: (20, 31, 28, 0.645, 0.714),
                cj.TUNED_ARM: (22, 31, 34, 0.710, 0.647), cj.CLAUDE_ARM: (31, 31, 31, 1.0, 1.0)}
    for name, (tp, positives, flagged, recall, precision) in expected.items():
        arm = arms[name]
        assert (arm["tp"], arm["positives"], arm["flagged"]) == (tp, positives, flagged), name
        assert round(arm["recall"], 3) == recall and round(arm["precision"], 3) == precision, name
    assert [round(x, 3) for x in arms[cj.JEV_ARM]["recall_wilson95"]] == [0.838, 0.994]
    assert [round(x, 3) for x in arms[cj.JEV_ARM]["precision_wilson95"]] == [0.581, 0.843]
    assert [round(x, 3) for x in arms[cj.PUBLISHED_ARM]["recall_wilson95"]] == [0.469, 0.789]
    assert [round(x, 3) for x in arms[cj.TUNED_ARM]["precision_wilson95"]] == [0.479, 0.785]
    assert result["items"] == 175 and result["asked"] == 175 and result["jev_threshold"] == 0.51
    assert "three Claude model readers" in result["reference_labels"]
    t = result["agreement_with_jev"]["table"]
    assert t["claude_yes_jev_yes"] == 30 and t["claude_no_jev_yes"] == 11 and t["claude_yes_jev_no"] == 1
    assert result["cost"]["jev"]["ledger_runs"] == ["sc12a-20260930T155813Z"]
    # One held out positive's Jev reply changed after the fact: the reference no longer reproduces.
    positive = next(k for k in held if labels[k]["positive"] and harness.load_replies(QHASH)[k]["noul"] >= 0.51)
    harness.append_reply(QHASH, {"item_id": positive, "noul": 0.01, "question_hash": QHASH,
                                 "model": config.JEV_MODEL, "error": None})
    with pytest.raises(harness.EvalRefusal, match="do not reproduce the published"):
        cj.score()


# A synthetic held out half: (AI majority positive, Jev's probability, Claude's reply, latency in seconds).
SYNTHETIC = {
    "A": (True, 0.90, {"flag": True}, 1.0),
    "B": (True, 0.80, {"flag": False}, 2.0),
    "C": (True, 0.20, {"flag": True}, 3.0),
    "D": (True, 0.30, {"flag": False, "answered": False, "declined": True, "category": "cyber"}, 4.0),
    "E": (False, 0.70, {"flag": False}, 5.0),
    "F": (False, 0.10, {"flag": True}, 6.0),
    "G": (False, 0.10, {"flag": False, "answered": False, "error": "unparsed"}, 7.0),
    "H": (False, 0.10, {"flag": True}, 10.0),
}


def _sid(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


@pytest.fixture
def synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Eight held out items and two tune items, their labels, Jev's replies, a lock and the Claude run."""
    eval_dir = tmp_path / "eval"
    items = [{"id": _sid(n), "appliance": "hot tub", "step": f"Synthetic step {n}", "detail": "", "half": "heldout",
              "pool": "D", "writer_flag": None} for n in SYNTHETIC]
    items += [{"id": _sid(n), "appliance": "hot tub", "step": f"Tune step {n}", "detail": "", "half": "tune",
               "pool": "D", "writer_flag": None} for n in ("T1", "T2")]
    (eval_dir / "sc12a").mkdir(parents=True)
    harness.write_json(eval_dir / "sc12a" / "items.json", {"items": items})
    committed = tmp_path / "committed_sc12a"
    labels = {_sid(n): {"label": "yes" if pos else "no", "positive": pos, "hazard": "electrical" if pos else "none"}
              for n, (pos, *_rest) in SYNTHETIC.items()}
    harness.write_json(committed / "labels.json", {"labels": labels})
    monkeypatch.setattr(config, "EVAL_DIR", eval_dir)
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed)
    monkeypatch.setattr(cj, "COMMITTED_DIR", tmp_path / "committed")
    monkeypatch.setattr(sc12c, "COMMITTED_DIR", tmp_path / "committed_sc12c")
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger" / "ledger.sqlite")
    for n, (_, noul, _, _) in SYNTHETIC.items():
        harness.append_reply(QHASH, {"item_id": _sid(n), "noul": noul, "question_hash": QHASH, "run_id": "sc12a-x",
                                     "input_tokens": 500, "model": config.JEV_MODEL, "error": None})
    lock = eval_dir / "sc12a" / "lock.json"
    harness.write_json(lock, {"question_hash": QHASH, "thresholds": {"jev": {"rule": "full_recall",
                                                                             "threshold": 0.51}}})
    monkeypatch.setattr(cj, "LOCK_FILE_SHA256", hashlib.sha256(lock.read_bytes()).hexdigest())
    monkeypatch.setattr(cj, "PUBLISHED_AI_ARMS", {cj.JEV_ARM: (2, 3, 4), cj.PUBLISHED_ARM: (0, 0, 4),
                                                  cj.TUNED_ARM: (0, 0, 4)})
    _write_claude_run({_sid(n): reply["flag"] for n, (_, _, reply, _) in SYNTHETIC.items()},
                      extra={_sid(n): {**reply, "latency_s": latency, "usd": 0.0001}
                             for n, (_, _, reply, latency) in SYNTHETIC.items()}, spent=0.0008)
    return tmp_path


def test_score_computes_the_claude_arm_agreement_and_costs_on_a_synthetic_set(
        synthetic: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Hand counts. Positives A to D; Jev (at 0.51) flags A, B, E; Claude flags A, C, F, H, declined D and
    left G unparsed. Claude: recall 2 of 4, precision 2 of 4. Claude against Jev on all 8: both yes 1 (A),
    Claude only 3 (C, F, H), Jev only 2 (B, E), neither 2 (D, G); agreement 3 of 8; chance agreement
    (4/8)(3/8) + (4/8)(5/8) = 1/2, so kappa (3/8 - 1/2) / (1/2) = -1/4. Caught by Claude only: C; by Jev
    only: B. Latency median (4 + 5) / 2 = 4.5, 90th percentile by nearest rank the 8th of 8, 10.0.

    Mutations: claude_judge_table_cells_swapped, claude_judge_caught_lists_swapped,
    claude_judge_p90_rounds_down."""
    Ledger(config.LEDGER_PATH, create=True)
    with sqlite3.connect(config.LEDGER_PATH) as conn:
        for run_id, usd in (("sc12a-x", 0.000021), ("claude-judge-0123456789ab", 0.0001)):
            conn.executemany("INSERT INTO entries (ts, run_id, mode, node, kind, usd) VALUES ('t', ?, 'cheap', 'x', "
                             "'charge', ?)", [(run_id, usd)] * 8)
    conn.close()
    result = cj.score()
    claude = result["arms"][cj.CLAUDE_ARM]
    assert (claude["tp"], claude["flagged"], claude["positives"]) == (2, 4, 4)
    assert claude["recall"] == 0.5 and claude["precision"] == 0.5
    assert claude["recall_wilson95"] == stats.wilson(2, 4) and claude["precision_wilson95"] == stats.wilson(2, 4)
    assert [round(x, 3) for x in claude["recall_wilson95"]] == [0.150, 0.850]
    jev = result["arms"][cj.JEV_ARM]
    assert (jev["tp"], jev["flagged"], jev["positives"]) == (2, 3, 4)
    a = result["agreement_with_jev"]
    assert a["table"] == {"claude_yes_jev_yes": 1, "claude_yes_jev_no": 3, "claude_no_jev_yes": 2,
                          "claude_no_jev_no": 2}
    assert a["agree"] == 3 and a["percent"] == 37.5 and Fraction(a["cohen_kappa"]).limit_denominator(100) == Fraction(-1, 4)
    assert result["caught_by_claude_only"] == [_sid("C")] and result["caught_by_jev_only"] == [_sid("B")]
    assert result["declines"] == {"count": 1, "ids": [_sid("D")], "categories": {"cyber": 1}}
    assert result["unanswered"] == {"count": 1, "ids": [_sid("G")], "kinds": {"unparsed": 1}}
    assert result["latency_s"]["median"] == 4.5 and result["latency_s"]["p90"] == 10.0
    cost = result["cost"]
    assert cost["claude_total_usd"] == pytest.approx(0.0008) and cost["claude_per_item_usd"] == pytest.approx(0.0001)
    assert cost["claude_basis"] == "ledger charges of the run"
    assert cost["jev"]["per_item_usd"] == pytest.approx(0.000021) and cost["jev"]["charges"] == 8
    assert result["models_seen"] == [config.HAIKU] and result["other_runs"] == []
    written = json.loads(cj.scores_path().read_text(encoding="utf-8"))
    assert written["agreement_with_jev"] == a and written["arms"][cj.CLAUDE_ARM]["tp"] == 2
    lines = cj.format_score(result)
    assert any("three Claude model readers" in line for line in lines)
    assert any("Cohen's kappa -0.250" in line for line in lines)
    # Without the ledger the Jev cost comes from its recorded tokens, and says so.
    config.LEDGER_PATH.unlink()
    fallback = cj.score()["cost"]
    assert fallback["claude_basis"] == "the run record" and fallback["claude_total_usd"] == pytest.approx(0.0008)
    price = config.PRICES_PER_MTOK[config.JEV_MODEL]["input"]
    assert fallback["jev"]["per_item_usd"] == pytest.approx(500 * price / 1_000_000)
    assert "recorded input tokens (4000)" in fallback["jev"]["basis"]


def test_score_writes_no_step_text(real: Path) -> None:
    """The committed scores hold ids, counts and numbers only, even where the arms disagree.

    Mutation: claude_judge_scores_carry_the_step (the caught lists carry the step)."""
    labels = harness.load_labels()
    held = _held_ids()
    jev = harness.load_replies(QHASH)
    # Claude catches every positive Jev missed and misses three Jev caught.
    flags = {k: labels[k]["positive"] for k in held}
    caught = [k for k in held if labels[k]["positive"] and jev[k]["noul"] >= 0.51][:3]
    flags.update({k: False for k in caught})
    _write_claude_run(flags)
    result = cj.score()
    assert result["caught_by_jev_only"] == sorted(caught) and len(result["caught_by_claude_only"]) == 1
    raw = cj.scores_path().read_text(encoding="utf-8")
    texts = {t for i in harness.load_items()["items"] for t in (i.get("step"), i.get("detail")) if t}
    assert len(texts) >= 175
    assert not [t for t in texts if t in raw or json.dumps(t, ensure_ascii=False)[1:-1] in raw]

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    assert not keys(json.loads(raw)) & {"step", "detail", "appliance", "text"}


# ---------------------------------------------------------------------------
# score against Roanuk's sealed blind labels (SC12c)
# ---------------------------------------------------------------------------

# Roanuk's synthetic answers: positives are a yes with an in scope hazard, so A, C, E and G; D's yes names only
# "other", which the protocol counts as no.
SYNTHETIC_HUMAN = {"A": ("yes", "electrical"), "B": ("no", "none"), "C": ("yes", "heat"), "D": ("yes", "other"),
                   "E": ("yes", "gas"), "F": ("no", "none"), "G": ("yes", "electrical"), "H": ("no", "none")}


def _seal_human_labels(answers: dict[str, tuple[str, str]]) -> Path:
    """Write human_labels.json and its seal where sc12c reads them, as sc12c.seal would; returns the labels."""
    labels_file = sc12c.committed(sc12c.HUMAN_LABELS)
    harness.write_json(labels_file, {"labels": {_sid(n): {"safety": s, "hazard": h, "answered_at": "t"}
                                                for n, (s, h) in answers.items()}})
    harness.write_json(sc12c.committed(sc12c.SEAL), {"human_labels_sha256": sc12c.file_sha256(labels_file)})
    return labels_file


def test_score_says_so_when_roanuk_labels_are_not_sealed(synthetic: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    """No labels, or labels with no seal: score refuses nothing, scores against the AI majority as before
    and says the labels are not sealed here, in the scores and in the printout.

    Mutation: claude_judge_roanuk_absent_refuses."""
    for step in ("no labels", "labels without a seal"):
        if step == "labels without a seal":
            _seal_human_labels(SYNTHETIC_HUMAN)
            sc12c.committed(sc12c.SEAL).unlink()
        result = cj.score()
        h = result["against_roanuk"]
        assert h["sealed"] is False and "not sealed here" in h["note"], step
        assert sc12c.SEAL in h["note"] and set(h) == {"sealed", "note"}, step
        assert result["arms"][cj.CLAUDE_ARM]["tp"] == 2, step
        assert json.loads(cj.scores_path().read_text(encoding="utf-8"))["against_roanuk"] == h, step
        assert cj.main(["score"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "against Roanuk's labels: not scored; Roanuk's blind labels are not sealed here" in out, step


def test_score_against_roanuk_matches_hand_counts_on_a_synthetic_set(synthetic: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """Hand counts. Roanuk's positives A, C, E, G (D names only "other"). Claude flags A, C, F, H: 2 of 4,
    precision 2 of 4, missed E and G. Jev flags A, B, E: 2 of 4, precision 2 of 3, missed C and G. Neither word
    list flags a synthetic step: 0 of 4, missed all four. Missed by both: G; by Jev and caught by Claude: C;
    by Claude and caught by Jev: E. A labels file changed after its seal, or one short of the held out ids,
    is refused.

    Mutations: claude_judge_roanuk_positive_counts_other, claude_judge_roanuk_both_missed_is_either,
    claude_judge_roanuk_claude_misses_by_jev, claude_judge_roanuk_seal_unchecked,
    claude_judge_roanuk_lines_drop_missed."""
    labels_file = _seal_human_labels(SYNTHETIC_HUMAN)
    result = cj.score()
    h = result["against_roanuk"]
    assert h["sealed"] is True and h["items"] == 8 and h["positives"] == 4
    assert h["human_labels_sha256"] == sc12c.file_sha256(labels_file) and "sealed blind labels" in h["note"]
    order = [_sid(n) for n in sorted(SYNTHETIC, key=_sid)]

    def ids(*names: str) -> list[str]:
        return [k for k in order if k in {_sid(n) for n in names}]

    expected = {cj.CLAUDE_ARM: (2, 4, ids("E", "G")), cj.JEV_ARM: (2, 3, ids("C", "G")),
                cj.PUBLISHED_ARM: (0, 0, ids("A", "C", "E", "G")), cj.TUNED_ARM: (0, 0, ids("A", "C", "E", "G"))}
    for name, (tp, flagged, missed) in expected.items():
        arm = h["arms"][name]
        assert (arm["tp"], arm["flagged"], arm["positives"], arm["other_flagged"]) == (tp, flagged, 4, 0), name
        assert arm["missed"] == len(missed) and arm["missed_ids"] == missed, name
        assert arm["recall_wilson95"] == stats.wilson(tp, 4) and arm["precision_wilson95"] == stats.wilson(tp, flagged)
    assert h["arms"][cj.CLAUDE_ARM]["precision"] == 0.5 and h["arms"][cj.JEV_ARM]["precision"] == pytest.approx(2 / 3)
    assert h["missed_by_both_jev_and_claude"] == {"count": 1, "ids": ids("G")}
    assert h["missed_by_jev_caught_by_claude"] == {"count": 1, "ids": ids("C")}
    assert h["missed_by_claude_caught_by_jev"] == {"count": 1, "ids": ids("E")}
    assert h["reproduces_sc12c_score"] is None and h["revisions_not_scored"] == 0
    # The AI majority arms are as before.
    assert (result["arms"][cj.CLAUDE_ARM]["tp"], result["arms"][cj.CLAUDE_ARM]["positives"]) == (2, 4)
    assert json.loads(cj.scores_path().read_text(encoding="utf-8"))["against_roanuk"] == json.loads(json.dumps(h))
    lines = cj.format_score(result)
    assert any(line.startswith("against Roanuk's sealed blind labels (SC12c, sha256 ") and "4 safety steps of 8" in line
               for line in lines)
    assert any(line.startswith(f"  {cj.CLAUDE_ARM}: recall 0.500 [0.150, 0.850] (2 of 4)")
               and line.endswith("; missed 2 of 4") for line in lines)
    assert any(line.startswith(f"  {cj.JEV_ARM}: recall") and "precision 0.667" in line
               and line.endswith("; missed 2 of 4") for line in lines)
    assert f"  Roanuk's safety steps missed by both Jev and Claude: 1 {[_sid('G')[:12]]}" in lines
    # A labels file changed after its seal (one byte) is refused, as sc12c refuses it.
    labels_file.write_text(labels_file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(harness.EvalRefusal, match="changed after it was sealed"):
        cj.score()
    # Sealed labels one short of the held out ids are refused.
    _seal_human_labels({n: a for n, a in SYNTHETIC_HUMAN.items() if n != "H"})
    with pytest.raises(harness.EvalRefusal, match="ids are not the held out half's ids"):
        cj.score()


REAL_CLAUDE_RUN = REAL_SC12A / cj.SUITE / cj.REGISTERED_SETTINGS_HASH
SC12C_COMMITTED = (sc12c.HUMAN_LABELS, sc12c.SEAL, sc12c.REVISED, sc12c.SCORES)


def test_score_against_roanuk_reproduces_sc12c_and_the_decision_log_on_the_real_files(
        real: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Copies of the real Claude run and the committed SC12c files: against Roanuk's sealed blind labels Jev
    catches 22 of 26 at precision 0.537, the published list 18 of 26 at 0.643, the tuned list 18 of 26 at
    0.529 (exactly the SC12c score), and Claude 19 of 26, recall 0.731 [0.539, 0.863], precision 0.576
    [0.408, 0.728] (the decision log). Every step Jev missed, Claude missed too. The SC12c files are read
    only, and a committed SC12c score these arms do not reproduce is refused.

    Mutations: claude_judge_roanuk_positive_counts_other, claude_judge_roanuk_reproduction_unchecked,
    claude_judge_roanuk_lines_drop_missed."""
    if not (REAL_CLAUDE_RUN / cj.REPLIES).is_file() or not (REAL_CLAUDE_RUN / cj.RUN_RECORD).is_file():
        pytest.skip("needs the recorded Claude judge run under data/eval/sc12a/claude_judge (gitignored)")
    cj.run_dir().mkdir(parents=True)
    for name in (cj.REPLIES, cj.RUN_RECORD):
        shutil.copy2(REAL_CLAUDE_RUN / name, cj.run_dir() / name)
    committed = sc12c.COMMITTED_DIR
    committed.mkdir()
    for name in SC12C_COMMITTED:
        shutil.copy2(SAFETY_EVAL_DIR / name, committed / name)
    before = _tree_hashes(committed, cj.run_dir())
    capsys.readouterr()
    assert cj.main(["score"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert _tree_hashes(committed, cj.run_dir()) == before
    result = json.loads(cj.scores_path().read_text(encoding="utf-8"))
    h = result["against_roanuk"]
    assert h["sealed"] is True and h["items"] == 175 and h["positives"] == 26
    assert h["reproduces_sc12c_score"] is True and h["revisions_not_scored"] == 3
    arms = h["arms"]
    published = json.loads((SAFETY_EVAL_DIR / sc12c.SCORES).read_text(encoding="utf-8"))["arms"]["against_roanuk"]
    for name in (cj.JEV_ARM, cj.PUBLISHED_ARM, cj.TUNED_ARM):
        assert {k: v for k, v in arms[name].items() if k not in ("missed", "missed_ids")} == published[name], name
    expected = {cj.JEV_ARM: (22, 41, 0.846, 0.537), cj.PUBLISHED_ARM: (18, 28, 0.692, 0.643),
                cj.TUNED_ARM: (18, 34, 0.692, 0.529), cj.CLAUDE_ARM: (19, 33, 0.731, 0.576)}
    for name, (tp, flagged, recall, precision) in expected.items():
        arm = arms[name]
        assert (arm["tp"], arm["flagged"], arm["positives"], arm["missed"]) == (tp, flagged, 26, 26 - tp), name
        assert round(arm["recall"], 3) == recall and round(arm["precision"], 3) == precision, name
    assert [round(x, 3) for x in arms[cj.CLAUDE_ARM]["recall_wilson95"]] == [0.539, 0.863]
    assert [round(x, 3) for x in arms[cj.CLAUDE_ARM]["precision_wilson95"]] == [0.408, 0.728]
    assert [round(x, 3) for x in arms[cj.JEV_ARM]["recall_wilson95"]] == [0.665, 0.938]
    assert [round(x, 3) for x in arms[cj.JEV_ARM]["precision_wilson95"]] == [0.387, 0.679]
    assert [round(x, 3) for x in arms[cj.PUBLISHED_ARM]["recall_wilson95"]] == [0.500, 0.835]
    # Every safety step Jev missed, Claude missed too.
    assert h["missed_by_jev_caught_by_claude"] == {"count": 0, "ids": []}
    assert h["missed_by_both_jev_and_claude"]["ids"] == arms[cj.JEV_ARM]["missed_ids"]
    assert h["missed_by_both_jev_and_claude"]["count"] == 4 and h["missed_by_claude_caught_by_jev"]["count"] == 3
    # Against the readers, unchanged: Claude 28 of 31, and Claude agrees with Jev on 163 of 175.
    assert (result["arms"][cj.CLAUDE_ARM]["tp"], result["arms"][cj.CLAUDE_ARM]["flagged"]) == (28, 33)
    assert result["agreement_with_jev"]["agree"] == 163
    assert (f"  {cj.CLAUDE_ARM}: recall 0.731 [0.539, 0.863] (19 of 26), precision 0.576 [0.408, 0.728] "
            "(19 of 33); missed 7 of 26") in out
    assert "  Jev and both word lists reproduce the SC12c score: yes" in out
    raw = cj.scores_path().read_text(encoding="utf-8")
    texts = {t for i in harness.load_items()["items"] for t in (i.get("step"), i.get("detail")) if t}
    assert not [t for t in texts if t in raw or json.dumps(t, ensure_ascii=False)[1:-1] in raw]
    # A committed SC12c score for the same sealed labels that these arms do not reproduce is refused.
    scores = json.loads((committed / sc12c.SCORES).read_text(encoding="utf-8"))
    scores["arms"]["against_roanuk"][cj.TUNED_ARM]["tp"] = 17
    (committed / sc12c.SCORES).write_text(json.dumps(scores), encoding="utf-8")
    with pytest.raises(harness.EvalRefusal, match="do not reproduce the SC12c score"):
        cj.score()


# ---------------------------------------------------------------------------
# paired tests (exact McNemar) between the arms
# ---------------------------------------------------------------------------


def test_mcnemar_exact_matches_hand_computed_values() -> None:
    """Hand computed: n = b + c, p = 2 * (C(n, 0) + ... + C(n, min(b, c))) / 2**n, capped at 1.

    (3, 0): n 3, 2 * 1 / 8 = 1/4. (7, 2): n 9, 2 * (1 + 9 + 36) / 512 = 23/128. (3, 1): n 4, 2 * (1 + 4) / 16
    = 5/8. (7, 1): n 8, 2 * (1 + 8) / 256 = 9/128. (10, 2): n 12, 2 * (1 + 12 + 66) / 4096 = 79/2048. (6, 0):
    2 / 64 = 1/32. The order of the two counts does not matter. Edge cases: no disagreements (0, 0) is 1; a
    single one (1, 0) is 2 * 1 / 2 = 1; an even split (2, 2) would be 2 * 11 / 16, capped at 1; (4, 3) would be
    2 * 64 / 128, exactly 1. A negative count is refused.

    Mutations: stats_mcnemar_one_tail (the tail is not doubled), stats_mcnemar_uncapped (the cap at 1 is
    dropped), stats_mcnemar_tail_short (the tail stops one short of the smaller count),
    stats_mcnemar_no_disagreement_zero (n 0 gives 0)."""
    assert stats.mcnemar_exact(3, 0) == Fraction(1, 4)
    assert stats.mcnemar_exact(7, 2) == Fraction(23, 128)
    assert stats.mcnemar_exact(3, 1) == Fraction(5, 8)
    assert stats.mcnemar_exact(7, 1) == Fraction(9, 128)
    assert stats.mcnemar_exact(10, 2) == Fraction(79, 2048)
    assert stats.mcnemar_exact(6, 0) == Fraction(1, 32)
    for b, c in ((3, 0), (7, 2), (3, 1), (7, 1), (10, 2), (0, 6)):
        assert stats.mcnemar_exact(b, c) == stats.mcnemar_exact(c, b), (b, c)
    assert round(float(stats.mcnemar_exact(7, 2)), 3) == 0.180 and round(float(stats.mcnemar_exact(7, 1)), 3) == 0.070
    assert stats.mcnemar_exact(0, 0) == 1
    assert stats.mcnemar_exact(1, 0) == 1
    assert stats.mcnemar_exact(2, 2) == 1
    assert stats.mcnemar_exact(4, 3) == 1
    assert isinstance(stats.mcnemar_exact(5, 1), Fraction)
    with pytest.raises(ValueError):
        stats.mcnemar_exact(-1, 2)


def _pair(reference: dict[str, Any], first: str, second: str) -> dict[str, Any]:
    found = [p for p in reference["pairs"] if (p["first"], p["second"]) == (first, second)]
    assert len(found) == 1, (first, second)
    return found[0]


def test_paired_tests_match_hand_counts_on_a_synthetic_set(synthetic: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Hand counts. Jev (at 0.51) flags A, B, E; Claude flags A, C, F, H; neither word list flags a step.
    Against the readers (positives A to D, others E to H): Jev against Claude, safety steps caught by Jev only
    B and by Claude only C (1 and 1, p 1); false flags by Jev only E and by Claude only F and H (1 and 2, p 1).
    Claude against the published list: caught A and C against none (p 2/4 = 1/2), false flags F and H against
    none (p 1/2). Against Roanuk (positives A, C, E, G, others B, D, F, H): Jev against Claude, caught by Jev
    only E, by Claude only C; false flags by Jev only B, by Claude only F and H. Jev against the tuned list:
    caught A and E against none (p 1/2), false flag B against none (p 1). The six pairs run Jev against
    Claude first; the printout names the method, says the test was chosen after the results and that many
    comparisons are reported, and gives the counts and p values.

    Mutations: claude_judge_paired_first_second_swapped, claude_judge_paired_false_flags_on_every_step,
    claude_judge_paired_readers_jev_is_claude, claude_judge_paired_lines_drop_the_note."""
    _seal_human_labels(SYNTHETIC_HUMAN)
    result = cj.score()
    paired = result["paired"]
    assert paired["method"] == cj.PAIRED_METHOD and paired["note"] == cj.PAIRED_NOTE
    assert "chosen after" in paired["note"] and "several comparisons" in paired["note"]
    order = [_sid(n) for n in sorted(SYNTHETIC, key=_sid)]

    def ids(*names: str) -> dict[str, Any]:
        found = [k for k in order if k in {_sid(n) for n in names}]
        return {"count": len(found), "ids": found}

    readers, roanuk = paired["against_readers"], paired["against_roanuk"]
    assert (readers["items"], readers["safety_steps"], readers["other_steps"]) == (8, 4, 4)
    assert (roanuk["items"], roanuk["safety_steps"], roanuk["other_steps"]) == (8, 4, 4)
    assert roanuk["sealed"] is True and "three Claude model readers" in readers["reference"]
    expected_pairs = [(cj.JEV_ARM, cj.CLAUDE_ARM), (cj.JEV_ARM, cj.PUBLISHED_ARM), (cj.JEV_ARM, cj.TUNED_ARM),
                      (cj.CLAUDE_ARM, cj.PUBLISHED_ARM), (cj.CLAUDE_ARM, cj.TUNED_ARM),
                      (cj.PUBLISHED_ARM, cj.TUNED_ARM)]
    for reference in (readers, roanuk):
        assert [(p["first"], p["second"]) for p in reference["pairs"]] == expected_pairs
    expected = {
        ("readers", cj.JEV_ARM, cj.CLAUDE_ARM): (ids("B"), ids("C"), "1", ids("E"), ids("F", "H"), "1"),
        ("readers", cj.CLAUDE_ARM, cj.PUBLISHED_ARM): (ids("A", "C"), ids(), "1/2", ids("F", "H"), ids(), "1/2"),
        ("readers", cj.PUBLISHED_ARM, cj.TUNED_ARM): (ids(), ids(), "1", ids(), ids(), "1"),
        ("roanuk", cj.JEV_ARM, cj.CLAUDE_ARM): (ids("E"), ids("C"), "1", ids("B"), ids("F", "H"), "1"),
        ("roanuk", cj.JEV_ARM, cj.TUNED_ARM): (ids("A", "E"), ids(), "1/2", ids("B"), ids(), "1"),
    }
    for (ref, first, second), (c1, c2, cp, f1, f2, fp) in expected.items():
        reference = {"readers": readers, "roanuk": roanuk}[ref]
        pair = _pair(reference, first, second)
        caught, false = pair["safety_steps_caught"], pair["false_flags_raised"]
        assert (caught["steps"], false["steps"]) == (4, 4), (first, second)
        assert (caught["first_only"], caught["second_only"], caught["p_value_exact"]) == (c1, c2, cp), (first, second)
        assert (false["first_only"], false["second_only"], false["p_value_exact"]) == (f1, f2, fp), (first, second)
        assert caught["p_value"] == float(Fraction(cp)) and false["p_value"] == float(Fraction(fp))
    assert json.loads(cj.scores_path().read_text(encoding="utf-8"))["paired"] == json.loads(json.dumps(paired))
    lines = cj.format_score(result)
    assert f"paired tests ({cj.PAIRED_METHOD})" in lines and f"note: {cj.PAIRED_NOTE}" in lines
    assert "  against Roanuk's sealed blind labels: 4 safety steps, 4 other steps" in lines
    assert (f"    {cj.CLAUDE_ARM} against {cj.PUBLISHED_ARM}: safety steps caught by the first only 2, by the second "
            "only 0, p 0.500; false flags raised by the first only 2, by the second only 0, p 0.500") in lines


def test_paired_tests_say_so_when_roanuk_labels_are_not_sealed(synthetic: Path) -> None:
    """No sealed labels: the paired tests against Roanuk say not sealed, and the readers' are still scored.

    Mutation: claude_judge_paired_unsealed_drops_the_note."""
    result = cj.score()
    paired = result["paired"]
    assert paired["against_roanuk"]["sealed"] is False and "not sealed here" in paired["against_roanuk"]["note"]
    assert set(paired["against_roanuk"]) == {"sealed", "note"}
    assert len(paired["against_readers"]["pairs"]) == 6
    assert any(line.startswith("  against Roanuk's sealed blind labels: not scored; Roanuk's blind labels are not")
               for line in cj.format_score(result))


def test_paired_tests_reproduce_the_hand_computation_on_the_real_files(
        real: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Copies of the real Claude run and the committed SC12c files. Jev against Claude, by Roanuk's sealed
    labels: Jev caught 3 safety steps Claude missed and Claude 0 that Jev missed (p 1/4 = 0.250); among the 149
    steps he did not call safety steps, Jev raised 7 false flags Claude did not and Claude 2 that Jev did not
    (p 23/128, 0.180). By the readers' majority: caught 3 and 1 (p 5/8 = 0.625), false flags 7 and 1 (p 9/128,
    0.070). The caught ids match the missed lists already scored; the SC12c files are read only.

    Mutations: claude_judge_paired_false_flags_on_every_step, claude_judge_paired_readers_jev_is_claude,
    claude_judge_paired_first_second_swapped."""
    if not (REAL_CLAUDE_RUN / cj.REPLIES).is_file() or not (REAL_CLAUDE_RUN / cj.RUN_RECORD).is_file():
        pytest.skip("needs the recorded Claude judge run under data/eval/sc12a/claude_judge (gitignored)")
    cj.run_dir().mkdir(parents=True)
    for name in (cj.REPLIES, cj.RUN_RECORD):
        shutil.copy2(REAL_CLAUDE_RUN / name, cj.run_dir() / name)
    committed = sc12c.COMMITTED_DIR
    committed.mkdir()
    for name in SC12C_COMMITTED:
        shutil.copy2(SAFETY_EVAL_DIR / name, committed / name)
    before = _tree_hashes(committed, cj.run_dir())
    capsys.readouterr()
    assert cj.main(["score"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert _tree_hashes(committed, cj.run_dir()) == before
    result = json.loads(cj.scores_path().read_text(encoding="utf-8"))
    roanuk, readers = result["paired"]["against_roanuk"], result["paired"]["against_readers"]
    assert (roanuk["items"], roanuk["safety_steps"], roanuk["other_steps"]) == (175, 26, 149)
    assert (readers["items"], readers["safety_steps"], readers["other_steps"]) == (175, 31, 144)
    assert roanuk["human_labels_sha256"] == result["against_roanuk"]["human_labels_sha256"]
    want = {"roanuk": ((3, 0, "1/4"), (7, 2, "23/128")), "readers": ((3, 1, "5/8"), (7, 1, "9/128"))}
    for ref, reference in (("roanuk", roanuk), ("readers", readers)):
        pair = reference["pairs"][0]
        assert (pair["first"], pair["second"]) == (cj.JEV_ARM, cj.CLAUDE_ARM)
        for kind, (first, second, p) in zip(("safety_steps_caught", "false_flags_raised"), want[ref]):
            split = pair[kind]
            assert (split["first_only"]["count"], split["second_only"]["count"], split["p_value_exact"]) == \
                (first, second, p), kind
            assert len(split["first_only"]["ids"]) == first and len(split["second_only"]["ids"]) == second
    caught = roanuk["pairs"][0]["safety_steps_caught"]
    assert caught["first_only"] == result["against_roanuk"]["missed_by_claude_caught_by_jev"]
    assert caught["second_only"] == result["against_roanuk"]["missed_by_jev_caught_by_claude"]
    readers_caught = readers["pairs"][0]["safety_steps_caught"]
    assert readers_caught["first_only"]["ids"] == result["caught_by_jev_only"]
    assert readers_caught["second_only"]["ids"] == result["caught_by_claude_only"]
    assert (f"    {cj.JEV_ARM} against {cj.CLAUDE_ARM}: safety steps caught by the first only 3, by the second only 0, "
            "p 0.250; false flags raised by the first only 7, by the second only 2, p 0.180") in out
    assert (f"    {cj.JEV_ARM} against {cj.CLAUDE_ARM}: safety steps caught by the first only 3, by the second only 1, "
            "p 0.625; false flags raised by the first only 7, by the second only 1, p 0.070") in out
    raw = cj.scores_path().read_text(encoding="utf-8")
    texts = {t for i in harness.load_items()["items"] for t in (i.get("step"), i.get("detail")) if t}
    assert not [t for t in texts if t in raw or json.dumps(t, ensure_ascii=False)[1:-1] in raw]


# ---------------------------------------------------------------------------
# SC12a stays untouched, and the build fingerprint does not move
# ---------------------------------------------------------------------------


def test_every_sc12a_file_is_unchanged_byte_for_byte(real: Path, live: dict[str, str]) -> None:
    """Before and after plan, run and score: every file of the SC12a copy and of the real folder, and the
    committed labels, item ids and wordings, byte for byte; the only new files are the run's three (replies,
    record and lock), under sc12a/claude_judge/<settings hash>/.

    Mutation: claude_judge_run_appends_a_jev_reply (the run writes a line to the Jev replies file)."""
    sc12a = config.EVAL_DIR / "sc12a"
    watched = (sc12a, *(harness.COMMITTED_DIR / n for n in COMMITTED_SC12A),
               *(SAFETY_EVAL_DIR / n for n in COMMITTED_SC12A))
    real_before = _tree_hashes(REAL_SC12A, skip=REAL_SC12A / cj.SUITE)
    before = _tree_hashes(*watched)
    assert cj.main(["plan"]) == cli.EXIT_OK
    assert _run(Fakes(), live) == cli.EXIT_OK
    cj.score()
    after = _tree_hashes(*watched, skip=sc12a / cj.SUITE)
    assert after == before
    assert _tree_hashes(REAL_SC12A, skip=REAL_SC12A / cj.SUITE) == real_before
    new = sorted(str(p.relative_to(sc12a)) for p in (sc12a / cj.SUITE).rglob("*") if p.is_file())
    assert new == sorted(f"{cj.SUITE}/{cj.REGISTERED_SETTINGS_HASH}/{name}"
                         for name in (cj.REPLIES, cj.RUN_RECORD, cj.RUN_LOCK))


NEW_FILES = ("safety_eval/claude_judge.py", f"safety_eval/{cj.SCORES}")


def test_claude_judge_files_stay_outside_the_build_fingerprint(tmp_path: Path) -> None:
    """Decision 65: build_id() is the same with this arm's files absent, and with them changed.

    Mutation: claude_judge_fingerprinted (safety_eval/ leaves the fingerprint's exclusions)."""
    for rel in NEW_FILES:
        assert build_info.is_excluded(rel), rel
    agent = config.REPO_ROOT / "agent"
    copy = tmp_path / "agent"
    shutil.copytree(agent, copy, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    for rel in NEW_FILES:
        (copy / rel).unlink(missing_ok=True)
    assert build_info.fingerprint(copy) == build_info.fingerprint(agent) == build_info.build_id()
    for rel in NEW_FILES:
        (copy / rel).write_text("{}\n", encoding="utf-8")
    assert build_info.fingerprint(copy) == build_info.build_id()


# ---------------------------------------------------------------------------
# One run: resume, the pinned settings, other runs, the lock, unexpected errors
# ---------------------------------------------------------------------------


def _overloaded() -> Exception:
    """A 529 as the SDK raises it, built offline (no client, no request sent)."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.invalid/v1/messages")
    return anthropic.OverloadedError("overloaded", response=httpx.Response(529, request=request), body=None)


def test_a_stopped_run_resumes_under_its_own_run_id_and_never_reasks(real: Path, live: dict[str, str],
                                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """A 529 at item 3 stops the run (not a transport error, so not retried); the next run resumes it under
    the same ledger run id, asks items 4 to 175 only, appends to the same replies and completes; a third run
    is refused, and the completed run scores with item 3 as not answered.

    Mutations: claude_judge_resume_reasks_recorded_items (a resume asks every item again),
    claude_judge_resume_mints_a_new_run_id (a resume gets a new ledger run id, and so a fresh run cap)."""
    held = _held_ids()

    def first_script(n: int, item_id: str) -> bool:
        if item_id == held[2]:
            raise _overloaded()
        return False

    first = Fakes(first_script)
    assert _run(first, live) == cli.EXIT_LIVE_ERROR
    record = harness.read_json(cj.run_record_path())
    run_id = record["run_id"]
    assert (record["status"], record["stop_reason"], record["asked"]) == ("stopped", "api_error", 3)
    assert [k for k, _ in first.sent] == held[:3]
    assert "resume it with" in capsys.readouterr().out
    plan_out = "\n".join(cj.plan())
    assert f"run {run_id} (stopped) has 3 recorded and resumes with the 172 not yet asked" in plan_out
    assert "planned calls: typical 172, at most 344" in plan_out
    second = Fakes()
    assert _run(second, live) == cli.EXIT_OK
    assert [k for k, _ in second.sent] == held[3:]
    record = harness.read_json(cj.run_record_path())
    assert record["run_id"] == run_id and record["status"] == "completed" and record["asked"] == 175
    assert len(record["resumes"]) == 1 and record["resumes"][0]["to_ask"] == 172
    assert record["resumes"][0]["stop_reason"] is None and record["resumes"][0]["finished_at"]
    replies = _replies()
    assert sorted(r["item_id"] for r in replies) == held and {r["run_id"] for r in replies} == {run_id}
    assert replies[2]["error"] == "OverloadedError" and replies[2]["attempts"] == 1
    assert {r["run_id"] for r in Ledger(config.LEDGER_PATH).rows()} == {run_id}
    assert len(_rows(run_id=run_id, kind="charge")) == 2 + 172
    third = Fakes()
    capsys.readouterr()
    assert _run(third, live) == cli.EXIT_REFUSED and third.sent == []
    assert "already exists (status completed" in capsys.readouterr().err
    result = cj.score()
    assert result["asked"] == 175 and result["unanswered"]["ids"] == [held[2]]
    assert result["unanswered"]["kinds"] == {"OverloadedError": 1}


def test_a_run_stopped_at_its_cap_stays_stopped_on_resume(real: Path, live: dict[str, str],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The run cap counts every call under the run id, so a resume of a run the cap stopped is refused its
    first call by the ledger again and sends nothing.

    Mutation: claude_judge_resume_mints_a_new_run_id (a new run id would carry a fresh cap)."""
    monkeypatch.setattr(config, "RUN_CAP_USD", {**config.RUN_CAP_USD, "cheap": 0.001})
    assert _run(Fakes(), live) == cli.EXIT_LIVE_ERROR
    record = harness.read_json(cj.run_record_path())
    asked = record["asked"]
    again = Fakes()
    assert _run(again, live) == cli.EXIT_LIVE_ERROR
    assert again.sent == []
    record = harness.read_json(cj.run_record_path())
    assert record["status"] == "stopped" and record["stop_reason"] == "run_cap" and record["asked"] == asked
    assert record["resumes"][0]["stop_reason"] == "run_cap"
    assert {r["run_id"] for r in Ledger(config.LEDGER_PATH).rows()} == {record["run_id"]}


def test_score_refuses_a_run_that_is_not_complete(real: Path) -> None:
    """A stopped run, even with a line for every item, a completed record short of an item, and an item
    with two lines under the run id are all refused by score, which then writes nothing.

    Mutations: claude_judge_score_takes_a_stopped_run, claude_judge_score_takes_a_short_run,
    claude_judge_score_keeps_duplicate_lines."""
    labels = harness.load_labels()
    held = _held_ids()
    flags = {k: labels[k]["positive"] for k in held}
    cj.scores_path().parent.mkdir(parents=True, exist_ok=True)
    cj.scores_path().write_bytes(b"previous\n")

    def fresh(**kwargs: Any) -> None:
        cj.replies_path().unlink(missing_ok=True)
        cj.run_record_path().unlink(missing_ok=True)
        _write_claude_run(**kwargs)

    fresh(flags=flags, status="stopped")
    with pytest.raises(harness.EvalRefusal, match="is stopped, not completed"):
        cj.score()
    fresh(flags={k: v for k, v in flags.items() if k != held[-1]})
    with pytest.raises(harness.EvalRefusal, match="no reply line for 1 held out items"):
        cj.score()
    fresh(flags=flags)
    cj._append(cj.replies_path(), {**_replies()[0], "flag": not _replies()[0]["flag"]})
    with pytest.raises(harness.EvalRefusal, match="two reply lines"):
        cj.score()
    assert cj.scores_path().read_bytes() == b"previous\n"
    # A line under another run id is never read as this run's.
    fresh(flags=flags)
    cj._append(cj.replies_path(), {**_replies()[0], "run_id": "claude-judge-ffffffffffff", "flag": True})
    assert cj.score()["arms"][cj.CLAUDE_ARM]["tp"] == 31


def test_moved_settings_refuse_plan_run_and_score(real: Path, live: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """A setting (or a config value SETTINGS reads) that moved since the registration moves the hash, and
    plan, run and score all refuse; run sends nothing and writes no ledger row, and score refuses even with
    the registered run completed.

    Mutation: claude_judge_registered_hash_unchecked."""
    labels = harness.load_labels()
    _write_claude_run({k: labels[k]["positive"] for k in _held_ids()})
    monkeypatch.setattr(cj, "SETTINGS", MappingProxyType({**cj.SETTINGS, "timeout_s": 40.0}))
    assert cj.settings_hash() != cj.REGISTERED_SETTINGS_HASH
    capsys.readouterr()
    assert cj.main(["plan"]) == cli.EXIT_REFUSED
    assert "is not the registered" in capsys.readouterr().err
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_REFUSED
    assert "is not the registered" in capsys.readouterr().err
    assert fakes.kwargs == [] and fakes.sent == [] and Ledger(config.LEDGER_PATH).rows() == []
    assert cj.main(["score"]) == cli.EXIT_REFUSED
    assert "is not the registered" in capsys.readouterr().err
    assert not cj.scores_path().exists()


def test_a_run_under_other_settings_blocks_run_unless_superseded(real: Path, live: dict[str, str],
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """A run record under another settings folder blocks run (named by hash, status and run id) before any
    plan or call; listed in SETTINGS "supersedes" (a new registration), the run goes on and score reports it.

    Mutation: claude_judge_other_folders_unchecked."""
    other = cj.runs_dir() / "0123456789abcdef"
    other.mkdir(parents=True)
    harness.write_json(other / cj.RUN_RECORD, {"status": "completed", "run_id": "claude-judge-aaaaaaaaaaaa"})
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert ("settings 0123456789abcdef, status completed, run id claude-judge-aaaaaaaaaaaa" in captured.err
            and "planned calls" not in captured.out)
    assert fakes.sent == [] and Ledger(config.LEDGER_PATH).rows() == []
    assert "run refuses: other claude_judge runs exist" in "\n".join(cj.plan())
    monkeypatch.setattr(cj, "SETTINGS", MappingProxyType({**cj.SETTINGS, "supersedes": ("0123456789abcdef",)}))
    monkeypatch.setattr(cj, "REGISTERED_SETTINGS_HASH", cj.settings_hash())
    assert _run(fakes, live) == cli.EXIT_OK and len(fakes.sent) == 175
    result = cj.score()
    assert [(r["settings_hash"], r["run_id"]) for r in result["other_runs"]] == [
        ("0123456789abcdef", "claude-judge-aaaaaaaaaaaa")]
    assert any("superseded" in line for line in cj.format_score(result))


def test_a_ledger_run_with_the_prefix_blocks_run(real: Path, live: dict[str, str],
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger run named with this arm's prefix that is not the registered run's blocks run, even with no
    run folder at all, and nothing is sent or written.

    Mutation: claude_judge_ledger_runs_unchecked."""
    ledger = Ledger(config.LEDGER_PATH)
    reservation = ledger.reserve("claude-judge-bbbbbbbbbbbb", mode="cheap", node="claude_judge",
                                 model=config.HAIKU, input_tokens_est=1000, max_tokens=1024)
    ledger.charge(reservation, {"input_tokens": 1000, "output_tokens": 20})
    rows = len(ledger.rows())
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_REFUSED
    assert "run id claude-judge-bbbbbbbbbbbb (from the ledger)" in capsys.readouterr().err
    assert fakes.sent == [] and len(Ledger(config.LEDGER_PATH).rows()) == rows
    assert not cj.runs_dir().exists()


def test_run_rechecks_under_its_lock_after_proceed(real: Path, live: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """While the plan waits for "proceed", another process may take the run lock or finish a run: run then
    refuses under the lock, having sent nothing and written no ledger row.

    Mutations: claude_judge_lock_not_taken (a held lock is ignored), claude_judge_no_recheck_after_proceed
    (the state is not checked again under the lock)."""
    import fcntl

    held_fds: list[int] = []

    def hold_the_lock(lines: list[str], stdin: Any, out: Any) -> bool:
        cj.run_lock_path().parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(cj.run_lock_path(), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held_fds.append(fd)
        return True

    def finish_a_run(lines: list[str], stdin: Any, out: Any) -> bool:
        _write_claude_run({k: False for k in _held_ids()})
        return True

    try:
        monkeypatch.setattr(cj, "ask_to_proceed", hold_the_lock)
        fakes = Fakes()
        assert _run(fakes, live) == cli.EXIT_REFUSED
        assert "holds" in capsys.readouterr().err
        assert fakes.kwargs == [] and fakes.sent == [] and Ledger(config.LEDGER_PATH).rows() == []
        assert not cj.run_record_path().exists() and cj.lock_is_held()
    finally:
        for fd in held_fds:
            os.close(fd)
    assert not cj.lock_is_held()
    monkeypatch.setattr(cj, "ask_to_proceed", finish_a_run)
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_REFUSED
    assert "changed while the plan waited" in capsys.readouterr().err
    assert fakes.kwargs == [] and fakes.sent == [] and Ledger(config.LEDGER_PATH).rows() == []
    assert harness.read_json(cj.run_record_path())["run_id"] == "claude-judge-0123456789ab"


def test_an_unexpected_error_stops_the_run_and_is_reported_redacted(real: Path, live: dict[str, str],
                                                                    monkeypatch: pytest.MonkeyPatch,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """An error outside the model call (here the reply file cannot be written at item 3) ends the run as a
    live failure: exit code 3, the message redacted, and the record stopped with its reason and spend, so
    the run can be resumed.

    Mutations: claude_judge_unexpected_error_escapes (no live failure report),
    claude_judge_unexpected_stop_unrecorded (the record does not say why it stopped)."""
    real_append = cj._append
    calls: list[str] = []

    def failing_append(path: Path, entry: Any) -> None:
        calls.append(entry["item_id"])
        if len(calls) == 3:
            raise OSError(f"disk full near {FAKE_KEY}")
        real_append(path, entry)

    monkeypatch.setattr(cj, "_append", failing_append)
    fakes = Fakes()
    assert _run(fakes, live) == cli.EXIT_LIVE_ERROR
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out and FAKE_KEY not in captured.err
    assert "the live call failed: OSError" in captured.err
    record = harness.read_json(cj.run_record_path())
    assert (record["status"], record["stop_reason"], record["asked"]) == ("stopped", "unexpected: OSError", 2)
    assert record["finished_at"] and record["spent_usd"] == pytest.approx(
        Ledger(config.LEDGER_PATH).run_total(record["run_id"])) and record["spent_usd"] > 0
    assert len(fakes.sent) == 3 and not cj.lock_is_held()
    state = cj.run_state(cj.heldout_items())
    assert state.refusal is None and len(state.to_ask) == 173
