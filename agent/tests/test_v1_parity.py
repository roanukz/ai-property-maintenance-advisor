"""The v1 parity layer: every check of v1 validateBrief, 1 to 31, and the PLAN
6.2 parity traps, at the Python level (SC1b compares the same function with v1).

Check numbers follow read_v1_rules.md section C.3. Each test names, in a
comment above it, a code change that turns it red.
"""

from __future__ import annotations

import copy
import json

import pytest

from agent.rules import v1_parity
from agent.rules.v1_parity import (
    REASON_CODES,
    V1_MESSAGE_TO_REASON,
    ParityError,
    loads_strict,
    reason_for_v1_error,
    validate_brief_v1,
)

SRC = {"title": "Maker manual", "url": "https://maker.example.com/manual", "tier": "manufacturer"}
SRC2 = {"title": "Dealer page", "url": "https://dealer.example.com/codes", "tier": "dealer"}


def ok(**overrides) -> dict:
    payload = {"status": "ok", "sources": [dict(SRC), dict(SRC2)], "try_first": [], "candidates": []}
    payload.update(overrides)
    return payload


def nra(**overrides) -> dict:
    payload = {
        "status": "no_reliable_answer",
        "sources": [],
        "try_first": [],
        "candidates": [],
        "no_reliable_answer": {"searched": ["model manual"], "found": [], "why_insufficient": "nothing"},
    }
    payload.update(overrides)
    return payload


def step(**overrides) -> dict:
    s = {"step": "Check the filter", "detail": "Pull it out", "safety_flag": False, "source_index": 0}
    s.update(overrides)
    return s


def cand(**overrides) -> dict:
    c = {"code": "FLO", "documented_meaning": "Low flow", "documented_action": "Clean the filter",
         "who": "anyone", "why_shown": "Shown on the panel", "source_index": 0, "confirmed": False}
    c.update(overrides)
    return c


def reason_of(payload, **kwargs) -> str:
    with pytest.raises(ParityError) as exc:
        validate_brief_v1(payload, **kwargs)
    assert exc.value.reason in REASON_CODES
    return exc.value.reason


# ---------------------------------------------------------------------------
# Every rejecting check, first failure wins
# ---------------------------------------------------------------------------

