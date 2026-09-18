"""The validate and refuse nodes, SC3a and the ported v1 refusal tests (PLAN 5, 6.1).

v1 shaped payloads are split into a model draft plus a code built source
registry by `split_v1_payload`, then run through the validate node. Each test
names, in a comment above it, a code change that turns it red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from langgraph.runtime import Runtime

from agent.nodes.refuse import build_budget_stopped_brief, refuse
from agent.nodes.validate import validate
from agent.rules.clearing import CLEARED_LISTS, clear_on_refusal
from agent.rules.pipeline import run_rules
from agent.state import RunContext, check_json_native

TRAIL = [
    {"query": "Maker Model 1 service manual", "n_results": 5, "credits": 1, "at": "t", "status": "ok"},
    {"query": "Maker Model 1 error codes", "n_results": 5, "credits": 1, "at": "t", "status": "ok"},
]
SOURCE = {"title": "Maker manual", "url": "https://maker.example.com/m", "tier": "manufacturer"}


def split_v1_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A v1 brief as (BriefDraft dict, code built source registry)."""
    sources = [
        {"source_id": f"src-{i}", "url": s["url"], "title": s.get("title"),
         "retrieved_at": "2026-09-18", "origin": "search", "text_sha256": None, "snippet": ""}
        for i, s in enumerate(payload.get("sources") or [])
    ]
    draft = {
        "status": payload["status"],
        "matched_identity": payload.get("matched_identity") if isinstance(payload.get("matched_identity"), str) else None,
        "warranty_caution": payload.get("warranty_caution"),
        "happened_before": None,
        "try_first": [
            {"step": s["step"], "detail": s.get("detail") or "", "safety_flag": s.get("safety_flag") is True,
             "source_index": s["source_index"]}
            for s in payload.get("try_first") or []
        ],
        "candidates": [
            {"code": c.get("code"), "documented_meaning": c["documented_meaning"],
             "documented_action": c["documented_action"], "who": c.get("who") or "technician",
             "why_shown": c.get("why_shown") or "", "source_index": c["source_index"],
             "confirmed": c.get("confirmed") is True, "evidence": ""}
            for c in payload.get("candidates") or []
        ],
        "warranty": None,
        "no_reliable_answer": (
            {"found": payload["no_reliable_answer"].get("found") or [],
             "why_insufficient": payload["no_reliable_answer"]["why_insufficient"]}
            if payload.get("no_reliable_answer") else None
        ),
        "upgrade_options": [],
        "maintenance_due": [],
        "source_tiers": [
            {"source_index": i, "tier": s["tier"], "authorship_quote": ""}
            for i, s in enumerate(payload.get("sources") or [])
        ],
    }
    return draft, sources


def make_ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id="run-test", mode="replay", ledger_path=tmp_path / "ledger.sqlite",
        registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
        pages_dir=tmp_path / "pages",
    )


def run_validate(tmp_path: Path, draft: dict | None, sources: list[dict], **state: Any) -> dict:
    full = {"draft": draft, "sources": sources, "search_trail": TRAIL, "observed_code": None,
            "history_hits": [], "identity": {"manufacturer": "Maker", "model": "Model 1"},
            "validation_failures": 0, "status": "running", **state}
    out = validate(full, Runtime(context=make_ctx(tmp_path)))
    check_json_native(out)
    return out


def upgrade(index: int = 0) -> dict:
    return {"successor_manufacturer": "Maker", "successor_model": "Model 2", "reason": "discontinued",
            "summary": "Replaced by Model 2", "source_index": index, "evidence": ""}


def maintenance(index: int = 0) -> dict:
    return {"task": "Drain and refill", "interval": "every 3 months", "source_index": index, "evidence": "",
            "last_done_record_id": ""}


