"""Rule 9, maintenance due (PLAN 8.5 rule 9; PRD:111; decision 27).

A maintenance entry stays in the brief only when all of these hold:

1. its `source_index` resolves into this run's source registry (rule 1
   drops it otherwise; see `upgrades.RuleOrderError`);
2. the cited source's final tier, after the host ceiling (rule 2), is
   `manufacturer`: an interval from a dealer page or a forum is dropped;
3. its `evidence` quote passes the full span check of PLAN 8.11 against the
   cited source's raw page text, with the entry's `interval` text as the
   target, so the interval is verbatim inside a verified quote.

Code then owns the dates:

- `last_done_record_id` is kept when it names a `maintenance_log` row of this
  appliance (`maintenance:<id>`). When it is empty or names no such row, code
  looks for the row itself: the newest `maintenance_log` row of this
  appliance whose task matches the entry's task (`task_key`: casefolded
  words, articles dropped, simple verb and plural endings removed, so "Clean
  the bucket filter" matches "Cleaned the bucket filter"). The model never
  sees maintenance_log record IDs (PLAN 8.3), so on a live run this lookup
  is what finds the row. With no row either way the field becomes None;
- `last_done_on` is that row's `done_on`, copied by code, so the page shows
  the date the due date was computed from;
- `due_date` is computed here from that row's `done_on` plus the interval,
  and is None when there is no such row or the interval is not one plain
  period ("every 3 months", "annually"). Any value the model supplied for
  either date is overwritten; the draft schema has neither field.

Nothing here raises a validation error: a bad entry costs the entry, not the
brief. Currency amounts in an entry are rule 10 (`prices.py`).
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Any

from agent.rules.citations import parse_partial_date
from agent.rules.evidence import verify_span
from agent.rules.upgrades import cited_url

LIST = "maintenance_due"
REQUIRED_TIER = "manufacturer"
DROP_TIER = "tier_not_manufacturer"

_NUMBER_WORDS = {
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty": 20,
    "thirty": 30, "other": 2,
}
_EVERY = re.compile(
    r"\bevery\s+(?:(?P<n>\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")\s+)?(?P<unit>day|week|month|year)s?\b",
    re.IGNORECASE,
)
# Single word periods, as (unit, count).
_WORDS = {
    "daily": ("day", 1), "weekly": ("week", 1), "monthly": ("month", 1), "quarterly": ("month", 3),
    "annually": ("year", 1), "yearly": ("year", 1), "biannually": ("month", 6),
    "semiannually": ("month", 6), "semi-annually": ("month", 6),
}
_WORD = re.compile(r"\b(" + "|".join(re.escape(w) for w in _WORDS) + r")\b", re.IGNORECASE)


def parse_interval(text: str | None) -> tuple[str, int] | None:
    """(unit, count) for an interval that names exactly one period, else None.

    "every 3 months" gives ("month", 3), "every other week" ("week", 2),
    "annually" ("year", 1). Text naming two different periods ("rinse every 2
    weeks and replace every 12 months") is ambiguous, so it gives None.
    """
    if not isinstance(text, str):
        return None
    found: set[tuple[str, int]] = set()
    for m in _EVERY.finditer(text):
        raw = (m.group("n") or "1").lower()
        count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        found.add((m.group("unit").lower(), count))
    for m in _WORD.finditer(text):
        found.add(_WORDS[m.group(1).lower()])
    found = {(unit, count) for unit, count in found if count > 0}
    return found.pop() if len(found) == 1 else None


def _add_months(start: date, months: int) -> date:
    total = start.month - 1 + months
    year, month = start.year + total // 12, total % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def due_date(done_on: str | None, interval: str | None) -> str | None:
    """The ISO date one interval after `done_on` (a full date), or None."""
    parsed = parse_partial_date(done_on)
    period = parse_interval(interval)
    if parsed is None or period is None or None in parsed:
        return None
    try:
        start = date(*parsed)  # type: ignore[arg-type]
    except ValueError:
        return None
    unit, count = period
    if unit == "day":
        return (start + timedelta(days=count)).isoformat()
    if unit == "week":
        return (start + timedelta(weeks=count)).isoformat()
    return _add_months(start, count * (12 if unit == "year" else 1)).isoformat()


_ARTICLES = {"a", "an", "the", "of", "to", "and"}
_WORD_RE = re.compile(r"[^\W_]+")
# One ending is removed per word, the first that leaves a stem of 3 or more letters.
_ENDINGS = ("ing", "ed", "es", "s", "e")


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if ending == "s" and word.endswith("ss"):
            continue
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def task_key(task: Any) -> tuple[str, ...]:
    """The words of a maintenance task, casefolded, articles dropped, one ending removed each."""
    if not isinstance(task, str):
        return ()
    return tuple(_stem(w) for w in _WORD_RE.findall(task.casefold()) if w not in _ARTICLES)


def newest_matching_row(rows: dict[str, dict[str, Any]], task: Any) -> dict[str, Any] | None:
    """The newest row (by done_on) whose task has the same task_key as `task`, or None."""
    key = task_key(task)
    if not key:
        return None
    matches = [row for row in rows.values() if task_key(row.get("task")) == key]
    if not matches:
        return None
    return max(matches, key=lambda row: str(row.get("done_on") or ""))


def _log_rows(maintenance_log: list[dict[str, Any]] | None, appliance_id: str | None) -> dict[str, dict[str, Any]]:
    """This appliance's maintenance_log rows by record_id."""
    rows = {}
    for row in maintenance_log or []:
        if appliance_id is not None and row.get("appliance_id") not in (None, appliance_id):
            continue
        record_id = row.get("record_id")
        if isinstance(record_id, str) and record_id:
            rows[record_id] = row
    return rows


def check_maintenance(brief: dict[str, Any], *, page_texts: dict[str, str],
                      maintenance_log: list[dict[str, Any]] | None = None,
                      appliance_id: str | None = None,
                      report: list[dict[str, Any]] | None = None) -> None:
    """Keep only manufacturer tier entries whose interval is inside a verified quote,
    and write `last_done_record_id`, `last_done_on` and `due_date` from the registry (in place)."""
    sources = brief.get("sources") or []
    rows = _log_rows(maintenance_log, appliance_id)
    kept: list[dict[str, Any]] = []
    for i, entry in enumerate(brief.get(LIST) or []):
        url = cited_url(brief, entry)
        if sources[entry["source_index"]].get("tier") != REQUIRED_TIER:
            reason = DROP_TIER
        else:
            result = verify_span(entry.get("evidence"), page_texts.get(url), target=entry.get("interval"))
            reason = None if result.ok else result.reason
        if reason is not None:
            if report is not None:
                report.append({"list": LIST, "index": i, "task": entry.get("task"), "reason": reason})
            continue
        record_id = entry.get("last_done_record_id")
        row = rows.get(record_id) if isinstance(record_id, str) else None
        if row is None:
            row = newest_matching_row(rows, entry.get("task"))
        entry["last_done_record_id"] = row.get("record_id") if row is not None else None
        entry["last_done_on"] = row.get("done_on") if row is not None else None
        entry["due_date"] = due_date(row.get("done_on"), entry.get("interval")) if row is not None else None
        kept.append(entry)
    brief[LIST] = kept

