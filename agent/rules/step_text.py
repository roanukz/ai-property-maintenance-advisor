"""Step text helpers shared by the safety rules and the safety evaluation.

Pure: no I/O, no model, no state. A step is identified by its normalized text,
never by its position, so a signal recorded for a step still finds it after the
list is reordered or revalidated (decision 56).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

# v1 HTML prefixes a flagged step's title with "Safety: "; it is not step text.
_SAFETY_PREFIX = re.compile(r"^\s*safety:\s*", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Lowercase, NFKC, single spaces, no "Safety:" prefix, no trailing period."""
    value = unicodedata.normalize("NFKC", text or "")
    value = _SAFETY_PREFIX.sub("", value)
    value = _SPACE.sub(" ", value).strip().lower()
    return value.rstrip(".").rstrip()


def step_key(step: str | None, detail: str | None) -> str:
    """sha256 hex of the normalized step and detail, joined by a newline."""
    joined = normalize_text(step) + "\n" + normalize_text(detail)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def word_rule(step: str | None, detail: str | None, words: Iterable[str]) -> bool:
    """True when the step or its detail contains any listed word, whole word, any case."""
    alternatives = sorted({w.strip().lower() for w in words if w and w.strip()}, key=len, reverse=True)
    if not alternatives:
        return False
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in alternatives) + r")\b", re.IGNORECASE)
    text = f"{step or ''}\n{detail or ''}"
    return pattern.search(unicodedata.normalize("NFKC", text)) is not None