# v1 guardrail test 2, plus an upgrade and a maintenance entry (decision 9).
# Mutation: `clear_on_refusal` stops clearing (for example CLEARING_STATUSES
# loses "no_reliable_answer"), so the upgrade and maintenance entries survive.
def test_refusal_clears_candidates_and_steps(tmp_path: Path) -> None:
    payload = {
        "status": "no_reliable_answer",
        "matched_identity": "Maker series",
        "try_first": [{"step": "Check the filter", "detail": "Generic advice.", "safety_flag": False,
                       "source_index": 0}],
        "candidates": [{"code": "FLO", "documented_meaning": "Invented meaning",
                        "documented_action": "Invented action", "who": "anyone",
                        "why_shown": "Guessed", "source_index": 0, "confirmed": False}],
        "no_reliable_answer": {"searched": ["model text"], "found": [], "why_insufficient": "Nothing found."},
        "sources": [dict(SOURCE)],
    }
    draft, sources = split_v1_payload(payload)
    draft["upgrade_options"] = [upgrade()]
    draft["maintenance_due"] = [maintenance()]
    out = run_validate(tmp_path, draft, sources)
    brief = out["brief"]
    assert out["status"] == "no_reliable_answer" and out["validation_errors"] == []
    assert out["refusal_origin"] == "model"
    for key in CLEARED_LISTS:
        assert brief[key] == [], key
    assert brief["no_reliable_answer"]["searched"] == [t["query"] for t in TRAIL]
    assert "Invented" not in str(brief)


_text = st.text(min_size=1, max_size=12)
_index = st.integers(min_value=0, max_value=2)
_steps = st.lists(st.builds(lambda s, f, i: {"step": s, "detail": "", "safety_flag": f, "source_index": i},
                            _text, st.booleans(), _index), max_size=4)
_cands = st.lists(st.builds(
    lambda code, m, c, i: {"code": code, "documented_meaning": m, "documented_action": m, "who": "anyone",
                           "why_shown": "", "source_index": i, "confirmed": c, "evidence": ""},
    st.one_of(st.none(), _text), _text, st.booleans(), _index), max_size=4)
_upgrades = st.lists(_index.map(upgrade), max_size=3)
_maint = st.lists(_index.map(maintenance), max_size=3)


# SC3a property. Mutation: remove the clearing loop in `clear_on_refusal`, or
# drop "budget_stopped" from CLEARING_STATUSES.
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(steps=_steps, cands=_cands, upgrades=_upgrades, maint=_maint, observed=st.sampled_from([None, "FLO"]))
def test_refusal_clearing_property(steps, cands, upgrades, maint, observed) -> None:
    sources = [{"url": f"https://s{i}.example.com/p", "title": f"S{i}", "origin": "search"} for i in range(3)]
    draft = {
        "status": "no_reliable_answer", "matched_identity": None, "warranty_caution": None,
        "happened_before": None, "try_first": steps, "candidates": cands, "warranty": None,
        "no_reliable_answer": {"found": [], "why_insufficient": "nothing documented"},
        "upgrade_options": upgrades, "maintenance_due": maint,
        "source_tiers": [{"source_index": i, "tier": "dealer", "authorship_quote": ""} for i in range(3)],
    }
    result = run_rules(draft, sources=sources, observed_code=observed, history_hits=[], registry=None,
                       mode="replay", provenance=None, pass_kind="synthesize", search_trail=TRAIL)
    assert result.errors == []
    for key in CLEARED_LISTS:
        assert result.brief[key] == []
    stopped = {"status": "budget_stopped", "try_first": list(steps), "candidates": list(cands),
               "upgrade_options": list(upgrades), "maintenance_due": list(maint)}
    clear_on_refusal(stopped)
    assert all(stopped[key] == [] for key in CLEARED_LISTS)
    budget = build_budget_stopped_brief({"sources": sources, "search_trail": TRAIL, "stop_reason": "run_cap"})
    assert all(budget[key] == [] for key in CLEARED_LISTS)


# v1 guardrail test 1. Mutation: the parity layer's refusal branch requires a
# non empty sources list, or validate reports the model's refusal as "forced".
def test_nra_with_empty_lists_is_accepted(tmp_path: Path) -> None:
    payload = {
        "status": "no_reliable_answer", "matched_identity": None, "try_first": [], "candidates": [],
        "no_reliable_answer": {"searched": ["x"], "found": [], "why_insufficient": "No documentation exists."},
        "sources": [],
    }
    draft, sources = split_v1_payload(payload)
    out = run_validate(tmp_path, draft, sources)
    assert out["validation_errors"] == []
    assert out["status"] == "no_reliable_answer" and out["refusal_origin"] == "model"
    assert out["brief"]["candidates"] == [] and out["brief"]["try_first"] == []
    assert out["brief"]["no_reliable_answer"]["why_insufficient"] == "No documentation exists."


