"""SC2: the recorded v1 cases replayed through the whole graph (PLAN section 5).

Every case runs the real graph on ReplayChatModel and the replay stub inners
inside the real tool wrappers, with a SqliteSaver checkpoint, ledger, pages
and brief under tmp_path. v1 derived cassettes are read through
helpers.resolve_cassette_path: while they wait for the privacy approval the
tests skip, and under ADVISOR_GATE=2 or later that skip is a failure. The
synthetic FLO variant runs before the recorded case, so it runs either way.
Each test names the mutation that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent import config
from agent.ledger import Ledger
from agent.nodes.persist import run_record_path
from agent.rules.observed_code import normalize_code
from agent.tests.helpers import load_case, run_case

SC2_CASES = ("flo", "notheating", "hvac", "unknown", "blurry")


def final(outcomes: list) -> dict[str, Any]:
    return outcomes[-1].state


def cited_url(brief: dict[str, Any], index: int | None) -> str | None:
    return None if index is None else brief["sources"][index]["url"]


def assert_cited_like_v1(cassette, brief: dict[str, Any]) -> None:
    """Each item, matched by text to its recorded counterpart, cites the URL v1 cited for it."""
    draft = cassette.data["synthesize"][-1]["draft"]
    urls = cassette.expect["cited_urls"]
    steps = {s["step"]: s for s in brief["try_first"]}
    for i, recorded in enumerate(draft["try_first"]):
        assert cited_url(brief, steps[recorded["step"]]["source_index"]) == urls["try_first"][i], recorded["step"]
    kept = {c["documented_meaning"]: c for c in brief["candidates"]}
    checked = 0
    for i, recorded in enumerate(draft["candidates"]):
        if recorded["documented_meaning"] in kept:
            checked += 1
            got = cited_url(brief, kept[recorded["documented_meaning"]]["source_index"])
            assert got == urls["candidates"][i], recorded["documented_meaning"]
    assert checked == len(brief["candidates"])
    if "warranty_caution" in urls and draft.get("warranty_caution"):
        assert cited_url(brief, brief["warranty_caution"]["source_index"]) == urls["warranty_caution"]
    if urls.get("warranty_cautions") and draft.get("warranty"):
        cautions = {c["text"]: c for c in brief["warranty"]["cautions"]}
        for i, recorded in enumerate(draft["warranty"]["cautions"]):
            got = cited_url(brief, cautions[recorded["text"]]["source_index"])
            assert got == urls["warranty_cautions"][i], recorded["text"]


def assert_every_index_resolves(brief: dict[str, Any]) -> None:
    n = len(brief["sources"])
    for key in ("try_first", "candidates", "upgrade_options", "maintenance_due"):
        for entry in brief[key]:
            assert 0 <= entry["source_index"] < n, (key, entry)
    cautions = list((brief.get("warranty") or {}).get("cautions") or [])
    if brief.get("warranty_caution"):
        cautions.append(brief["warranty_caution"])
    for caution in cautions:
        assert caution["source_index"] is None or 0 <= caution["source_index"] < n


def assert_one_confirmed_flo(state: dict[str, Any]) -> dict[str, Any]:
    assert state["status"] == "ok"
    brief = state["brief"]
    assert brief["status"] == "ok"
    assert len(brief["candidates"]) == 1
    [candidate] = brief["candidates"]
    assert normalize_code(candidate["code"]) == "FLO"
    assert candidate["confirmed"] is True
    assert brief["observed_code"] == "FLO"
    return brief


def test_flo_gives_one_confirmed_candidate(tmp_path) -> None:
    """Mutations: sc2_narrowing_off (the variant keeps three candidates);
    sc2_model_confirmed_kept (the variant ends with none confirmed);
    sc2_remap_shift_left and sc2_remap_shift_right (a citation lands on a decoy
    or the wrong real source)."""
    variant = load_case("synthetic/flo_three_candidates")
    assert "synthetic" in " ".join(variant.provenance["synthetic_fields"])
    scripted = variant.data["synthesize"][0]["draft"]["candidates"]
    assert [c["confirmed"] for c in scripted] == [False, False, False]
    _, _, outcomes = run_case(variant, tmp_path / "variant")
    assert outcomes[0].paused and not outcomes[-1].paused
    brief = assert_one_confirmed_flo(final(outcomes))
    assert_cited_like_v1(variant, brief)

    recorded = load_case("flo")
    _, _, outcomes = run_case(recorded, tmp_path / "recorded")
    brief = assert_one_confirmed_flo(final(outcomes))
    assert final(outcomes)["status"] != "budget_stopped"
    assert_cited_like_v1(recorded, brief)


def test_vague_symptom_gives_multiple_candidates(tmp_path) -> None:
    """Mutations: sc2_safety_rejects_instead_of_reorders (the run falls to a
    refusal); sc2_remap_shift_left and sc2_remap_shift_right."""
    cassette = load_case("notheating")
    draft = cassette.data["synthesize"][-1]["draft"]
    flags = [s["safety_flag"] for s in draft["try_first"]]
    assert True in flags and flags != sorted(flags, reverse=True), "the recorded order put a safety step late"
    _, _, outcomes = run_case(cassette, tmp_path)
    state = final(outcomes)
    assert state["status"] == "ok"
    brief = state["brief"]
    assert len(brief["candidates"]) >= 2
    assert not any(c["confirmed"] for c in brief["candidates"])
    safety = [s["safety_flag"] for s in brief["try_first"]]
    assert safety == sorted(safety, reverse=True), "safety steps first"
    assert_cited_like_v1(cassette, brief)


def test_hvac_grounded_with_no_invented_codes(tmp_path) -> None:
    """The synthetic grounding variant (Phase 3), then the recorded half.

    Variant: a code absent from the cited source's text fails validation
    twice, the run ends a forced NO RELIABLE ANSWER (not budget_stopped), and
    the invented code appears nowhere in the final brief or its HTML. Recorded:
    every code null, every index resolves, and the grounding check reports
    "unverifiable" because the cassette's provenance names a v1 lookup (v1
    recorded no page text; decision 25).

    Mutations: sc2_candidate_code_invented (a code appears on a candidate);
    sc2_remap_shift_left (an index no longer resolves, or the run fails);
    sc2_grounding_hook_off (the variant's invented code passes);
    sc2_grounding_exempt_on_mode_only (the exemption keyed on replay alone, so
    the synthetic variant passes as unverifiable); sc2_refusal_echoes_errors
    (the forced refusal quotes the error text, code and all);
    sc2_grounding_status_not_in_state (validate drops rule 4's report, so the
    recorded half cannot say "unverifiable")."""
    variant = load_case("synthetic/hvac_invented_code")
    invented = variant.expect["invented_code"]
    assert variant.provenance["derived_from"] is None
    assert all(c["code"] == invented for a in variant.data["synthesize"] for c in a["draft"]["candidates"])
    _, vctx, voutcomes = run_case(variant, tmp_path / "variant")
    vstate = final(voutcomes)
    assert vstate["status"] == "no_reliable_answer"
    assert vstate["status"] != "budget_stopped" and vstate.get("stop_reason") is None
    assert vstate["refusal_origin"] == "forced"
    assert vstate["validation_failures"] == 2
    assert all(e.startswith("code_not_grounded") for e in vstate["validation_errors"])
    assert vstate["grounding_status"] == "failed"
    ran = [n for o in voutcomes for n in o.nodes]
    assert ran.count("synthesize") == 2 and "refuse" in ran
    vbrief = vstate["brief"]
    assert vbrief["candidates"] == [] and vbrief["try_first"] == []
    assert invented not in json.dumps(vbrief)
    assert invented not in Path(vstate["html_path"]).read_text(encoding="utf-8")

    cassette = load_case("hvac")
    _, ctx, outcomes = run_case(cassette, tmp_path / "recorded")
    state = final(outcomes)
    assert state["status"] == "ok"
    brief = state["brief"]
    assert brief["candidates"]
    assert all(c["code"] is None for c in brief["candidates"])
    assert_every_index_resolves(brief)
    assert_cited_like_v1(cassette, brief)
    assert cassette.derived_from_v1
    assert state["grounding_status"] == "unverifiable"
    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["grounding_status"] == "unverifiable"


def test_fabricated_model_gives_no_reliable_answer(tmp_path) -> None:
    """Mutations: sc2_searched_from_titles (validate fills searched with source
    titles); sc2_searched_empty; sc2_model_refusal_reported_forced."""
    cassette = load_case("unknown")
    _, _, outcomes = run_case(cassette, tmp_path)
    state = final(outcomes)
    assert state["status"] == "no_reliable_answer"
    brief = state["brief"]
    assert brief["candidates"] == [] and brief["try_first"] == []
    trail_queries = [e["query"] for e in state["search_trail"] if e["tool"] == "search"]
    searched = brief["no_reliable_answer"]["searched"]
    assert searched == trail_queries == cassette.expect["searched"]
    assert searched != cassette.expect["v1_searched"]
    assert state["refusal_origin"] == "model"


def test_blurry_plate_halts_at_confirmation(tmp_path) -> None:
    """Mutations: sc2_confirm_proceeds_without_model (confirm_identity passes an
    extraction with no model through); nodes_read_plate_keeps_guesses."""
    cassette = load_case("blurry")
    _, ctx, outcomes = run_case(cassette, tmp_path, resume=False)
    [ask] = outcomes
    assert ask.paused
    payload = ask.interrupt
    assert payload["identity"]["model"] is None
    assert "model" in payload["missing"]
    assert payload["extraction"]["confidence"]["model"] == "unreadable"
    models = ctx.replay.get("models", {})
    assert "research" not in models and "synthesize" not in models
    assert getattr(ctx.replay.get("stubs", {}).get("search"), "calls", 0) == 0
    assert "research" not in ask.state.get("latency", {})
    assert {r["node"] for r in Ledger(ctx.ledger_path).rows(ctx.run_id)} == {"read_plate"}


@pytest.mark.parametrize("case", SC2_CASES)
def test_no_sc2_case_ends_budget_stopped(case: str, tmp_path) -> None:
    """Mutations: sc2_replay_priced_at_opus (replay rows priced at Opus rates);
    sc2_replay_run_cap_lowered (the cap sits below a typical pass)."""
    cassette = load_case(case)
    assert cassette.caps is None, "SC2 cases run under REPLAY_RUN_CAP_USD"
    _, ctx, outcomes = run_case(cassette, tmp_path)
    state = final(outcomes)
    assert state["status"] != "budget_stopped"
    assert state.get("stop_reason") is None
    ledger = Ledger(ctx.ledger_path)
    assert not [r for r in ledger.rows(ctx.run_id) if r["kind"] == "stop"]
    assert ledger.run_total(ctx.run_id) <= config.REPLAY_RUN_CAP_USD
    if case == "blurry":
        assert outcomes[-1].paused
    else:
        assert state["status"] == cassette.expect["status"]
