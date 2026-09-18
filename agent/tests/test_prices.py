"""Prices (PLAN section 5, decision 28; 8.5 rule 10): an amount needs a verified quote.

Offline: drafts go straight through `run_rules` against the synthetic page
texts of agent/tests/fixtures/discontinued.json (example.com and
example.org URLs only; every price there is synthetic). Each test names the
mutation that turns it red.
"""

from __future__ import annotations

import copy
import json

import pytest

from agent.rules.pipeline import run_rules
from agent.rules.prices import DROP_PRICE, PRICE_UNQUOTED, amounts, check_prices
from agent.tests.test_sc9_upgrades import FIXTURE, NOTICE, SX200_QUOTE, page

PRICE_QUOTE = "The SX-200 lists at $349 in this synthetic notice."
SX300_QUOTE = "Owners who want more capacity can also look at the SX-300 in the same synthetic line."
SOURCES = [{"source_id": "src-0", "url": NOTICE, "title": "Synthetic notice", "host": "example.com",
            "retrieved_at": "2026-09-18T00:00:00+00:00", "origin": "search"}]
PAGES = {NOTICE: page(NOTICE)}


def _upgrade(successor: str, summary: str, evidence: str) -> dict:
    return {"successor_manufacturer": "", "successor_model": successor, "reason": "discontinued",
            "summary": summary, "source_index": 0, "evidence": evidence}


def _draft(**over) -> dict:
    draft = {
        "status": "ok", "matched_identity": "Synthetic match", "warranty_caution": None, "happened_before": None,
        "try_first": [{"step": "Clean the bucket filter", "detail": "", "safety_flag": False, "source_index": 0}],
        "candidates": [], "warranty": None, "no_reliable_answer": None,
        "upgrade_options": [], "maintenance_due": [],
        "source_tiers": [{"source_index": 0, "tier": "manufacturer", "authorship_quote": FIXTURE["authorship_quote"]}],
    }
    draft.update(over)
    return draft


def _rules(draft: dict):
    return run_rules(draft, sources=copy.deepcopy(SOURCES), observed_code=None, history_hits=[], registry=None,
                     mode="replay", provenance=None, pass_kind="synthesize",
                     identity=dict(FIXTURE["identity"]), page_texts=PAGES)


def test_currency_amount_needs_verified_quote() -> None:
    """Mutations: prices_hook_off (the pipeline stops calling rule 10);
    prices_quote_not_verified (an amount inside the quote passes even when the
    quote is not verbatim in the page); prices_v1_fields_unchecked (a v1 field
    with an amount no longer fails validation)."""
    assert amounts("It lists at $349, or 12 USD, for 5 pounds of refrigerant.") == ["$349", "12 USD"]

    upgrades = [
        _upgrade("SX-200", "The maker lists the SX-200 at $349.", PRICE_QUOTE),  # amount in the verified quote
        _upgrade("SX-200", "The SX-200 costs about $299.", SX200_QUOTE),  # quote verifies, amount not in it
        _upgrade("SX-200", "The SX-200 lists at $349.",
                 "The SX-200 lists at $349 in this notice."),  # quote not in the page: rule 9 drops it first
        _upgrade("SX-300", "Also consider the SX-300.", SX300_QUOTE),  # no amount at all
    ]
    maintenance = [
        {"task": "Clean the bucket filter (a $20 kit)", "interval": "every 3 months", "source_index": 0,
         "evidence": "Clean the bucket filter every 3 months.", "last_done_record_id": ""},
        {"task": "Clean the bucket filter", "interval": "every 3 months", "source_index": 0,
         "evidence": "Clean the bucket filter every 3 months.", "last_done_record_id": ""},
    ]
    result = _rules(_draft(upgrade_options=upgrades, maintenance_due=maintenance))
    assert result.errors == []
    brief = result.brief
    assert [(o["successor_model"], o["summary"]) for o in brief["upgrade_options"]] == [
        ("SX-200", "The maker lists the SX-200 at $349."), ("SX-300", "Also consider the SX-300.")]
    assert [m["task"] for m in brief["maintenance_due"]] == ["Clean the bucket filter"]
    assert [(d["list"], d["index"], d["reason"]) for d in result.dropped] == [
        ("upgrade_options", 2, "not_in_page_text"),
        ("upgrade_options", 1, "price_not_in_verified_quote"),
        ("maintenance_due", 0, "price_not_in_verified_quote")]

    # A v1 field: an amount with no quote fails validation; inside a candidate's
    # verified quote it passes.
    step = {"step": "Replace the filter", "detail": "A new filter costs $25.", "safety_flag": False,
            "source_index": 0}
    failed = _rules(_draft(try_first=[step]))
    assert failed.brief is None
    assert [e.split(":")[0] for e in failed.errors] == [PRICE_UNQUOTED]
    candidate = {"code": None, "documented_meaning": "The unit is discontinued.",
                 "documented_action": "The SX-200 replaces it and lists at $349.", "who": "anyone",
                 "why_shown": "", "source_index": 0, "confirmed": False, "evidence": PRICE_QUOTE}
    assert _rules(_draft(candidates=[candidate])).errors == []
    reworded = {**candidate, "evidence": "The SX-200 is listed at $349 in this synthetic notice."}
    assert [e.split(":")[0] for e in _rules(_draft(candidates=[reworded])).errors] == [PRICE_UNQUOTED]


