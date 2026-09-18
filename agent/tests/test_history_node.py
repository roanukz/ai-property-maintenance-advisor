"""The history node (PLAN 8.3, 8.12; SC8 and G3 inputs).

history reads the synthetic registry seed at the RunContext path. Each test
names the mutation that turns it red.
"""

from __future__ import annotations

from pathlib import Path

from langgraph.runtime import Runtime

from agent.nodes.history import history, load_history
from agent.state import RunContext, check_json_native
from agent.tests.test_router import seed_registry

OPTIMA = "appl-optima880"


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(run_id="run-history", mode="replay", ledger_path=tmp_path / "l.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "g.json",
                      pages_dir=tmp_path / "pages")


def test_history_records_carry_record_ids(tmp_path: Path) -> None:
    """Mutations: history_drops_record_id (hits lose their record_id);
    history_skips_service_records; history_skips_registry_dates (no appliance entry, so
    the install date and warranty terms never reach synthesize)."""
    seed_registry(tmp_path / "registry.sqlite")
    out = history({"appliance_id": OPTIMA}, Runtime(context=_ctx(tmp_path)))
    check_json_native(out)
    hits = out["history_hits"]
    assert all(isinstance(h.get("record_id"), str) and h["record_id"] for h in hits)
    service = [h for h in hits if h["kind"] == "service"]
    assert [(h["record_id"], h["date"], h["observed_code"]) for h in service] == [
        ("service:svc-0001", "2025-11-14", "FLO")]
    assert service[0]["appliance_id"] == OPTIMA and service[0]["synthetic"] is True
    assert "FLO" in service[0]["symptom"]
    appliance = [h for h in hits if h["kind"] == "appliance"]
    assert len(appliance) == 1
    assert appliance[0]["record_id"] == f"appliance:{OPTIMA}"
    assert (appliance[0]["install_date"], appliance[0]["purchase_date"]) == ("2021-06-10", "2021-05-28")
    assert appliance[0]["warranty_terms"].startswith("Synthetic warranty terms")
    assert "history" in out["latency"]


def test_history_is_only_this_appliances(tmp_path: Path) -> None:
    """Mutation history_ignores_appliance: every appliance's service records are loaded."""
    seed_registry(tmp_path / "registry.sqlite")
    hits = load_history(tmp_path / "registry.sqlite", "appl-xr16")
    assert {h["appliance_id"] for h in hits} == {"appl-xr16"}
    assert [h["kind"] for h in hits] == ["appliance"]  # dates only, no service records


def test_appliance_with_no_records_yields_empty(tmp_path: Path) -> None:
    """Mutation history_empty_appliance_entry: an appliance entry is written even when the
    registry holds no date or terms for it (route would then add history to every run)."""
    seed_registry(tmp_path / "registry.sqlite")
    ctx = _ctx(tmp_path)
    assert history({"appliance_id": "appl-bare"}, Runtime(context=ctx))["history_hits"] == []
    assert history({"appliance_id": None}, Runtime(context=ctx))["history_hits"] == []
    assert load_history(tmp_path / "registry.sqlite", "appl-unknown") == []
    assert load_history(tmp_path / "missing.sqlite", OPTIMA) == []


def test_service_records_come_newest_first(tmp_path: Path) -> None:
    """Mutation history_oldest_first: service records are loaded oldest first,
    so synthesize reads the stale record as the latest one."""
    from agent.tests.test_sc8_history import seed_registry as seed_with

    older = {"id": "svc-0000", "appliance_id": OPTIMA, "date": "2023-02-03",
             "symptom": "Synthetic older record: jets were weak.", "observed_code": None,
             "work_done": "Synthetic: cleaned the jet inserts.", "performed_by": "Synthetic technician",
             "synthetic": True}
    seed_with(tmp_path / "registry.sqlite", extra_records=[older])
    hits = load_history(tmp_path / "registry.sqlite", OPTIMA)
    service = [h["record_id"] for h in hits if h["kind"] == "service"]
    assert service == ["service:svc-0001", "service:svc-0000"]
