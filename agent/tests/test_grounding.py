"""Code grounding (PLAN 8.5 rule 4, decision 25).

A candidate code must appear in its cited source's raw text, or in the
snippet when no raw text was recorded; "unverifiable" is allowed only in
replay of a cassette whose provenance names a v1 lookup. Page and snippet
texts are synthetic, on example.com URLs (decision 15). Each test names, in
its docstring, the mutation that turns it red.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from agent.nodes.research import build_sources
from agent.replay.cassettes import load_cassette
from agent.rules import grounding
from agent.rules.grounding import (
    FAILED,
    NOT_APPLICABLE,
    NOT_CHECKED,
    UNVERIFIABLE,
    VERIFIED,
    check_grounding,
)
from agent.rules.pipeline import run_rules
from agent.state import RunContext
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR, load_case, run_case

URL = "https://example.com/synthetic/spa/textless"
SOURCE = {"source_id": "src-t", "url": URL, "title": "Synthetic textless page", "origin": "search"}
SNIPPET_WITHOUT = "Synthetic snippet: the heater senses no water flow."
SNIPPET_WITH = "Synthetic snippet: code E7 means the heater senses no water flow."
RAW_WITH = "Synthetic page text. Code E7: the heater senses no water flow. Code E10: high limit."
NON_V1 = {"derived_from": None, "synthetic_fields": ["everything"]}
V1 = {"derived_from": "v1 lookup 549892815cb6", "synthetic_fields": []}


def grounded(code: str, *, page_text: str | None, snippet: str | None, mode: str = "replay",
             provenance: dict | None = None, **kw: Any):
    return check_grounding(code, SOURCE, page_text=page_text, snippet=snippet, mode=mode,
                           provenance=provenance, **kw)


def draft(code: str | None, evidence: str = "") -> dict[str, Any]:
    return {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [{"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": 0}],
        "candidates": [{"code": code, "documented_meaning": "No flow", "documented_action": "Clean the filter",
                        "who": "technician", "why_shown": "", "source_index": 0, "confirmed": False,
                        "evidence": evidence}],
        "warranty": None, "no_reliable_answer": None, "upgrade_options": [], "maintenance_due": [],
        "source_tiers": [],
    }


def rules(code: str | None, *, snippet: str | None, mode: str, provenance: dict | None,
          page_texts: dict[str, str] | None = None, observed: str | None = None):
    sources = [{**SOURCE, "retrieved_at": "2026-09-18", "text_sha256": None, "snippet": snippet}]
    return run_rules(draft(code), sources=sources, observed_code=observed, history_hits=[], registry=None,
                     mode=mode, provenance=provenance, pass_kind="synthesize", page_texts=page_texts)


def test_code_on_textless_source_fails_outside_v1_cassettes(tmp_path: Path) -> None:
    """Mutation grounding_exempt_when_no_text: the exemption is keyed on "no
    text recorded" instead of replay mode and v1 provenance."""
    # Unit: every mode and provenance other than a v1 derived replay fails.
    for mode, prov in (("replay", NON_V1), ("replay", None), ("cheap", None), ("full", None), ("cheap", V1)):
        result = grounded("E7", page_text=None, snippet=SNIPPET_WITHOUT, mode=mode, provenance=prov)
        assert (result.status, result.passes) == (FAILED, False), (mode, prov)
        assert grounded("E7", page_text=None, snippet=None, mode=mode, provenance=prov).status == FAILED
    assert grounded("E7", page_text=None, snippet=SNIPPET_WITH, mode="cheap").status == VERIFIED
    assert grounded("E7", page_text=RAW_WITH, snippet=None, mode="cheap").status == VERIFIED
    assert grounded("E1", page_text=RAW_WITH, snippet=None, mode="cheap").status == FAILED  # not inside E10
    assert grounded("e7", page_text=RAW_WITH, snippet=None, mode="cheap").status == FAILED  # case sensitive
    assert grounded("E7.", page_text=RAW_WITH, snippet=None, mode="cheap").status == VERIFIED
    # Raw text wins over the snippet: a code only in the snippet of a page with text fails.
    assert grounded("E9", page_text=RAW_WITH, snippet="Code E9", mode="cheap").status == FAILED
    # A lower case code the owner confirmed as printed on the panel is grounded by that form.
    assert grounded("e7", page_text=RAW_WITH, snippet=None, mode="cheap", observed_code="E7").status == VERIFIED
    no_source = check_grounding("E7", None, page_text=None, snippet=SNIPPET_WITH, mode="cheap", provenance=None)
    assert no_source.status == FAILED

    # Through the rules pipeline: live mode and a non v1 replay cassette fail validation.
    for mode, prov in (("cheap", None), ("replay", NON_V1)):
        result = rules("E7", snippet=SNIPPET_WITHOUT, mode=mode, provenance=prov)
        assert result.brief is None
        assert [e.split(":")[0] for e in result.errors] == [grounding.GROUNDING_FAILED]
        assert result.grounding_status == FAILED
    assert rules("E7", snippet=SNIPPET_WITH, mode="cheap", provenance=None).errors == []
    assert rules(None, snippet=SNIPPET_WITHOUT, mode="cheap", provenance=None).errors == []

    # A non v1 synthetic replay cassette whose cited page lost its raw text and
    # whose snippet lacks the code: the registry comes from its tool results.
    cassette = load_cassette(SYNTHETIC_CASSETTE_DIR / "flo_three_candidates.json")
    assert not grounding.derived_from_v1(cassette.provenance)
    ctx = RunContext(run_id="run-g", mode="replay", ledger_path=tmp_path / "l.sqlite",
                     registry_path=tmp_path / "r.sqlite", graph_path=tmp_path / "g.json",
                     pages_dir=tmp_path / "pages")
    artifacts = []
    for entry in cassette.tool_results_for("search"):
        entry = copy.deepcopy(entry)
        for result in entry.get("results") or []:
            if "FLO" in (result.get("raw_content") or ""):
                result["raw_content"] = None
                result["content"] = "Synthetic snippet with no panel code in it."
        artifacts.append({"tool": "search", "results": entry.get("results") or []})
    sources = build_sources(ctx, artifacts, [], set())
    [attempt] = cassette.data["synthesize"]
    result = run_rules(attempt["draft"], sources=sources, observed_code="FLO", history_hits=[], registry=None,
                       mode="replay", provenance=cassette.provenance, pass_kind="synthesize")
    assert result.brief is None and result.grounding_status == FAILED
    assert [e.split(":")[0] for e in result.errors] == [grounding.GROUNDING_FAILED]


