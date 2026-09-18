"""Citation rules: web sources, service records and registry dates.

- Rule 1 (PLAN 8.5): every `source_index` resolves into this run's code built
  source registry. Steps and candidates out of range already failed in the
  parity layer; here a caution with a bad index becomes uncited (as v1 does on
  the ok path, now on every path), and an upgrade or maintenance entry with a
  bad index is dropped (rule 9 drops it; its evidence check is Phase 4).
- Rule 7: `happened_before.record_id` must name a service record of this
  appliance loaded in this run, else `happened_before` becomes null.
- Rule 8 (decision 29): code writes `warranty.age_statement` from the registry
  install or purchase date, else the manufacture date, citing the registry
  `record_id`; warranty terms are quoted from the registry only.
- Rule 11: uncited sources are pruned and every index renumbered, last.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from agent.rules.v1_parity import is_integer

CITING_LISTS = ("try_first", "candidates", "upgrade_options", "maintenance_due")
# history_hits kinds (agent/nodes/history.py); an entry with no kind is a service record.
SERVICE_KIND = "service"


def _in_range(index: Any, n: int) -> bool:
    return is_integer(index) and 0 <= index < n


def cautions(brief: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if isinstance(brief.get("warranty_caution"), dict):
        out.append(brief["warranty_caution"])
    warranty = brief.get("warranty")
    if isinstance(warranty, dict):
        out.extend(c for c in warranty.get("cautions") or [] if isinstance(c, dict))
    return out


def resolve_citations(brief: dict[str, Any]) -> None:
    """Rule 1 for the fields the parity layer does not reject on (in place)."""
    n = len(brief.get("sources") or [])
    for caution in cautions(brief):
        if not _in_range(caution.get("source_index"), n):
            caution["source_index"] = None
    for key in ("upgrade_options", "maintenance_due"):
        brief[key] = [e for e in brief.get(key) or [] if _in_range(e.get("source_index"), n)]


def cited_indexes(brief: dict[str, Any]) -> set[int]:
    cited = {int(e["source_index"]) for key in CITING_LISTS for e in brief.get(key) or []}
    cited |= {int(c["source_index"]) for c in cautions(brief) if c.get("source_index") is not None}
    return cited


def prune_and_renumber(brief: dict[str, Any]) -> None:
    """Rule 11: drop uncited sources and renumber every index, keeping source order."""
    sources = brief.get("sources") or []
    keep = sorted(i for i in cited_indexes(brief) if 0 <= i < len(sources))
    new_index = {old: new for new, old in enumerate(keep)}
    for key in CITING_LISTS:
        for entry in brief.get(key) or []:
            entry["source_index"] = new_index[int(entry["source_index"])]
    for caution in cautions(brief):
        if caution.get("source_index") is not None:
            caution["source_index"] = new_index[int(caution["source_index"])]
    brief["sources"] = [sources[i] for i in keep]


def check_happened_before(brief: dict[str, Any], history_hits: list[dict[str, Any]] | None,
                          appliance_id: str | None) -> None:
    """Rule 7: keep happened_before only when it cites a loaded record of this appliance."""
    hb = brief.get("happened_before")
    if hb is None:
        return
    # Only service records count: the appliance's own registry entry (install
    # date, warranty terms) is loaded beside them but is not a prior event.
    loaded = {
        hit.get("record_id")
        for hit in history_hits or []
        if appliance_id is None or hit.get("appliance_id") in (None, appliance_id)
        if hit.get("kind", SERVICE_KIND) == SERVICE_KIND
    }
    record_id = hb.get("record_id") if isinstance(hb, dict) else None
    if not isinstance(record_id, str) or record_id not in loaded:
        brief["happened_before"] = None


_DATE = re.compile(r"^\s*(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?\s*$")
_MONTH_FIRST = re.compile(r"^\s*(\d{1,2})[-/](?:(\d{1,2})[-/])?(\d{4})\s*$")


def parse_partial_date(text: str | None) -> tuple[int, int | None, int | None] | None:
    """Year, optional month and optional day from "2019", "2019-06", "2019-06-03",
    "06/2019" or "06/03/2019" (month first)."""
    if not text:
        return None
    m = _DATE.match(text)
    if m:
        year = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else None
        day = int(m.group(3)) if m.group(3) else None
    else:
        m = _MONTH_FIRST.match(text)
        if not m:
            return None
        year, month = int(m.group(3)), int(m.group(1))
        day = int(m.group(2)) if m.group(2) else None
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and (month is None or not 1 <= day <= 31):
        return None
    return year, month, day


def age_in_years(start: tuple[int, int | None, int | None], today: date) -> int | None:
    """Whole years from `start` to `today`, using the month and day when known.

    None when the start is after today (as far as its known parts tell), so a
    future install date falls through to the next date instead of reading as
    "about 0 years old".
    """
    year, month, day = start
    known = tuple(part for part in (year, month, day) if part is not None)
    if known > (today.year, today.month, today.day)[: len(known)]:
        return None
    before_anniversary = month is not None and (today.month, today.day) < (month, day or 1)
    return today.year - year - (1 if before_anniversary else 0)


def age_statement(*, registry: dict[str, Any] | None, manufacture_date: str | None,
                  today: date) -> tuple[str, str | None]:
    """The code written age statement and the registry record it cites, if any."""
    registry = registry or {}
    for field, label in (("install_date", "Installed"), ("purchase_date", "Purchased")):
        value = registry.get(field)
        parsed = parse_partial_date(value)
        if parsed is not None:
            age = age_in_years(parsed, today)
            if age is not None:
                return (f"{label} {value} per property record {registry.get('record_id')}; "
                        f"about {age} years old as of {today.isoformat()}.", registry.get("record_id"))
    parsed = parse_partial_date(manufacture_date)
    if parsed is not None and age_in_years(parsed, today) is not None:
        return (f"Manufactured {manufacture_date} per the data plate; about "
                f"{age_in_years(parsed, today)} years old as of {today.isoformat()}.", None)
    if manufacture_date:
        return (f'The manufacture date "{manufacture_date}" could not be read as a date, '
                "so the unit's age is unknown.", None)
    return "No install, purchase or manufacture date is recorded, so the unit's age is unknown.", None


def apply_registry_dates(brief: dict[str, Any], *, registry: dict[str, Any] | None,
                         manufacture_date: str | None, today: date) -> None:
    """Rule 8: code owns the age statement and the quoted terms (in place).

    A warranty block is created on an ok brief when the registry has a date or
    terms to show, so G3's purchase date reaches the brief even when the model
    left the block out.
    """
    warranty = brief.get("warranty")
    registry = registry or {}
    has_registry_facts = any(registry.get(k) for k in ("install_date", "purchase_date", "warranty_terms"))
    if not isinstance(warranty, dict):
        if not (has_registry_facts and brief.get("status") == "ok"):
            return
        warranty = {"age_statement": "", "cautions": [], "verify": []}
        brief["warranty"] = warranty
    statement, record_id = age_statement(registry=registry, manufacture_date=manufacture_date, today=today)
    terms = registry.get("warranty_terms") or None
    warranty["age_statement"] = statement
    warranty["terms"] = terms
    warranty["record_id"] = record_id or (registry.get("record_id") if terms else None)
