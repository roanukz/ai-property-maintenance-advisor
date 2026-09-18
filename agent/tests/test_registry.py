"""Registry and synthetic seed tests (PLAN section 8.12, SC8, G3, decision 13)."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.registry import DEFAULT_SEED_PATH, Registry, SeedError, open_registry

OPTIMA = "appl-optima880"


@pytest.fixture()
def registry(tmp_path: Path) -> Registry:
    return open_registry(tmp_path / "registry.sqlite")


def _all_rows(reg: Registry) -> list[dict]:
    rows = reg.list_properties() + reg.list_appliances()
    for appliance in reg.list_appliances():
        rows += reg.service_history(appliance["id"])
        rows += reg.maintenance_log(appliance["id"])
    return rows


def test_seed_loads_two_properties_six_appliances_all_synthetic(registry: Registry) -> None:
    # Mutation: load_seed skips the last appliance, or the synthetic column is
    # stored as 0 (or not converted to True), turns this red.
    assert len(registry.list_properties()) == 2
    assert len(registry.list_appliances()) == 6
    labels = {p["label"] for p in registry.list_properties()}
    assert labels == {"Synthetic property A", "Synthetic property B"}
    rows = _all_rows(registry)
    assert rows and all(row["synthetic"] is True for row in rows)


def test_seed_rejects_a_row_not_marked_synthetic(tmp_path: Path) -> None:
    # Mutation: drop the synthetic check in load_seed.
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    data["appliances"][2]["synthetic"] = False
    bad = tmp_path / "bad_seed.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    reg = Registry(tmp_path / "registry.sqlite")
    with pytest.raises(SeedError):
        reg.load_seed(bad)
    assert reg.list_appliances() == []  # nothing half loaded


def test_seed_has_one_discontinued_synthetic_model_and_flags_third_real_model(
    registry: Registry,
) -> None:
    # Mutation: seed status "active" on the SC9 model, or drop pending_owner_choice,
    # or drop the dated record of the check of the maker's page (the check itself is
    # also recorded in the Phase 1 DECISION-LOG entry).
    discontinued = [a for a in registry.list_appliances() if a["status"] == "discontinued"]
    assert len(discontinued) == 1
    assert "(synthetic)" in discontinued[0]["model"]
    pending = [a for a in registry.list_appliances() if a["pending_owner_choice"]]
    assert [a["category"] for a in pending] == ["water_heater"]
    assert "rheem.com" in pending[0]["notes"]
    assert "Checked on 18 September 2026: the maker's product page" in pending[0]["notes"]


def test_optima_has_one_flo_service_record_with_citation_id(registry: Registry) -> None:
    # Mutation: record_id built with the wrong prefix or from appliance_id, or
    # service_history not filtered by appliance_id (would return records of others).
    [optima] = registry.find_by_model("Sundance Spas", "Optima 880")
    assert optima["id"] == OPTIMA
    history = registry.service_history(OPTIMA)
    assert len(history) == 1
    record = history[0]
    assert record["record_id"] == f"service:{record['id']}"
    assert record["appliance_id"] == OPTIMA
    assert record["observed_code"] == "FLO" and "FLO" in record["symptom"]
    assert registry.get_service_record(record["record_id"])["appliance_id"] == OPTIMA


def test_optima_carries_install_date_and_warranty_citation(registry: Registry) -> None:
    # Mutation: appliance record_id not set to "appliance:<id>", or install_date
    # or warranty_terms dropped from the upsert.
    optima = registry.get_appliance(OPTIMA)
    assert optima["record_id"] == f"appliance:{OPTIMA}"
    assert optima["install_date"] and optima["purchase_date"]
    assert optima["warranty_terms"] and "Synthetic" in optima["warranty_terms"]


def test_schema_rejects_a_mismatched_citation_id(registry: Registry) -> None:
    # Mutation: remove the CHECK (record_id = 'service:' || id) constraint.
    with sqlite3.connect(registry.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO service_records (id, record_id, appliance_id, date, symptom, synthetic)"
            " VALUES ('svc-x', 'service:svc-other', ?, '2026-01-01', 'x', 1)",
            (OPTIMA,),
        )


def test_fabricated_aquarest_model_is_absent(registry: Registry) -> None:
    # Mutation: add an Aquarest ZX-9000 Pro row to the seed.
    assert registry.find_by_model("Aquarest", "ZX-9000 Pro") == []
    assert registry.find_by_model(None, "ZX-9000 Pro") == []
    text = DEFAULT_SEED_PATH.read_text(encoding="utf-8").lower()
    assert "aquarest" not in text and "zx-9000" not in text


def test_find_by_model_ignores_case_and_spacing_but_not_manufacturer(registry: Registry) -> None:
    # Mutation: drop the manufacturer clause, or drop normalization of the argument.
    assert [a["id"] for a in registry.find_by_model("trane", "  xr16   4ttr6036 ")] == ["appl-xr16"]
    assert registry.find_by_model("Rheem", "XR16 4TTR6036") == []


def test_history_is_empty_for_appliance_with_none(registry: Registry) -> None:
    # Mutation: service_history ignores appliance_id (returns the Optima record).
    assert registry.service_history("appl-xr16") == []
    assert registry.service_history("no-such-appliance") == []


def test_maintenance_log_rows_carry_citation_ids(registry: Registry) -> None:
    # Mutation: maintenance_log query not filtered by appliance, or wrong prefix.
    [entry] = registry.maintenance_log("appl-dishwasher")
    assert entry["record_id"] == f"maintenance:{entry['id']}"
    assert entry["synthetic"] is True
    assert registry.maintenance_log(OPTIMA) == []


def test_lookups_round_trip(registry: Registry) -> None:
    # Mutation: record_lookup drops a column, swaps thread_id and route, or
    # lookups() ignores the run_id filter.
    stored = registry.record_lookup(
        "run-1", thread_id="t-1", appliance_id=OPTIMA, status="ok", route="research",
        cost_usd=0.0123, created_at="2026-09-17T00:00:00+00:00",
    )
    registry.record_lookup("run-2", status="no_reliable_answer")
    [row] = registry.lookups(run_id="run-1")
    assert row == stored
    assert (row["thread_id"], row["appliance_id"], row["status"], row["route"]) == (
        "t-1", OPTIMA, "ok", "research",
    )
    assert row["cost_usd"] == pytest.approx(0.0123)
    assert row["synthetic"] is True
    assert registry.lookups(run_id="run-2")[0]["synthetic"] is False
    assert len(registry.lookups()) == 2


def test_lookups_survive_reseed(registry: Registry) -> None:
    # Mutation: load_seed deletes and reinserts appliances (foreign key failure).
    registry.record_lookup("run-1", appliance_id=OPTIMA, status="ok")
    registry.load_seed()
    assert len(registry.lookups(appliance_id=OPTIMA)) == 1
    assert len(registry.list_appliances()) == 6


def test_record_lookup_rejects_unknown_appliance(registry: Registry) -> None:
    # Mutation: skip the appliance existence check (lookup stored as not synthetic).
    with pytest.raises(KeyError):
        registry.record_lookup("run-1", appliance_id="appl-missing", status="ok")
    assert registry.lookups() == []


def test_parameterized_queries_survive_quotes(registry: Registry) -> None:
    # Mutation: build any query with an f-string (syntax error, or the OR
    # clause matches every row).
    injected = "Optima 880' OR '1'='1"
    assert registry.find_by_model("Sundance Spas", injected) == []
    assert registry.find_by_model(None, injected) == []
    assert registry.service_history("x' OR '1'='1") == []
    assert registry.get_appliance("x' OR '1'='1") is None
    run_id = "run-'quoted'\"; DROP TABLE lookups; --"
    registry.record_lookup(run_id, status="ok")
    assert registry.lookups(run_id=run_id)[0]["run_id"] == run_id


def test_concurrent_reads_from_threads(registry: Registry) -> None:
    # Mutation: share one connection opened with check_same_thread=True, or
    # drop WAL (journal_mode assertion).
    def read(_: int) -> tuple[int, int]:
        return len(registry.list_appliances()), len(registry.service_history(OPTIMA))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read, range(64)))
    assert results == [(6, 1)] * 64
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_concurrent_lookup_writes_from_threads(registry: Registry) -> None:
    # Mutation: set the busy timeout to 0 and drop WAL (writers collide with
    # "database is locked").
    def write(i: int) -> None:
        registry.record_lookup(f"run-{i}", appliance_id=OPTIMA, status="ok")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    assert len(registry.lookups(appliance_id=OPTIMA)) == 40
