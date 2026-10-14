"""Rule 13: the model never writes a warranty date (SC13).

All text here is synthetic or model written; none is third party page text.
Each test names, in its docstring, the planted bugs in mutations.toml that
turn it red.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent.nodes.refuse import build_forced_refusal_brief
from agent.rules.pipeline import run_rules
from agent.rules.warranty_dates import WARRANTY_DATE_WRITTEN, check_warranty_dates, dates

SOURCE = {"url": "https://maker.example.com/manual", "title": "Manual (synthetic)", "retrieved_at": "2026-10-01",
          "origin": "search", "text_sha256": None, "snippet": "Synthetic snippet."}

DATED = [
    "Coverage ends 2027-06-10.",
    "Covered from 2021-06 on.",
    "Warranty valid until 2027.",
    "The labor warranty is expiring in June.",
    "Parts are covered through the end of 2026.",
    "Coverage lapsed in 2025.",
    "Expires December.",
    "Registered on June 10.",
    "Registered 10 June.",
    "Ends June 2027.",
    "Ends Jun. 10, 2027.",
    "Ends May 2027.",
    "Ends May 10, 2027.",
    "Ends 10 May 2027.",
    "Ends the 10th of June.",
    "Ends 6/10/2021.",
    "Ends 6/10/27.",
    "Ends 06/2021.",
    "Ends 10.06.2021.",
    "Ends 06-10-2021.",
    "Ends 2027/06/10.",
    "Expires: June.",
    "Expires: 2027.",
    "Valid to 2027.",
    "Covered by warranty into 2027.",
    "Installed in 2021, so coverage has likely lapsed.",
    "Covered from 2021 to 2026.",
    "Covered since 2021.",
    "Ends Fall 2027.",
    "A 2000 series unit.",  # a bare four digit number counts, on purpose: a false positive costs only a retry
]

# Ordinary warranty wording, including the phrase the recorded notheating
# brief's caution uses ("purchasing through an authorized dealer").
BENIGN = [
    "Coverage generally applies to original residential owners purchasing through an authorized dealer.",
    "Purchasing through an authorized Sundance dealer may matter for coverage.",
    "Confirm through the dealer, in writing, how rental use is treated.",
    "Coverage may be reduced for rental use.",
    "Clause 4 may apply to rental use.",
    "Terms run 2 years from the install date, within 60 days of purchase.",
    "Recover the serial number and manufacture date from the data plate (model 4TTR6036xxxxxx).",
    "Shell 5 years, equipment 2 years, labor 1 year.",
    "Locate the date-of-manufacture on the plate.",
    "Use a 1/2 inch fitting; support is 24/7.",
    "Ask the dealer before May.",
    "The SX-100 (synthetic) uses firmware 1.5.10.",
    "Repairs near $2000 are common (synthetic).",
    "The SX-2000 (synthetic) is a different model.",
]


@pytest.mark.parametrize("text", DATED)
def test_every_date_form_is_found(text: str) -> None:
    """Mutations: warranty_dates_keyword_branch_dropped (until, through,
    expiring and the like no longer count), warranty_dates_numeric_dropped
    (2021-06-10 style dates no longer count), warranty_dates_month_day_dropped
    (June 10 no longer counts), warranty_dates_dashed_month_first_dropped
    (06-10-2021 no longer counts), warranty_dates_year_first_slash_dropped
    (2027/06/10 no longer counts), warranty_dates_bare_year_dropped (a year
    alone no longer counts), warranty_dates_keyword_colon_dropped (a colon
    after the keyword hides "Expires: June")."""
    assert dates(text), text


@pytest.mark.parametrize("text", BENIGN)
def test_ordinary_warranty_wording_passes(text: str) -> None:
    """Mutations: warranty_dates_keyword_any_word (a keyword followed by any
    word counts, so "through an authorized dealer" fails),
    warranty_dates_may_alone (the verb "may" before a number counts as May),
    warranty_dates_dollar_lookbehind_dropped (a price such as $2000 counts as
    a year)."""
    assert dates(text) == [], text


def _draft(**overrides: Any) -> dict[str, Any]:
    d = {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [{"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": 0}],
        "candidates": [], "no_reliable_answer": None, "upgrade_options": [], "maintenance_due": [],
        "warranty": {"cautions": [{"text": "Rental use may change coverage (synthetic).", "source_index": 0}],
                     "verify": ["Find the purchase paperwork (synthetic)."]},
        "source_tiers": [{"source_index": 0, "tier": "manufacturer", "authorship_quote": ""}],
    }
    d.update(overrides)
    return d


def _rules(draft: dict[str, Any]):
    registry = {"id": "appl-x", "record_id": "appliance:appl-x", "install_date": "2021-06-10"}
    return run_rules(draft, sources=[dict(SOURCE)], observed_code=None, history_hits=[], registry=registry,
                     mode="replay", provenance=None, pass_kind="synthesize", today=date(2026, 10, 1),
                     warranty_coverage=[])


def test_a_model_written_warranty_date_fails_validation_in_each_field() -> None:
    """Each model written warranty field fails on a date, and the code written
    age statement (which states the run date) never does. Mutations:
    pipeline_warranty_dates_unchecked (run_rules skips rule 13),
    warranty_dates_verify_unchecked (verify items are not checked),
    warranty_dates_top_caution_unchecked (warranty_caution is not checked)."""
    clean = _rules(_draft())
    assert clean.errors == [], clean.errors
    assert "as of 2026-10-01" in clean.brief["warranty"]["age_statement"]

    cases = {
        "warranty.verify": _draft(warranty={"cautions": [], "verify": ["Coverage ends June 2027 (synthetic)."]}),
        "warranty.cautions": _draft(warranty={"cautions": [{"text": "Labor is covered until 2027.",
                                                            "source_index": 0}], "verify": []}),
        "warranty_caution": _draft(warranty_caution={"text": "Coverage expired 2025-06-10.", "source_index": 0}),
    }
    for path, draft in cases.items():
        result = _rules(draft)
        assert result.brief is None, path
        assert [e for e in result.errors if e.startswith(f"{WARRANTY_DATE_WRITTEN}: {path} states ")], (
            path, result.errors)


def test_a_refusal_with_a_warranty_date_fails_too() -> None:
    """A refusal keeps its warranty block (decision 9), so it is checked the
    same way. Mutation warranty_dates_ok_only (only an answer is checked)."""
    refusal = _draft(status="no_reliable_answer", try_first=[],
                     no_reliable_answer={"found": [], "why_insufficient": "Nothing documented (synthetic)."},
                     warranty={"cautions": [], "verify": ["Ask whether coverage runs through March."]})
    result = _rules(refusal)
    assert any(e.startswith(WARRANTY_DATE_WRITTEN) for e in result.errors), result.errors


def test_the_retry_message_names_the_rule_and_a_forced_refusal_keeps_only_the_code(tmp_path: Path) -> None:
    """The error the retry shows the model says why; a forced refusal after two
    failures shows only the reason code, never the model's date. Mutation
    warranty_dates_message_without_reason (the error drops the instruction)."""
    brief = {"warranty": {"cautions": [], "verify": ["Coverage ends June 2027."]}}
    [error] = check_warranty_dates(brief)
    assert error == (f"{WARRANTY_DATE_WRITTEN}: warranty.verify states ends June 2027; code writes every warranty "
                     "date, so leave dates out of warranty text")
    forced = build_forced_refusal_brief({"validation_errors": [error, error], "search_trail": []})
    why = forced["no_reliable_answer"]["why_insufficient"]
    assert why.endswith(f"Last errors: {WARRANTY_DATE_WRITTEN}") and "June 2027" not in why