# v1 guardrail test 4. Mutation: `_check_refusal_block` skips the truthiness
# check, or validate does not increment validation_failures on an error.
def test_nra_without_explanation_block_is_rejected(tmp_path: Path) -> None:
    payload = {"status": "no_reliable_answer", "try_first": [], "candidates": [], "sources": []}
    draft, sources = split_v1_payload(payload)
    out = run_validate(tmp_path, draft, sources, validation_failures=1)
    assert out["brief"] is None and out["status"] == "running"
    assert out["validation_failures"] == 2
    assert [e.split(":")[0] for e in out["validation_errors"]] == ["nra_block_missing"]


# v1 guardrail test 5. Mutation: remove the `len(sources) == 0` check in
# `validate_brief_v1`, or the post prune `ok_no_cited_sources` check.
def test_ok_with_empty_sources_is_rejected(tmp_path: Path) -> None:
    payload = {"status": "ok", "try_first": [], "candidates": [], "sources": [], "no_reliable_answer": None}
    draft, sources = split_v1_payload(payload)
    out = run_validate(tmp_path, draft, sources)
    assert out["brief"] is None and out["validation_failures"] == 1
    assert [e.split(":")[0] for e in out["validation_errors"]] == ["ok_empty_sources"]
    # Registered but uncited sources are pruned, and an ok brief must still cite one.
    uncited = [{"url": "https://s.example.com/p", "title": "S", "origin": "search"}]
    out = run_validate(tmp_path, draft, uncited)
    assert [e.split(":")[0] for e in out["validation_errors"]] == ["ok_no_cited_sources"]


# Mutation: validate treats a missing draft as a pass (no increment), or
# keeps the previous brief; or the draft schema check is skipped.
def test_missing_or_malformed_draft_counts_as_a_failure(tmp_path: Path) -> None:
    out = run_validate(tmp_path, None, [], validation_errors=["draft_schema: parsing_error"])
    assert out["validation_failures"] == 1 and out["brief"] is None
    assert out["validation_errors"] == ["draft_schema: parsing_error"]
    out = run_validate(tmp_path, {"status": "ok"}, [])
    assert out["validation_failures"] == 1
    assert out["validation_errors"] and all(e.startswith("draft_schema:") for e in out["validation_errors"])


# Mutation: refuse sets refusal_origin "model", copies the draft's candidates,
# or fills `searched` from source titles instead of the trail queries.
def test_refuse_node_builds_forced_refusal_from_the_trail(tmp_path: Path) -> None:
    state = {"search_trail": TRAIL, "validation_errors": ["observed_code_no_match: none"],
             "draft": {"candidates": [{"documented_meaning": "Invented"}]},
             "sources": [{"url": "https://s.example.com/p", "title": "Title"}], "observed_code": "FLO"}
    out = refuse(state, Runtime(context=make_ctx(tmp_path)))
    check_json_native(out)
    brief = out["brief"]
    assert out["status"] == "no_reliable_answer" and out["refusal_origin"] == "forced"
    assert brief["status"] == "no_reliable_answer"
    assert brief["no_reliable_answer"]["searched"] == [t["query"] for t in TRAIL]
    assert "observed_code_no_match" in brief["no_reliable_answer"]["why_insufficient"]
    assert all(brief[key] == [] for key in CLEARED_LISTS)
    assert "Invented" not in str(brief)


# Mutation: the budget stop brief keeps a non web source, drops the trail, or
# validate overwrites a budget stop with a validation failure.
def test_budget_stop_brief_carries_sources_trail_and_reason(tmp_path: Path) -> None:
    sources = [{"url": "https://www.reddit.com/r/x", "title": "Thread", "origin": "search"},
               {"url": "javascript:alert(1)", "title": "Bad", "origin": "search"}]
    out = run_validate(tmp_path, None, sources, status="budget_stopped", stop_reason="run_cap_usd")
    brief = out["brief"]
    assert "validation_failures" not in out and "status" not in out
    assert brief["status"] == "budget_stopped"
    assert [s["url"] for s in brief["sources"]] == ["https://www.reddit.com/r/x"]
    assert brief["sources"][0]["tier"] == "forum"
    assert brief["no_reliable_answer"]["searched"] == [t["query"] for t in TRAIL]
    assert "run_cap_usd" in brief["no_reliable_answer"]["why_insufficient"]