def test_v1_cassette_exemption_is_reported_as_unverifiable(tmp_path: Path) -> None:
    """Mutation grounding_v1_exemption_removed: a v1 derived replay fails like
    any other textless source (the FLO replay then never reaches ok)."""
    result = grounded("FLO", page_text=None, snippet="", mode="replay", provenance=V1)
    assert (result.status, result.passes) == (UNVERIFIABLE, True)
    assert grounding.summarize([], mode="replay", provenance=V1) == UNVERIFIABLE
    assert grounding.summarize([], mode="cheap", provenance=V1) == NOT_APPLICABLE
    via_rules = rules("FLO", snippet="", mode="replay", provenance=V1)
    assert via_rules.errors == [] and via_rules.grounding_status == UNVERIFIABLE
    assert [e["status"] for e in via_rules.grounding] == [UNVERIFIABLE]

    # The recorded v1 FLO case: its cited sources have no text and empty
    # snippets, so the brief reaches ok only through the exemption, and the
    # rules report says so.
    cassette = load_case("flo")
    assert grounding.derived_from_v1(cassette.provenance)
    graph, ctx, outcomes = run_case(cassette, tmp_path)
    state = graph.get_state({"configurable": {"thread_id": "thread-1"}}).values
    assert state["status"] == "ok"
    replayed = run_rules(state["draft"], sources=state["sources"], observed_code=state["observed_code"],
                         history_hits=[], registry=None, mode="replay", provenance=cassette.provenance,
                         pass_kind="synthesize", identity=state.get("identity"),
                         search_trail=state.get("search_trail"))
    assert replayed.errors == []
    assert replayed.grounding_status == UNVERIFIABLE
    assert [(e["code"], e["status"]) for e in replayed.grounding] == [("FLO", UNVERIFIABLE)]


