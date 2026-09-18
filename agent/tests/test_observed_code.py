"""The observed code rule (decision 7, PLAN 8.5 rule 3), through the rules pipeline.

Each case scripts the model's candidates and its `confirmed` values; code
decides the outcome. The mutation that turns the test red is named above it.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.rules.pipeline import run_rules

# Each snippet prints every code the cases below use, so rule 4 (code
# grounding, Phase 3) passes and only rule 3 decides each case.
CODE_TABLE = "Synthetic code table: FLO; flo; COOL; ICE; \u00c9R1; F-LO; FL.O; COOL / ICE at start-up."
SOURCES = [
    {"source_id": "src-0", "url": "https://maker.example.com/manual", "title": "Manual", "origin": "search",
     "snippet": CODE_TABLE},
    {"source_id": "src-1", "url": "https://dealer.example.com/codes", "title": "Codes", "origin": "search",
     "snippet": CODE_TABLE},
]


def cand(code: str | None, confirmed: bool = False, index: int = 0) -> dict[str, Any]:
    return {"code": code, "documented_meaning": f"Meaning of {code}", "documented_action": "Act",
            "who": "technician", "why_shown": "", "source_index": index, "confirmed": confirmed,
            "evidence": ""}


def draft(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [{"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": 1}],
        "candidates": candidates, "warranty": None, "no_reliable_answer": None,
        "upgrade_options": [], "maintenance_due": [], "source_tiers": [],
    }


# (id, observed code, scripted candidates, expected kept codes, expected confirmed, error)
CASES = [
    ("one_match_scripted_false_ends_confirmed", "FLO",
     [cand("COOL", True), cand("FLO", False, 1), cand("ICE")], ["FLO"], [True], None),
    ("two_matches_none_confirmed", "FLO",
     [cand("FLO", True), cand("flo", True, 1), cand("COOL")], ["FLO", "flo"], [False, False], None),
    ("zero_matches_is_a_failure", "FLO", [cand("COOL"), cand("ICE")], None, None, "observed_code_no_match"),
    ("zero_candidates_is_a_failure", "FLO", [], None, None, "observed_code_no_match"),
    ("no_observed_code_nothing_confirmed", None,
     [cand("FLO", True), cand("COOL", True)], ["FLO", "COOL"], [False, False], None),
    ("blank_observed_code_is_none", "  ", [cand("FLO", True)], ["FLO"], [False], None),
    ("case_folds", "flo", [cand("FLO"), cand("COOL")], ["FLO"], [True], None),
    ("surrounding_punctuation_stripped", "FLO", [cand('"FLO."'), cand("COOL")], ['"FLO."'], [True], None),
    ("surrounding_whitespace_stripped", " FLO\n", [cand("  FLO  "), cand("COOL")], ["  FLO  "], [True], None),
    ("nfc_equal_forms_match", "ÉR1", [cand("ÉR1"), cand("COOL")], ["ÉR1"], [True], None),
    ("inner_punctuation_kept", "FLO", [cand("F-LO"), cand("FL.O")], None, None, "observed_code_no_match"),
    ("free_text_mode_needs_equal_text", "COOL", [cand("COOL / ICE at start-up")], None, None,
     "observed_code_no_match"),
    ("free_text_mode_equal_after_normalizing", "cool / ice at start-up",
     [cand("COOL / ICE at start-up"), cand("COOL")], ["COOL / ICE at start-up"], [True], None),
    ("null_code_never_matches", "FLO", [cand(None), cand("FLO")], ["FLO"], [True], None),
]


# Mutations: `apply_observed_code` keeps the model's `confirmed` value; the
# zero match branch returns no error; `normalize_code` stops uppercasing,
# stripping punctuation or applying NFC; a single match keeps the other
# candidates; a missing observed code leaves `confirmed` as scripted.
@pytest.mark.parametrize("observed,candidates,kept,confirmed,error", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_narrowing_rule(observed, candidates, kept, confirmed, error) -> None:
    result = run_rules(draft(candidates), sources=SOURCES, observed_code=observed, history_hits=[],
                       registry=None, mode="replay", provenance=None, pass_kind="synthesize")
    if error is not None:
        assert result.brief is None
        assert [e.split(":")[0] for e in result.errors] == [error]
        return
    assert result.errors == []
    brief = result.brief
    assert [c["code"] for c in brief["candidates"]] == kept
    assert [c["confirmed"] for c in brief["candidates"]] == confirmed
    assert brief["observed_code"] == observed