# Mutation: `run_rules` stops checking pass_kind.
@pytest.mark.parametrize("pass_kind", ["main", ""])
def test_unknown_pass_kind_is_refused(pass_kind: str) -> None:
    with pytest.raises(ValueError):
        run_rules({}, sources=[], observed_code=None, history_hits=[], registry=None, mode="replay",
                  provenance=None, pass_kind=pass_kind)


def _ok_draft(**overrides: Any) -> dict:
    d = {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [], "candidates": [], "warranty": None, "no_reliable_answer": None,
        "upgrade_options": [], "maintenance_due": [], "source_tiers": [],
    }
    d.update(overrides)
    return d


def _rules(d: dict, **kwargs: Any):
    base = {"sources": [{"url": "https://s.example.com/p", "title": "S", "origin": "search"}],
            "observed_code": None, "history_hits": [], "registry": None, "mode": "replay",
            "provenance": None, "pass_kind": "synthesize"}
    base.update(kwargs)
    return run_rules(d, **base)


# Decision 8. Mutation: `safety_first` sorts on the step text, or reverses the
# key (safety last), or is not stable (a non stable sort shuffles equal keys).
def test_safety_steps_move_first_in_stable_order() -> None:
    flags = [True, False, True, False, False, True]
    steps = [{"step": f"step {i}", "detail": "", "safety_flag": f, "source_index": 0} for i, f in enumerate(flags)]
    brief = _rules(_ok_draft(try_first=steps)).brief
    assert [s["step"] for s in brief["try_first"]] == [
        "step 0", "step 2", "step 5", "step 1", "step 3", "step 4"]


HIT = {"record_id": "service:svc-1", "appliance_id": "appl-1", "date": "2026-03-02", "symptom": "FLO"}


# Rule 7. Mutation: `check_happened_before` keeps a record_id that was not
# loaded, ignores the appliance, or passes the model's value through.
@pytest.mark.parametrize("record_id,hits,appliance,kept", [
    ("service:svc-1", [HIT], "appl-1", True),
    ("service:svc-9", [HIT], "appl-1", False),
    ("service:svc-1", [{**HIT, "appliance_id": "appl-2"}], "appl-1", False),
    ("service:svc-1", [], "appl-1", False),
    ("", [HIT], "appl-1", False),
])
def test_happened_before_must_cite_a_loaded_record(record_id, hits, appliance, kept) -> None:
    hb = {"matches": True, "summary": "Same code last spring", "record_id": record_id}
    brief = _rules(_ok_draft(happened_before=hb, try_first=[
        {"step": "s", "detail": "", "safety_flag": False, "source_index": 0}]),
        history_hits=hits, registry={"id": appliance, "record_id": f"appliance:{appliance}"}).brief
    if kept:
        assert brief["happened_before"] == {"matches": True, "summary": "Same code last spring",
                                            "record_id": record_id}
    else:
        assert brief["happened_before"] is None


# Decision 29. Mutation: the age comes from the manufacture date when the
# registry has an install date, the record_id is dropped, a model supplied age
# statement passes through, or the terms are not copied from the registry.
def test_age_statement_and_terms_are_written_by_code() -> None:
    from datetime import date

    today = date(2026, 9, 18)
    warranty = {"cautions": [], "verify": [], "age_statement": "Model says brand new."}
    step = [{"step": "s", "detail": "", "safety_flag": False, "source_index": 0}]
    row = {"id": "appl-1", "record_id": "appliance:appl-1", "install_date": "2021-10-04",
           "purchase_date": "2021-09-01", "warranty_terms": "Five years parts, one year labor."}
    brief = _rules(_ok_draft(warranty=warranty, try_first=step), registry=row,
                   identity={"manufacture_date": "2019-06"}, today=today).brief
    w = brief["warranty"]
    assert w["age_statement"].startswith("Installed 2021-10-04") and "about 4 years old" in w["age_statement"]
    assert w["record_id"] == "appliance:appl-1"
    assert w["terms"] == "Five years parts, one year labor."
    assert "Model says" not in str(brief)
    plate = _rules(_ok_draft(warranty=warranty, try_first=step), identity={"manufacture_date": "2019-06"},
                   today=today).brief["warranty"]
    assert plate["age_statement"].startswith("Manufactured 2019-06") and "about 7 years old" in plate["age_statement"]
    assert plate["record_id"] is None and plate["terms"] is None
    unknown = _rules(_ok_draft(warranty=warranty, try_first=step), today=today).brief["warranty"]
    assert "unknown" in unknown["age_statement"]
    created = _rules(_ok_draft(try_first=step), registry=row, today=today).brief["warranty"]
    assert created["record_id"] == "appliance:appl-1" and created["cautions"] == []


