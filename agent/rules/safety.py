"""Safety ordering (PLAN 8.5 rule 5, decision 8).

Safety flagged steps move to the front with a stable sort, so the relative
order within each group is the model's. The brief is reordered, never
rejected: two recorded v1 briefs put a safety step last.
"""

from __future__ import annotations

from typing import Any


def safety_first(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the steps with every safety step first, order otherwise kept."""
    return sorted(steps, key=lambda step: step.get("safety_flag") is not True)


def apply_safety_order(brief: dict[str, Any]) -> None:
    brief["try_first"] = safety_first(brief.get("try_first") or [])
