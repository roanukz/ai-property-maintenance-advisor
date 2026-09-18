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
