"""Rule 9, upgrade options (PLAN 8.5 rule 9; SC9; decisions 27 and 28).

An upgrade option stays in the brief only when all of these hold:

1. its `source_index` resolves into this run's source registry. Rule 1
   (`citations.resolve_citations`) drops an entry that does not, and runs
   first; an unresolved index reaching this rule means the order was broken,
   so it raises `RuleOrderError` instead of quietly dropping the entry and
   hiding a broken rule 1;
2. its `evidence` quote passes the full span check of PLAN 8.11
   (`evidence.verify_span`) against the cited source's raw page text;
3. the successor model, as the entry prints it, is inside that quote (the
   span check's target) and stands as its own token where the quote sits in
   the page (`evidence.target_is_token`): "Optima 880" cut from "Optima
   880X" does not name the Optima 880. A failure is reported as
   `target_not_a_token`.

A failing entry is dropped, never kept with a flag and never rendered as
"unverified". Options the model proposed and options upgrade_check added from
graph edges go through exactly the same checks: this module does not know
where an entry came from.

Nothing here raises a validation error: a bad upgrade entry costs the entry,
not the brief. Currency amounts in an entry are rule 10 (`prices.py`).
"""

from __future__ import annotations

from typing import Any

from agent.rules.evidence import TARGET_NOT_TOKEN, target_is_token, verify_span
from agent.rules.v1_parity import is_integer

LIST = "upgrade_options"


class RuleOrderError(RuntimeError):
    """Rule 9 or 10 saw an entry whose index rule 1 should already have dropped."""


def cited_url(brief: dict[str, Any], entry: dict[str, Any]) -> str:
    """The URL of the source an entry cites; raises RuleOrderError when its index does not resolve."""
    sources = brief.get("sources") or []
    index = entry.get("source_index")
    if not is_integer(index) or not 0 <= index < len(sources) or not isinstance(sources[index].get("url"), str):
        raise RuleOrderError(f"source_index {index!r} does not resolve; rule 1 must run before rules 9 and 10")
    return sources[index]["url"]


def check_upgrade_options(brief: dict[str, Any], *, page_texts: dict[str, str],
                          report: list[dict[str, Any]] | None = None) -> None:
    """Keep only upgrade options whose quote verifies with the successor inside it (in place).

    Each dropped entry is appended to `report` as {list, index, successor,
    reason}, where reason is the span check's reason.
    """
    kept: list[dict[str, Any]] = []
    for i, entry in enumerate(brief.get(LIST) or []):
        url = cited_url(brief, entry)
        result = verify_span(entry.get("evidence"), page_texts.get(url), target=entry.get("successor_model"))
        reason = None if result.ok else result.reason
        if reason is None and not target_is_token(entry["evidence"], page_texts[url],
                                                  target=entry["successor_model"]):
            reason = TARGET_NOT_TOKEN
        if reason is None:
            kept.append(entry)
        elif report is not None:
            report.append({"list": LIST, "index": i, "successor": entry.get("successor_model"), "reason": reason})
    brief[LIST] = kept