def test_evidence_quote_never_grounds_a_code_the_page_lacks() -> None:
    """Mutations: grounding_accepts_evidence_quote (a code named in the
    candidate's evidence quote is grounded without the quote being checked
    against the page); grounding_evidence_substring_only (the old IN_EVIDENCE
    branch: a quote that verifies against the page grounds a code cut off at
    the quote's edge).

    PLAN 8.5 rule 4 also allows a code in an evidence quote verified against
    the raw text, but such a quote is a substring of the page, so it adds only
    codes the page does not print as a token.
    """
    invented = "Code HX9: the heater senses no water flow."
    result = grounded("HX9", page_text=RAW_WITH, snippet=None, mode="cheap", evidence=invented)
    assert result.status == FAILED
    page = "Service manual (synthetic). When the panel shows E10 the heater has tripped. Reset the breaker."
    cut = "Service manual (synthetic). When the panel shows E1"
    assert grounded("E1", page_text=page, snippet=None, mode="cheap", evidence=cut).status == FAILED
    # A quote cut from the page that prints the code as a token: grounded by the page text.
    whole = "When the panel shows E10 the heater has tripped."
    ok = grounded("E10", page_text=page, snippet=None, mode="cheap", evidence=whole)
    assert (ok.status, ok.reason) == (VERIFIED, grounding.IN_RAW_TEXT)


def test_code_is_not_found_inside_a_longer_token_on_either_side() -> None:
    """Mutation grounding_left_boundary_off: only the right hand boundary is
    checked, so "LO" is grounded by "FLO" and "7" by "E7"."""
    assert grounded("7", page_text=RAW_WITH, snippet=None, mode="cheap").status == FAILED
    assert grounded("LO", page_text="Code FLO: no flow.", snippet=None, mode="cheap").status == FAILED
    assert grounded("LO", page_text=None, snippet="Code FLO: no flow.", mode="cheap").status == FAILED
    assert grounded("FLO", page_text="Code FLO: no flow.", snippet=None, mode="cheap").status == VERIFIED


def test_status_never_claims_a_check_that_did_not_run() -> None:
    """Mutation grounding_empty_reads_verified: a brief with no code to ground
    reports verified. Mutation grounding_skip_reads_summarized: a draft rejected
    by the observed code rule reports a grounding summary instead of not_checked."""
    null_code = rules(None, snippet=SNIPPET_WITHOUT, mode="cheap", provenance=None)
    assert null_code.errors == [] and null_code.grounding_status == NOT_APPLICABLE
    assert grounding.summarize([], mode="cheap", provenance=None) == NOT_APPLICABLE

    found = rules("E7", snippet=SNIPPET_WITH, mode="cheap", provenance=None)
    assert found.errors == [] and found.grounding_status == VERIFIED

    # Observed code E9 matches no candidate, so rule 3 rejects the draft before grounding.
    skipped = rules("E7", snippet=SNIPPET_WITH, mode="cheap", provenance=None, observed="E9")
    assert skipped.errors and skipped.grounding == [] and skipped.grounding_status == NOT_CHECKED


LIVE = {"derived_from": "recorded live 2026-09-18 run t-0000000000000000", "synthetic_fields": []}


def test_live_recording_without_page_text_is_unverifiable() -> None:
    """Mutations: grounding_live_exemption_removed (a replayed live recording
    with no local page text fails like any textless source);
    grounding_live_exemption_ignores_text (a live recording whose page text is
    present but lacks the code passes as unverifiable);
    grounding_live_exemption_any_mode (a live run with live provenance is
    exempt); grounding_live_prefix_any (any named provenance counts as live);
    grounding_live_exemption_without_source (a candidate citing no source
    passes). D4, 18 September 2026."""
    textless = grounded("E7", page_text=None, snippet="", mode="replay", provenance=LIVE)
    assert (textless.status, textless.reason, textless.passes) == (UNVERIFIABLE, grounding.LIVE_NO_TEXT, True)
    # With the page text present, a live recording is checked like a live run.
    assert grounded("E7", page_text=RAW_WITH, snippet=None, mode="replay", provenance=LIVE).status == VERIFIED
    assert grounded("E9", page_text=RAW_WITH, snippet=None, mode="replay", provenance=LIVE).status == FAILED
    assert grounded("E7", page_text=None, snippet="", mode="cheap", provenance=LIVE).status == FAILED
    other = {"derived_from": "hand built from notes", "synthetic_fields": []}
    assert grounded("E7", page_text=None, snippet="", mode="replay", provenance=other).status == FAILED
    no_source = check_grounding("E7", None, page_text=None, snippet=None, mode="replay", provenance=LIVE)
    assert no_source.status == FAILED
    assert grounding.recorded_live(LIVE) and not grounding.recorded_live(V1) and not grounding.recorded_live(None)

    # Through the rules: the brief passes, and the run's summary says unverifiable, not verified.
    via_rules = rules("E7", snippet="", mode="replay", provenance=LIVE)
    assert via_rules.errors == [] and via_rules.grounding_status == UNVERIFIABLE
    assert [(e["status"], e["reason"]) for e in via_rules.grounding] == [(UNVERIFIABLE, grounding.LIVE_NO_TEXT)]
    found = rules("E7", snippet="", mode="replay", provenance=LIVE, page_texts={URL: RAW_WITH})
    assert found.errors == [] and found.grounding_status == VERIFIED
    # A live recording with no code to ground claims nothing (unlike a v1 derived one).
    assert grounding.summarize([], mode="replay", provenance=LIVE) == NOT_APPLICABLE


