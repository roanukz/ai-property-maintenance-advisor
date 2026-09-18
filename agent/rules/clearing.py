"""Refusal and budget stop clearing (PLAN 8.5 rule 6, decision 9).

On NO RELIABLE ANSWER and on `budget_stopped`, code clears candidates,
try_first, upgrade_options and maintenance_due whatever the model returned.
warranty, warranty_caution and happened_before are kept, as v1 kept them.
The refusal block's `searched` list is built by code from the search trail.
"""

from __future__ import annotations

from typing import Any

CLEARING_STATUSES = ("no_reliable_answer", "budget_stopped")
CLEARED_LISTS = ("candidates", "try_first", "upgrade_options", "maintenance_due")


def clear_on_refusal(brief: dict[str, Any]) -> None:
    """Empty every answer list in place when the status is a refusal or a budget stop."""
    if brief.get("status") in CLEARING_STATUSES:
        for key in CLEARED_LISTS:
            brief[key] = []


def searched_from_trail(search_trail: list[dict[str, Any]] | None) -> list[str]:
    """The queries of the search calls in the code built trail, in order.

    Fetch entries (marked `tool: "fetch"`) are not searches, so they are left out.
    """
    out = []
    for entry in search_trail or []:
        if entry.get("tool", "search") != "search":
            continue
        query = entry.get("query")
        if isinstance(query, str) and query:
            out.append(query)
    return out
