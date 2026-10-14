"""The appliance model number (13 October 2026): the registry column and its migration, the seed, the
product recall matcher, the property page, and stored checks that never collide.

Roanuk chose on 13 October 2026 that each appliance record gains an exact model number, used by the
product recall check; the Trane unit (model on record "XR16 4TTR6036", XR16 being the product line)
has model number "4TTR6036". Every registry row and recall here is synthetic, recalls come through an
injected fetch, and sockets are blocked. Each test names, in its docstring, the planted bugs in
mutations.toml that turn it red.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent import config
from agent import product_recalls as pr
from agent.registry import Registry, open_registry
from agent.render.brief_html import esc
from agent.render.property_page import render_property_page
from agent.tests.test_product_recalls import APPLIANCES, MATCHES, NEAR_MISSES, NOON, RHEEM, SEED, TRANE, \
    FakeFetch, _body, _check

OPTIMA = APPLIANCES["appl-optima880"]
HOSTILE = "<script>alert(1)</script>\"'<img src=x onerror=alert(2)>"

# The registry schema as committed before warranty coverage and the model number (b9e4ca3), comments
# left out: the local registry file was made with it, so it is the oldest file the registry must open.
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    id        TEXT PRIMARY KEY,
    label     TEXT NOT NULL,
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);
CREATE TABLE IF NOT EXISTS appliances (
    id                   TEXT PRIMARY KEY,
    record_id            TEXT NOT NULL UNIQUE CHECK (record_id = 'appliance:' || id),
    property_id          TEXT NOT NULL REFERENCES properties(id),
    category             TEXT NOT NULL,
    manufacturer         TEXT NOT NULL,
    model                TEXT NOT NULL,
    serial               TEXT,
    purchase_date        TEXT,
    install_date         TEXT,
    warranty_terms       TEXT,
    warranty_source      TEXT,
    status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'discontinued')),
    notes                TEXT,
    pending_owner_choice INTEGER NOT NULL DEFAULT 0 CHECK (pending_owner_choice IN (0, 1)),
    synthetic            INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);
CREATE INDEX IF NOT EXISTS appliances_by_property ON appliances(property_id);
CREATE TABLE IF NOT EXISTS service_records (
    id            TEXT PRIMARY KEY,
    record_id     TEXT NOT NULL UNIQUE CHECK (record_id = 'service:' || id),
    appliance_id  TEXT NOT NULL REFERENCES appliances(id),
    date          TEXT NOT NULL,
    symptom       TEXT NOT NULL,
    observed_code TEXT,
    work_done     TEXT,
    performed_by  TEXT,
    synthetic     INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);
CREATE INDEX IF NOT EXISTS service_by_appliance ON service_records(appliance_id);
CREATE TABLE IF NOT EXISTS maintenance_log (
    id           TEXT PRIMARY KEY,
    appliance_id TEXT NOT NULL REFERENCES appliances(id),
    task         TEXT NOT NULL,
    done_on      TEXT NOT NULL,
    record_id    TEXT NOT NULL UNIQUE CHECK (record_id = 'maintenance:' || id),
    synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);
CREATE INDEX IF NOT EXISTS maintenance_by_appliance ON maintenance_log(appliance_id);
CREATE TABLE IF NOT EXISTS lookups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    thread_id    TEXT,
    appliance_id TEXT REFERENCES appliances(id),
    status       TEXT NOT NULL,
    route        TEXT,
    cost_usd     REAL NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);
CREATE INDEX IF NOT EXISTS lookups_by_run ON lookups(run_id);
CREATE INDEX IF NOT EXISTS lookups_by_appliance ON lookups(appliance_id);
"""


