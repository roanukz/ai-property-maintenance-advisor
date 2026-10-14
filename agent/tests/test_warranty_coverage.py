"""Warranty coverage status in code, and the run's one date (SC13; decision 29).

Every registry row here is synthetic (the seed, or rows written in the test).
Each test names, in its docstring, the planted bugs in mutations.toml that
turn it red.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from agent import cli, config
from agent.nodes.intake import intake, intake_input
from agent.nodes.refuse import build_forced_refusal_brief
from agent.registry import open_registry
from agent.rules import coverage
from agent.rules.pipeline import run_rules
from agent.state import RunContext, run_date

OPTIMA = "appl-optima880"
SOFTENER = "appl-softener"
OCTOBER_FIRST = date(2026, 10, 1)
SOURCE = {"url": "https://maker.example.com/manual", "title": "Manual (synthetic)", "retrieved_at": "2026-10-01",
          "origin": "search", "text_sha256": None, "snippet": "Synthetic snippet."}
WARRANTY_DRAFT = {"cautions": [{"text": "Rental use may change coverage (synthetic).", "source_index": 0}],
                  "verify": ["Find the purchase paperwork (synthetic)."]}
NEVER = ("out of warranty", "covers the", "is covered", "not covered")


@pytest.fixture()
def seeded(tmp_path: Path):
    return open_registry(tmp_path / "registry.sqlite")


def _draft(**overrides: Any) -> dict[str, Any]:
    d = {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [{"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": 0}],
        "candidates": [], "warranty": dict(WARRANTY_DRAFT), "no_reliable_answer": None,
        "upgrade_options": [], "maintenance_due": [],
        "source_tiers": [{"source_index": 0, "tier": "manufacturer", "authorship_quote": ""}],
    }
    d.update(overrides)
    return d


def _rules(draft: dict, *, registry: dict | None, rows: list[dict] | None, today: date = OCTOBER_FIRST):
    return run_rules(draft, sources=[dict(SOURCE)], observed_code=None, history_hits=[], registry=registry,
                     mode="replay", provenance=None, pass_kind="synthesize", today=today,
                     warranty_coverage=rows)


def _row(row_id: str, component: str, months: int, event: str = "install", appliance: str = "appl-x") -> dict:
    return {"id": row_id, "record_id": f"warranty:{row_id}", "appliance_id": appliance, "component": component,
            "term_months": months, "start_event": event, "source_text": "synthetic", "synthetic": True}


# ---------------------------------------------------------------------------
# End dates and status
# ---------------------------------------------------------------------------


def test_end_date_adds_months_and_clamps_to_month_end() -> None:
    """Mutations: coverage_end_date_thirty_day_months (the term counted as 30
    day months), maintenance_add_months_no_clamp (no month end clamping, so a
    29 February start gives no end date)."""
    assert coverage.end_date("2021-06-10", 60) == date(2026, 6, 10)
    assert coverage.end_date("2024-02-29", 12) == date(2025, 2, 28)
    assert coverage.end_date("2024-02-29", 48) == date(2028, 2, 29)
    assert coverage.end_date("2023-01-31", 1) == date(2023, 2, 28)
    assert coverage.end_date("2024-01-31", 1) == date(2024, 2, 29)
    assert coverage.end_date("2024-08-31", 1) == date(2024, 9, 30)


FULL_DATES = st.dates(min_value=date(1990, 1, 1), max_value=date(2090, 12, 31))
TERMS = st.integers(min_value=1, max_value=1200)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(start=FULL_DATES, term=TERMS, extra=st.integers(min_value=0, max_value=120))
@example(start=date(2024, 2, 29), term=12, extra=0)
@example(start=date(2000, 2, 29), term=1, extra=11)
@example(start=date(2023, 1, 31), term=1, extra=1)
def test_end_date_properties(start: date, term: int, extra: int) -> None:
    """End after start, exactly `term` calendar months later, never past the
    start's day of month, monotonic in the term, and a 29 February start ends
    on the last day of February when the end year is not a leap year.
    Mutations: coverage_term_off_by_one (one month short),
    coverage_end_date_thirty_day_months, maintenance_add_months_no_clamp."""
    end = coverage.end_date(start.isoformat(), term)
    assert end is not None and end > start
    assert (end.year * 12 + end.month) - (start.year * 12 + start.month) == term
    assert end.day <= start.day
    if end.day < start.day:  # clamped: the end is the last day of its month
        assert (end + timedelta(days=1)).day == 1
    later = coverage.end_date(start.isoformat(), term + extra)
    assert later is not None and later >= end
    if (start.month, start.day) == (2, 29) and end.month == 2:
        assert end.day == (29 if (end + timedelta(days=1)).month == 2 else 28)


def test_status_is_active_only_before_the_end_date() -> None:
    """Mutation coverage_active_through_end_day: still active on the end date itself."""
    end = date(2026, 6, 10)
    assert coverage.status(end, date(2026, 6, 9)) == coverage.ACTIVE
    assert coverage.status(end, end) == coverage.ENDED
    assert coverage.status(end, date(2030, 1, 1)) == coverage.ENDED
    assert coverage.status(None, date(2030, 1, 1)) == coverage.END_UNKNOWN


PARTIAL_DATES = st.one_of(
    st.integers(min_value=1950, max_value=2090).map(str),
    st.tuples(st.integers(min_value=1950, max_value=2090), st.integers(min_value=1, max_value=12)).map(
        lambda ym: f"{ym[0]}-{ym[1]:02d}"),
    st.tuples(st.integers(min_value=1, max_value=12), st.integers(min_value=1950, max_value=2090)).map(
        lambda my: f"{my[0]:02d}/{my[1]}"),
)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(start=PARTIAL_DATES, term=TERMS, today=FULL_DATES)
@example(start="2001", term=12, today=date(2026, 10, 1))
def test_partial_start_dates_are_never_ended(start: str, term: int, today: date) -> None:
    """A start date known only to the year or month gives no end date, so its
    status is "end_date_unknown" whatever the run date: never ended, never
    active. Mutation coverage_partial_date_day_one (a missing month or day
    taken as 1)."""
    end = coverage.end_date(start, term)
    assert end is None
    assert coverage.status(end, today) == coverage.END_UNKNOWN
    [row] = coverage.coverage_rows([_row("p", "parts", term)], {"install_date": start}, today)
    assert row["status"] == coverage.END_UNKNOWN and row["end_date"] is None
    statement = coverage.coverage_statement([row], today)
    assert "known to be active" in statement and "latest end date" not in statement


def test_start_event_picks_the_install_or_purchase_date() -> None:
    """Mutation coverage_purchase_reads_install: a purchase start reads the install date."""
    appliance = {"install_date": "2026-05-04", "purchase_date": "2026-04-20"}
    rows = coverage.coverage_rows([_row("a", "valve", 60), _row("b", "labor", 12, "purchase")], appliance,
                                  OCTOBER_FIRST)
    assert [(r["start_date"], r["end_date"]) for r in rows] == [("2026-05-04", "2031-05-04"),
                                                                ("2026-04-20", "2027-04-20")]
    assert coverage.coverage_rows([_row("c", "labor", 12, "delivery")], appliance, OCTOBER_FIRST)[0][
        "status"] == coverage.END_UNKNOWN


# ---------------------------------------------------------------------------
# The seeded rows and the coverage line's wording
# ---------------------------------------------------------------------------


def test_optima_terms_map_to_three_rows_all_ended_in_october_2026(seeded) -> None:
    """The Optima 880's synthetic terms (shell 5 years, equipment 2 years,
    labor 1 year, from install) give three rows, all ended; the line says no
    coverage on file is active, with the latest end date, never "out of
    warranty". Mutations: coverage_latest_is_earliest (the earliest end date
    shown as the latest), registry_term_stored_in_years (5 stored for 5 years)."""
    optima = seeded.get_appliance(OPTIMA)
    rows = coverage.coverage_rows(seeded.warranty_coverage(OPTIMA), optima, OCTOBER_FIRST)
    assert {(r["component"], r["term_months"], r["start_event"], r["end_date"], r["status"]) for r in rows} == {
        ("shell", 60, "install", "2026-06-10", "ended"),
        ("equipment", 24, "install", "2023-06-10", "ended"),
        ("labor", 12, "install", "2022-06-10", "ended"),
    }
    statement = coverage.coverage_statement(rows, OCTOBER_FIRST)
    assert statement == ("No warranty coverage on file is active as of 2026-10-01 (UTC). The latest end date on "
                         "file is 2026-06-10 (shell, warranty:warr-optima-shell, synthetic).")
    assert not any(word in statement.lower() for word in NEVER)


def test_active_coverage_lists_each_component_and_tells_the_owner_to_check(seeded) -> None:
    """The synthetic softener's three rows are active in October 2026. The line
    names each component with its end date and record id, labels each
    synthetic, and tells the owner to check with the maker; it never says which
    component covers the repair. Mutations: coverage_check_sentence_dropped,
    coverage_synthetic_label_dropped, registry_seed_softener_installed_long_ago."""
    softener = seeded.get_appliance(SOFTENER)
    assert softener["synthetic"] is True and softener["property_id"] == "prop-b"
    for day in range(1, 32):
        rows = coverage.coverage_rows(seeded.warranty_coverage(SOFTENER), softener, date(2026, 10, day))
        assert [r["status"] for r in rows] == [coverage.ACTIVE] * 3, day
    statement = coverage.coverage_statement(rows, OCTOBER_FIRST)
    assert statement.startswith("Warranty coverage on file that is active as of 2026-10-01 (UTC): ")
    for part in ("labor, ends 2027-04-20 (warranty:warr-softener-labor, synthetic)",
                 "resin tank, ends 2036-05-04 (warranty:warr-softener-tank, synthetic)",
                 "control valve, ends 2031-05-04 (warranty:warr-softener-valve, synthetic)"):
        assert part in statement, part
    assert statement.endswith(coverage.CHECK_WITH_MAKER)
    assert "Check with the maker before paying for a repair" in statement
    assert not any(word in statement.lower() for word in NEVER)


def test_no_rows_says_no_warranty_on_file() -> None:
    """Mutation coverage_no_rows_out_of_warranty: no rows read as "Out of warranty."."""
    assert coverage.coverage_statement([], OCTOBER_FIRST) == "No warranty on file."
    brief = _rules(_draft(), registry={"id": "appl-x", "record_id": "appliance:appl-x"}, rows=[]).brief
    assert brief["warranty"]["coverage_statement"] == "No warranty on file."
    assert brief["warranty"]["coverage"] == []


def test_unknown_end_dates_are_never_counted_as_ended() -> None:
    """With an ended row and a row whose start is a year only, nothing is said
    to be inactive for certain, the unknown row is named, and the owner is
    told to check. Mutation coverage_unknown_called_inactive."""
    appliance = {"install_date": "2019-04-15", "purchase_date": "2019"}
    rows = coverage.coverage_rows([_row("a", "parts", 24), _row("b", "labor", 12, "purchase")], appliance,
                                  OCTOBER_FIRST)
    statement = coverage.coverage_statement(rows, OCTOBER_FIRST)
    assert statement.startswith("No warranty coverage on file is known to be active as of 2026-10-01 (UTC).")
    assert "The latest end date on file is 2021-04-15 (parts, warranty:a, synthetic)." in statement
    assert "End date unknown" in statement and "labor (warranty:b, synthetic)" in statement
    assert statement.endswith(coverage.CHECK_WITH_MAKER)


def test_a_future_start_is_not_started_never_active() -> None:
    """A full start date after the run date is "not_started": its end date is
    still computed, it is never counted as active or ended, it is named with
    its start date, and the owner is told to check. On the start day itself
    the row is active. Mutation coverage_future_start_active (status() checks
    only the end date, so a term that has not begun reads active)."""
    [row] = coverage.coverage_rows([_row("x", "labor", 12)], {"install_date": "2026-12-01"}, OCTOBER_FIRST)
    assert (row["status"], row["start_date"], row["end_date"]) == (coverage.NOT_STARTED, "2026-12-01", "2027-12-01")
    assert coverage.STATUS_LABELS[coverage.NOT_STARTED] == "not started"
    statement = coverage.coverage_statement([row], OCTOBER_FIRST)
    assert statement == ("No warranty coverage on file is known to be active as of 2026-10-01 (UTC). Not yet "
                         "started as of 2026-10-01 (UTC): labor, starts 2026-12-01 (warranty:x, synthetic). "
                         f"{coverage.CHECK_WITH_MAKER}")
    assert coverage.coverage_rows([_row("x", "labor", 12)], {"install_date": "2026-12-01"},
                                  date(2026, 12, 1))[0]["status"] == coverage.ACTIVE
    assert not any(word in statement.lower() for word in NEVER)


def test_softener_between_purchase_and_install_lists_only_the_purchase_term_as_active(seeded) -> None:
    """The seeded softener was bought 2026-04-20 and installed 2026-05-04. Run
    between them, its purchase based labor term is active and its install
    based terms have not started; the brief keeps the not started rows.
    Mutations: coverage_future_start_active, schemas_coverage_not_started_undeclared
    (Brief.model_validate refuses the new status)."""
    softener = seeded.get_appliance(SOFTENER)
    between = date(2026, 4, 25)
    rows = coverage.coverage_rows(seeded.warranty_coverage(SOFTENER), softener, between)
    assert [(r["component"], r["status"]) for r in rows] == [
        ("labor", coverage.ACTIVE), ("resin tank", coverage.NOT_STARTED), ("control valve", coverage.NOT_STARTED)]
    statement = coverage.coverage_statement(rows, between)
    assert statement.startswith("Warranty coverage on file that is active as of 2026-04-25 (UTC): labor, ends "
                                "2027-04-20 (warranty:warr-softener-labor, synthetic). Not yet started as of "
                                "2026-04-25 (UTC): resin tank, starts 2026-05-04")
    assert "resin tank, ends" not in statement and "control valve, ends" not in statement
    assert statement.endswith(coverage.CHECK_WITH_MAKER)
    result = _rules(_draft(), registry=softener, rows=seeded.warranty_coverage(SOFTENER), today=between)
    assert result.errors == [], result.errors
    assert [c["status"] for c in result.brief["warranty"]["coverage"]] == ["active", "not_started", "not_started"]
    assert result.brief["warranty"]["coverage_statement"] == statement


def test_terms_without_rows_never_say_no_warranty(seeded) -> None:
    """An appliance whose record holds warranty terms text but no coverage rows
    (a registry made before SC13's table was seeded) gets a line that says the
    terms are not broken into components and tells the owner to check, never
    "No warranty on file." under the quoted terms. Mutation
    coverage_terms_only_says_no_warranty."""
    from agent.render.brief_html import esc, render_brief

    assert coverage.coverage_statement([], OCTOBER_FIRST, terms_on_file=True) == (
        f"{coverage.TERMS_ONLY} {coverage.CHECK_WITH_MAKER}")
    optima = seeded.get_appliance(OPTIMA)
    assert optima["warranty_terms"]
    brief = _rules(_draft(), registry=optima, rows=[]).brief
    warranty = brief["warranty"]
    assert warranty["terms"] == optima["warranty_terms"] and warranty["coverage"] == []
    assert warranty["coverage_statement"] == f"{coverage.TERMS_ONLY} {coverage.CHECK_WITH_MAKER}"
    page = render_brief(brief, identity={"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "",
                                         "manufacture_date": ""}, symptom="s", generated_at="2026-10-01")
    assert esc(optima["warranty_terms"]) in page and esc(coverage.TERMS_ONLY) in page
    assert coverage.NO_WARRANTY not in page
    assert not any(word in warranty["coverage_statement"].lower() for word in NEVER)


# ---------------------------------------------------------------------------
# Through validate: the brief keeps the code written fields
# ---------------------------------------------------------------------------


def test_validate_writes_coverage_and_the_brief_keeps_it(seeded) -> None:
    """Brief.model_validate keeps the declared coverage fields; the model's
    warranty block gains them in code. Mutations: schemas_coverage_statement_undeclared
    (the field left off Warranty, so the brief drops it), pipeline_coverage_not_passed
    (run_rules never hands the rows to rule 8)."""
    optima = seeded.get_appliance(OPTIMA)
    result = _rules(_draft(), registry=optima, rows=seeded.warranty_coverage(OPTIMA))
    assert result.errors == [], result.errors
    warranty = result.brief["warranty"]
    assert warranty["coverage_as_of"] == "2026-10-01"
    assert warranty["coverage_statement"].startswith("No warranty coverage on file is active as of 2026-10-01")
    assert [c["record_id"] for c in warranty["coverage"]] == [
        "warranty:warr-optima-equipment", "warranty:warr-optima-labor", "warranty:warr-optima-shell"]
    assert all(c["synthetic"] is True and c["status"] == "ended" for c in warranty["coverage"])
    assert warranty["age_statement"].endswith("about 5 years old as of 2026-10-01.")


def test_rows_alone_create_the_block_and_other_appliances_rows_are_ignored() -> None:
    """Mutations: citations_coverage_rows_not_facts (coverage rows alone do not
    create the warranty block), citations_coverage_other_appliance (a row of
    another appliance is shown)."""
    registry = {"id": "appl-x", "record_id": "appliance:appl-x"}
    rows = [_row("mine", "labor", 12), _row("theirs", "shell", 60, appliance="appl-other")]
    brief = _rules(_draft(warranty=None), registry={**registry, "install_date": None}, rows=rows,
                   today=date(2026, 10, 1)).brief
    assert brief["warranty"] is not None
    assert [c["record_id"] for c in brief["warranty"]["coverage"]] == ["warranty:mine"]
    assert "warranty:theirs" not in brief["warranty"]["coverage_statement"]


def test_refusal_keeps_the_code_coverage_line_and_a_forced_refusal_has_none(seeded) -> None:
    """Decision for SC13: the coverage line is a code fact, so it stays with the
    model's warranty block on the model's own refusal (decision 9 keeps that
    block), and a forced refusal, which carries no warranty block, shows none.
    Mutation citations_coverage_ok_only (the rows are dropped on a refusal)."""
    softener = seeded.get_appliance(SOFTENER)
    refusal = _draft(status="no_reliable_answer", try_first=[],
                     no_reliable_answer={"found": [], "why_insufficient": "Nothing documented (synthetic)."})
    brief = _rules(refusal, registry=softener, rows=seeded.warranty_coverage(SOFTENER)).brief
    assert brief["status"] == "no_reliable_answer"
    assert brief["warranty"]["coverage_statement"].startswith("Warranty coverage on file that is active")
    assert len(brief["warranty"]["coverage"]) == 3
    forced = build_forced_refusal_brief({"validation_errors": ["warranty_date_written: x"], "search_trail": []})
    assert forced["warranty"] is None


# ---------------------------------------------------------------------------
# One run date
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path, day: date) -> RunContext:
    return RunContext(run_id="run-date", mode="replay", ledger_path=tmp_path / "ledger.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages", run_date=day)


def test_intake_pins_the_run_date_and_state_wins_on_resume(tmp_path: Path) -> None:
    """intake copies RunContext.run_date into state, and a context built later
    (a resume on another day) reads the pinned date. Mutations:
    intake_run_date_not_pinned, state_run_date_ignores_state."""
    from langgraph.runtime import Runtime

    out = intake(intake_input(symptom="panel shows FLO"), Runtime(context=_ctx(tmp_path, OCTOBER_FIRST)))
    assert out["run_date"] == "2026-10-01"
    later = _ctx(tmp_path, date(2026, 10, 20))
    assert run_date(out, later) == OCTOBER_FIRST
    assert run_date({}, later) == date(2026, 10, 20)


def test_one_run_date_reaches_the_prompt_the_brief_and_the_page(tmp_path: Path) -> None:
    """One replay run with the synthetic Optima cassette: the prompt's date, the
    age statement, the coverage line and the page footer are all the pinned
    day, not the wall clock. Mutations: synthesize_prompt_wall_clock,
    validate_today_wall_clock, render_footer_wall_clock."""
    from agent.tests.helpers import load_case, replay_ctx
    from agent.tests.test_sc8_history import final_state, run_phase3_case

    cassette = load_case("synthetic/optima_history")
    pinned = date(2026, 9, 3)  # any day that is not today
    ctx = replay_ctx(tmp_path, cassette, run_id="thread-1")
    ctx.run_date = pinned
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path, appliance_id=OPTIMA, ctx=ctx)
    state = final_state(outcomes)
    assert state["status"] == "ok" and state["run_date"] == "2026-09-03"
    [request] = ctx.replay["models"]["synthesize"].received
    assert "Today's date is 2026-09-03." in request[0].content
    warranty = state["brief"]["warranty"]
    assert warranty["age_statement"].endswith("as of 2026-09-03.")
    assert warranty["coverage_as_of"] == "2026-09-03"
    assert "active as of 2026-09-03 (UTC)" in warranty["coverage_statement"]
    html = Path(state["html_path"]).read_text(encoding="utf-8")
    assert "found at the listed sources on 2026-09-03." in html
    assert "active as of 2026-09-03 (UTC)" in html
    assert "warranty:warr-optima-shell, synthetic" in html


@pytest.fixture()
def cli_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "REGISTRY_PATH", data / "registry.sqlite")
    monkeypatch.setattr(config, "PROPERTY_OUT_DIR", data / "property")
    monkeypatch.setattr(config, "GRAPH_PATH", data / "graph.json")
    monkeypatch.setattr(config, "REPLAY_GRAPH_PATH", data / "replay_graph.json")
    monkeypatch.setattr(config, "PAGES_DIR", data / "pages")
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 2))
    monkeypatch.delenv(config.ENV_MODE, raising=False)
    return data


def test_cli_pins_one_date_per_run_and_per_property_page(cli_data: Path) -> None:
    """The CLI pins the run's date from cli._today() (UTC), and the property
    page takes its one date the same way. Mutations: cli_run_date_not_pinned,
    cli_property_page_wall_clock."""
    assert cli.run_context("run-x", "replay", None).run_date == date(2026, 10, 2)
    assert cli.main(["property", "show", "prop-b"]) == cli.EXIT_OK
    page = (config.PROPERTY_OUT_DIR / "prop-b.html").read_text(encoding="utf-8")
    assert "Warranty coverage as of 2026-10-02 (UTC)" in page
    assert "Generated on 2026-10-02 (UTC)" in page
