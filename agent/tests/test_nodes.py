"""Unit tests for the model and plumbing nodes on ReplayChatModel.

Each node runs against a temp ledger, a temp registry and a small synthetic
cassette built in the test. confirm_identity runs inside a two node graph with
an in memory checkpointer, because interrupt() needs one. Each test names the
mutation that turns it red.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import ValidationError

from agent import config
from agent.ledger import Ledger
from agent.nodes import confirm_identity as confirm_mod
from agent.nodes import render as render_mod
from agent.nodes import synthesize as synth_mod
from agent.nodes.confirm_identity import confirm_identity, next_after_confirm
from agent.nodes.gather import gather, next_after_gather
from agent.nodes.intake import intake, intake_input, observed_code_candidate
from agent.nodes.read_plate import read_plate
from agent.nodes.render import render
from agent.nodes.route import route
from agent.nodes.synthesize import PARSE_ERROR_PREFIX, build_sources_block, synthesize
from agent.registry import open_registry
from agent.replay.cassettes import cassette_from_dict
from agent.state import AdvisorState, RunContext, check_json_native

USAGE = {"input_tokens": 2000, "output_tokens": 100}
EXTRACTION_CLEAR = {
    "manufacturer": "SUNDANCE SPAS",
    "model": "OPTIMA 880",
    "serial": "100000001",
    "manufacture_date": "06/2014",
    "confidence": {"manufacturer": "high", "model": "high", "serial": "high", "manufacture_date": "high"},
}
# Synthetic: the model guessed text for fields it marked unreadable (SC4 variant shape).
EXTRACTION_GUESSED = {
    "manufacturer": "SUNDANCE SPAS",
    "model": "OPTIMA 88?",
    "serial": "1009",
    "manufacture_date": "06/2014",
    "confidence": {"manufacturer": "low", "model": "unreadable", "serial": "unreadable",
                   "manufacture_date": "high"},
}


def _draft(**overrides: Any) -> dict[str, Any]:
    draft = {
        "status": "ok",
        "matched_identity": "Synthetic Model X",
        "warranty_caution": None,
        "happened_before": None,
        "try_first": [{"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": 0}],
        "candidates": [{"code": "FLO", "documented_meaning": "Low flow", "documented_action": "Clean filter",
                        "who": "anyone", "why_shown": "", "source_index": 0, "confirmed": False,
                        "evidence": ""}],
        "warranty": None,
        "no_reliable_answer": None,
        "upgrade_options": [],
        "maintenance_due": [],
        "source_tiers": [{"source_index": 0, "tier": "dealer", "authorship_quote": ""}],
    }
    draft.update(overrides)
    return draft


def _cassette(*, read_plate: dict | None = None, drafts: list[dict] = (), caps: dict | None = None):
    return cassette_from_dict({
        "cassette_version": 1,
        "case": "nodes_unit",
        "provenance": {
            "derived_from": None,
            "copied_fields": [],
            "synthetic_fields": ["everything: synthetic unit test cassette"],
            "built_by": "agent/tests/test_nodes.py",
            "reviewed": None,
        },
        "input": {"identity": None, "symptom": "panel shows FLO", "plate_sha256": None},
        "read_plate": None if read_plate is None else {"extraction": read_plate, "usage": USAGE},
        "resume": None,
        "research": {"script": [], "tool_results": []},
        "synthesize": [{"attempt": i + 1, "draft": d, "usage": USAGE} for i, d in enumerate(drafts)],
        "page_texts": {},
        "caps": caps,
        "expect": {"note": "unit test"},
    })


@pytest.fixture()
def make_ctx(tmp_path: Path):
    def make(cassette=None, run_id: str = "run-unit") -> RunContext:
        return RunContext(
            run_id=run_id,
            mode="replay",
            ledger_path=tmp_path / "ledger" / "replay_ledger.sqlite",
            registry_path=tmp_path / "registry.sqlite",
            graph_path=tmp_path / "graph.json",
            pages_dir=tmp_path / "pages",
            cassette=cassette,
            caps=cassette.caps if cassette is not None else None,
        )
    return make


@pytest.fixture()
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "plate.jpg"
    path.write_bytes(b"\xff\xd8synthetic plate bytes, not an image\xff\xd9")
    return path


def _rt(ctx: RunContext) -> Runtime:
    return Runtime(context=ctx)


def _charges(ctx: RunContext, node: str) -> list[dict]:
    return [r for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["kind"] == "charge" and r["node"] == node]


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------


def test_intake_rejects_non_string_identity(make_ctx) -> None:
    """Mutation nodes_intake_stringifies_identity: intake str()s identity values; 880 passes."""
    state = intake_input(symptom="not heating",
                         identity={"manufacturer": "Sundance Spas", "model": 880, "serial": "", "manufacture_date": ""})
    with pytest.raises(ValidationError):
        intake(state, _rt(make_ctx()))


def test_intake_sets_run_mode_and_photo_hash_without_bytes(make_ctx, photo: Path) -> None:
    """Mutation nodes_intake_photo_base64: photo keeps the file bytes in state."""
    ctx = make_ctx(run_id="run-photo")
    out = intake(intake_input(symptom="panel shows FLO", photo_path=str(photo)), _rt(ctx))
    assert out["run_id"] == "run-photo" and out["mode"] == "replay"
    assert out["photo"] == {"path": str(photo.resolve()), "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()}
    assert out["identity"] is None and out["observed_code"] is None
    assert out["observed_code_candidate"] == "FLO"
    check_json_native(out)


def test_intake_loads_the_appliance_from_the_registry(make_ctx) -> None:
    """Mutation nodes_intake_skips_property_check: an appliance at another property is accepted."""
    ctx = make_ctx()
    open_registry(ctx.registry_path)
    out = intake(intake_input(symptom="not heating", appliance_id="appl-optima880"), _rt(ctx))
    assert out["property_id"] == "prop-a"
    assert out["identity"]["manufacturer"] == "Sundance Spas" and out["identity"]["model"] == "Optima 880"
    with pytest.raises(ValueError, match="unknown appliance"):
        intake(intake_input(symptom="x", appliance_id="appl-missing"), _rt(ctx))
    with pytest.raises(ValueError, match="not at property"):
        intake(intake_input(symptom="x", appliance_id="appl-optima880", property_id="prop-b"), _rt(ctx))


def test_intake_code_flag_wins_over_candidate(make_ctx) -> None:
    """Mutation nodes_intake_candidate_wins: the symptom candidate replaces --code."""
    identity = {"manufacturer": "M", "model": "X", "serial": "", "manufacture_date": ""}
    out = intake(intake_input(symptom="panel shows FLO", identity=identity, code=" OH "), _rt(make_ctx()))
    assert out["observed_code"] == "OH"
    assert out["identity"] == {"manufacturer": "M", "model": "X", "serial": None, "manufacture_date": None}


@pytest.mark.parametrize("symptom,expected", [
    ("panel shows FLO", "FLO"),
    ("PANEL SHOWS FLO", "FLO"),
    ("display shows \"OH\" and beeps", "OH"),
    ("ERROR CODE E3", "E3"),
    ("Error code: E5 flashing", "E5"),
    ("topside displays 'ICE'", "ICE"),
    ("screen says 'HL'", "HL"),
    ("not heating", None),
    ("AC not cooling upstairs", None),
    ("shows flo", None),
    ("shows F", None),
    ("shows ABCDEFG", None),
])
def test_observed_code_candidate(symptom: str, expected: str | None) -> None:
    """Mutation nodes_candidate_consumes_token: the keyword match consumes its token; "ERROR CODE E3" gives None."""
    assert observed_code_candidate(symptom) == expected


# ---------------------------------------------------------------------------
# read_plate
# ---------------------------------------------------------------------------


def test_read_plate_nulls_unreadable_fields_the_model_guessed(make_ctx, photo: Path) -> None:
    """Mutation nodes_read_plate_keeps_guesses: the unreadable nulling line is cut."""
    ctx = make_ctx(_cassette(read_plate=EXTRACTION_GUESSED))
    state = intake(intake_input(symptom="not heating", photo_path=str(photo)), _rt(ctx))
    out = read_plate(state, _rt(ctx))
    ext = out["extraction"]
    assert ext["model"] is None and ext["serial"] is None
    assert ext["manufacturer"] == "SUNDANCE SPAS" and ext["manufacture_date"] == "06/2014"
    assert ext["confidence"]["model"] == "unreadable"
    check_json_native(out)


def test_read_plate_charged_once_with_the_image_estimate(make_ctx, photo: Path) -> None:
    """Mutations nodes_read_plate_no_image_estimate (images=0) and nodes_paid_call_not_charged."""
    ctx = make_ctx(_cassette(read_plate=EXTRACTION_CLEAR))
    state = intake(intake_input(symptom="panel shows FLO", photo_path=str(photo)), _rt(ctx))
    out = read_plate(state, _rt(ctx))
    assert out["extraction"]["model"] == "OPTIMA 880"
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"]
    assert rows[0]["input_tokens"] >= config.IMAGE_TOKENS_ESTIMATE
    assert len(_charges(ctx, "read_plate")) == 1
    assert out["cost_usd"] == pytest.approx(Ledger(ctx.ledger_path).run_total(ctx.run_id))
    model = ctx.replay["models"]["read_plate"]
    assert model.cursor == 1
    sent = model.received[0][1].content
    assert any(block.get("type") == "image" for block in sent)


def test_read_plate_skipped_when_identity_typed(make_ctx) -> None:
    """Mutation nodes_read_plate_never_skips: the typed identity check is cut; the script runs dry."""
    ctx = make_ctx(_cassette(read_plate=None))
    identity = {"manufacturer": "M", "model": "X", "serial": "", "manufacture_date": ""}
    state = intake(intake_input(symptom="not heating", identity=identity), _rt(ctx))
    out = read_plate(state, _rt(ctx))
    assert set(out) == {"latency"}
    assert "models" not in ctx.replay
    assert not Path(ctx.ledger_path).exists()


# ---------------------------------------------------------------------------
# confirm_identity (interrupt semantics need a checkpointer)
# ---------------------------------------------------------------------------


def _confirm_graph():
    builder = StateGraph(AdvisorState, context_schema=RunContext)
    builder.add_node("confirm_identity", confirm_identity)
    builder.add_node("route", route)
    builder.add_edge(START, "confirm_identity")
    builder.add_conditional_edges("confirm_identity", next_after_confirm, ["confirm_identity", "route"])
    builder.add_edge("route", END)
    return builder.compile(checkpointer=InMemorySaver())


def _run(graph, value, ctx: RunContext, thread: str):
    return graph.invoke(
        value, context=ctx, durability="sync", version="v2",
        config={"configurable": {"thread_id": thread}, "recursion_limit": config.RECURSION_LIMIT},
    )


def test_confirm_identity_interrupt_payload(make_ctx) -> None:
    """Mutation nodes_confirm_payload_no_missing: missing is always []."""
    graph = _confirm_graph()
    extraction = dict(EXTRACTION_GUESSED, model=None, serial=None)
    out = _run(graph, {"extraction": extraction, "identity": None, "observed_code": "FLO"}, make_ctx(), "t1")
    assert len(out.interrupts) == 1
    payload = out.interrupts[0].value
    assert set(payload) == {"extraction", "identity", "observed_code_candidate", "missing", "prompt"}
    assert payload["extraction"] == extraction
    assert payload["identity"] == {"manufacturer": "SUNDANCE SPAS", "model": None, "serial": None,
                                   "manufacture_date": "06/2014"}
    assert payload["observed_code_candidate"] == "FLO"
    assert payload["missing"] == ["model", "serial"]
    assert payload["prompt"] == confirm_mod.FIRST_PROMPT
    json.dumps(payload)


def test_confirm_identity_loops_until_a_model_is_given(make_ctx) -> None:
    """Mutation nodes_confirm_accepts_empty_model: the missing model check is cut; the run moves on."""
    graph, ctx = _confirm_graph(), make_ctx()
    extraction = dict(EXTRACTION_GUESSED, model=None, serial=None)
    _run(graph, {"extraction": extraction, "identity": None, "observed_code": "FLO"}, ctx, "t2")
    edited = {"manufacturer": "Sundance Spas", "model": "", "serial": "", "manufacture_date": ""}
    again = _run(graph, Command(resume={"identity": edited, "observed_code": "FLO"}), ctx, "t2")
    assert len(again.interrupts) == 1, "a resume without a model must stay paused"
    payload = again.interrupts[0].value
    assert payload["prompt"] == confirm_mod.MISSING_MODEL_PROMPT
    assert payload["identity"]["manufacturer"] == "Sundance Spas" and payload["missing"][0] == "model"
    assert graph.get_state({"configurable": {"thread_id": "t2"}}).values["identity_confirmed"] is False
    done = _run(graph, Command(resume={"identity": dict(edited, model="Optima 880"), "observed_code": "FLO"}),
                ctx, "t2")
    assert done.interrupts == ()
    assert done.value["identity_confirmed"] is True
    assert done.value["identity"]["model"] == "Optima 880" and done.value["identity"]["serial"] is None
    assert done.value["observed_code"] == "FLO" and done.value["confirm_prompt"] is None
    assert done.value["route"] == ["research"]


def test_confirm_identity_typed_identity_needs_no_pause(make_ctx) -> None:
    """Mutation nodes_confirm_always_pauses: the typed identity pass through is cut."""
    identity = {"manufacturer": "Trane", "model": "XR16 4TTR6036", "serial": None, "manufacture_date": None}
    out = _run(_confirm_graph(), {"identity": identity, "extraction": None, "observed_code": None}, make_ctx(), "t3")
    assert out.interrupts == ()
    assert out.value["identity_confirmed"] is True and out.value["identity"] == identity


def test_confirm_identity_resume_code_rules(make_ctx) -> None:
    """Mutation nodes_confirm_code_absent_clears: a resume without observed_code drops the candidate."""
    graph, ctx = _confirm_graph(), make_ctx()
    start = {"extraction": EXTRACTION_CLEAR, "identity": None, "observed_code": "FLO"}
    identity = {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "", "manufacture_date": ""}
    _run(graph, start, ctx, "t4")
    kept = _run(graph, Command(resume={"identity": identity}), ctx, "t4")
    assert kept.value["observed_code"] == "FLO"
    _run(graph, start, ctx, "t5")
    cleared = _run(graph, Command(resume={"identity": identity, "observed_code": "  "}), ctx, "t5")
    assert cleared.value["observed_code"] is None


# ---------------------------------------------------------------------------
# route and gather
# ---------------------------------------------------------------------------


def test_route_is_research_only_in_phase_2(make_ctx) -> None:
    """With nothing in the graph, route is research alone (route table row 5).

    Mutation nodes_route_empty: row 5 returns no branch."""
    out = route({}, _rt(make_ctx()))
    assert out["route"] == ["research"]
    assert "Route table row 5" in out["route_reason"]


def test_gather_sends_a_budget_stop_to_validate(make_ctx) -> None:
    """Mutation nodes_gather_ignores_budget_stop: next_after_gather always says synthesize."""
    assert set(gather({}, _rt(make_ctx()))) == {"latency"}
    assert next_after_gather({"status": "budget_stopped"}) == "validate"
    assert next_after_gather({"status": "running"}) == "synthesize"


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------


def _sources(n: int = 2, text: str = "Remove the filter; if FLO clears, the filter is clogged.") -> list[dict]:
    return [
        {"source_id": f"s{i}", "url": f"https://example.com/manual-{i}", "host": "example.com",
         "title": f"Synthetic manual {i}", "retrieved_at": "2026-09-18", "origin": "search",
         "text_sha256": None, "snippet": text}
        for i in range(n)
    ]


def _synth_state(**extra: Any) -> dict[str, Any]:
    state = {
        "run_id": "run-unit", "mode": "replay", "symptom": "panel shows FLO",
        "identity": {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": None, "manufacture_date": None},
        "observed_code": "FLO", "sources": _sources(), "search_trail": [], "validation_errors": [],
    }
    state.update(extra)
    return state


def test_synthesize_writes_a_draft_and_shows_numbered_sources(make_ctx) -> None:
    """Mutation nodes_synth_urls_not_hosts: the source header shows the URL instead of the host."""
    ctx = make_ctx(_cassette(drafts=[_draft()]))
    out = synthesize(_synth_state(), _rt(ctx))
    assert out["draft"]["status"] == "ok" and out["draft"]["candidates"][0]["code"] == "FLO"
    assert "validation_errors" not in out
    check_json_native(out)
    user = ctx.replay["models"]["synthesize"].received[0][1].content
    assert "[0] Synthetic manual 0\nHost: example.com\nExcerpts:\nRemove the filter" in user
    assert "https://example.com/manual-0" not in user
    assert len(_charges(ctx, "synthesize")) == 1


def test_synthesize_parse_failure_is_a_validation_failure_and_is_charged(make_ctx) -> None:
    """Mutations nodes_synth_parse_failure_empty_draft (draft {} on a parse error) and nodes_paid_call_not_charged."""
    ctx = make_ctx(_cassette(drafts=[{"status": "ok", "candidates": "not a list"}]))
    out = synthesize(_synth_state(), _rt(ctx))
    assert out["draft"] is None
    assert len(out["validation_errors"]) == 1 and out["validation_errors"][0].startswith(PARSE_ERROR_PREFIX)
    charges = _charges(ctx, "synthesize")
    assert len(charges) == 1 and charges[0]["input_tokens"] == USAGE["input_tokens"]
    assert out["cost_usd"] == pytest.approx(charges[0]["usd"]) and out["cost_usd"] > 0
    assert "status" not in out


def test_synthesize_sees_prior_errors_on_a_retry(make_ctx) -> None:
    """Mutation nodes_synth_drops_prior_errors: synthesis_messages passes prior_errors=[]."""
    ctx = make_ctx(_cassette(drafts=[_draft(), _draft()]))
    synthesize(_synth_state(), _rt(ctx))
    error = "candidates[0].source_index 9 is not a source of this run"
    synthesize(_synth_state(validation_errors=[error]), _rt(ctx))
    first, second = (msgs[1].content for msgs in ctx.replay["models"]["synthesize"].received)
    assert error not in first
    assert f"- {error}" in second


def test_synthesize_budget_stop_makes_no_call(make_ctx) -> None:
    """Mutation nodes_synth_no_budget_catch: BudgetExceeded escapes the node."""
    ctx = make_ctx(_cassette(drafts=[_draft()], caps={"run_cap_usd": 0.0001}))
    out = synthesize(_synth_state(), _rt(ctx))
    assert out["status"] == "budget_stopped" and out["stop_reason"] == "run_cap"
    assert out["draft"] is None
    assert ctx.replay.get("models", {}).get("synthesize") is None or \
        ctx.replay["models"]["synthesize"].received == []
    assert _charges(ctx, "synthesize") == []


def test_sources_block_is_cut_to_the_token_budget(tmp_path: Path) -> None:
    """Mutation nodes_synth_no_truncation: texts are not cut to their share."""
    budget = config.SYNTH_SOURCE_TOKEN_BUDGET * config.CHARS_PER_TOKEN_ESTIMATE
    long_text = "x" * budget
    sources = _sources(3, text=long_text) + _sources(1, text="short and whole")
    block = build_sources_block(sources, tmp_path)
    assert len(block) <= budget
    for i in range(3):
        assert f"[{i}] Synthetic manual {i}" in block
    assert "[3] Synthetic manual 0\nHost: example.com\nExcerpts:\nshort and whole" in block
    assert block.count("x" * 1000) >= 3, "each long source keeps a fair share"


def test_sources_block_prefers_excerpts_then_page_text(tmp_path: Path) -> None:
    """Mutation nodes_synth_snippet_first: the snippet is used even when page text exists."""
    page = "Verbatim page text from the stored raw content."
    sha = hashlib.sha256(page.encode()).hexdigest()
    (tmp_path / f"{sha}.txt").write_text(page, encoding="utf-8")
    with_page = dict(_sources(1)[0], text_sha256=sha)
    with_excerpts = dict(_sources(1)[0], excerpts=["first window", "second window"])
    assert synth_mod.source_text(with_page, tmp_path) == page
    assert synth_mod.source_text(with_excerpts, tmp_path) == "first window\n...\nsecond window"
    assert synth_mod.source_text(_sources(1)[0], tmp_path).startswith("Remove the filter")


# ---------------------------------------------------------------------------
# render node
# ---------------------------------------------------------------------------


def _ok_brief() -> dict[str, Any]:
    return {"status": "ok", "sources": [{"url": "https://example.com/m", "tier": "dealer", "host": "example.com"}],
            "try_first": [], "candidates": []}


def test_render_writes_to_the_state_path_or_the_default(make_ctx, tmp_path: Path, monkeypatch) -> None:
    """Mutation nodes_render_ignores_state_path: the node always writes the default path."""
    monkeypatch.setattr(config, "BRIEFS_OUT_DIR", tmp_path / "briefs_out")
    state = {"run_id": "run-r", "brief": _ok_brief(), "status": "ok", "symptom": "s", "identity": None}
    out = render(state, _rt(make_ctx()))
    assert out["html_path"] == str(tmp_path / "briefs_out" / "run-r.html")
    chosen = tmp_path / "chosen" / "b.html"
    out = render(dict(state, html_path=str(chosen)), _rt(make_ctx()))
    assert out["html_path"] == str(chosen) and chosen.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_render_refuses_the_published_pages(make_ctx) -> None:
    """Mutation nodes_render_no_published_check: inside_published always returns False."""
    for rel in ("briefs/x.html", "src/x.html", "index.html", "BRIEFS/y.html"):
        state = {"run_id": "r", "brief": _ok_brief(), "status": "ok", "symptom": "s",
                 "html_path": str(config.REPO_ROOT / rel)}
        with pytest.raises(ValueError, match="published"):
            render(state, _rt(make_ctx()))
        assert render_mod.inside_published(config.REPO_ROOT / rel)


def test_render_budget_stop_without_a_brief(make_ctx, tmp_path: Path) -> None:
    """Mutation nodes_render_budget_brief_keeps_candidates: the budget brief carries a candidate."""
    state = {"run_id": "r", "brief": None, "status": "budget_stopped", "stop_reason": "run_cap",
             "symptom": "s", "identity": None, "sources": _sources(1),
             "search_trail": [{"query": "optima 880 flo", "n_results": 5, "credits": 1, "at": "t", "status": "ok"}],
             "html_path": str(tmp_path / "b.html")}
    out = render(state, _rt(make_ctx()))
    brief = out["brief"]
    assert brief["status"] == "budget_stopped"
    assert brief["candidates"] == [] and brief["try_first"] == []
    assert brief["upgrade_options"] == [] and brief["maintenance_due"] == []
    assert [s["url"] for s in brief["sources"]] == ["https://example.com/manual-0"]
    page = Path(out["html_path"]).read_text(encoding="utf-8")
    assert "optima 880 flo" in page and "run_cap" in page
    check_json_native(out)
    with pytest.raises(ValueError, match="needs a brief"):
        render(dict(state, status="ok"), _rt(make_ctx()))


def test_sources_block_never_cuts_a_word(tmp_path: Path) -> None:
    """Mutations synth_block_cut_mid_word and synth_share_cut_mid_word: fitting the
    sources to the budget cuts a word in half, and the model copies the cut text
    into the brief (the Phase 6 "deactivated and filte")."""
    from agent.nodes.synthesize import build_sources_block, cut_at_word

    assert cut_at_word("The heater is deactivated and filter", 34) == "The heater is deactivated and"
    assert cut_at_word("short text", 50) == "short text"
    assert cut_at_word("one two", 3) == "one"
    assert cut_at_word("unbroken", 4) == "unbr"  # no boundary exists, so the plain cut stands
    budget = config.SYNTH_SOURCE_TOKEN_BUDGET * config.CHARS_PER_TOKEN_ESTIMATE
    words = " ".join(f"word{i:05d}" for i in range(budget // 5))
    sources = [{"title": f"Synthetic page {i}", "url": f"https://example.com/{i}", "host": "example.com",
                "excerpts": [words]} for i in range(3)]
    block = build_sources_block(sources, tmp_path)
    assert len(block) <= budget
    for part in block.split("\n\n"):
        last = part.split()[-1]
        assert re.fullmatch(r"word\d{5}", last) or last == "Excerpts:", last