REJECTS = [
    ("c1_status_missing", {"sources": []}, "invalid_status"),
    ("c1_status_case", ok(status="OK"), "invalid_status"),
    ("c1_status_untrimmed", ok(status=" ok"), "invalid_status"),
    ("c1_status_not_text", ok(status=True), "invalid_status"),
    ("c1_brief_is_array", [ok()], "invalid_status"),
    ("c1_brief_is_null", None, "v1_type_error"),
    ("c2_sources_missing", {"status": "ok"}, "sources_not_array"),
    ("c2_sources_object", ok(sources={"0": SRC}), "sources_not_array"),
    ("c3a_url_not_text", ok(sources=[{"url": 5, "tier": "dealer"}]), "source_url_not_text"),
    ("c3a_url_missing", ok(sources=[{"tier": "dealer"}]), "source_url_not_text"),
    ("c3a_source_is_number", ok(sources=[5]), "source_url_not_text"),
    ("c3a_source_is_string", ok(sources=["https://x.example"]), "source_url_not_text"),
    ("c3a_null_source_is_type_error", ok(sources=[None]), "v1_type_error"),
    ("c3b_title_not_text", ok(sources=[{**SRC, "title": 7}]), "source_title_not_text"),
    ("c3b_before_url_scheme", ok(sources=[{**SRC, "title": [], "url": "ftp://x"}]), "source_title_not_text"),
    ("c3c_javascript_url", ok(sources=[{**SRC, "url": "javascript:alert(1)"}]), "source_url_not_web"),
    ("c3c_protocol_relative", ok(sources=[{**SRC, "url": "//x.example"}]), "source_url_not_web"),
    ("c3c_long_s_not_folded", ok(sources=[{**SRC, "url": "http\u017f://x.example"}]), "source_url_not_web"),
    ("c3d_tier_case", ok(sources=[{**SRC, "tier": "Manufacturer"}]), "source_tier_invalid"),
    ("c3d_tier_missing", ok(sources=[{"url": "https://x.example"}]), "source_tier_invalid"),
    ("c3_checked_on_refusal_path", nra(sources=[{**SRC, "tier": "official"}]), "source_tier_invalid"),
    ("c5_block_missing", nra(no_reliable_answer=None), "nra_block_missing"),
    ("c5_block_false", nra(no_reliable_answer=False), "nra_block_missing"),
    ("c5_block_absent", {k: v for k, v in nra().items() if k != "no_reliable_answer"}, "nra_block_missing"),
    ("c6_searched_string", nra(no_reliable_answer={"searched": "x", "why_insufficient": "w"}),
     "nra_searched_malformed"),
    ("c6_searched_missing_no_default", nra(no_reliable_answer={"found": [], "why_insufficient": "w"}),
     "nra_searched_malformed"),
    ("c6_searched_non_string_item", nra(no_reliable_answer={"searched": [1], "why_insufficient": "w"}),
     "nra_searched_malformed"),
    ("c6_block_truthy_string", nra(no_reliable_answer="nothing found"), "nra_searched_malformed"),
    ("c6_block_empty_array", nra(no_reliable_answer=[]), "nra_searched_malformed"),
    ("c7_found_string", nra(no_reliable_answer={"searched": [], "found": "x", "why_insufficient": "w"}),
     "nra_found_malformed"),
    ("c8_why_missing", nra(no_reliable_answer={"searched": [], "found": []}), "nra_why_insufficient_not_text"),
    ("c10_ok_empty_sources", ok(sources=[]), "ok_empty_sources"),
    ("c12_step_not_text", ok(try_first=[step(step=None)]), "step_not_text"),
    ("c12_step_is_string", ok(try_first=["Check the filter"]), "step_not_text"),
    ("c12_null_step_is_type_error", ok(try_first=[None]), "v1_type_error"),
    ("c13_detail_not_text", ok(try_first=[step(detail=3)]), "step_detail_not_text"),
    ("c15_step_index_out_of_range", ok(try_first=[step(source_index=2)]), "step_index_out_of_range"),
    ("c15_step_index_negative", ok(try_first=[step(source_index=-1)]), "step_index_out_of_range"),
    ("c15_step_index_true", ok(try_first=[step(source_index=True)]), "step_index_out_of_range"),
    ("c15_step_index_string", ok(try_first=[step(source_index="0")]), "step_index_out_of_range"),
    ("c15_step_index_fraction", ok(try_first=[step(source_index=0.5)]), "step_index_out_of_range"),
    ("c15_step_index_missing", ok(try_first=[{"step": "s"}]), "step_index_out_of_range"),
    ("c16_meaning_not_text", ok(candidates=[cand(documented_meaning=None)]), "candidate_meaning_not_text"),
    ("c16_candidate_is_number", ok(candidates=[4]), "candidate_meaning_not_text"),
    ("c16_null_candidate_is_type_error", ok(candidates=[None]), "v1_type_error"),
    ("c17_action_not_text", ok(candidates=[cand(documented_action=["a"])]), "candidate_action_not_text"),
    ("c18_why_shown_not_text", ok(candidates=[cand(why_shown=False)]), "candidate_why_shown_not_text"),
    ("c21_candidate_index_out_of_range", ok(candidates=[cand(source_index=7)]), "candidate_index_out_of_range"),
    ("c21_candidate_index_false", ok(candidates=[cand(source_index=False)]), "candidate_index_out_of_range"),
    ("c22_caution_bare_string", ok(warranty_caution="Check the date"), "warranty_caution_not_text"),
    ("c22_caution_empty_array_is_truthy", ok(warranty_caution=[]), "warranty_caution_not_text"),
    ("c22_caution_text_missing", ok(warranty_caution={"source_index": 0}), "warranty_caution_not_text"),
    ("c24_age_not_text", ok(warranty={"age_statement": 12, "cautions": []}), "warranty_age_statement_not_text"),
    ("c24_warranty_truthy_number", ok(warranty=1), "warranty_age_statement_not_text"),
    ("c27_caution_entry_not_text", ok(warranty={"age_statement": "a", "cautions": [{"text": 1}]}),
     "warranty_caution_entry_not_text"),
    ("c27_null_entry_is_not_type_error", ok(warranty={"age_statement": "a", "cautions": [None]}),
     "warranty_caution_entry_not_text"),
    ("c29_verify_not_list", ok(warranty={"age_statement": "a", "verify": "date"}), "warranty_verify_malformed"),
    ("c29_verify_non_string", ok(warranty={"age_statement": "a", "verify": [None]}), "warranty_verify_malformed"),
    ("c31_summary_not_text", ok(happened_before={"matches": True, "summary": 1}),
     "happened_before_summary_not_text"),
    ("c30_truthy_string_is_type_error", ok(happened_before="yes"), "v1_type_error"),
    ("c30_truthy_number_is_type_error", ok(happened_before=1), "v1_type_error"),
    ("c30_true_is_type_error", ok(happened_before=True), "v1_type_error"),
    ("first_failure_steps_before_candidates",
     ok(try_first=[step(source_index=9)], candidates=[cand(source_index=9)]), "step_index_out_of_range"),
    ("first_failure_candidates_before_caution",
     ok(candidates=[cand(documented_meaning=1)], warranty_caution="x"), "candidate_meaning_not_text"),
    ("first_failure_caution_before_warranty",
     ok(warranty_caution={"text": 1}, warranty={"age_statement": 1}), "warranty_caution_not_text"),
    ("first_failure_warranty_before_happened_before",
     ok(warranty={"age_statement": 1}, happened_before="yes"), "warranty_age_statement_not_text"),
    ("first_failure_sources_before_block", nra(sources=[None], no_reliable_answer=None), "v1_type_error"),
]


