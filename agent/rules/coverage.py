"""Warranty coverage status, computed in code (SC13; rule 8, decision 29).

Each `warranty_coverage` registry row (`warranty:<id>`) names one component,
a term in months and the event its term starts from (the appliance's own
install or purchase date). Code, never the model, computes:

- the end date: start plus the term in months (`maintenance._add_months`,
  clamped to the end of the month, so a 29 February start plus 12 months ends
  on 28 February);
- the status as of the run's one UTC date: "not_started" while the run date
  is before a full start date (the end date is still computed), then "active"
  while the run date is before the end date, else "ended". A start date the
  registry holds only as a year or a year and month (`parse_partial_date`),
  or not at all, gives no end date and the status "end_date_unknown", which
  is never shown as ended, and so does a start date that is not on the
  calendar (2019-02-30), which the coverage line says could not be read as
  a date rather than that it is missing;
- the brief's one coverage line (`coverage_statement`), dated "<run date>
  (UTC)". It lists active coverage with each component, its end date and its
  record id, and tells the owner to check with the maker before paying for a
  repair. With rows but none active it says no coverage on file is active as
  of the run date ("known to be active" when a row's end date is unknown or
  its term has not started) and gives the latest end date. A row whose term
  has not started is never counted as active or ended: it is named with its
  start date, as is a row with an unknown end date, and in either case the
  owner is told to check. With no rows it says "No warranty on file.", unless
  the appliance's record holds warranty terms text: then it says the terms
  are not broken into components, so nothing is computed from them, and tells
  the owner to check (`TERMS_ONLY`; the brief quotes the terms on the line
  before, so "No warranty on file." would contradict it). It never says "out
  of warranty", and never says which component covers a repair. Every
  synthetic row it names is labeled "synthetic".

The check with the maker sentence goes with coverage that is or may be
active (active, not started, end date unknown, or terms not broken into
rows), never after "No warranty on file." or when every row has ended.

The property page shows every row with the same functions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from agent.rules.citations import parse_partial_date
from agent.rules.maintenance import _add_months

ACTIVE = "active"
ENDED = "ended"
END_UNKNOWN = "end_date_unknown"
NOT_STARTED = "not_started"
STATUS_LABELS = {ACTIVE: "active", ENDED: "ended", END_UNKNOWN: "end date unknown", NOT_STARTED: "not started"}
START_FIELDS = {"install": "install_date", "purchase": "purchase_date"}

NO_WARRANTY = "No warranty on file."
TERMS_ONLY = ("The warranty terms on file are not broken into components, so no end date or status is "
              "computed from them.")
CHECK_WITH_MAKER = ("Check with the maker before paying for a repair; this brief does not say which "
                    "coverage, if any, applies to it.")


def start_date(appliance: Mapping[str, Any] | None, start_event: Any) -> str | None:
    """The appliance's date the term starts from, as the registry holds it, or None."""
    field = START_FIELDS.get(start_event) if isinstance(start_event, str) else None
    value = (appliance or {}).get(field) if field else None
    return value if isinstance(value, str) and value.strip() else None


def start_day(start: str | None) -> date | None:
    """The start as a date, or None when it is not a full, valid date."""
    parsed = parse_partial_date(start)
    if parsed is None or None in parsed:
        return None
    try:
        return date(*parsed)  # type: ignore[arg-type]
    except ValueError:
        return None


def end_date(start: str | None, term_months: int) -> date | None:
    """Start plus `term_months`, or None when the start is not a full, valid date."""
    begin = start_day(start)
    if begin is None:
        return None
    try:
        return _add_months(begin, int(term_months))
    except (ValueError, OverflowError):
        return None


def status(end: date | None, run_date: date, begin: date | None = None) -> str:
    """Not started before a full start date; then active while the run date is before the end date;
    unknown with no end date."""
    if begin is not None and run_date < begin:
        return NOT_STARTED
    if end is None:
        return END_UNKNOWN
    return ACTIVE if run_date < end else ENDED


def coverage_rows(rows: Sequence[Mapping[str, Any]] | None, appliance: Mapping[str, Any] | None,
                  run_date: date) -> list[dict[str, Any]]:
    """Each coverage row with its start date, end date and status as of `run_date`, in row order."""
    out = []
    for row in rows or []:
        start = start_date(appliance, row.get("start_event"))
        end = end_date(start, row.get("term_months") or 0)
        out.append({
            "record_id": row.get("record_id"),
            "component": row.get("component"),
            "term_months": row.get("term_months"),
            "start_event": row.get("start_event"),
            "start_date": start,
            "end_date": end.isoformat() if end is not None else None,
            "status": status(end, run_date, start_day(start)),
            "synthetic": bool(row.get("synthetic")),
        })
    return out


def _cite(row: Mapping[str, Any]) -> str:
    return f"{row.get('record_id')}{', synthetic' if row.get('synthetic') else ''}"


def no_rows_statement(terms_on_file: bool) -> str:
    """What the brief and the property page say for an appliance with no coverage rows."""
    return TERMS_ONLY if terms_on_file else NO_WARRANTY


def coverage_statement(rows: Sequence[Mapping[str, Any]], run_date: date, *, terms_on_file: bool = False) -> str:
    """The brief's code written coverage line for `coverage_rows` output.

    `terms_on_file` says the appliance's record holds warranty terms text,
    which matters only when there are no rows."""
    if not rows:
        statement = no_rows_statement(terms_on_file)
        return f"{statement} {CHECK_WITH_MAKER}" if statement == TERMS_ONLY else statement
    as_of = f"{run_date.isoformat()} (UTC)"
    active = [r for r in rows if r.get("status") == ACTIVE]
    ended = [r for r in rows if r.get("status") == ENDED]
    unknown = [r for r in rows if r.get("status") == END_UNKNOWN]
    not_started = [r for r in rows if r.get("status") == NOT_STARTED]
    parts = []
    if active:
        listed = "; ".join(f"{r.get('component')}, ends {r.get('end_date')} ({_cite(r)})" for r in active)
        parts.append(f"Warranty coverage on file that is active as of {as_of}: {listed}.")
    else:
        # A row whose end date is unknown, or whose term has not started, may be active
        # later or already, so neither is ever counted as ended.
        known = "known to be " if unknown or not_started else ""
        parts.append(f"No warranty coverage on file is {known}active as of {as_of}.")
        if ended:
            latest = max(ended, key=lambda r: str(r.get("end_date")))
            parts.append(f"The latest end date on file is {latest.get('end_date')} "
                         f"({latest.get('component')}, {_cite(latest)}).")
    if not_started:
        listed = "; ".join(f"{r.get('component')}, starts {r.get('start_date')} ({_cite(r)})" for r in not_started)
        parts.append(f"Not yet started as of {as_of}: {listed}.")
    # A start date that is set but is no date at all (2019-02-30) is unreadable, which is not the same
    # as missing or held only as a year or a year and month.
    unreadable = [r for r in unknown if r.get("start_date") and parse_partial_date(r.get("start_date")) is None]
    partial = [r for r in unknown if r not in unreadable]
    if partial:
        listed = "; ".join(f"{r.get('component')} ({_cite(r)})" for r in partial)
        parts.append(f"End date unknown, because the start date on file is missing or not a full date: {listed}.")
    if unreadable:
        listed = "; ".join(f"{r.get('component')} ({_cite(r)})" for r in unreadable)
        parts.append(f"End date unknown, because the start date on file could not be read as a date: {listed}.")
    if active or unknown or not_started:
        parts.append(CHECK_WITH_MAKER)
    return " ".join(parts)