# Page text reaches the tier rule through the validate node (T6): a dealer host
# keeps a manufacturer proposal only with a verbatim authorship quote found in
# the fetched page text, read from pages/<sha256>.txt, else from the cassette's
# synthetic page_texts. Mutation validate_page_texts_off: validate passes
# page_texts={} to run_rules, so every case ends dealer.
@pytest.mark.parametrize("where", ["pages_dir", "cassette", "absent"])
def test_validate_reads_page_text_for_the_authorship_quote(tmp_path: Path, where: str) -> None:
    from agent.research.tools import save_page_text

    url = "https://www.spaparts.example.com/optima-manual.pdf"
    page = "Optima owner's manual. This manual was written and published by Sundance Spas. Section 4."
    ctx = make_ctx(tmp_path)
    sha = None
    if where == "pages_dir":
        sha = save_page_text(ctx.pages_dir, page)
    elif where == "cassette":
        ctx.cassette = {"page_texts": {url: page}}
    source = {"source_id": "src-0", "url": url, "host": "www.spaparts.example.com", "title": "Manual",
              "retrieved_at": "2026-09-18", "origin": "extract", "text_sha256": sha, "snippet": ""}
    draft = _ok_draft(
        try_first=[{"step": "Clean the filter", "detail": "", "safety_flag": False, "source_index": 0}],
        source_tiers=[{"source_index": 0, "tier": "manufacturer",
                       "authorship_quote": "This manual was written and published by Sundance Spas."}],
    )
    state = {"draft": draft, "sources": [source], "search_trail": TRAIL, "observed_code": None,
             "history_hits": [], "identity": {"manufacturer": "Sundance Spas", "model": "Optima 880"},
             "validation_failures": 0, "status": "running"}
    out = validate(state, Runtime(context=ctx))
    assert out["validation_errors"] == []
    expected = "dealer" if where == "absent" else "manufacturer"
    assert out["brief"]["sources"][0]["tier"] == expected


def test_future_install_date_falls_back_to_the_next_date() -> None:
    """Mutation citations_future_date_counts: an install date later this month
    reads as "about 0 years old" instead of falling back to the purchase date;
    citations_age_ignores_day: the day of the month is ignored, so a unit
    installed a week after this date five years ago is "about 5 years old"."""
    from datetime import date

    from agent.rules.citations import age_statement

    today = date(2026, 9, 18)
    statement, record = age_statement(
        registry={"record_id": "appliance:appl-1", "install_date": "2026-09-25", "purchase_date": "2026-08-01"},
        manufacture_date="2019-06", today=today)
    assert statement.startswith("Purchased 2026-08-01") and "about 0 years old" in statement
    assert record == "appliance:appl-1"
    plate, record = age_statement(registry={"record_id": "appliance:appl-1", "install_date": "2026-09-25"},
                                  manufacture_date="2019-06", today=today)
    assert plate.startswith("Manufactured 2019-06") and record is None
    days, _ = age_statement(registry={"record_id": "appliance:appl-1", "install_date": "2021-09-25"},
                            manufacture_date=None, today=today)
    assert "about 4 years old" in days
    on_the_day, _ = age_statement(registry={"record_id": "appliance:appl-1", "install_date": "2021-09-18"},
                                  manufacture_date=None, today=today)
    assert "about 5 years old" in on_the_day