# ---------------------------------------------------------------------------
# Every v1 field group, the entry fields, the signs, and whole amounts
# ---------------------------------------------------------------------------


def _ok_brief(**over) -> dict:
    """A brief in the validated shape, for calling rule 10 directly."""
    brief = {"status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
             "try_first": [], "candidates": [], "warranty": None, "no_reliable_answer": None,
             "upgrade_options": [], "maintenance_due": [],
             "sources": [{"url": NOTICE, "tier": "manufacturer", "title": "Synthetic notice"}]}
    brief.update(over)
    return brief


_CANDIDATE = {"code": None, "documented_meaning": "The unit is discontinued.", "documented_action": "Replace it.",
              "who": "anyone", "why_shown": "", "source_index": 0, "confirmed": False, "evidence": SX200_QUOTE}
_REFUSAL = {"searched": ["sx-100 synthetic"], "found": [], "why_insufficient": "Nothing documented (synthetic)."}
_WARRANTY = {"age_statement": "", "cautions": [], "verify": []}

# (group path in the error, brief overrides putting "$75" in exactly that group)
V1_GROUP_CASES = {
    "candidates[0]": {"candidates": [{**_CANDIDATE, "why_shown": "The part costs $75 (synthetic)."}]},
    "happened_before": {"happened_before": {"matches": True, "summary": "Last fix cost $75 (synthetic).",
                                            "record_id": "service:svc-1"}},
    "warranty": {"warranty": {**_WARRANTY, "age_statement": "A $75 extension applies (synthetic)."}},
    "warranty_caution": {"warranty_caution": {"text": "A $75 fee voids it (synthetic).", "source_index": 0}},
    "warranty.verify": {"warranty": {**_WARRANTY, "verify": ["Expect a $75 service fee (synthetic)."]}},
    "matched_identity": {"matched_identity": "SX-100 (synthetic), sold for $75"},
    "no_reliable_answer.found": {"status": "no_reliable_answer", "sources": [],
                                 "no_reliable_answer": {**_REFUSAL, "found": ["A new pump runs about $75."]}},
    "no_reliable_answer.why_insufficient": {"status": "no_reliable_answer", "sources": [], "no_reliable_answer": {
        **_REFUSAL, "why_insufficient": "Replacement usually costs $75."}},
}


@pytest.mark.parametrize("path", list(V1_GROUP_CASES))
def test_every_v1_text_group_is_price_checked(path: str) -> None:
    """Mutations: prices_why_shown_unchecked (a candidate's why_shown left out),
    prices_happened_before_unchecked (the happened_before group left out),
    prices_age_statement_unchecked (the warranty age statement left out),
    prices_warranty_caution_unchecked (the top warranty caution left out),
    prices_warranty_verify_unchecked, prices_matched_identity_unchecked,
    prices_refusal_found_unchecked, prices_refusal_why_unchecked (each of
    those groups left out)."""
    errors = check_prices(_ok_brief(**copy.deepcopy(V1_GROUP_CASES[path])), page_texts=PAGES)
    assert [e.split(":")[0] for e in errors] == [PRICE_UNQUOTED], errors
    assert errors[0].startswith(f"{PRICE_UNQUOTED}: {path} states $75"), errors
    # The control: the same brief without the amount passes.
    clean = json.loads(json.dumps(V1_GROUP_CASES[path]).replace("$75", "a fee"))
    assert check_prices(_ok_brief(**clean), page_texts=PAGES) == []