def _old_registry(path: Path) -> list[tuple]:
    """A registry file made with OLD_SCHEMA: the Trane unit and an extra appliance, two lookups and a
    service record; returns the lookups rows as written."""
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO properties VALUES ('prop-a', 'Synthetic property A', 1)")
        conn.execute("INSERT INTO appliances (id, record_id, property_id, category, manufacturer, model, synthetic)"
                     " VALUES ('appl-xr16', 'appliance:appl-xr16', 'prop-a', 'air_conditioner', 'Trane',"
                     " 'XR16 4TTR6036', 1)")
        conn.execute("INSERT INTO appliances (id, record_id, property_id, category, manufacturer, model, synthetic)"
                     " VALUES ('appl-old', 'appliance:appl-old', 'prop-a', 'furnace', 'Synthetic Old Co.',"
                     " 'OF-1 (synthetic)', 1)")
        conn.execute("INSERT INTO service_records (id, record_id, appliance_id, date, symptom, synthetic)"
                     " VALUES ('svc-old', 'service:svc-old', 'appl-old', '2025-01-02', 'Synthetic symptom', 1)")
        rows = [("run-1", "thread-1", "appl-xr16", "ok", "graph", 0.0123, "2026-10-01T10:00:00+00:00", 1),
                ("run-2", None, None, "refused", None, 0.0, "2026-10-01T11:00:00+00:00", 0)]
        conn.executemany("INSERT INTO lookups (run_id, thread_id, appliance_id, status, route, cost_usd,"
                         " created_at, synthetic) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return rows


def _lookups(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT run_id, thread_id, appliance_id, status, route, cost_usd, created_at,"
                            " synthetic FROM lookups ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# The registry column, its migration and the seed
# ---------------------------------------------------------------------------


def test_an_old_registry_file_gains_the_column_and_keeps_every_row(tmp_path: Path) -> None:
    """A file made with the old schema opens, gains model_number (null on its own rows) and keeps every
    row, lookups included; loading the seed then upserts the model numbers and still keeps the lookups,
    and opening it again changes nothing. Mutations: registry_migration_dropped (the old file is opened
    without the column, so the seed cannot load), registry_migration_clears_lookups,
    registry_seed_model_number_not_inserted, registry_seed_model_number_not_updated."""
    path = tmp_path / "old.sqlite"
    lookups = _old_registry(path)
    registry = Registry(path)
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(appliances)")]
    assert columns[-1] == "model_number" and columns.count("model_number") == 1
    assert [a["id"] for a in registry.list_appliances()] == ["appl-old", "appl-xr16"]
    assert all(a["model_number"] is None for a in registry.list_appliances())
    assert registry.service_history("appl-old")[0]["record_id"] == "service:svc-old"
    assert _lookups(path) == lookups

    registry = open_registry(path)  # seeds: appl-xr16 is updated in place, the others inserted
    assert registry.get_appliance("appl-xr16")["model_number"] == "4TTR6036"
    assert registry.get_appliance("appl-optima880")["model_number"] is None
    assert registry.get_appliance("appl-old")["model"] == "OF-1 (synthetic)"  # kept, not in the seed
    assert len(registry.list_appliances()) == len(SEED["appliances"]) + 1
    assert _lookups(path) == lookups
    assert [r["run_id"] for r in registry.lookups(appliance_id="appl-xr16")] == ["run-1"]

    reopened = open_registry(path)
    assert reopened.list_appliances() == registry.list_appliances() and _lookups(path) == lookups
    with sqlite3.connect(path) as conn:
        assert [row[1] for row in conn.execute("PRAGMA table_info(appliances)")] == columns


def test_a_new_registry_file_has_the_same_columns_as_a_migrated_one(tmp_path: Path) -> None:
    """The schema puts model_number last, where ALTER TABLE adds it, so SELECT * gives one shape.
    Mutation registry_schema_model_number_moved (the column declared after model)."""
    old = tmp_path / "old.sqlite"
    _old_registry(old)
    Registry(old)
    Registry(tmp_path / "new.sqlite")
    shapes = []
    for path in (old, tmp_path / "new.sqlite"):
        with sqlite3.connect(path) as conn:
            shapes.append([row[1:3] for row in conn.execute("PRAGMA table_info(appliances)")])
    assert shapes[0] == shapes[1]


def test_the_seed_sets_the_trane_model_number_and_says_where_it_comes_from(tmp_path: Path) -> None:
    """The Trane unit's model number is the model number part of its model on record, never the product
    line; the Rheem unit's model on record is already its model number; the rest are null. Mutations:
    registry_seed_trane_model_number_is_the_line, registry_seed_model_number_not_inserted."""
    registry = open_registry(tmp_path / "registry.sqlite")
    numbers = {a["id"]: a["model_number"] for a in registry.list_appliances()}
    assert numbers == {"appl-xr16": "4TTR6036", "appl-waterheater": "PRO+E50 M2 RH92 CL",
                       "appl-optima880": None, "appl-sx100": None, "appl-dishwasher": None,
                       "appl-washer": None, "appl-softener": None}
    trane = registry.get_appliance("appl-xr16")
    assert trane["model"].split() == ["XR16", trane["model_number"]]
    assert "model number part of that name" in trane["notes"]
    assert "already the model number" in registry.get_appliance("appl-waterheater")["notes"]


# ---------------------------------------------------------------------------
# The product recall matcher
# ---------------------------------------------------------------------------


def invented(number: str, *, model: str = "", description: str = "", title: str = "") -> dict[str, Any]:
    """A synthetic recall for these tests only, never a real one."""
    return {"RecallID": 990000 + len(number), "RecallNumber": number,
            "Description": "SYNTHETIC TEST FIXTURE, not a real recall. " + description,
            "URL": f"https://example.com/{number.lower()}", "Title": title or f"Synthetic recall {number}",
            "Products": [{"Name": "Synthetic unit", "Description": "", "Model": model}],
            "Hazards": [{"Name": "Synthetic hazard."}], "Remedies": [{"Name": "Synthetic remedy."}]}


def test_a_recall_naming_only_the_model_number_matches_the_trane_unit() -> None:
    """In the model field or in the text, "4TTR6036" alone matches; each hit says the model number
    matched, and the sentences name what was searched. Mutations: product_recalls_model_number_not_searched,
    product_recalls_check_drops_model_number, product_recalls_hit_kind_lost,
    product_recalls_number_only_head_dropped."""
    in_field = invented("RN01", model="4TTR6036")
    in_text = invented("RN02", description="Affected model numbers: 4TTR6036, 4TTR6048.")
    check, _ = _check(TRANE, {"Manufacturer": _body([in_field, in_text])})
    assert check["model_number"] == "4TTR6036"
    result = pr.evaluate(check)
    assert result["status"] == pr.MATCHED
    hits = {m["recall_number"]: m["hits"] for m in result["matches"]}
    assert hits == {
        "RN01": [{"field": "Products[0].Model", "text": "4TTR6036", "matched": "model_number",
                  "searched": "4TTR6036"}],
        "RN02": [{"field": "Description", "text": "4TTR6036", "matched": "model_number", "searched": "4TTR6036"}],
    }
    said = pr.statements(result)
    assert said[0] == ("CPSC product recall RN01 names the model number 4TTR6036 (model on record "
                       "XR16 4TTR6036): Synthetic recall RN01.")
    assert said[1] == "The model number appears in the product recall's product model field: \"4TTR6036\"."
    assert "The model number appears in the product recall's description: \"4TTR6036\"." in said
    entry = pr.run_record_entry(check)
    assert {m["matched"] for m in entry["matches"]} == {"model_number"}


@pytest.mark.parametrize("printed", ["4TTR6036A", "4TTR603", "XR16", "XR16 4TTR603", "4TTR6036 A", "X4TTR6036"])
def test_a_longer_or_shorter_number_or_the_product_line_alone_never_matches(printed: str) -> None:
    """No family or product line alone, no prefix, no substring, in the model field or the text; the
    no match sentence names both strings searched. Mutations: product_recalls_product_line_searched,
    product_recalls_model_number_prefix, product_recalls_no_match_hides_model_number."""
    recalls = [invented("NM01", model=printed), invented("NM02", description=f"Models {printed} only.")]
    result = pr.evaluate(_check(TRANE, {"Manufacturer": _body(recalls)})[0])
    assert result["status"] == pr.NO_MATCH and result["counts"]["recalls"] == 2
    assert pr.statements(result)[0] == ("No CPSC product recall names this exact model: model number 4TTR6036 "
                                        "or model on record XR16 4TTR6036.")


def test_the_model_on_record_still_matches_and_the_number_inside_it_is_not_a_second_hit() -> None:
    """"XR16 4TTR6036" in the text is one hit, the model on record; the model number printed inside it
    is left out. Mutation product_recalls_inner_number_hit_kept."""
    recall = invented("MR01", description="Model XR16 4TTR6036 outdoor units.")
    targets = pr.model_targets(TRANE["model"], TRANE["model_number"])
    assert [t["matched"] for t in targets] == ["model_number", "model"]
    assert pr.match_targets(recall, targets) == [{"field": "Description", "text": "XR16 4TTR6036",
                                                  "matched": "model", "searched": "XR16 4TTR6036"}]
    result = pr.evaluate(_check(TRANE, {"Manufacturer": _body([recall, MATCHES[0]])})[0])
    assert [m["recall_number"] for m in result["matches"]] == ["MR01", "SYN001"]
    assert all(h["matched"] == "model" for m in result["matches"] for h in m["hits"])
    assert pr.statements(result)[0].startswith("CPSC product recall MR01 names the model XR16 4TTR6036:")


def test_the_rheem_and_optima_units_match_exactly_as_before() -> None:
    """The Rheem unit's model number is its model on record, so it is one target and its sentences are
    unchanged; the Optima 880 has none, and "Optima" or "880" alone is not it. Mutation
    product_recalls_number_like_model_kept_apart (a model number equal to the model on record becomes a
    second target, and the Rheem sentences change)."""
    assert pr.model_targets(RHEEM["model"], RHEEM["model_number"]) == [
        {"matched": "model", "searched": "PRO+E50 M2 RH92 CL", "target": pr.model_target(RHEEM["model"])}]
    result = pr.evaluate(_check(RHEEM, {"Distributor": _body([MATCHES[2]])})[0])
    assert result["matches"][0]["hits"] == [{"field": "Title", "text": "PRO+E50 M2 RH92 CL", "matched": "model",
                                             "searched": "PRO+E50 M2 RH92 CL"}]
    said = pr.statements(result)
    assert said[0].startswith("CPSC product recall SYN003 names the model PRO+E50 M2 RH92 CL:")
    assert "The model appears in the product recall's title: \"PRO+E50 M2 RH92 CL\"." in said
    near = pr.evaluate(_check(RHEEM, {"Manufacturer": _body(NEAR_MISSES)})[0])
    assert pr.statements(near)[0] == "No CPSC product recall names this exact model: PRO+E50 M2 RH92 CL."

    optima_recalls = [invented("OP01", model="Optima 880"), invented("OP02", description="The Optima line."),
                      invented("OP03", description="Model 880 only.")]
    result = pr.evaluate(_check(OPTIMA, {"Manufacturer": _body(optima_recalls)})[0])
    assert [m["recall_number"] for m in result["matches"]] == ["OP01"]
    assert result["matches"][0]["hits"] == [{"field": "Products[0].Model", "text": "Optima 880", "matched": "model",
                                             "searched": "Optima 880"}]
    assert pr.statements(pr.evaluate(_check(OPTIMA, {})[0]))[0] == (
        "No CPSC product recall names this exact model: Optima 880.")


def test_a_check_stored_before_the_model_number_is_read_on_its_model_alone() -> None:
    """A version 1 check has no model_number, so only its model on record is compared; no number is
    guessed from the model name. Mutation product_recalls_number_guessed_from_model."""
    check, _ = _check(TRANE, {"Manufacturer": _body([invented("V101", model="4TTR6036")])})
    old = {k: v for k, v in check.items() if k != "model_number"}
    old["check_version"] = 1
    assert pr.evaluate(check)["status"] == pr.MATCHED
    result = pr.evaluate(old)
    assert result["status"] == pr.NO_MATCH
    assert pr.statements(result)[0] == "No CPSC product recall names this exact model: XR16 4TTR6036."


def test_the_live_preflight_names_the_model_strings_each_product_recall_is_compared_with(
        tmp_path: Path) -> None:
    """Before anything is sent, each queried unit's line is followed by the strings the matcher will
    compare, exactly and in order: the Trane unit's model number, then its model on record; one string
    where the model number repeats the model on record or none is recorded; an invented maker is not
    queried, so nothing is compared. Mutation: product_recalls_plan_hides_compared_models."""
    registry = open_registry(tmp_path / "registry.sqlite")
    lines = pr.plan_lines(registry.list_appliances(), tmp_path / "store")
    after = {lines[i]: lines[i + 1] for i in range(len(lines) - 1) if ": maker value " in lines[i]}
    assert after['  appl-xr16 Trane XR16 4TTR6036: maker value "Trane"'] == (
        '    compared exactly in each product recall: model number "4TTR6036", then model on record '
        '"XR16 4TTR6036"')
    assert after['  appl-waterheater Rheem PRO+E50 M2 RH92 CL: maker value "Rheem"'] == (
        '    compared exactly in each product recall: model on record "PRO+E50 M2 RH92 CL"')
    assert after['  appl-optima880 Sundance Spas Optima 880: maker value "Sundance Spas"'] == (
        '    compared exactly in each product recall: model on record "Optima 880"')
    assert len(after) == sum(1 for line in lines if line.startswith("    compared exactly in each product recall: "))
    assert pr.compared_line({"model": "--", "model_number": None}).startswith("    compared: nothing")


# ---------------------------------------------------------------------------
# The property page
# ---------------------------------------------------------------------------


def _appliance_rows(page: str) -> dict[str, list[str]]:
    table = page[page.index("<h2>Appliances</h2>"):]
    table = table[:table.index("</table>")]
    rows = re.findall(r"<tr>\s*(.*?)\s*</tr>", table, re.S)
    cells = [re.findall(r"<td>(.*?)</td>", row) for row in rows]
    return {c[0]: c for c in cells if c}


def test_the_appliance_table_shows_the_model_number_escaped(tmp_path: Path) -> None:
    """The Trane row shows 4TTR6036 in its own column, a unit with none says "not recorded", and a hostile
    model number is escaped. Mutations: property_page_model_number_dropped, property_page_model_number_raw."""
    registry = open_registry(tmp_path / "registry.sqlite")
    page = render_property_page("prop-a", registry, generated_at="2026-10-13")
    assert "<th>Maker and model</th><th>Model number</th><th>Serial</th>" in page
    rows = _appliance_rows(page)
    assert rows["appl-xr16"][2:4] == ["Trane XR16 4TTR6036", "4TTR6036"]
    assert rows["appl-optima880"][3] == "not recorded"

    seed = json.loads(config.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    for appliance in seed["appliances"]:
        if appliance["id"] == "appl-xr16":
            appliance["model_number"] = "4TTR6036" + HOSTILE
    hostile_seed = tmp_path / "hostile_seed.json"
    hostile_seed.write_text(json.dumps(seed), encoding="utf-8")
    page = render_property_page("prop-a", open_registry(tmp_path / "hostile.sqlite", seed=hostile_seed),
                                generated_at="2026-10-13")
    assert _appliance_rows(page)["appl-xr16"][3] == esc("4TTR6036" + HOSTILE)
    assert HOSTILE not in page and "<script>" not in page


# ---------------------------------------------------------------------------
# Stored checks never collide
# ---------------------------------------------------------------------------


def _two_checks_in_one_second() -> tuple[dict[str, Any], dict[str, Any]]:
    first = pr.check_appliance(TRANE, fetch=FakeFetch({"Manufacturer": _body(NEAR_MISSES[:1])}), now=lambda: NOON)
    second = pr.check_appliance(TRANE, fetch=FakeFetch({"Manufacturer": _body(MATCHES[:1])}), now=lambda: NOON)
    assert first["checked_at"] == second["checked_at"] and first != second
    return first, second


def test_two_checks_in_the_same_second_each_get_their_own_file(tmp_path: Path) -> None:
    """Stored in either order, two different checks of one appliance in one UTC second keep two files,
    and the one stored last is read back as the latest; the same check stored twice keeps two files.
    Mutations: product_recalls_store_seq_ignored (every file of a second gets number 1, so the latest is
    the one whose hash sorts last), product_recalls_store_overwrites."""
    first, second = _two_checks_in_one_second()
    for name, order in (("ab", (first, second)), ("ba", (second, first))):
        store = tmp_path / name
        paths = [pr.store_check(store, check) for check in order]
        assert len(set(paths)) == 2 and all(p.name.startswith("20261013T120000Z-") for p in paths)
        assert [json.loads(p.read_text(encoding="utf-8")) for p in paths] == list(order)
        assert pr.latest_check(store, TRANE["id"]) == order[-1]
    same = [pr.store_check(tmp_path / "same", first) for _ in range(3)]
    assert len(set(same)) == 3 and {p.read_text(encoding="utf-8") for p in same} == {same[0].read_text("utf-8")}
    assert sorted((tmp_path / "same" / TRANE["id"]).iterdir()) == sorted(same)


def test_a_name_already_taken_is_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two writers that pick the same number (a race) still keep both files: the file is linked into
    place, which never replaces one already there, and the second moves to the next number. Mutation
    product_recalls_store_link_replaced."""
    first, _ = _two_checks_in_one_second()
    monkeypatch.setattr(pr, "_next_seq", lambda folder, stamp: 1)
    p1 = pr.store_check(tmp_path, first)
    p2 = pr.store_check(tmp_path, first)
    assert p1 != p2 and p1.exists() and p2.exists()
    assert sorted(p.name for p in p1.parent.iterdir()) == sorted([p1.name, p2.name])  # no temp file left


def test_the_latest_check_follows_the_sequence_and_ignores_other_files(tmp_path: Path) -> None:
    """A file named before the sequence number existed counts as number 0 of its second, so a check
    stored after it in the same second is the latest, whatever the hashes; a file store_check did not
    name is never taken for the newest. Mutations: product_recalls_latest_ignores_sequence,
    product_recalls_stray_file_newest."""
    first, second = _two_checks_in_one_second()
    folder = tmp_path / TRANE["id"]
    folder.mkdir()
    (folder / "20261013T120000Z-ffffffff.json").write_text(json.dumps(first), encoding="utf-8")
    stored = pr.store_check(tmp_path, second)
    assert stored.name.startswith("20261013T120000Z-001-")
    assert pr.latest_check(tmp_path, TRANE["id"]) == second
    (folder / "zz-notes.json").write_text("not a check", encoding="utf-8")
    assert pr.latest_check(tmp_path, TRANE["id"]) == second
