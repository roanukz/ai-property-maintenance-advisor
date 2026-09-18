"""Observed code rule (PLAN 8.5 rule 3, decision 7).

Code sets `observed_code` from the confirmed state value, never from the model,
and decides `confirmed` itself:

- no observed code: nothing is confirmed;
- exactly one candidate whose code matches: keep it, confirmed, drop the rest;
- more than one match: keep the matches, none confirmed;
- zero matches: a validation failure.

Matching compares normalized codes: Unicode NFC, uppercase, surrounding
whitespace and punctuation stripped. A free text code (for example a mode
description) matches only when it normalizes equal.
"""

from __future__ import annotations

import unicodedata
from typing import Any

NO_MATCH = "observed_code_no_match"


def normalize_code(code: Any) -> str | None:
    """The comparable form of a code, or None when there is nothing to compare."""
    if not isinstance(code, str):
        return None
    text = unicodedata.normalize("NFC", code).upper()
    start, end = 0, len(text)
    while start < end and _is_edge(text[start]):
        start += 1
    while end > start and _is_edge(text[end - 1]):
        end -= 1
    return text[start:end] or None


def _is_edge(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def codes_match(candidate_code: Any, observed: Any) -> bool:
    a, b = normalize_code(candidate_code), normalize_code(observed)
    return a is not None and a == b


def apply_observed_code(brief: dict[str, Any], observed_code: str | None) -> list[str]:
    """Narrow the candidates in place; return errors (empty when the rule passes).

    Only an ok brief is narrowed: a refusal or budget stop has no candidates.
    """
    brief["observed_code"] = observed_code
    candidates = brief.get("candidates") or []
    if normalize_code(observed_code) is None:
        for cand in candidates:
            cand["confirmed"] = False
        return []
    if brief.get("status") != "ok":
        return []
    matches = [c for c in candidates if codes_match(c.get("code"), observed_code)]
    if not matches:
        return [f"{NO_MATCH}: no candidate documents the observed code {observed_code!r}"]
    for cand in matches:
        cand["confirmed"] = len(matches) == 1
    brief["candidates"] = matches
    return []
