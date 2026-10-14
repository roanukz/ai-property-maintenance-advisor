"""Review of round 2 (13 October 2026), the offline findings: the product recall matcher (R1, R3), year 0
(R4), an unreadable warranty start date (R5) and a registry that differs from the seed (L4).

- R1: a printing may merge the recorded string's tokens but may not split where it does not, so
  "4TTR60 36", "XR1 64TTR6036" and "PRO+E50 M2 RH9 2CL" never match; a comma grouping thousands
  ("36,000") stays inside its number, while "4TTR6036,4TTR6048" is still a list.
- R3: wildcard family notation ("4TTR6036*") is kept as a hit and says it names a series.
- R4: year 0 is no date, with or without a month.
- R5: a warranty start date that is not on the calendar could not be read; it is not missing.
- L4: registry.seed_drift names each difference from the seed, reading the file without changing it.

Every recall and registry row here is synthetic, recalls come through an injected fetch, and sockets
are blocked. Each test names, in its docstring, the planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from agent import config
from agent import product_recalls as pr
from agent import sweep
from agent.registry import Registry, open_registry, seed_drift
from agent.rules import coverage
from agent.rules.citations import age_statement, parse_partial_date
from agent.tests.test_product_recalls import RHEEM, SEED, TRANE, _body, _check
from agent.tests.test_round2_model_number import _old_registry, invented

TODAY = date(2026, 10, 1)


# ---------------------------------------------------------------------------
# R1: no new splits, and a thousands comma is part of its number
# ---------------------------------------------------------------------------

SPLIT_ELSEWHERE = [
    (TRANE, "Description", "Model 4TTR60 36,000 BTU units recalled."),
    (TRANE, "Description", "Model 4TTR60 36 units recalled."),
    (TRANE, "Description", "Model XR1 64TTR6036 recalled."),
    (TRANE, "Model", "4TTR60 36"),
    (TRANE, "Model", "XR1 64TTR6036"),
    (RHEEM, "Description", "The PRO+E50 M2 RH9 2CL water heaters."),
    (RHEEM, "Model", "PRO+E50 M2 RH9 2CL"),
]


@pytest.mark.parametrize(("appliance", "field", "printed"), SPLIT_ELSEWHERE,
                         ids=[f"{a['id']}-{f}-{p}" for a, f, p in SPLIT_ELSEWHERE])
def test_a_printing_split_where_the_recorded_string_is_not_never_matches(appliance: dict, field: str,
                                                                         printed: str) -> None:
    """Two tokens whose characters happen to spell the model number or the model on record are not it,
    in free text or in a product's Model field, so no product recall names the unit. Mutations:
    product_recalls_split_guard_off (free text runs may split anywhere),
    product_recalls_field_split_guard_off (a Model field segment may split anywhere),
    product_recalls_targets_skip_recorded (the matcher is not given the recorded strings)."""
    recall = invented("SP01", model=printed) if field == "Model" else invented("SP01", description=printed)
    targets = pr.model_targets(appliance["model"], appliance.get("model_number"))
    assert pr.match_targets(recall, targets) == []
    result = pr.evaluate(_check(appliance, {"Manufacturer": _body([recall])})[0])
    assert result["status"] == pr.NO_MATCH and result["counts"]["recalls"] == 1
    assert not any("names the model" in s for s in pr.statements(result))


def test_a_printing_that_merges_the_recorded_tokens_still_matches() -> None:
    """Merging is allowed: "XR16-4TTR6036", "PRO+E50M2RH92CL" and the strings as recorded match; the
    recorded breaks are offsets into the normalized string. Mutation: product_recalls_merge_rejected
    (a printing must split exactly where the recorded string does, so "XR16-4TTR6036" is refused)."""
    assert pr.recorded_breaks("XR16 4TTR6036") == frozenset({4})
    assert pr.recorded_breaks("4TTR6036") == frozenset()
    assert pr.recorded_breaks("PRO+E50 M2 RH92 CL") == frozenset({3, 6, 8, 12})
    trane = pr.model_targets(TRANE["model"], TRANE["model_number"])
    rheem = pr.model_targets(RHEEM["model"], RHEEM["model_number"])
    for targets, printed in ((trane, "XR16-4TTR6036"), (trane, "XR16 4TTR6036"), (rheem, "PRO+E50M2RH92CL"),
                             (rheem, "PRO+E50 M2 RH92 CL"), (rheem, "PRO+E50 M2RH92 CL")):
        hits = pr.match_targets(invented("MG01", description=f"Model {printed} units."), targets)
        assert [h["text"] for h in hits] == [printed], printed
        hits = pr.match_targets(invented("MG02", model=printed), targets)
        assert [(h["field"], h["text"]) for h in hits] == [("Products[0].Model", printed)], printed


def test_a_thousands_comma_stays_inside_its_number_and_a_list_comma_still_splits() -> None:
    """"36,000" is one number: for a unit recorded as "XR 36" (break and all) it is not "XR 36".
    "4TTR6036,4TTR6048" is a list, so the Trane model number in it still matches, and "XR16, 4TTR6036"
    is still two names (the model number alone matches, never the model on record). Mutations: product_recalls_thousands_comma_splits (every comma breaks, as
    before), product_recalls_digit_comma_never_splits (no comma between two digits breaks)."""
    spaced = pr.model_targets("XR 36")
    assert pr.match_targets(invented("TC01", description="Model XR 36,000 BTU units."), spaced) == []
    assert [h["text"] for h in pr.match_targets(invented("TC02", description="Model XR 36, sold 2019."),
                                                spaced)] == ["XR 36"]
    trane = pr.model_targets(TRANE["model"], TRANE["model_number"])
    hits = pr.match_targets(invented("TC03", description="Model numbers 4TTR6036,4TTR6048 recalled."), trane)
    assert [(h["text"], h["matched"]) for h in hits] == [("4TTR6036", "model_number")]
    assert [h["matched"] for h in pr.match_targets(invented("TC04", description="XR16, 4TTR6036"), trane)] == [
        "model_number"]


# ---------------------------------------------------------------------------
# R3: wildcard family notation is a series
# ---------------------------------------------------------------------------


def test_a_wildcard_printing_is_kept_and_said_to_name_a_series() -> None:
    """"4TTR6036*" (every model number starting 4TTR6036) is a hit, printed with its wildcard, and the
    sentences say the product recall prints the model as part of a series; a spaced wildcard
    ("4TTR6036 *") is printed with the run, and a plain hit says nothing about a series. Mutations:
    product_recalls_series_sentence_dropped, product_recalls_spaced_wildcard_dropped."""
    star = invented("WC01", description="Models 4TTR6036* are affected.")
    spaced = invented("WC02", description="Models 4TTR6036 * are affected.")
    plain = invented("WC03", description="Model 4TTR6036 is affected.")
    result = pr.evaluate(_check(TRANE, {"Manufacturer": _body([star, spaced, plain])})[0])
    hits = {m["recall_number"]: [h["text"] for h in m["hits"]] for m in result["matches"]}
    assert hits == {"WC01": ["4TTR6036*"], "WC02": ["4TTR6036 *"], "WC03": ["4TTR6036"]}
    by_number = {m["recall_number"]: pr.match_sentences(m, TRANE["model"]) for m in result["matches"]}
    assert pr.SERIES_SENTENCE.format(text="4TTR6036*") in by_number["WC01"]
    assert by_number["WC01"][by_number["WC01"].index(pr.MENTION_ONLY) + 1] == (
        "The product recall prints this model as part of a series (\"4TTR6036*\"); read it to see whether "
        "your unit is included.")
    assert pr.SERIES_SENTENCE.format(text="4TTR6036 *") in by_number["WC02"]
    assert not any("series" in s for s in by_number["WC03"])
    assert pr.SERIES_SENTENCE.format(text="4TTR6036*") in "\n".join(pr.text_lines(result))

    rheem = pr.evaluate(_check(RHEEM, {"Manufacturer": _body([invented("WC04", model="PRO+E50 M2 RH92 CL*")])})[0])
    [match] = rheem["matches"]
    said = pr.match_sentences(match, RHEEM["model"])
    assert pr.SERIES_SENTENCE.format(text="PRO+E50 M2 RH92 CL*") in said and pr.MENTION_ONLY not in said


# ---------------------------------------------------------------------------
# R4: year 0
# ---------------------------------------------------------------------------


def test_year_zero_is_no_date_with_or_without_a_month() -> None:
    """"0000" and "0000-06" give None, as "0000-06-10" does, so the brief says the date could not be
    read and the sweep gives no age, never "about 2026 years". Mutation: citations_year_zero_accepted."""
    for text in ("0000", "0000-06", "0000-06-10", "06/0000"):
        assert parse_partial_date(text) is None, text
    assert parse_partial_date("0001") == (1, None, None)
    statement, cited = age_statement(registry={"install_date": "0000-06", "record_id": "appliance:x"},
                                     manufacture_date=None, today=TODAY)
    assert statement == ('The install date "0000-06" in property record appliance:x could not be read as a '
                         "date, so the unit's age is unknown.")
    assert cited == "appliance:x"
    for text in ("0000", "0000-06"):
        age = sweep.install_age({"install_date": text, "record_id": "appliance:x"}, TODAY)
        assert age == {"years": None, "statement": f"The install date on record, {text}, could not be read as a "
                                                   "date, so the age is not computed."}


# ---------------------------------------------------------------------------
# R5: a start date not on the calendar could not be read
# ---------------------------------------------------------------------------


def _row(row_id: str, component: str) -> dict:
    return {"record_id": f"warranty:{row_id}", "component": component, "term_months": 12, "start_event": "install",
            "synthetic": True}


def test_a_start_date_not_on_the_calendar_could_not_be_read_and_a_partial_one_is_not_a_full_date() -> None:
    """2019-02-30 is set but is no date, so the coverage line says it could not be read as a date, as
    the age statement does; a start held only as a year and month keeps "missing or not a full date".
    Mutation: coverage_unreadable_merged (both kinds share the missing sentence)."""
    unreadable = coverage.coverage_rows([_row("a", "tank")], {"install_date": "2019-02-30"}, TODAY)
    assert unreadable[0]["status"] == coverage.END_UNKNOWN
    line = coverage.coverage_statement(unreadable, TODAY)
    assert ("End date unknown, because the start date on file could not be read as a date: tank (warranty:a, "
            "synthetic).") in line
    assert "missing or not a full date" not in line and line.endswith(coverage.CHECK_WITH_MAKER)

    partial = coverage.coverage_rows([_row("b", "valve")], {"install_date": "2019-06"}, TODAY)
    line = coverage.coverage_statement(partial, TODAY)
    assert "End date unknown, because the start date on file is missing or not a full date: valve" in line
    assert "could not be read" not in line

    both = coverage.coverage_rows([_row("a", "tank")], {"install_date": "2019-02-30"}, TODAY) + \
        coverage.coverage_rows([_row("b", "valve")], {}, TODAY)
    line = coverage.coverage_statement(both, TODAY)
    assert line.index("missing or not a full date: valve") < line.index("could not be read as a date: tank")


# ---------------------------------------------------------------------------
# L4: the registry file against the seed
# ---------------------------------------------------------------------------


def test_seed_drift_names_what_an_old_registry_file_lacks_and_changes_nothing(tmp_path: Path) -> None:
    """A file made before the model number and warranty coverage existed (as data/registry.sqlite
    was) reports the missing seed appliances, every model number it does not hold and every missing
    coverage row, and is left byte for byte; after open_registry loads the seed it reports nothing.
    A missing file reports nothing. Mutations: registry_seed_drift_ignores_model_numbers,
    registry_seed_drift_ignores_coverage, registry_seed_drift_ignores_missing_appliances."""
    path = tmp_path / "old registry.sqlite"
    _old_registry(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    problems = seed_drift(path)
    numbered = [a for a in SEED["appliances"] if a.get("model_number")]
    assert problems[0] == "seed appliances missing from the registry: " + ", ".join(
        a["id"] for a in SEED["appliances"] if a["id"] != "appl-xr16")
    assert problems[1] == "model numbers that differ from the seed: appl-xr16 (registry none, seed \"4TTR6036\")"
    assert problems[2] == "seed warranty coverage rows missing from the registry: " + ", ".join(
        r["id"] for r in SEED["warranty_coverage"])
    assert len(problems) == 3 and len(numbered) == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as conn:
        assert "model_number" not in [row[1] for row in conn.execute("PRAGMA table_info(appliances)")]

    migrated = Registry(path)  # the column, null: the model numbers still differ
    assert [p.split(":")[0] for p in migrated.seed_drift()] == [
        "seed appliances missing from the registry", "model numbers that differ from the seed",
        "seed warranty coverage rows missing from the registry"]
    assert open_registry(path).seed_drift() == []
    assert seed_drift(tmp_path / "absent.sqlite") == []

    changed = json.loads(Path(config.DEFAULT_SEED_PATH).read_text(encoding="utf-8"))
    changed["appliances"][1]["model_number"] = "4TTR6048"
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(changed), encoding="utf-8")
    assert seed_drift(path, seed) == [
        "model numbers that differ from the seed: appl-xr16 (registry \"4TTR6036\", seed \"4TTR6048\")"]