def test_validate_reads_a_recorded_page_by_its_hash(tmp_path: Path) -> None:
    """Mutations: validate_recorded_hash_ignored (a source with no hash of its
    own never finds the page the recording names); validate_recorded_page_unchecked
    (a file whose text no longer hashes to the recorded name is read). D4."""
    import hashlib

    from agent.nodes.validate import page_texts_for

    pages = tmp_path / "pages"
    pages.mkdir()
    sha = hashlib.sha256(RAW_WITH.encode("utf-8")).hexdigest()
    (pages / f"{sha}.txt").write_text(RAW_WITH, encoding="utf-8")
    stale = "c" * 64
    (pages / f"{stale}.txt").write_text(RAW_WITH, encoding="utf-8")  # text that does not hash to its name
    other = "https://example.com/synthetic/spa/stale"
    missing = "https://example.com/synthetic/spa/missing"
    cassette = {"research": {"tool_results": [
        {"tool": "search", "query": "q", "results": [
            {"url": URL, "title": "", "content": "", "raw_content": None, "score": 0.5, "text_sha256": sha},
            {"url": other, "title": "", "content": "", "raw_content": None, "score": 0.4, "text_sha256": stale},
            {"url": missing, "title": "", "content": "", "raw_content": None, "score": 0.3,
             "text_sha256": "d" * 64}]}]}}
    sources = [{"url": u, "text_sha256": None} for u in (URL, other, missing)]

    def ctx(pages_dir: Path, cas: Any) -> RunContext:
        return RunContext(run_id="run-p", mode="replay", ledger_path=tmp_path / "l.sqlite",
                          registry_path=tmp_path / "r.sqlite", graph_path=tmp_path / "g.json",
                          pages_dir=pages_dir, cassette=cas)

    assert page_texts_for(sources, ctx(pages, cassette)) == {URL: RAW_WITH}
    assert page_texts_for(sources, ctx(tmp_path / "no_pages", cassette)) == {}
    assert page_texts_for(sources, ctx(pages, None)) == {}


