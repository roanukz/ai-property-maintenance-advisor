"""Schema layer tests (PLAN.md section 7, decision 22).

Each test names, in a comment above it, a code change that turns it red.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from agent import config
from agent.schemas import (
    Brief,
    BriefDraft,
    BriefSource,
    Extraction,
    Identity,
    Intake,
    RunResult,
    count_structured_output_params,
    draft_to_brief_dict,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES_JS = REPO / "src" / "fixtures.js"
SOURCE = {"title": "Maker manual", "url": "https://maker.example.com/manual", "tier": "manufacturer"}


def _reject_constant(name: str) -> None:
    raise ValueError(f"non JSON constant {name} in fixtures.js")


def _load_fixture_cases() -> list[dict]:
    """Read the public demo fixtures: a JS assignment whose value is JSON."""
    text = FIXTURES_JS.read_text(encoding="utf-8")
    marker = "window.BRIEFCASE_FIXTURES"
    start = text.index("{", text.index(marker))
    end = text.rindex("}") + 1
    return json.loads(text[start:end], parse_constant=_reject_constant)["cases"]


CASES = _load_fixture_cases()
RECORDED = [(c["id"], c["brief"]["brief"]) for c in CASES if c.get("brief")]
EXTRACTIONS = [(c["id"], c["extraction"]) for c in CASES if c.get("extraction")]


def _error_locs(exc: ValidationError) -> list[tuple]:
    return [tuple(e["loc"]) for e in exc.errors()]


def _ok(**overrides) -> dict:
    payload = {"status": "ok", "sources": [dict(SOURCE)], "try_first": [], "candidates": []}
    payload.update(overrides)
    return payload


def _step(**overrides) -> dict:
    step = {"step": "Check the filter", "source_index": 0}
    step.update(overrides)
    return step


def _candidate(**overrides) -> dict:
    cand = {"documented_meaning": "Low flow", "documented_action": "Clean the filter", "source_index": 0}
    cand.update(overrides)
    return cand


# ---------------------------------------------------------------------------
# The recorded corpus
# ---------------------------------------------------------------------------


# Guards the fixture reader itself. Mutation: the extractor slices the wrong
# span (for example the first "}" instead of the last) or skips cases.
def test_fixture_corpus_has_the_four_recorded_briefs() -> None:
    assert [case_id for case_id, _ in RECORDED] == ["flo", "notheating", "hvac", "unknown"]
    statuses = sorted(brief["status"] for _, brief in RECORDED)
    assert statuses == ["no_reliable_answer", "ok", "ok", "ok"]


# Mutation: make a v2 addition required (for example `BriefSource.host` or
# `Candidate.evidence` without a default), or have `Brief._v1_writes` set
# `upgrade_options` explicitly, so the dump grows a key v1 never wrote.
@pytest.mark.parametrize("case_id,brief", RECORDED, ids=[c for c, _ in RECORDED])
def test_recorded_brief_accepted_and_round_trips(case_id: str, brief: dict) -> None:
    original = copy.deepcopy(brief)
    model = Brief.model_validate(brief)
    assert brief == original, "validation mutated the caller's payload"
    assert model.model_dump(exclude_unset=True) == original


# Mutation: `Extraction.manufacturer` typed as `StrictStr` (not nullable), or an
# `Identity` field given `min_length=1` (the recorded serial is "").
@pytest.mark.parametrize("case_id,extraction", EXTRACTIONS, ids=[c for c, _ in EXTRACTIONS])
def test_recorded_extraction_and_identity_validate(case_id: str, extraction: dict) -> None:
    assert Extraction.model_validate(extraction).model_dump() == extraction
    for case in CASES:
        if case.get("brief"):
            identity = case["brief"]["identity"]
            assert Identity.model_validate(identity).model_dump() == identity


# ---------------------------------------------------------------------------
# Structured output limits (decision 22)
# ---------------------------------------------------------------------------


# Mutation: give any BriefDraft field a default (for example `detail: str = ""`),
# or add nullable fields until unions pass 16.
def test_brief_draft_within_structured_output_limits() -> None:
    counts = count_structured_output_params(BriefDraft)
    assert counts["optional_per_defs"] == 0
    assert counts["optional_per_use_site"] == 0
    assert counts["unions_per_defs"] <= 16
    assert counts["unions_per_use_site"] <= 16


class _Inner(BaseModel):
    a: int | None
    b: str = "x"


class _Outer(BaseModel):
    first: _Inner
    second: list[_Inner]
    third: str | None


# Proves the counter can see what the limit test guards. Mutation: the counter
# stops following `$ref` for use sites, ignores `required`, or skips `items`.
def test_counter_counts_defaults_and_unions_per_defs_and_use_site() -> None:
    counts = count_structured_output_params(_Outer)
    assert counts["optional_per_defs"] == 1
    assert counts["optional_per_use_site"] == 2
    assert counts["unions_per_defs"] == 2
    assert counts["unions_per_use_site"] == 3


# ---------------------------------------------------------------------------
# Ported v1 guardrail tests 3, 8 and 17
# ---------------------------------------------------------------------------


# v1 guardrail test 3. Mutation: `NoReliableAnswer.searched` given a default or
# a before validator that turns a non list into [].
def test_nra_with_non_list_searched_is_rejected() -> None:
    payload = {
        "status": "no_reliable_answer",
        "try_first": [],
        "candidates": [],
        "no_reliable_answer": {"searched": "not a list", "found": [], "why_insufficient": "x"},
        "sources": [],
    }
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate(payload)
    assert _error_locs(exc.value) == [("no_reliable_answer", "searched")]


# v1 guardrail test 8. Mutation: `BriefSource.tier` typed `str`, or
# `SourceTier` widened, or the draft tier losing its Literal.
def test_invalid_tier_label_is_rejected() -> None:
    payload = _ok(sources=[{"title": "t", "url": "https://x.example", "tier": "official"}])
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate(payload)
    assert _error_locs(exc.value) == [("sources", 0, "tier")]
    with pytest.raises(ValidationError) as exc:
        BriefDraft.model_validate(_draft(source_tiers=[_tier(tier="official")]))
    assert _error_locs(exc.value) == [("source_tiers", 0, "tier")]


# v1 guardrail test 17. Mutation: remove the bare string branch in
# `WarrantyCaution._v1_writes`, or give it `source_index: 0`.
def test_bare_string_caution_becomes_uncited() -> None:
    payload = _ok(warranty={"age_statement": "old", "cautions": ["plain caution"], "verify": []},
                  no_reliable_answer=None)
    dumped = Brief.model_validate(payload).model_dump(exclude_unset=True)
    assert dumped["warranty"]["cautions"] == [{"text": "plain caution", "source_index": None}]


# Mutation: type Brief.warranty_caution as WarrantyCaution again (the class
# that turns a bare string into an uncited caution).
def test_top_level_warranty_caution_rejects_a_bare_string() -> None:
    # v1 check 22 reads `.text` off a truthy caution, so a string throws there;
    # only warranty.cautions entries may be bare strings (check 26).
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate(_ok(warranty_caution="Check the date"))
    assert _error_locs(exc.value)[0][0] == "warranty_caution"
    kept = Brief.model_validate(_ok(warranty_caution={"text": "Check the date", "source_index": 0}))
    assert kept.warranty_caution.text == "Check the date"


# ---------------------------------------------------------------------------
# Parity traps (PLAN 6.2)
# ---------------------------------------------------------------------------


# Mutation: drop the bool exclusion in `_is_integral` (Python's True is an int).
@pytest.mark.parametrize("slot", ["try_first", "candidates"])
def test_source_index_true_is_rejected(slot: str) -> None:
    item = _step(source_index=True) if slot == "try_first" else _candidate(source_index=True)
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate(_ok(**{slot: [item]}))
    assert _error_locs(exc.value) == [(slot, 0, "source_index")]


# Mutation: `StrictIndex` replaced by `StrictInt` (JSON 2.0 is an integer in v1).
def test_integral_float_index_is_accepted_as_int() -> None:
    brief = Brief.model_validate(_ok(try_first=[_step(source_index=0.0)], candidates=[_candidate(source_index=2.0)]))
    assert brief.try_first[0].source_index == 0 and type(brief.try_first[0].source_index) is int
    assert brief.candidates[0].source_index == 2 and type(brief.candidates[0].source_index) is int


# Mutation: `_strict_index` falls back to `int(value)` (lax coercion of "0"),
# or accepts any float by truncating 1.5.
@pytest.mark.parametrize("bad", ["0", 1.5, None])
def test_non_integer_index_is_rejected(bad: object) -> None:
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate(_ok(candidates=[_candidate(source_index=bad)]))
    assert _error_locs(exc.value) == [("candidates", 0, "source_index")]


# Mutation: caution `source_index` typed `StrictIndex` (rejects instead of
# nulling), or `_lenient_index` keeping True as 1.
@pytest.mark.parametrize("given,expected", [(True, None), ("0", None), (1.0, 1), (None, None)])
def test_caution_index_of_wrong_type_becomes_none(given: object, expected: int | None) -> None:
    brief = Brief.model_validate(_ok(warranty_caution={"text": "Rental use may void coverage", "source_index": given}))
    assert brief.warranty_caution.source_index == expected
    dumped = Brief.model_validate(_ok(warranty_caution={"text": "c"})).model_dump(exclude_unset=True)
    assert dumped["warranty_caution"] == {"text": "c", "source_index": None}


# Mutation: drop the `_is_true` write for `safety_flag`, `confirmed` or
# `matches` in the parity `_v1_writes` validators (lax mode turns "true" and 1
# into True, the unsafe direction for `confirmed`).
@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_truthy_non_boolean_becomes_false(value: object) -> None:
    brief = Brief.model_validate(
        _ok(
            try_first=[_step(safety_flag=value)],
            candidates=[_candidate(confirmed=value)],
            happened_before={"matches": value},
        )
    )
    assert brief.try_first[0].safety_flag is False
    assert brief.candidates[0].confirmed is False
    assert brief.happened_before.matches is False
    real = Brief.model_validate(_ok(candidates=[_candidate(confirmed=True)]))
    assert real.candidates[0].confirmed is True


# Mutation: remove `_normalize_who` from `Candidate._v1_writes`, so the Literal
# rejects the value instead of downgrading it.
@pytest.mark.parametrize("value", ["Anyone", "owner", None, 3])
def test_unknown_who_becomes_technician(value: object) -> None:
    brief = Brief.model_validate(_ok(candidates=[_candidate(who=value)]))
    assert brief.candidates[0].who == "technician"
    assert Brief.model_validate(_ok(candidates=[_candidate(who="anyone")])).candidates[0].who == "anyone"


# Mutation: `extra="forbid"` (rejects) or `extra="allow"` (dump keeps the key).
def test_extra_keys_are_ignored() -> None:
    payload = _ok(
        sources=[dict(SOURCE, rank=1)],
        candidates=[_candidate(severity="high")],
        share_path="x.html",
    )
    dumped = Brief.model_validate(payload).model_dump(exclude_unset=True)
    assert "share_path" not in dumped
    assert "rank" not in dumped["sources"][0]
    assert "severity" not in dumped["candidates"][0]


# Mutation (either direction): a validator that writes `detail`, `why_shown`,
# `title`, `code` or `summary` when absent; or removing the explicit write of
# `safety_flag`, `who`, `confirmed` or `matches`, which v1 always writes.
def test_absent_keys_stay_absent_and_v1_written_keys_are_present() -> None:
    payload = {
        "status": "ok",
        "sources": [{"url": "https://maker.example.com/m", "tier": "dealer"}],
        "try_first": [_step()],
        "candidates": [_candidate()],
        "happened_before": {"matches": True},
    }
    dumped = Brief.model_validate(payload).model_dump(exclude_unset=True)
    assert set(dumped) == {"status", "sources", "try_first", "candidates", "happened_before"}
    assert dumped["sources"][0] == {"url": "https://maker.example.com/m", "tier": "dealer"}
    assert dumped["try_first"][0] == {"step": "Check the filter", "source_index": 0, "safety_flag": False}
    assert dumped["candidates"][0] == {**_candidate(), "who": "technician", "confirmed": False}
    assert dumped["happened_before"] == {"matches": True}


# Mutation: `Candidate.code`, `Brief.matched_identity` or `Brief.observed_code`
# typed `str | None` (v1 never checks them).
def test_code_and_identity_fields_accept_any_json_value() -> None:
    payload = _ok(candidates=[_candidate(code=5)], matched_identity=["x"], observed_code={"a": 1})
    dumped = Brief.model_validate(payload).model_dump(exclude_unset=True)
    assert dumped["candidates"][0]["code"] == 5
    assert dumped["matched_identity"] == ["x"]
    assert dumped["observed_code"] == {"a": 1}


# Mutation: remove `_list_or_empty` from `Brief._v1_writes`.
@pytest.mark.parametrize("value", ["x", None, {"step": "s"}, "missing"])
def test_non_list_steps_and_candidates_become_empty(value: object) -> None:
    payload = {"status": "ok", "sources": [dict(SOURCE)]}
    if value != "missing":
        payload["try_first"] = value
        payload["candidates"] = value
    dumped = Brief.model_validate(payload).model_dump(exclude_unset=True)
    assert dumped["try_first"] == [] and dumped["candidates"] == []


# Mutation: remove the `cautions` or `verify` defaults written by
# `Warranty._v1_writes` (v1 writes both), or the `found` default in
# `NoReliableAnswer._v1_writes`.
def test_v1_list_defaults_are_written() -> None:
    brief = _ok(warranty={"age_statement": "unknown", "cautions": "not a list"})
    dumped = Brief.model_validate(brief).model_dump(exclude_unset=True)
    assert dumped["warranty"] == {"age_statement": "unknown", "cautions": [], "verify": []}
    nra = {
        "status": "no_reliable_answer",
        "sources": [],
        "no_reliable_answer": {"searched": ["q"], "why_insufficient": "nothing"},
    }
    dumped = Brief.model_validate(nra).model_dump(exclude_unset=True)
    assert dumped["no_reliable_answer"] == {"searched": ["q"], "found": [], "why_insufficient": "nothing"}


# Mutation: remove `_falsy_to_none` (a falsy primitive in an object slot is
# skipped by v1, not rejected).
@pytest.mark.parametrize("value", [False, 0, ""])
def test_falsy_primitive_in_object_slot_is_treated_as_absent(value: object) -> None:
    brief = Brief.model_validate(_ok(happened_before=value, warranty=value, warranty_caution=value))
    assert brief.happened_before is None and brief.warranty is None and brief.warranty_caution is None


# Mutation: drop `re.IGNORECASE` from `HTTP_URL`, or remove the url check.
def test_source_url_must_be_web_url_with_any_scheme_case() -> None:
    assert BriefSource.model_validate({"url": "HTTPS://Maker.example.com", "tier": "forum"}).url.startswith("HTTPS")
    for bad in ("javascript:alert(1)", "//maker.example.com", "ftp://maker.example.com"):
        with pytest.raises(ValidationError):
            BriefSource.model_validate({"url": bad, "tier": "forum"})


# Mutation: drop re.ASCII from HTTP_URL; Python then folds U+017F (long s) to
# "s" and U+212A (Kelvin sign) to "k", which JavaScript's /i never does.
def test_url_scheme_folds_ascii_letters_only() -> None:
    for bad in ("http\u017f://maker.example.com", "HTTP\u017f://maker.example.com"):
        with pytest.raises(ValidationError):
            BriefSource.model_validate({"url": bad, "tier": "forum"})
    assert BriefSource.model_validate({"url": "hTtPs://maker.example.com", "tier": "forum"})


# Mutation: type Brief.happened_before as HappenedBefore | None again (an
# array is then rejected, where v1 accepts it and outputs it unchanged).
@pytest.mark.parametrize("value", [[], [1], [{"matches": True}]])
def test_happened_before_array_is_kept_as_v1_outputs_it(value: list) -> None:
    dumped = Brief.model_validate(_ok(happened_before=value)).model_dump(exclude_unset=True)
    assert dumped["happened_before"] == value


def test_happened_before_truthy_primitive_is_still_rejected() -> None:
    # Mutation: type happened_before as Any; v1 throws assigning .matches on a
    # primitive in strict mode, so "yes" and 1 must stay rejected.
    for value in ("yes", 1):
        with pytest.raises(ValidationError):
            Brief.model_validate(_ok(happened_before=value))


# Mutation: `sources` given a default, or its items typed `Any`.
def test_missing_sources_and_null_source_element_are_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate({"status": "ok"})
    assert ("sources",) in _error_locs(exc.value)
    with pytest.raises(ValidationError) as exc:
        Brief.model_validate(_ok(sources=[None]))
    assert _error_locs(exc.value) == [("sources", 0)]


# Mutation: `BriefStatus` loses `budget_stopped`, or the parity status gains a
# lowercasing validator (v1 compares case sensitively).
def test_status_values() -> None:
    assert Brief.model_validate(_ok(status="budget_stopped")).status == "budget_stopped"
    for bad in ("OK", "done", None):
        with pytest.raises(ValidationError):
            Brief.model_validate(_ok(status=bad))


# ---------------------------------------------------------------------------
# BriefDraft and the converter
# ---------------------------------------------------------------------------


def _tier(**overrides) -> dict:
    entry = {"source_index": 0, "tier": "manufacturer", "authorship_quote": ""}
    entry.update(overrides)
    return entry


def _draft(**overrides) -> dict:
    draft = {
        "status": "ok",
        "matched_identity": "Maker Model 1",
        "warranty_caution": None,
        "happened_before": None,
        "try_first": [
            {"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": 0},
        ],
        "candidates": [
            {
                "code": "FLO",
                "documented_meaning": "Low flow",
                "documented_action": "Clean the filter",
                "who": "anyone",
                "why_shown": "",
                "source_index": 1,
                "confirmed": False,
                "evidence": "",
            }
        ],
        "warranty": None,
        "no_reliable_answer": None,
        "upgrade_options": [],
        "maintenance_due": [],
        "source_tiers": [_tier()],
    }
    draft.update(overrides)
    return draft


REGISTRY = [
    {"url": "https://maker.example.com/manual", "title": "Manual", "host": "maker.example.com", "origin": "search"},
    {"url": "https://dealer.example.com/codes", "title": "Codes", "host": "dealer.example.com", "origin": "extract"},
]


# Mutation: remove `BeforeValidator(_lowercase)` from any draft enum
# (status, who, tier, reason).
def test_brief_draft_lowercases_enums() -> None:
    draft = BriefDraft.model_validate(
        _draft(
            status="OK",
            candidates=[{**_draft()["candidates"][0], "who": "ANYONE"}],
            source_tiers=[_tier(tier="Manufacturer")],
            upgrade_options=[
                {
                    "successor_manufacturer": "",
                    "successor_model": "Model 2",
                    "reason": "Discontinued",
                    "summary": "Replaced",
                    "source_index": 0,
                    "evidence": "",
                }
            ],
        )
    )
    assert draft.status == "ok"
    assert draft.candidates[0].who == "anyone"
    assert draft.source_tiers[0].tier == "manufacturer"
    assert draft.upgrade_options[0].reason == "discontinued"


# Mutation: `LowerWho` without `_normalize_who` (an unknown value is rejected
# instead of downgraded, drifting from the parity layer).
def test_brief_draft_unknown_who_becomes_technician() -> None:
    draft = BriefDraft.model_validate(_draft(candidates=[{**_draft()["candidates"][0], "who": "homeowner"}]))
    assert draft.candidates[0].who == "technician"


# Mutation: remove `_none_if_empty` from any one of these fields in
# `draft_to_brief_dict`.
def test_converter_turns_empty_strings_into_none() -> None:
    draft = BriefDraft.model_validate(
        _draft(
            matched_identity="",
            candidates=[{**_draft()["candidates"][0], "code": ""}],
            happened_before={"matches": True, "summary": "", "record_id": ""},
            maintenance_due=[
                {"task": "Drain", "interval": "every 3 months", "source_index": 0, "evidence": "",
                 "last_done_record_id": ""}
            ],
        )
    )
    brief = draft_to_brief_dict(draft, REGISTRY)
    assert brief["matched_identity"] is None
    assert brief["try_first"][0]["detail"] is None
    cand = brief["candidates"][0]
    assert cand["code"] is None and cand["why_shown"] is None and cand["evidence"] is None
    assert brief["happened_before"] == {"matches": True, "summary": None, "record_id": None}
    assert brief["maintenance_due"][0]["evidence"] is None
    assert brief["maintenance_due"][0]["last_done_record_id"] is None
    kept = draft_to_brief_dict(BriefDraft.model_validate(_draft()), REGISTRY)
    assert kept["candidates"][0]["code"] == "FLO"
    assert kept["matched_identity"] == "Maker Model 1"


# Mutation: `_attach_sources` reads the proposal for index i + 1, defaults an
# unproposed source to a tier other than forum, or takes sources from the draft.
def test_converter_attaches_code_built_sources_with_proposed_tiers() -> None:
    raw = _draft(
        source_tiers=[_tier(source_index=0, authorship_quote="Written by Maker"), _tier(source_index=7)],
        sources=[{"url": "https://invented.example.com", "tier": "manufacturer"}],
    )
    brief = draft_to_brief_dict(BriefDraft.model_validate(raw), REGISTRY)
    assert [s["url"] for s in brief["sources"]] == [s["url"] for s in REGISTRY]
    assert brief["sources"][0]["proposed_tier"] == "manufacturer"
    assert brief["sources"][0]["authorship_quote"] == "Written by Maker"
    assert brief["sources"][1]["proposed_tier"] is None
    assert brief["sources"][1]["tier"] == "forum"
    validated = Brief.model_validate(brief)
    assert validated.sources[0].host == "maker.example.com"
    assert validated.candidates[0].source_index == 1


# Mutation: the converter copies `searched` from the draft instead of the code
# built trail, or `BriefDraft` gains a `searched` field.
def test_refusal_block_gets_code_built_searched_trail() -> None:
    draft = BriefDraft.model_validate(
        _draft(
            status="no_reliable_answer",
            try_first=[],
            candidates=[],
            no_reliable_answer={"found": [], "why_insufficient": "nothing documented", "searched": ["model text"]},
        )
    )
    brief = draft_to_brief_dict(draft, [], searched=["Maker Model 1 manual"])
    assert brief["no_reliable_answer"]["searched"] == ["Maker Model 1 manual"]
    assert Brief.model_validate(brief).no_reliable_answer.searched == ["Maker Model 1 manual"]


# Mutation: add `sources`, `observed_code` or `searched` to the draft models,
# or give any draft field a default (the model must fill everything).
def test_draft_leaves_code_owned_fields_out_and_requires_everything() -> None:
    assert not {"sources", "observed_code"} & set(BriefDraft.model_fields)
    schema = BriefDraft.model_json_schema()
    assert "searched" not in json.dumps(schema)
    for field in BriefDraft.model_fields:
        broken = _draft()
        del broken[field]
        with pytest.raises(ValidationError):
            BriefDraft.model_validate(broken)


# Mutation: `TrueOnly` replaced by plain `bool` (the draft has no model level
# write, so lax mode would accept "true" and 1 as True).
@pytest.mark.parametrize("value", ["true", 1])
def test_draft_booleans_are_true_only(value: object) -> None:
    raw = _draft(
        try_first=[{**_draft()["try_first"][0], "safety_flag": value}],
        candidates=[{**_draft()["candidates"][0], "confirmed": value}],
        happened_before={"matches": value, "summary": "", "record_id": ""},
    )
    draft = BriefDraft.model_validate(raw)
    assert draft.try_first[0].safety_flag is False
    assert draft.candidates[0].confirmed is False
    assert draft.happened_before.matches is False


# Mutation: draft `source_index` typed plain `int` (lax: "1" and True accepted).
@pytest.mark.parametrize("bad", [True, "1"])
def test_draft_index_is_strict(bad: object) -> None:
    with pytest.raises(ValidationError):
        BriefDraft.model_validate(_draft(try_first=[{**_draft()["try_first"][0], "source_index": bad}]))


# ---------------------------------------------------------------------------
# Intake and the run wrapper
# ---------------------------------------------------------------------------


# Mutation: `IntakeIdentity` fields typed `Any`, or `_text_or_none` coercing
# with `str(value)`.
@pytest.mark.parametrize("field,value,kind", [("model", 880, "int"), ("serial", ["1"], "list"), ("manufacturer", True, "bool")])
def test_intake_rejects_non_string_identity_fields(field: str, value: object, kind: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Intake.model_validate({"symptom": "not heating", "identity": {field: value}})
    errors = exc.value.errors()
    assert [tuple(e["loc"]) for e in errors] == [("identity", field)]
    assert f"{field} must be text or empty, got {kind}" in errors[0]["msg"]


# Mutation: `_text_or_none` returns `value` instead of `value or None`.
def test_intake_converts_empty_identity_fields_to_none() -> None:
    intake = Intake.model_validate(
        {"symptom": "panel shows FLO", "identity": {"manufacturer": "Maker", "model": "Model 1", "serial": "",
                                                     "manufacture_date": ""}, "code": ""}
    )
    assert intake.identity.serial is None and intake.identity.manufacture_date is None
    assert intake.identity.model == "Model 1"
    assert intake.code is None


# Mutation: remove `_photo_or_identity`, the symptom check, or type `mode` as
# plain `str`.
def test_intake_rejects_bad_combinations() -> None:
    with pytest.raises(ValidationError):
        Intake.model_validate({"symptom": "x", "photo_path": "p.jpg", "identity": {"model": "M"}})
    for symptom in ("", "   ", None, 5):
        with pytest.raises(ValidationError):
            Intake.model_validate({"symptom": symptom})
    with pytest.raises(ValidationError):
        Intake.model_validate({"symptom": "x", "mode": "turbo"})
    assert {Intake.model_validate({"symptom": "x", "mode": m}).mode for m in config.MODES} == set(config.MODES)


# Mutation: `RunResult.mode` typed `str`, or `extra` changed to "ignore"
# (a misspelled wrapper field would be dropped silently).
def test_run_result_wraps_a_brief() -> None:
    result = RunResult(brief=Brief.model_validate(_ok()), thread_id="t1", mode="replay", generated_at="2026-09-17")
    assert result.brief.status == "ok" and result.cost_usd == 0.0
    with pytest.raises(ValidationError):
        RunResult(brief=_ok(), thread_id="t1", mode="live", generated_at="x")
    with pytest.raises(ValidationError):
        RunResult(brief=_ok(), thread_id="t1", mode="replay", generated_at="x", cost=1)


# ---------------------------------------------------------------------------
# Phase 2 schema pass (decision 29)
# ---------------------------------------------------------------------------


# Mutation: `Warranty.record_id` or `Warranty.terms` given no default (a v1
# warranty fails), or written by `Warranty._v1_writes` (the v1 dump grows keys).
def test_warranty_record_id_and_terms_are_code_fields_absent_from_v1_dumps() -> None:
    v1 = _ok(warranty={"age_statement": "About 12 years old.", "cautions": [], "verify": []})
    assert Brief.model_validate(v1).model_dump(exclude_unset=True)["warranty"] == v1["warranty"]
    v2 = _ok(warranty={"age_statement": "Installed 2021-05-01", "cautions": [], "verify": [],
                       "record_id": "appliance:appl-1", "terms": "Five years parts."})
    warranty = Brief.model_validate(v2).warranty
    assert warranty.record_id == "appliance:appl-1" and warranty.terms == "Five years parts."
    with pytest.raises(ValidationError):
        Brief.model_validate(_ok(warranty={"age_statement": "a", "record_id": 5}))


# Mutation: `DraftWarranty` keeps an `age_statement` field, or the converter
# copies a model supplied age statement instead of writing the placeholder.
def test_draft_warranty_leaves_the_age_statement_to_code() -> None:
    assert "age_statement" not in BriefDraft.model_fields["warranty"].annotation.__args__[0].model_fields
    raw = _draft(warranty={"age_statement": "The model says 3 years old.", "cautions": [], "verify": ["date"]})
    brief = draft_to_brief_dict(BriefDraft.model_validate(raw), REGISTRY)
    assert brief["warranty"] == {"age_statement": "", "cautions": [], "verify": ["date"],
                                 "record_id": None, "terms": None}
    missing = _draft(warranty={"cautions": [], "verify": []})
    assert BriefDraft.model_validate(missing).warranty.verify == []


# ---------------------------------------------------------------------------
# SC1b schema layer (PLAN section 5): BriefDraft against the parity layer
# ---------------------------------------------------------------------------
# The payloads are the SC1b ones, split by `helpers.split_sc1b_payload` into a
# draft of the fields BriefDraft shares with v1, a code built source registry,
# and the v1 view of the same split. The v1 view goes through `v1_parity`
# alone; the draft goes through `BriefDraft.model_validate` and the validate
# node. A schema error is named by the v1 reason code for the same field,
# taking the error v1 would meet first.

_SC1B_TRAIL = [{"query": q, "n_results": 5, "credits": 1, "at": "t", "status": "ok"}
               for q in ("Aquarest ZX-9000 service manual", "Aquarest ZX-9000 error codes")]
_V1_FIELD_ORDER = ("status", "no_reliable_answer", "try_first", "candidates", "warranty_caution",
                   "warranty", "happened_before")
_V1_SUBFIELD_ORDER = {
    "try_first": ("step", "detail", "source_index"),
    "candidates": ("documented_meaning", "documented_action", "why_shown", "source_index"),
    "warranty": ("cautions", "verify"),
    "no_reliable_answer": ("found", "why_insufficient"),
}
_V1_REASON_BY_FIELD = {
    ("try_first", "step"): "step_not_text",
    ("try_first", "detail"): "step_detail_not_text",
    ("try_first", "source_index"): "step_index_out_of_range",
    ("candidates", "documented_meaning"): "candidate_meaning_not_text",
    ("candidates", "documented_action"): "candidate_action_not_text",
    ("candidates", "why_shown"): "candidate_why_shown_not_text",
    ("candidates", "source_index"): "candidate_index_out_of_range",
    ("warranty_caution", "text"): "warranty_caution_not_text",
    ("warranty", "cautions"): "warranty_caution_entry_not_text",
    ("warranty", "verify"): "warranty_verify_malformed",
    ("happened_before", "summary"): "happened_before_summary_not_text",
    ("no_reliable_answer", "found"): "nra_found_malformed",
    ("no_reliable_answer", "why_insufficient"): "nra_why_insufficient_not_text",
}


def _schema_error_reason(err: dict) -> str:
    """The v1 reason code for one BriefDraft validation error."""
    from agent.rules.v1_parity import js_truthy

    loc, value = tuple(err["loc"]), err.get("input")
    if loc == ():
        return "v1_type_error" if value is None else "invalid_status"
    field, rest = loc[0], loc[1:]
    if field == "status":
        return "invalid_status"
    if field in ("try_first", "candidates") and len(rest) == 1:
        # A whole element of the wrong type: v1 throws on null, else fails the first text check.
        return "v1_type_error" if value is None else _V1_REASON_BY_FIELD[(field, _V1_SUBFIELD_ORDER[field][0])]
    if field in ("try_first", "candidates") and len(rest) >= 2:
        return _V1_REASON_BY_FIELD.get((field, rest[1]), f"unmapped:{'.'.join(map(str, loc))}")
    if field == "warranty_caution":
        return "warranty_caution_not_text"
    if field == "happened_before" and not rest:
        return "v1_type_error" if js_truthy(value) and not isinstance(value, (list, dict)) else "unmapped:happened_before"
    if field == "warranty" and not rest:
        return "warranty_age_statement_not_text" if js_truthy(value) else "unmapped:warranty"
    if field == "warranty" and rest == ("cautions",):
        return "unmapped:warranty.cautions"
    if field == "no_reliable_answer" and not rest:
        return "nra_searched_malformed" if js_truthy(value) else "unmapped:no_reliable_answer"
    if rest:
        return _V1_REASON_BY_FIELD.get((field, rest[0]), f"unmapped:{'.'.join(map(str, loc))}")
    return f"unmapped:{field}"


def _v1_order(loc: tuple) -> tuple:
    if not loc:
        return (-1,)
    field = loc[0]
    rank = _V1_FIELD_ORDER.index(field) if field in _V1_FIELD_ORDER else len(_V1_FIELD_ORDER)
    rest = [p if isinstance(p, int) else _V1_SUBFIELD_ORDER.get(field, ()).index(p)
            if p in _V1_SUBFIELD_ORDER.get(field, ()) else 99 for p in loc[1:]]
    return (rank, *rest)


def _schema_layer_outcome(draft: object, registry: list[dict], tmp_path: Path) -> tuple[str, str | None]:
    """(decision, reason code) from BriefDraft plus the validate node."""
    from langgraph.runtime import Runtime

    from agent.nodes.validate import validate
    from agent.state import RunContext

    try:
        BriefDraft.model_validate(draft)
    except ValidationError as exc:
        first = min(exc.errors(), key=lambda e: _v1_order(tuple(e["loc"])))
        return "reject", _schema_error_reason(first)
    ctx = RunContext(run_id="run-sc1b", mode="replay", ledger_path=tmp_path / "ledger.sqlite",
                     registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                     pages_dir=tmp_path / "pages")
    state = {"draft": draft, "sources": registry, "search_trail": _SC1B_TRAIL, "observed_code": None,
             "history_hits": [], "identity": {"manufacturer": "Aquarest", "model": "ZX-9000"},
             "validation_failures": 0, "status": "running"}
    out = validate(state, Runtime(context=ctx))
    if out["validation_errors"]:
        return "reject", out["validation_errors"][0].split(":", 1)[0]
    return "accept", None


def _parity_outcome(view: object) -> tuple[str, str | None]:
    from agent.rules.v1_parity import ParityError, validate_brief_v1

    try:
        validate_brief_v1(view, accept_budget_stopped=False)
    except ParityError as exc:
        return "reject", exc.reason
    return "accept", None


# Payloads where the schema layer and the parity layer are known to part ways,
# each as (parity outcome, schema layer outcome, why). The test asserts both
# outcomes exactly, so a delta cannot hide any further drift. None of these can
# reach production: under json_schema output the model's reply already fits
# BriefDraft. Rows naming v1_test_* apply once the v1 payload file resolves.
_NO_CITES = "v2 rule 11: after pruning, an ok brief must cite a source (ok_no_cited_sources)"
_NO_LIST = "BriefDraft requires a list; v1 turns a non list into []"
_FALSY = "BriefDraft takes an object or null; v1 skips a falsy primitive"
_OK = ("accept", None)
SCHEMA_DELTAS: dict[str, tuple[tuple[str, str | None], tuple[str, str | None], str]] = {
    "v1_test_08": (_OK, ("reject", "ok_no_cited_sources"), _NO_CITES),
    "v1_test_09": (_OK, ("reject", "ok_no_cited_sources"), _NO_CITES),
    "edge_lists_missing": (_OK, ("reject", "ok_no_cited_sources"), _NO_CITES),
    "v1_test_17": (_OK, ("reject", "warranty_caution_entry_not_text"),
                   "DraftCaution is an object; v1 turns a bare string caution into one"),
    "edge_warranty_caution_mixed": (_OK, ("reject", "warranty_caution_entry_not_text"),
                                    "DraftCaution is an object; v1 turns a bare string caution into one"),
    "edge_warranty_entry_index_missing": (_OK, ("reject", "warranty_caution_entry_not_text"),
                                          "DraftCaution.source_index is required (null when uncited)"),
    "edge_caution_index_missing": (_OK, ("reject", "warranty_caution_not_text"),
                                   "DraftCaution.source_index is required (null when uncited)"),
    "edge_status_uppercase": (("reject", "invalid_status"), _OK, "BriefDraft lowercases status (decision 22)"),
    "edge_try_first_string": (_OK, ("reject", "unmapped:try_first"), _NO_LIST),
    "edge_candidates_object": (_OK, ("reject", "unmapped:candidates"), _NO_LIST),
    "edge_lists_null_on_nra": (_OK, ("reject", "unmapped:try_first"), _NO_LIST),
    "edge_warranty_cautions_string": (_OK, ("reject", "unmapped:warranty.cautions"), _NO_LIST),
    "edge_nra_block_false": (("reject", "nra_block_missing"), ("reject", "unmapped:no_reliable_answer"), _FALSY),
    "edge_caution_zero": (_OK, ("reject", "warranty_caution_not_text"), _FALSY),
    "edge_caution_empty_string": (_OK, ("reject", "warranty_caution_not_text"), _FALSY),
    "edge_caution_false": (_OK, ("reject", "warranty_caution_not_text"), _FALSY),
    "edge_warranty_zero": (_OK, ("reject", "unmapped:warranty"), _FALSY),
    "edge_hb_false": (_OK, ("reject", "unmapped:happened_before"), _FALSY),
    "edge_hb_empty_string": (_OK, ("reject", "unmapped:happened_before"), _FALSY),
    "edge_hb_array": (_OK, ("reject", "unmapped:happened_before"),
                      "BriefDraft takes an object or null; v1 keeps an array unchanged"),
    "edge_nra_skips_item_checks": (_OK, ("reject", "v1_type_error"),
                                   "BriefDraft checks items on the refusal path too; v1 skips them"),
    "edge_ok_nra_malformed": (_OK, ("reject", "nra_searched_malformed"),
                              "BriefDraft checks the refusal block on the ok path too; v1 nulls it unchecked"),
    "edge_cand_code_number": (_OK, ("reject", "unmapped:candidates.0.code"),
                              "DraftCandidate.code is text or null; v1 never checks code"),
    "edge_cand_code_object": (_OK, ("reject", "unmapped:candidates.0.code"),
                              "DraftCandidate.code is text or null; v1 never checks code"),
    "edge_empty_sources_before_steps": (("reject", "ok_empty_sources"), ("reject", "step_not_text"),
                                        "the schema runs before the parity layer's empty bibliography check"),
}


def _sc1b_schema_params() -> list:
    from agent.tests.helpers import sc1b_payloads

    return [pytest.param(row["id"], row["payload"], id=row["id"]) for row in sc1b_payloads()]


# Mutation: schemas_draft_who_bare_literal (`DraftCandidate.who` becomes a
# bare `Who` Literal with no before validator, so "plumber" is rejected), or
# schemas_draft_step_index_lax (`DraftStep.source_index` becomes a lax int, so
# true and "0" pass).
@pytest.mark.parametrize(("payload_id", "payload"), _sc1b_schema_params())
def test_draft_fields_share_parity_semantics(payload_id: str, payload: object, tmp_path: Path) -> None:
    from agent.tests.helpers import split_sc1b_payload

    draft, registry, view = split_sc1b_payload(payload)
    parity = _parity_outcome(view)
    schema = _schema_layer_outcome(copy.deepcopy(draft), registry, tmp_path)
    if payload_id in SCHEMA_DELTAS:
        expected_parity, expected_schema, _why = SCHEMA_DELTAS[payload_id]
        assert (parity, schema) == (expected_parity, expected_schema)
    else:
        assert schema == parity