# Mutations: `_get` returns undefined for null (no v1_type_error); `js_truthy`
# uses Python truthiness (an empty array slot is skipped); steps checked after
# candidates; the invalid status check trims or lowercases; `is_integer`
# accepts bools; `_check_warranty` reads `entry.text` without the optional
# chain (a null caution entry becomes a type error).
@pytest.mark.parametrize("payload,reason", [(p, r) for _, p, r in REJECTS], ids=[i for i, _, _ in REJECTS])
def test_v1_check_rejects_with_reason_code(payload, reason: str) -> None:
    before = copy.deepcopy(payload)
    assert reason_of(payload) == reason
    assert payload == before, "the port mutated its input"


# ---------------------------------------------------------------------------
# Every normalizing check: the exact output v1 writes
# ---------------------------------------------------------------------------

NORMALIZES = [
    (
        "c4_non_lists_become_empty_on_ok",
        ok(try_first="x", candidates={"a": 1}),
        {"try_first": [], "candidates": [], "no_reliable_answer": None},
    ),
    (
        "c4_missing_lists_written_on_refusal",
        {k: v for k, v in nra().items() if k not in ("try_first", "candidates")},
        {"try_first": [], "candidates": []},
    ),
    (
        "c7_found_defaults_to_empty",
        nra(no_reliable_answer={"searched": ["q"], "why_insufficient": "w"}),
        {"no_reliable_answer": {"searched": ["q"], "found": [], "why_insufficient": "w"}},
    ),
    (
        "c9_refusal_clears_and_skips_item_checks",
        nra(try_first=[None], candidates=[cand(source_index=99)], warranty_caution="x",
            warranty=5, happened_before="yes", observed_code="FLO"),
        {"try_first": [], "candidates": [], "warranty_caution": "x", "warranty": 5,
         "happened_before": "yes", "observed_code": "FLO"},
    ),
    (
        "c11_ok_nulls_filled_refusal_block",
        ok(no_reliable_answer={"searched": 3}),
        {"no_reliable_answer": None},
    ),
    (
        "c11_ok_writes_absent_refusal_block",
        ok(),
        {"no_reliable_answer": None},
    ),
    (
        "c13_null_detail_kept",
        ok(try_first=[step(detail=None)]),
        {"try_first": [step(detail=None)]},
    ),
    (
        "c14_safety_flag_true_only",
        ok(try_first=[step(safety_flag="true"), step(safety_flag=1), step(safety_flag=True),
                      {"step": "s", "source_index": 1}]),
        {"try_first": [step(safety_flag=False), step(safety_flag=False), step(safety_flag=True),
                       {"step": "s", "source_index": 1, "safety_flag": False}]},
    ),
    (
        "c19_unknown_who_becomes_technician",
        ok(candidates=[cand(who="Anyone"), cand(who=None), cand(who="anyone"), cand(who=3),
                       {k: v for k, v in cand().items() if k != "who"}]),
        {"candidates": [cand(who="technician"), cand(who="technician"), cand(who="anyone"),
                        cand(who="technician"), cand(who="technician")]},
    ),
    (
        "c20_confirmed_true_only",
        ok(candidates=[cand(confirmed="true"), cand(confirmed=1), cand(confirmed=True)]),
        {"candidates": [cand(confirmed=False), cand(confirmed=False), cand(confirmed=True)]},
    ),
    (
        "c21_code_never_checked",
        ok(candidates=[cand(code=5), cand(code=None), cand(code={"x": [1]})]),
        {"candidates": [cand(code=5), cand(code=None), cand(code={"x": [1]})]},
    ),
    (
        "c23_bad_caution_index_nulled",
        ok(warranty_caution={"text": "t", "source_index": 5}),
        {"warranty_caution": {"text": "t", "source_index": None}},
    ),
    (
        "c23_missing_caution_index_written",
        ok(warranty_caution={"text": "t"}),
        {"warranty_caution": {"text": "t", "source_index": None}},
    ),
    (
        "c23_good_caution_index_kept",
        ok(warranty_caution={"text": "t", "source_index": 1, "extra": "kept"}),
        {"warranty_caution": {"text": "t", "source_index": 1, "extra": "kept"}},
    ),
    (
        "c25_cautions_not_list_become_empty",
        ok(warranty={"age_statement": "a", "cautions": "x"}),
        {"warranty": {"age_statement": "a", "cautions": [], "verify": []}},
    ),
    (
        "c26_bare_string_caution_uncited",
        ok(warranty={"age_statement": "a", "cautions": ["plain"], "verify": ["v"]}),
        {"warranty": {"age_statement": "a", "cautions": [{"text": "plain", "source_index": None}],
                      "verify": ["v"]}},
    ),
    (
        "c28_caution_entries_rebuilt",
        ok(warranty={"age_statement": "a", "cautions": [
            {"text": "cited", "source_index": 1, "extra": "dropped"},
            {"text": "bad", "source_index": 2},
            {"text": "bool", "source_index": True},
        ]}),
        {"warranty": {"age_statement": "a", "cautions": [
            {"text": "cited", "source_index": 1},
            {"text": "bad", "source_index": None},
            {"text": "bool", "source_index": None},
        ], "verify": []}},
    ),
    (
        "c29_verify_null_defaults",
        ok(warranty={"age_statement": "a", "cautions": [], "verify": None}),
        {"warranty": {"age_statement": "a", "cautions": [], "verify": []}},
    ),
    (
        "c30_matches_true_only",
        ok(happened_before={"matches": "true", "summary": None}),
        {"happened_before": {"matches": False, "summary": None}},
    ),
    (
        "c30_matches_written_when_absent",
        ok(happened_before={}),
        {"happened_before": {"matches": False}},
    ),
    (
        "c30_array_kept_unchanged",
        ok(happened_before=[{"matches": "yes"}]),
        {"happened_before": [{"matches": "yes"}]},
    ),
    (
        "falsy_primitives_left_as_is",
        ok(warranty_caution=0, warranty="", happened_before=False),
        {"warranty_caution": 0, "warranty": "", "happened_before": False},
    ),
    (
        "extra_keys_kept_as_v1_keeps_them",
        ok(unknown_top="x", sources=[{**SRC, "note": "n"}, dict(SRC2)], try_first=[step(extra=1)]),
        {"unknown_top": "x", "sources": [{**SRC, "note": "n"}, dict(SRC2)], "try_first": [step(extra=1)]},
    ),
    (
        "any_json_in_identity_fields",
        ok(matched_identity=880, observed_code=["FLO"]),
        {"matched_identity": 880, "observed_code": ["FLO"]},
    ),
    (
        "integral_float_index_accepted_unchanged",
        ok(try_first=[step(source_index=1.0)], candidates=[cand(source_index=0.0)]),
        {"try_first": [step(source_index=1.0)], "candidates": [cand(source_index=0.0)]},
    ),
    (
        "url_scheme_case_insensitive",
        ok(sources=[{**SRC, "url": "HTTPS://Maker.example.com/m"}]),
        {"sources": [{**SRC, "url": "HTTPS://Maker.example.com/m"}]},
    ),
    (
        "null_title_kept_not_normalized",
        ok(sources=[{"url": "https://x.example", "tier": "forum", "title": None}]),
        {"sources": [{"url": "https://x.example", "tier": "forum", "title": None}]},
    ),
    (
        "nothing_trimmed",
        ok(try_first=[step(step="  spaced  ")]),
        {"try_first": [step(step="  spaced  ")]},
    ),
]