def test_live_replay_without_page_is_unverifiable_even_if_snippet_has_code(tmp_path: Path) -> None:
    """Mutation: grounding_live_snippet_first (a replayed live recording whose
    page text is missing locally is verified from its snippet, although live
    checked the page, where a code only in the snippet fails). Review finding E4."""
    had = grounded("E7", page_text=None, snippet=SNIPPET_WITH, provenance=LIVE, had_raw_text=True)
    assert (had.status, had.reason) == (UNVERIFIABLE, grounding.LIVE_NO_TEXT)
    # A source the live run saved no text for keeps the snippet rule.
    assert grounded("E7", page_text=None, snippet=SNIPPET_WITH, provenance=LIVE).status == VERIFIED
    # The page text, when present, still decides.
    assert grounded("E9", page_text=RAW_WITH, snippet="code E9", provenance=LIVE, had_raw_text=True).status == FAILED

    # Through the rules: the recording names the page (its URL), or the source carries its hash.
    via_url = run_rules(draft("E7"), sources=[{**SOURCE, "retrieved_at": "2026-09-18", "text_sha256": None,
                                               "snippet": SNIPPET_WITH}],
                        observed_code=None, history_hits=[], registry=None, mode="replay", provenance=LIVE,
                        pass_kind="synthesize", page_texts={}, recorded_text_urls={URL})
    assert via_url.errors == [] and via_url.grounding_status == UNVERIFIABLE
    assert [e["reason"] for e in via_url.grounding] == [grounding.LIVE_NO_TEXT]
    via_hash = run_rules(draft("E7"), sources=[{**SOURCE, "retrieved_at": "2026-09-18", "text_sha256": "a" * 64,
                                                "snippet": SNIPPET_WITH}],
                         observed_code=None, history_hits=[], registry=None, mode="replay", provenance=LIVE,
                         pass_kind="synthesize", page_texts={})
    assert via_hash.grounding_status == UNVERIFIABLE
    assert rules("E7", snippet=SNIPPET_WITH, mode="replay", provenance=LIVE).grounding_status == VERIFIED

    # Through the validate node: the recording's own hash for the URL marks the
    # page as one live had (validate_recorded_urls_not_passed).
    from agent.nodes.validate import _validate

    recording = {"provenance": LIVE, "research": {"tool_results": [{"tool": "search", "query": "q", "results": [
        {"url": URL, "title": "", "content": SNIPPET_WITH, "raw_content": None, "score": 0.5,
         "text_sha256": "e" * 64}]}]}}
    ctx = RunContext(run_id="run-g", mode="replay", ledger_path=tmp_path / "l.sqlite",
                     registry_path=tmp_path / "r.sqlite", graph_path=tmp_path / "g.json",
                     pages_dir=tmp_path / "pages", cassette=recording)
    state = {"draft": draft("E7"), "observed_code": None, "identity": {},
             "sources": [{**SOURCE, "retrieved_at": "2026-09-18", "text_sha256": None, "snippet": SNIPPET_WITH}]}
    out = _validate(state, ctx)
    assert out["grounding_status"] == UNVERIFIABLE and out["brief"] is not None
    assert [e["reason"] for e in out["grounding"]] == [grounding.LIVE_NO_TEXT]


def test_recording_without_inline_hashes_finds_pages_through_its_texts_file(tmp_path: Path) -> None:
    """Mutation: validate_sidecar_ignored (a recording made before the recorder
    wrote text_sha256 inline, such as the Phase 5 one, never finds its pages,
    so its grounding can only be unverifiable). Review finding P1."""
    import hashlib
    import json as _json

    from agent.nodes.validate import page_texts_for, recorded_text_hashes

    pages = tmp_path / "pages"
    pages.mkdir()
    sha = hashlib.sha256(RAW_WITH.encode("utf-8")).hexdigest()
    (pages / f"{sha}.txt").write_text(RAW_WITH, encoding="utf-8")
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    data = {"research": {"tool_results": [{"tool": "search", "query": "q", "results": [
        {"url": URL, "title": "", "content": "", "raw_content": None, "score": 0.5}]}]}}
    (rec_dir / "live_t_x.texts.json").write_text(_json.dumps({"pages_dir": str(pages), "withheld": [
        {"path": "research.tool_results[0].results[0]", "url": URL, "content_sha256": None,
         "raw_content_sha256": None},
        {"path": "research.tool_results[1].results[0]", "url": URL, "content_sha256": None,
         "raw_content_sha256": sha},
        {"path": "research.tool_results[1].results[1]", "url": URL, "content_sha256": None,
         "raw_content_sha256": "b" * 64}]}), encoding="utf-8")

    class Recording:
        path = rec_dir / "live_t_x.json"

    Recording.data = data
    assert recorded_text_hashes(Recording()) == {URL: sha}
    ctx = RunContext(run_id="run-s", mode="replay", ledger_path=tmp_path / "l.sqlite",
                     registry_path=tmp_path / "r.sqlite", graph_path=tmp_path / "g.json",
                     pages_dir=pages, cassette=Recording())
    assert page_texts_for([{"url": URL, "text_sha256": None}], ctx) == {URL: RAW_WITH}
    # With no texts file beside it, nothing is found.
    Recording.path = rec_dir / "other.json"
    assert recorded_text_hashes(Recording()) == {} and page_texts_for([{"url": URL}], ctx) == {}
