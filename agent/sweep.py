"""The "Plan a replacement" sweep (SC14, decision 72): offline facts, ages and eligibility.

`advisor property sweep <property_id>` (or `--all`) reads, for each appliance,
the knowledge graph's verified SUPERSEDED_BY edges of its model and the
verified PART_DISCONTINUED edges of its model and family, and turns them into
replacement facts with upgrade_check's rule: a successor is shown only when
the verified evidence quote of the fact names it (`names_successor`).

- A SUPERSEDED_BY edge gives a "discontinued" fact naming its successor, when
  its quote names it (the graph already refuses an edge whose quote does not,
  and the sweep checks again). The successor's maker is shown only when
  the quote names the maker too, since nothing else verifies it.
- A PART_DISCONTINUED edge gives a "parts_unavailable" fact for its part. Its
  successor is one the model has a SUPERSEDED_BY edge to, and only when the
  part edge's own quote names that successor; with none named the fact shows
  the part and its quote and says no successor is named. A part quote never
  supplies a successor the graph does not hold.

Every fact carries the edge's source URL, retrieved date, verbatim evidence
and evidence hash, so what the page shows traces to one graph edge. Where the
graph has no fact for an appliance the sweep says exactly NONE_DOCUMENTED,
never that the unit is not discontinued.

The age of each unit is computed here from its registry install date as of
the page's one UTC date (decision 73), in whole years. A date held only as a
year or a year and month, a date that cannot be read, a date after the page
date, and a missing date each say so, and no age is guessed.

This module is offline: it reads a graph object and registry rows, imports no
model or search client and opens no ledger. The lifecycle research mode that
can add facts (agent/lifecycle.py) uses `lookup_refusal` and
`has_lifecycle_facts` from here to pick the units it may look up.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from agent.kg import KnowledgeGraph
from agent.rules.citations import age_in_years, parse_partial_date
from agent.rules.evidence import normalize_for_span
from agent.rules.tiers import maker_keys

DISCONTINUED = "discontinued"
PARTS_UNAVAILABLE = "parts_unavailable"
REASON_LABELS = {DISCONTINUED: "discontinued", PARTS_UNAVAILABLE: "parts unavailable"}

NONE_DOCUMENTED = "No discontinuation documented in what this tool has verified."
NO_SUCCESSOR_NAMED = "No successor is named in this evidence."

# Why the lifecycle research mode refuses a unit before any call.
REFUSED_NO_IDENTITY = "no maker and model on record"
REFUSED_SYNTHETIC = "its maker or model is labeled synthetic (an invented unit)"
REFUSED_UNKNOWN_MAKER = "its maker is not one whose own web domain this tool knows (config.MAKER_DOMAINS)"
SYNTHETIC_WORD = "synthetic"


def names_successor(quote: Any, successor: Any) -> bool:
    """True when the quote, normalized as section 8.11 does, contains the successor as printed."""
    if not isinstance(successor, str) or not isinstance(quote, str):
        return False
    wanted = normalize_for_span(successor)
    return bool(wanted) and wanted in normalize_for_span(quote)


def lifecycle_edges(kg: KnowledgeGraph | None, maker: Any, model: Any) -> tuple[list[dict[str, Any]],
                                                                                 list[dict[str, Any]]]:
    """(verified SUPERSEDED_BY edges of the model, verified PART_DISCONTINUED edges of the model or family)."""
    if kg is None or not isinstance(maker, str) or not isinstance(model, str):
        return [], []
    try:
        return kg.superseded_by(maker, model), kg.parts_discontinued(maker, model)
    except ValueError:  # a maker or model that normalizes to nothing has no key
        return [], []


def has_lifecycle_facts(kg: KnowledgeGraph | None, appliance: Mapping[str, Any]) -> bool:
    """True when the graph holds any verified lifecycle edge for the unit's model or family."""
    supers, parts = lifecycle_edges(kg, appliance.get("manufacturer"), appliance.get("model"))
    return bool(supers or parts)


def _fact(reason: str, edge: Mapping[str, Any], successor: Mapping[str, Any] | None) -> dict[str, Any]:
    source = edge.get("source") or {}
    # Only the successor model is checked against the quote, so its maker is shown only when the quote
    # names it too; otherwise the page would claim a maker the evidence never gives.
    maker = (successor or {}).get("successor_manufacturer")
    return {
        "reason": reason,
        "kind": edge.get("kind"),
        "src": edge.get("src"),
        "dst": edge.get("dst"),
        "part": edge.get("target") if reason == PARTS_UNAVAILABLE else None,
        "successor_manufacturer": maker if names_successor(edge.get("evidence"), maker) else None,
        "successor_model": (successor or {}).get("successor_model"),
        "evidence": edge.get("evidence"),
        "evidence_sha256": edge.get("evidence_sha256"),
        "source_url": edge.get("source_url"),
        "retrieved_at": edge.get("retrieved_at"),
        "source_title": source.get("title"),
        "brief_run_id": edge.get("brief_run_id"),
    }