# Mutations: remove `brief["no_reliable_answer"] = None` on the ok path; keep
# extra keys on rebuilt caution entries; drop the `v is True` coercion for
# confirmed; `_set` on a list raises (array happened_before rejected); the
# refusal path runs the item checks before returning.
@pytest.mark.parametrize("payload,changes", [(p, c) for _, p, c in NORMALIZES], ids=[i for i, _, _ in NORMALIZES])
def test_v1_check_normalizes_exactly(payload: dict, changes: dict) -> None:
    before = copy.deepcopy(payload)
    expected = {**copy.deepcopy(payload), **changes}
    if payload.get("status") == "ok":
        expected["no_reliable_answer"] = None  # check 11 writes it on every ok brief
    assert validate_brief_v1(payload) == expected
    assert payload == before, "the port mutated its input"


# ---------------------------------------------------------------------------
# The budget_stopped flag, the reason table and strict parsing
# ---------------------------------------------------------------------------


# Mutation: `accept_budget_stopped` ignored (always on, or always off), or the
# budget stop path keeps candidates.
def test_budget_stopped_is_behind_a_flag() -> None:
    payload = ok(status="budget_stopped", sources=[], candidates=[cand()], try_first=[step()])
    out = validate_brief_v1(payload)
    assert out["status"] == "budget_stopped"
    assert out["candidates"] == [] and out["try_first"] == []
    assert "no_reliable_answer" not in out
    assert reason_of(payload, accept_budget_stopped=False) == "invalid_status"
    assert reason_of(ok(status="budget_stopped", sources=[None])) == "v1_type_error"


