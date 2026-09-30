"""Safety step flagging: raise only rules, the safety_check node, the notice line and the run record.

No test here calls TypeSafe. The judge is a scripted fake honoring the
SafetyJudge contract (`FakeJudge`), or the replay judge built from scripted
cassette entries. Every step text marked SYNTHETIC below was written for these
tests and is never counted in SC12a. Each test names the mutation in
agent/tests/mutations.toml that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from langgraph.runtime import Runtime

from agent import config, models
from agent.graph import jev_reservation_usd, retry_need_usd, synthesize_reservation_usd
from agent.ledger import price_usage
from agent.nodes import safety_check as node
from agent.nodes.persist import safety_record
from agent.nodes.render import render
from agent.nodes.validate import validate
from agent.render.brief_html import SAFETY_NOTICE, render_brief
from agent.rules.pipeline import run_rules
from agent.rules.safety import LAYERS, raise_flags
from agent.rules.step_text import step_key, word_rule
from agent.safety_judge import JudgeReply, SafetyCheckError, build_replay_judge
from agent.state import RunContext, check_json_native

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "safety"
PUBLISHED_MISS = json.loads((FIXTURES / "published_miss.json").read_text(encoding="utf-8"))
QHASH = "0123456789abcdef"
FAILURE_KINDS = ("reservation_refused", "timeout", "rate_limited", "api_error", "unparseable",
                 "model_mismatch", "missing_key", "other")

# SYNTHETIC items, never counted in SC12a: paraphrases with none of the word
# rule's words, a negation, and near misses the word rule flags.
PARAPHRASES = ("Kill the circuit that feeds the spa", "Close the gas valve before lifting the cover",
               "Let the element cool before touching it")
NEGATION = "Do not open the equipment bay while the pump is running"
NEAR_MISSES = ("Press the Power button on the topside control", "Set the heat mode to Standard")
# Scripted Jev probabilities for them (SYNTHETIC replies, not Jev's).
SCRIPTED = {**{s: 0.93 for s in PARAPHRASES}, NEGATION: 0.18, NEAR_MISSES[0]: 0.12, NEAR_MISSES[1]: 0.07}


class FakeJudge:
    """A SafetyJudge that answers from a script keyed by step text; a str answer is a failure kind."""

    question_hash = QHASH

    def __init__(self, script: dict[str, float | str], default: float | str = 0.05) -> None:
        self.script = script
        self.default = default
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def ask(self, step_sha256: str, state: dict) -> JudgeReply:
        self.calls.append((step_sha256, dict(state)))
        answer = self.script.get(state["step"], self.default)
        if isinstance(answer, str):
            raise SafetyCheckError(answer, f"scripted {answer}")
        return JudgeReply(step_sha256=step_sha256, noul=answer, model=config.JEV_MODEL,
                          request_id=f"req-{len(self.calls)}", input_tokens=384, output_tokens=2,
                          question_hash=QHASH)


def use_judge(monkeypatch: pytest.MonkeyPatch, judge: Any) -> list[Any]:
    """Serve `judge` from models.make_safety_judge; returns the list of contexts it was built for."""
    built: list[Any] = []

    def factory(ctx: Any) -> Any:
        built.append(ctx)
        return judge

    monkeypatch.setattr(models, "make_safety_judge", factory)
    return built


def ctx_for(tmp_path: Path, mode: str = "replay") -> RunContext:
    return RunContext(run_id="run-safety", mode=mode, ledger_path=tmp_path / "ledger.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages")


def step(text: str, *, flag: bool = False, detail: str = "", index: int = 0) -> dict[str, Any]:
    return {"step": text, "detail": detail, "safety_flag": flag, "source_index": index}


def ok_draft(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
            "try_first": steps, "candidates": [], "warranty": None, "no_reliable_answer": None,
            "upgrade_options": [], "maintenance_due": [], "source_tiers": []}


SOURCES = [{"url": f"https://s{i}.example.com/p", "title": f"S{i}", "origin": "search"} for i in range(3)]


def rules(draft: dict[str, Any], signals: dict | None = None):
    return run_rules(draft, sources=SOURCES, observed_code=None, history_hits=[], registry=None, mode="replay",
                     provenance=None, pass_kind="synthesize", safety_signals=signals)


def signals_for(nouls: dict[tuple[str, str], float | None], status: str = "ran") -> dict[str, Any]:
    return {"status": status, "reason": "test", "question_hash": QHASH, "model": config.JEV_MODEL,
            "steps": [{"step_sha256": step_key(s, d), "noul": n, "model": config.JEV_MODEL, "request_id": None,
                       "input_tokens": None, "error": None if n is not None else "timeout"}
                      for (s, d), n in nouls.items()]}


def layers(monkeypatch: pytest.MonkeyPatch, active: tuple[str, ...], threshold: float | None) -> None:
    monkeypatch.setattr(config, "SAFETY_LAYERS", active)
    monkeypatch.setattr(config, "JEV_THRESHOLD", threshold)


def flags(brief: dict[str, Any]) -> dict[str, bool]:
    return {s["step"]: s["safety_flag"] for s in brief["try_first"]}


# ---------------------------------------------------------------------------
# raise_flags
# ---------------------------------------------------------------------------


def test_raise_flags_records_every_layer_and_raises_only_through_active_ones() -> None:
    """Mutation: jev_threshold_none_raises (a None threshold lets any
    recorded probability raise a flag, though Jev is meant to be recorded only)."""
    steps = [step("Drain the spa", flag=True), step("Reset the breaker"), step("Kill the circuit"),
             step("Rinse the filter")]
    signals = signals_for({("Kill the circuit", ""): 0.9, ("Rinse the filter", ""): 0.1})
    prov = raise_flags(steps, signals, layers=("word", "jev"), words=config.SAFETY_WORDS_PUBLISHED,
                       jev_threshold=None)
    assert [s["safety_flag"] for s in steps] == [True, True, False, False]
    assert [(p["raised_by"], p["word_rule"], p["jev_noul"]) for p in prov] == [
        ("writer", False, None), ("word", True, None), (None, False, 0.9), (None, False, 0.1)]

    steps = [step("Kill the circuit"), step("Reset the breaker")]
    prov = raise_flags(steps, signals, layers=("jev",), words=config.SAFETY_WORDS_PUBLISHED, jev_threshold=0.9)
    assert [s["safety_flag"] for s in steps] == [True, False]
    assert prov[0] == {"step_sha256": step_key("Kill the circuit", ""), "writer_flag": False, "word_rule": False,
                       "jev_noul": 0.9, "final_flag": True, "raised_by": "jev"}
    assert prov[1]["word_rule"] is True and prov[1]["raised_by"] is None
    with pytest.raises(ValueError):
        raise_flags([], None, layers=("regex",), words=(), jev_threshold=None)


_TEXTS = ["Turn off power", "Reset the breaker", "Clean the filter", "Check the water", "Let the heater cool",
          "Kill the circuit", "Press Power", "Drain the spa"]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    specs=st.lists(st.tuples(st.sampled_from(_TEXTS), st.sampled_from(["", "at the breaker", "gently"]),
                             st.booleans(), st.one_of(st.none(), st.floats(0, 1)), st.booleans()),
                   min_size=1, max_size=8),
    active=st.lists(st.sampled_from(LAYERS), unique=True).map(tuple),
    words=st.lists(st.sampled_from([*config.SAFETY_WORDS_PUBLISHED, "gently", "circuit"]), unique=True),
    threshold=st.one_of(st.none(), st.floats(0, 1)),
    status=st.sampled_from(node.STATUSES),
)
def test_raise_only_through_run_rules(specs, active, words, threshold, status) -> None:
    """SC 1, raise only. No combination of layers, words, thresholds and
    signals turns a true flag false, adds, drops or reorders steps beyond rule
    5's stable sort, or raises a flag no active layer justifies.

    Mutation: raise_flags_layer_clears (the word layer sets the flag to its
    own answer, so a writer flag on a step with no listed word is cleared)."""
    steps = [step(f"{i}. {text}", flag=writer, detail=detail) for i, (text, detail, writer, _, _) in enumerate(specs)]
    nouls = {(s["step"], s["detail"]): noul for s, (_, _, _, noul, recorded) in zip(steps, specs) if recorded}
    signals = signals_for(nouls, status) if nouls else None
    with mock.patch.object(config, "SAFETY_LAYERS", active), mock.patch.object(config, "SAFETY_WORDS", tuple(words)), \
            mock.patch.object(config, "JEV_THRESHOLD", threshold):
        result = rules(ok_draft([dict(s) for s in steps]), signals)
    assert result.errors == []
    out = result.brief["try_first"]

    def expected_flag(s: dict[str, Any]) -> bool:
        noul = nouls.get((s["step"], s["detail"]))
        return (s["safety_flag"] or ("word" in active and word_rule(s["step"], s["detail"], words))
                or ("jev" in active and threshold is not None and noul is not None and noul >= threshold))

    final = [expected_flag(s) for s in steps]
    expected = [s for s, f in zip(steps, final) if f] + [s for s, f in zip(steps, final) if not f]
    # The brief keeps each step; an empty detail comes back as None.
    assert [(s["step"], s["detail"] or "") for s in out] == [(s["step"], s["detail"]) for s in expected]
    assert [s["safety_flag"] for s in out] == sorted(final, reverse=True)
    for before in steps:
        if before["safety_flag"]:
            assert next(s for s in out if s["step"] == before["step"])["safety_flag"] is True
    assert [p["step_sha256"] for p in result.safety_provenance] == [step_key(s["step"], s["detail"]) for s in out]
    assert [p["final_flag"] for p in result.safety_provenance] == [s["safety_flag"] for s in out]


# ---------------------------------------------------------------------------
# The published miss (SC 2)
# ---------------------------------------------------------------------------


def test_published_miss_is_flagged_by_the_shipped_configuration() -> None:
    """Run t-267045726dd04118's step list (a tracked fixture copied from the
    published brief, since its draft lives only under data/) through the rules
    as shipped, with Jev's recorded 0.98 for the breaker step, flags that step
    and puts it first.

    Mutation: safety_words_published_no_breaker_power (the word list loses
    "breaker" and "power" while the Jev threshold rises above 0.98)."""
    published = (config.REPO_ROOT / PUBLISHED_MISS["published_brief"]).read_text(encoding="utf-8")
    for s in PUBLISHED_MISS["steps"]:
        assert f"<strong>{s['step']}</strong>" in published and s["detail"] in published
        assert s["safety_flag"] is False  # the writer flagged nothing
    missed = next(s for s in PUBLISHED_MISS["steps"] if s["step"] == PUBLISHED_MISS["missed_step"])
    signals = signals_for({(missed["step"], missed["detail"]): PUBLISHED_MISS["jev_recorded"]["noul"]})
    result = rules(ok_draft([dict(s) for s in PUBLISHED_MISS["steps"]]), signals)
    assert result.errors == []
    first = result.brief["try_first"][0]
    assert first["step"] == missed["step"] and first["safety_flag"] is True
    assert [s["safety_flag"] for s in result.brief["try_first"]] == [True, False, False, False]
    assert result.safety_provenance[0]["raised_by"] in config.SAFETY_LAYERS


# ---------------------------------------------------------------------------
# Jev on scripted replies (synthetic items)
# ---------------------------------------------------------------------------


def _synthetic_state() -> dict[str, Any]:
    items = [*PARAPHRASES, NEGATION, *NEAR_MISSES]
    return {"status": "running", "identity": {"manufacturer": "Maker", "model": "Model 1"},
            "draft": ok_draft([step(text, index=0) for text in items])}


def test_jev_on_scripted_cassette_replies_raises_what_the_word_rule_misses(tmp_path, monkeypatch) -> None:
    """The replay judge serves scripted cassette entries by step hash; with Jev
    among the layers, its paraphrase answers raise flags the word rule cannot,
    the negation stays unflagged, and the word rule's near misses are flagged
    only while the word layer is on.

    Mutation: jev_matched_by_position (every step is served the first recorded
    answer, not its own)."""
    state = _synthetic_state()
    entries = [{"step_sha256": step_key(text, ""), "question_hash": QHASH, "model": config.JEV_MODEL,
                "noul": noul, "input_tokens": 384, "output_tokens": 2, "request_id": None, "error": None}
               for text, noul in SCRIPTED.items()]
    use_judge(monkeypatch, build_replay_judge(entries, question_hash=QHASH))
    signals = node.check_draft(state, ctx_for(tmp_path))
    assert signals["status"] == "ran" and len(signals["steps"]) == len(SCRIPTED)
    check_json_native(signals)
    for text in (*PARAPHRASES, NEGATION):
        assert not word_rule(text, "", config.SAFETY_WORDS_PUBLISHED), text
    for text in NEAR_MISSES:
        assert word_rule(text, "", config.SAFETY_WORDS_PUBLISHED), text

    # The layer mechanics are checked against the published word list, whose near
    # misses these are; the tuned list drops "heat" and so no longer flags one.
    monkeypatch.setattr(config, "SAFETY_WORDS", config.SAFETY_WORDS_PUBLISHED)
    layers(monkeypatch, ("jev",), 0.5)
    jev_only = flags(rules(state["draft"], signals).brief)
    assert {t for t, f in jev_only.items() if f} == set(PARAPHRASES)
    layers(monkeypatch, ("word", "jev"), 0.5)
    both = rules(state["draft"], signals)
    assert {t for t, f in flags(both.brief).items() if f} == {*PARAPHRASES, *NEAR_MISSES}
    raised = {p["step_sha256"]: p["raised_by"] for p in both.safety_provenance}
    assert raised[step_key(NEAR_MISSES[0], "")] == "word" and raised[step_key(PARAPHRASES[0], "")] == "jev"
    assert raised[step_key(NEGATION, "")] is None


def test_the_judge_sees_only_appliance_step_and_detail(tmp_path, monkeypatch) -> None:
    """Mutations: safety_state_leaks_symptom (the symptom joins Jev's state), safety_appliance_no_maker_match
    (a registered maker's model written another way is sent as "appliance")."""
    from agent.registry import open_registry

    ctx = ctx_for(tmp_path)
    open_registry(ctx.registry_path, seed=config.DEFAULT_SEED_PATH)
    judge = FakeJudge({})
    use_judge(monkeypatch, judge)
    state = {"status": "running", "symptom": "panel shows FLO", "appliance_id": None,
             "identity": {"manufacturer": "Sundance Spas", "model": "Optima 880"},
             "draft": ok_draft([step("Check water level", detail="Top it up"),
                                step("check water level.", detail="top it up")])}
    node.check_draft(state, ctx)
    assert [c[1] for c in judge.calls] == [{"appliance": "hot tub", "step": "Check water level",
                                            "detail": "Top it up"}]  # the same step twice is one question
    judge.calls.clear()
    node.check_draft({**state, "appliance_id": "appl-xr16", "identity": {"manufacturer": "X", "model": "Y"}}, ctx)
    assert judge.calls[0][1]["appliance"] == "air conditioner"
    judge.calls.clear()
    node.check_draft({**state, "identity": {"manufacturer": "X", "model": "Y"}}, ctx)
    assert judge.calls[0][1]["appliance"] == node.UNKNOWN_APPLIANCE
    judge.calls.clear()  # SC12b's air conditioner input writes the registered model another way
    node.check_draft({**state, "identity": {"manufacturer": "trane ", "model": "XR16 (4TTR6036)"}}, ctx)
    assert judge.calls[0][1]["appliance"] == "air conditioner"


# ---------------------------------------------------------------------------
# Failures, skips and the notice line (SC 5)
# ---------------------------------------------------------------------------


WRITER_STEP = "Drain the spa before service"
WORD_STEP = "Turn off power at the breaker"
PLAIN_STEP = "Kill the circuit that feeds the spa"  # SYNTHETIC


def _failure_state() -> dict[str, Any]:
    return {"run_id": "run-safety", "status": "running", "symptom": "FLO",
            "identity": {"manufacturer": "Maker", "model": "Model 1"}, "search_trail": [], "sources": SOURCES,
            "history_hits": [], "observed_code": None, "validation_failures": 0,
            "draft": ok_draft([step(PLAIN_STEP, index=0), step(WORD_STEP, index=1),
                               step(WRITER_STEP, flag=True, index=2)])}


def _through_render(state: dict[str, Any], tmp_path: Path) -> tuple[dict[str, Any], str]:
    ctx = ctx_for(tmp_path)
    state = {**state, **validate(state, Runtime(context=ctx))}
    assert state["status"] == "ok", state.get("validation_errors")
    state["html_path"] = str(tmp_path / "brief.html")
    state = {**state, **render(state, Runtime(context=ctx))}
    return state, Path(state["html_path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", FAILURE_KINDS)
@pytest.mark.parametrize("partial", [False, True])
def test_each_failure_kind_keeps_word_and_writer_flags_notes_the_record_and_shows_the_notice(
        kind: str, partial: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: safety_failures_counted_as_not_recorded (a failed call reads
    as missing from a cassette, so no notice and no note); safety_notice_failed_only
    (a partial failure shows no notice line)."""
    layers(monkeypatch, ("word", "jev"), 0.5)
    script: dict[str, float | str] = {PLAIN_STEP: kind, WORD_STEP: kind, WRITER_STEP: kind}
    if partial:
        script[WRITER_STEP] = 0.2
    judge = FakeJudge(script)
    use_judge(monkeypatch, judge)
    state = _failure_state()
    signals = node.check_draft(state, ctx_for(tmp_path))
    assert signals["status"] == ("partial" if partial else "failed")
    assert kind in signals["reason"]
    failed = [e for e in signals["steps"] if e["error"] is not None]
    assert {e["error"] for e in failed} == {kind} and all(e["noul"] is None for e in failed)
    assert len(judge.calls) == 3

    final, html = _through_render({**state, "safety_signals": signals}, tmp_path)
    assert flags(final["brief"]) == {WORD_STEP: True, WRITER_STEP: True, PLAIN_STEP: False}
    assert html.count(SAFETY_NOTICE) == 1
    record = safety_record(final)
    assert record["status"] == signals["status"] and kind in record["reason"]
    by_step = {s["step"]: s for s in record["steps"]}
    assert by_step[PLAIN_STEP]["jev_error"] == kind and by_step[PLAIN_STEP]["jev_noul"] is None
    assert by_step[WORD_STEP]["raised_by"] == "word" and by_step[WRITER_STEP]["raised_by"] == "writer"


@pytest.mark.parametrize("status", node.STATUSES)
def test_notice_line_only_for_partial_and_failed(status: str, tmp_path: Path, monkeypatch) -> None:
    """Mutation: safety_notice_failed_only (NOTICE_STATUSES loses "partial")."""
    state = {**_failure_state(), "safety_signals": {"status": status, "reason": "r", "question_hash": QHASH,
                                                    "model": config.JEV_MODEL, "steps": []}}
    _, html = _through_render(state, tmp_path)
    assert (SAFETY_NOTICE in html) is (status in ("partial", "failed"))
    monkeypatch.setattr(config, "JEV_SAFETY_ENABLED", False)
    assert node.notice_needed({"status": "failed"}) is False


def test_notice_line_never_in_legacy_or_without_the_flag() -> None:
    brief = {"status": "ok", "try_first": [step("A step")], "candidates": [], "sources": []}
    args = {"identity": {}, "symptom": "s", "generated_at": "2026-09-29"}
    assert SAFETY_NOTICE in render_brief(brief, run={"safety_notice": True}, **args)
    assert SAFETY_NOTICE not in render_brief(brief, run={}, **args)
    assert SAFETY_NOTICE not in render_brief(brief, legacy=True, run={"safety_notice": True}, **args)


def test_not_recorded_is_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    """Mutation: safety_not_recorded_is_a_failure (a cassette with no recorded
    answer reads as a failed check, so replayed briefs gain the notice line)."""
    use_judge(monkeypatch, FakeJudge({}, default="not_recorded"))
    state = _failure_state()
    signals = node.check_draft(state, ctx_for(tmp_path))
    assert signals["status"] == "not_recorded" and signals["reason"] == node.REASON_NOT_RECORDED
    final, html = _through_render({**state, "safety_signals": signals}, tmp_path)
    assert SAFETY_NOTICE not in html
    assert safety_record(final)["status"] == "not_recorded"


@pytest.mark.parametrize("change,reason", [
    ({"status": "budget_stopped"}, node.REASON_BUDGET_STOPPED),
    ({"draft": None}, node.REASON_NO_DRAFT),
    ({"draft": ok_draft([])}, node.REASON_NO_STEPS),
])
def test_skips_without_a_call(change: dict, reason: str, tmp_path: Path, monkeypatch) -> None:
    """Mutation: safety_check_ignores_budget_stop (a budget stopped run still asks Jev)."""
    judge = FakeJudge({})
    built = use_judge(monkeypatch, judge)
    signals = node.check_draft({**_failure_state(), **change}, ctx_for(tmp_path))
    assert (signals["status"], signals["reason"], signals["steps"]) == ("skipped", reason, [])
    assert signals["question_hash"] and signals["model"] == config.JEV_MODEL
    assert built == [] and judge.calls == []


def test_disabled_makes_no_call(tmp_path: Path, monkeypatch) -> None:
    """Mutation: safety_check_ignores_disabled (JEV_SAFETY_ENABLED False still asks Jev)."""
    monkeypatch.setattr(config, "JEV_SAFETY_ENABLED", False)
    built = use_judge(monkeypatch, FakeJudge({}))
    signals = node.check_draft(_failure_state(), ctx_for(tmp_path))
    assert signals["status"] == "disabled" and built == []
    assert jev_reservation_usd(8) == 0.0


def test_retry_affordability_counts_the_jev_calls() -> None:
    """Mutations: retry_need_skips_jev (the retry's Jev calls are left out of the need),
    retry_need_one_jev_attempt (each Jev call counted at one attempt, not the reservation)."""
    # Each call reserves both attempts, as the judge does (a retried attempt may be billed).
    per_call = price_usage(config.PRICES_PER_MTOK[config.JEV_MODEL],
                           {"input_tokens": config.JEV_EST_INPUT_TOKENS * (1 + config.JEV_RETRY_MAX),
                            "output_tokens": 0})
    assert per_call > 0 and config.JEV_RETRY_MAX >= 1
    for mode in config.MODES:
        assert retry_need_usd(mode) == pytest.approx(
            config.RETRY_TYPICAL_USD + synthesize_reservation_usd(mode) + config.JEV_PLAN_STEPS_PER_PASS * per_call)


# ---------------------------------------------------------------------------
# Through the real graph
# ---------------------------------------------------------------------------


def _nodes(outcomes: list) -> list[str]:
    return [n for o in outcomes for n in o.nodes]


def test_retry_asks_jev_about_the_new_draft(tmp_path: Path, monkeypatch) -> None:
    """Mutation: safety_check_not_wired (synthesize goes straight to validate)."""
    from agent.nodes.persist import run_record_path
    from agent.tests.helpers import load_case, run_case

    judge = FakeJudge({})
    use_judge(monkeypatch, judge)
    _, ctx, outcomes = run_case(load_case("synthetic/retry_typical"), tmp_path)
    nodes = _nodes(outcomes)
    assert nodes.count("synthesize") == 2 and nodes.count("safety_check") == 2
    for i, name in enumerate(nodes):
        if name == "synthesize":
            assert nodes[i + 1:i + 3] == ["safety_check", "validate"]
    assert len(judge.calls) == 2
    assert outcomes[-1].state["safety_signals"]["status"] == "ran"
    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["safety"]["status"] == "ran"


def test_upgrade_pass_reuses_the_signals(tmp_path: Path, monkeypatch) -> None:
    """The upgrade pass revalidates without a new draft; the Jev answer is
    matched to the step by its text and still raises its flag.

    Mutation: upgrade_pass_drops_signals (validate reads no signals on the
    upgrade pass)."""
    from agent.nodes.persist import run_record_path
    from agent.tests.helpers import load_case
    from agent.tests.test_sc9_upgrades import run_sx

    layers(monkeypatch, ("jev",), 0.5)
    judge = FakeJudge({"Clean the bucket filter": 0.8})
    use_judge(monkeypatch, judge)
    ctx, outcomes = run_sx(load_case("synthetic/sx100_graph_top_up"), tmp_path, graph=True)
    state = outcomes[-1].state
    nodes = _nodes(outcomes)
    assert nodes.count("validate") == 2 and nodes.count("safety_check") == 1 and len(judge.calls) == 1
    assert judge.calls[0][1]["appliance"] == "dehumidifier"
    assert state["brief"]["upgrade_options"], "the upgrade pass added an option"
    assert flags(state["brief"]) == {"Clean the bucket filter": True}
    assert [p["raised_by"] for p in state["safety_provenance"]] == ["jev"]
    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["safety"]["steps"] == [{
        "step": "Clean the bucket filter", "step_sha256": step_key("Clean the bucket filter", ""),
        "writer_flag": False, "word_rule": False, "jev_noul": 0.8, "jev_error": None, "final_flag": True,
        "raised_by": "jev"}]
    assert record["safety"]["status"] == "ran"


def test_resume_after_a_crash_does_not_ask_jev_again(tmp_path: Path, monkeypatch) -> None:
    """A thread that stops after safety_check resumes from its checkpoint:
    validate runs again, Jev is not asked again.

    Mutation: safety_asked_in_validate (validate asks Jev itself, as an edge
    or an inline check would, so the resumed thread asks twice)."""
    from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end
    from agent.nodes import validate as validate_module
    from agent.tests.helpers import cassette_input, load_case, replay_ctx, resume_command

    judge = FakeJudge({})
    use_judge(monkeypatch, judge)
    real = validate_module.run_rules
    crashes: list[int] = []

    def crash_once(*args: Any, **kwargs: Any):
        if not crashes:
            crashes.append(1)
            raise RuntimeError("simulated crash in validate")
        return real(*args, **kwargs)

    monkeypatch.setattr(validate_module, "run_rules", crash_once)
    cassette = load_case("synthetic/retry_typical")
    ctx = replay_ctx(tmp_path, cassette, run_id="thread-1")
    graph = build_graph(open_checkpointer(tmp_path / "checkpoints.sqlite"))
    with pytest.raises(RuntimeError, match="simulated crash"):
        first = run_until_pause_or_end(graph, cassette_input(cassette, tmp_path), ctx, "thread-1")
        if first.paused:
            run_until_pause_or_end(graph, resume_command(cassette), ctx, "thread-1")
    assert len(judge.calls) == 1
    outcome = run_until_pause_or_end(graph, None, ctx, "thread-1")
    assert not outcome.paused
    assert outcome.nodes[0] == "validate"
    # The retry drafts again, so safety_check runs once more, on the new draft only.
    assert outcome.nodes.count("safety_check") == 1 and len(judge.calls) == 2


# ---------------------------------------------------------------------------
# Two copies of one step in a brief
# ---------------------------------------------------------------------------


def test_duplicate_steps_keep_their_own_provenance() -> None:
    """Two try_first steps with one key, only one flagged by the writer (no
    listed word, so the word rule stays off): the run record and SC12b's
    run_steps credit the writer on the flagged copy only.

    Mutations: pair_provenance_last_wins (the join keeps the last entry per
    key, so both copies get the unflagged copy's provenance);
    persist_safety_record_dict_join and sc12b_run_steps_dict_join (either call
    site goes back to its own last wins join)."""
    from agent.safety_eval import sc12b as sc12b_offline

    result = rules(ok_draft([step("Check the jets", flag=True), step("Check the jets", index=1)]))
    assert result.errors == []
    final = result.brief["try_first"]
    assert [s["safety_flag"] for s in final] == [True, False]
    expected = [("writer", True, True), (None, False, False)]
    record = safety_record({"brief": result.brief, "safety_provenance": result.safety_provenance,
                            "safety_signals": None})
    assert [(s["raised_by"], s["writer_flag"], s["final_flag"]) for s in record["steps"]] == expected

    run = {"run_id": "t-dup", "steps": [{"step": s["step"], "detail": s.get("detail"),
                                         "safety_flag": s["safety_flag"]} for s in final],
           "safety_provenance": result.safety_provenance}
    got = sc12b_offline.run_steps(run)
    assert [(s["provenance"].get("raised_by"), s["provenance"].get("writer_flag"), s["safety_flag"])
            for s in got] == expected
