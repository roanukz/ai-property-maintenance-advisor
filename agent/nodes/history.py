"""history: the appliance's registry records (PLAN 8.3, 8.12; SC8, G3).

Code only. Reads the registry at the RunContext path and writes history_hits,
a list of JSON native records, each with its record_id:

- one entry per service record (kind "service", record_id "service:<id>"),
  newest first, with date, symptom, observed code and work done;
- one entry for the appliance itself (kind "appliance", record_id
  "appliance:<id>") when the registry holds a purchase date, an install date
  or warranty terms for it (decision 29).

An appliance with none of these, or a run with no appliance, yields [].
history is the only writer of history_hits, so the key needs no reducer in the
fan out (PLAN 8.2).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent.registry import Registry
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "history"
SERVICE_KIND = "service"
APPLIANCE_KIND = "appliance"
SERVICE_FIELDS = ("record_id", "appliance_id", "date", "symptom", "observed_code", "work_done",
                  "performed_by", "synthetic")
APPLIANCE_FIELDS = ("record_id", "appliance_id", "purchase_date", "install_date", "warranty_terms",
                    "warranty_source", "synthetic")
APPLIANCE_FACTS = ("purchase_date", "install_date", "warranty_terms")


def load_history(registry_path: Path | str | None, appliance_id: str | None) -> list[dict[str, Any]]:
    """The appliance's service records and registry dates, each with its record_id."""
    if not appliance_id or registry_path is None or not Path(registry_path).is_file():
        return []
    registry = Registry(registry_path)
    appliance = registry.get_appliance(appliance_id)
    if appliance is None:
        return []
    hits: list[dict[str, Any]] = []
    for row in registry.service_history(appliance_id):
        hit = {"kind": SERVICE_KIND, **{k: row.get(k) for k in SERVICE_FIELDS}}
        hit["synthetic"] = bool(hit["synthetic"])
        hits.append(hit)
    if any(appliance.get(k) for k in APPLIANCE_FACTS):
        entry = {"kind": APPLIANCE_KIND, **{k: appliance.get(k) for k in APPLIANCE_FIELDS}}
        entry["appliance_id"] = appliance["id"]
        entry["synthetic"] = bool(entry["synthetic"])
        hits.append(entry)
    return hits


def history(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Load the appliance's records into history_hits."""
    started = time.perf_counter()
    hits = load_history(runtime.context.registry_path, state.get("appliance_id"))
    return {"history_hits": hits, **latency_entry(state, NODE, time.perf_counter() - started)}