# Mutation: a message in the table misspelled or mapped to the wrong code, the
# TypeError branch removed, or the invalid status prefix matched exactly.
def test_every_v1_message_maps_to_the_code_the_port_raises() -> None:
    assert len(set(V1_MESSAGE_TO_REASON.values())) == len(V1_MESSAGE_TO_REASON)
    for message, code in V1_MESSAGE_TO_REASON.items():
        assert reason_for_v1_error(message) == code
        assert v1_parity._fail(code).message == message
    assert reason_for_v1_error("Brief has invalid status: undefined") == "invalid_status"
    assert reason_for_v1_error("Cannot read properties of null (reading 'url')",
                               error_name="TypeError") == "v1_type_error"
    with pytest.raises(ParityError) as exc:
        validate_brief_v1(ok(status=None))
    assert exc.value.message == "Brief has invalid status: null"
    with pytest.raises(KeyError):
        reason_for_v1_error("Some new message.")


# Mutation: `loads_strict` drops its parse_constant hook (Python accepts NaN).
@pytest.mark.parametrize("text", ['{"status": NaN}', '{"i": Infinity}', '{"i": -Infinity}'])
def test_strict_json_rejects_non_finite_constants(text: str) -> None:
    with pytest.raises(ValueError):
        loads_strict(text)
    assert loads_strict('{"i": 2.0, "s": "ok"}') == json.loads('{"i": 2.0, "s": "ok"}')