def replacement_facts(kg: KnowledgeGraph | None, appliance: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The unit's replacement facts, superseded first, each traced to one verified edge."""
    supers, parts = lifecycle_edges(kg, appliance.get("manufacturer"), appliance.get("model"))
    facts: list[dict[str, Any]] = []
    named = [e for e in supers if names_successor(e.get("evidence"), e.get("successor_model"))]
    for edge in named:
        facts.append(_fact(DISCONTINUED, edge, edge))
    for edge in parts:
        successors = [e for e in named if names_successor(edge.get("evidence"), e.get("successor_model"))]
        if not successors:
            facts.append(_fact(PARTS_UNAVAILABLE, edge, None))
        for succ in successors:
            facts.append(_fact(PARTS_UNAVAILABLE, edge, succ))
    return facts


def _years(n: int) -> str:
    if n < 1:
        return "less than 1 year old"
    return "1 year old" if n == 1 else f"{n} years old"


def install_age(appliance: Mapping[str, Any], as_of: date) -> dict[str, Any]:
    """{"years": whole years or None, "statement": the code written age line} as of `as_of` (UTC)."""
    value = appliance.get("install_date")
    record = appliance.get("record_id")
    if not isinstance(value, str) or not value.strip():
        return {"years": None, "statement": "No install date is recorded, so the age is not computed."}
    unreadable = {"years": None, "statement": f"The install date on record, {value}, could not be read as a "
                                              "date, so the age is not computed."}
    parsed = parse_partial_date(value)  # None for a full date not on the calendar, such as 2019-02-30
    if parsed is None:
        return unreadable
    _, month, day = parsed
    if day is None:
        held = "the year and month" if month is not None else "the year"
        return {"years": None, "statement": f"The install date on record, {value}, gives only {held}, "
                                            "so the age is not computed."}
    years = age_in_years(parsed, as_of)
    if years is None:
        return {"years": None, "statement": f"The install date on record, {value}, is after "
                                            f"{as_of.isoformat()} (UTC), so the age is not computed."}
    return {"years": years, "statement": f"Installed {value} per property record {record}; "
                                         f"{_years(years)} as of {as_of.isoformat()} (UTC)."}


def sweep_row(kg: KnowledgeGraph | None, appliance: Mapping[str, Any], as_of: date) -> dict[str, Any]:
    """One appliance's sweep result: identity, age, facts, and the none documented sentence when empty."""
    facts = replacement_facts(kg, appliance)
    return {
        "appliance_id": appliance.get("id"),
        "property_id": appliance.get("property_id"),
        "manufacturer": appliance.get("manufacturer"),
        "model": appliance.get("model"),
        "category": appliance.get("category"),
        "record_id": appliance.get("record_id"),
        "synthetic": bool(appliance.get("synthetic")),
        "age": install_age(appliance, as_of),
        "facts": facts,
        "statement": None if facts else NONE_DOCUMENTED,
    }


def sweep_rows(kg: KnowledgeGraph | None, appliances: Sequence[Mapping[str, Any]], as_of: date) -> list[dict[str, Any]]:
    return [sweep_row(kg, a, as_of) for a in appliances]


def lookup_refusal(appliance: Mapping[str, Any]) -> str | None:
    """Why the lifecycle research mode refuses this unit before any call, or None.

    A unit is looked up only when its maker is one whose own web domain the
    tool knows (config.MAKER_DOMAINS, the map the tier ceiling uses) and
    neither its maker nor its model is labeled synthetic.
    """
    maker, model = appliance.get("manufacturer"), appliance.get("model")
    if not isinstance(maker, str) or not maker.strip() or not isinstance(model, str) or not model.strip():
        return REFUSED_NO_IDENTITY
    if SYNTHETIC_WORD in maker.casefold() or SYNTHETIC_WORD in model.casefold():
        return REFUSED_SYNTHETIC
    if not maker_keys(maker):
        return REFUSED_UNKNOWN_MAKER
    return None


def _day(value: Any) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4:5] == "-" else text


def fact_line(fact: Mapping[str, Any]) -> str:
    """One fact as the sweep prints it: reason, successor or part, source, date and hash."""
    reason = REASON_LABELS.get(fact.get("reason"), str(fact.get("reason")))
    head = reason if fact.get("part") is None else f"{reason} (part {fact.get('part')})"
    successor = " ".join(str(v) for v in (fact.get("successor_manufacturer"), fact.get("successor_model")) if v)
    named = f"successor {successor}" if fact.get("successor_model") else "no successor named"
    return (f"{head}; {named}; source {fact.get('source_url')}; retrieved {_day(fact.get('retrieved_at'))}; "
            f"evidence sha256 {fact.get('evidence_sha256')}")


def row_lines(row: Mapping[str, Any]) -> list[str]:
    """The lines the sweep prints for one appliance."""
    unit = (f"  {row.get('appliance_id')} {row.get('manufacturer')} {row.get('model')}"
            f"{' (synthetic record)' if row.get('synthetic') else ''}")
    lines = [f"{unit}: {row['age']['statement']}"]
    if row.get("statement"):
        lines.append(f"    {row['statement']}")
    lines += [f"    {fact_line(fact)}" for fact in row.get("facts") or []]
    return lines