def test_entry_fields_and_currency_signs_are_checked() -> None:
    """Mutations: prices_interval_unchecked (a maintenance interval is not
    checked), prices_successor_unchecked (the successor fields are not
    checked), prices_signs_dollar_only (only the dollar sign counts)."""
    assert amounts("costs \u20ac40 or \u00a330 or \u00a55000") == ["\u20ac40", "\u00a330", "\u00a55000"]
    maintenance = {"task": "Clean the bucket filter", "interval": "every 3 months ($20 visit)", "source_index": 0,
                   "evidence": "Clean the bucket filter every 3 months.", "last_done_record_id": None}
    upgrade = {"successor_manufacturer": None, "successor_model": "SX-200 $299 bundle", "reason": "discontinued",
               "summary": "The SX-200 replaces it.", "source_index": 0, "evidence": SX200_QUOTE}
    brief = _ok_brief(maintenance_due=[maintenance], upgrade_options=[upgrade])
    report: list[dict] = []
    assert check_prices(brief, page_texts=PAGES, report=report) == []
    assert brief["maintenance_due"] == [] and brief["upgrade_options"] == []
    assert [(d["list"], d["amounts"], d["reason"]) for d in report] == [
        ("upgrade_options", ["$299"], DROP_PRICE), ("maintenance_due", ["$20"], DROP_PRICE)]


BOUNDARY_URL = "https://example.com/synthetic/sx/price-boundary"
BOUNDARY_PAGE = ("SYNTHETIC PAGE TEXT for tests (not a real document, not written by any maker).\n"
                 "The replacement SX-200 kit is priced at $129 from the maker.\n"
                 "A synthetic service visit costs $1,299 or 1,299 USD, plus $1.25 per mile.\n")
BOUNDARY_QUOTE = "The replacement SX-200 kit is priced at $129 from the maker."
VISIT_QUOTE = "A synthetic service visit costs $1,299 or 1,299 USD, plus $1.25 per mile."


def test_amount_cut_from_a_longer_amount_is_not_quoted() -> None:
    """Mutations: prices_amount_not_whole (an amount that is only the start of
    a longer amount in the quote passes); prices_separator_joins_ignored (a
    "," or "." followed by a digit no longer marks a longer number)."""
    pages = {BOUNDARY_URL: BOUNDARY_PAGE}
    sources = [{"url": BOUNDARY_URL, "tier": "manufacturer", "title": "Synthetic price page"}]

    def upgrade(summary: str) -> dict:
        return {"successor_manufacturer": None, "successor_model": "SX-200", "reason": "discontinued",
                "summary": summary, "source_index": 0, "evidence": BOUNDARY_QUOTE}

    brief = _ok_brief(sources=sources, upgrade_options=[upgrade("The kit costs $12."), upgrade("The kit costs $129.")])
    report: list[dict] = []
    assert check_prices(brief, page_texts=pages, report=report) == []
    assert [o["summary"] for o in brief["upgrade_options"]] == ["The kit costs $129."]
    assert [(d["index"], d["amounts"]) for d in report] == [(0, ["$12"])]

    def with_why(why: str, evidence: str) -> dict:
        return _ok_brief(sources=sources, candidates=[{**_CANDIDATE, "why_shown": why, "evidence": evidence}])

    for why, evidence in (("Part costs $12.", BOUNDARY_QUOTE), ("A visit is $1.", VISIT_QUOTE),
                          ("Travel is $1 per mile.", VISIT_QUOTE), ("It is 299 USD.", VISIT_QUOTE)):
        errors = check_prices(with_why(why, evidence), page_texts=pages)
        assert [e.split(":")[0] for e in errors] == [PRICE_UNQUOTED], why
    for why, evidence in (("Part costs $129.", BOUNDARY_QUOTE), ("A visit is $1,299.", VISIT_QUOTE),
                          ("Travel is $1.25 per mile.", VISIT_QUOTE), ("It is 1,299 USD.", VISIT_QUOTE)):
        assert check_prices(with_why(why, evidence), page_texts=pages) == [], why
