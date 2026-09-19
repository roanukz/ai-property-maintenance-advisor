"""Verbatim excerpts cut from a page's raw text (decision 24, PLAN 8.7).

The models never see a whole page. Code cuts windows of `raw_content` around
the observed code, the model, its family, part numbers and symptom terms,
under a character budget. Every excerpt is an exact substring of the raw text,
so a quote the model copies from an excerpt can be verified against the page.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent import config

# Rendered between two excerpts; counts toward the budget.
JOIN = f" {config.SNIPPET_JOINER} "

# A token of 5 or more capitals, digits and dashes holding at least one digit
# and one capital letter: "HC-1234A" and "6600194X" match, "6600-194" does not.
PART_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Z0-9-]{5,}(?![A-Za-z0-9]))(?=[A-Z0-9-]*[0-9])(?=[A-Z0-9-]*[A-Z])"
    r"[A-Z0-9]+(?:-[A-Z0-9]+)*(?![A-Za-z0-9])"
)

STOPWORDS = frozenset(
    """
    about after again also because been before being does doesn done down
    from have into just keeps like more most much never only over some that
    their them then there these they this very what when where which while
    will with won't would unit shows says display displays code error
    """.split()
)


@dataclass(frozen=True)
class Terms:
    """Excerpt anchors in priority order: identity terms first, then symptom words."""

    ids: tuple[str, ...] = ()
    symptom: tuple[str, ...] = ()


def model_family(model: str) -> str | None:
    """The documented family a model most likely belongs to, or None.

    "Optima 880" gives "Optima"; "XR16" gives "XR". A family equal to the
    model, or shorter than 2 characters, is not a separate anchor.
    """
    model = model.strip()
    if not model:
        return None
    first = model.split()[0]
    if first != model and len(first) >= 3:
        return first
    letters = re.match(r"[A-Za-z]+", model)
    if letters and len(letters.group()) >= 2 and letters.group() != model:
        return letters.group()
    return None


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out = []
    for value in values:
        value = value.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return tuple(out)


def excerpt_terms(identity: dict[str, Any] | None, observed_code: str | None, symptom: str | None) -> Terms:
    """Anchors for one run: observed code, model, family, then symptom words."""
    ids: list[str] = []
    if observed_code:
        ids.append(observed_code)
    model = str((identity or {}).get("model") or "")
    if model.strip():
        ids.append(model)
        family = model_family(model)
        if family:
            ids.append(family)
    words = re.findall(r"[A-Za-z][A-Za-z']+", symptom or "")
    symptom_words = [
        w for w in words
        if len(w) >= config.EXCERPT_MIN_SYMPTOM_WORD_CHARS and w.casefold() not in STOPWORDS
    ]
    return Terms(ids=_dedupe(ids), symptom=_dedupe(symptom_words))


def _term_pattern(term: str) -> re.Pattern[str]:
    # Match at the start of a word, any case, with any run of whitespace where
    # the term has a space ("Optima  880" still matches "Optima 880").
    parts = [re.escape(p) for p in term.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(parts), re.IGNORECASE)


def _anchors(text: str, terms: Terms) -> list[tuple[int, int]]:
    """(start, end) of every anchor match, in priority order."""
    found: list[tuple[int, int]] = []
    for term in terms.ids:
        found += [m.span() for m in _term_pattern(term).finditer(text)]
    found += [m.span() for m in PART_NUMBER.finditer(text)]
    for term in terms.symptom:
        found += [m.span() for m in _term_pattern(term).finditer(text)]
    return found


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _cost(spans: list[tuple[int, int]]) -> int:
    if not spans:
        return 0
    return sum(end - start for start, end in spans) + len(JOIN) * (len(spans) - 1)


def _boundary(text: str, i: int) -> bool:
    """True when position i does not sit inside a word (whitespace or a text edge on one side)."""
    return i <= 0 or i >= len(text) or text[i - 1].isspace() or text[i].isspace()


def _prev_boundary(text: str, i: int) -> int:
    while not _boundary(text, i):
        i -= 1
    return i


def _next_boundary(text: str, i: int) -> int:
    while not _boundary(text, i):
        i += 1
    return i


def _snap(text: str, start: int, end: int, anchor: tuple[int, int]) -> tuple[int, int]:
    """Window edges moved to word boundaries, so no excerpt starts or ends mid word (Phase 6 finding 3).

    Each edge moves inward to the nearest boundary, or outward when moving
    inward would cut into the anchor; edge whitespace is then trimmed. The
    caller checks the budget on the result, since an outward move can grow it.
    """
    s = _next_boundary(text, start)
    if s > anchor[0]:
        s = _prev_boundary(text, min(start, anchor[0]))
    e = _prev_boundary(text, end)
    if e < anchor[1]:
        e = _next_boundary(text, max(end, anchor[1]))
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def _opening(text: str, max_chars: int) -> list[tuple[int, int]]:
    """The page's opening in whole words within max_chars, or nothing when its first word is longer."""
    s = _next_boundary(text, 0)
    while s < len(text) and text[s].isspace():
        s += 1
    e = _prev_boundary(text, min(len(text), s + max_chars))
    while e > s and text[e - 1].isspace():
        e -= 1
    return [(s, e)] if e > s else []


def cut_spans(text: str | None, terms: Terms, max_chars: int, *,
              window: int = config.EXCERPT_WINDOW_CHARS) -> list[tuple[int, int]]:
    """(start, end) of each excerpt in page order; each starts and ends at a word boundary.

    With no anchor in the text, the page's opening is used instead.
    """
    if not text or max_chars <= 0:
        return []
    if len(text) <= max_chars:
        return [(0, len(text))]
    chosen: list[tuple[int, int]] = []
    for anchor in _anchors(text, terms):
        start, end = anchor
        if any(s <= start and end <= e for s, e in chosen):
            continue
        wide = _snap(text, max(0, start - window), min(len(text), end + window), anchor)
        candidate = _merge(chosen + [wide])
        if _cost(candidate) > max_chars:
            spare = max_chars - _cost(chosen) - (len(JOIN) if chosen else 0) - (end - start)
            if spare < 0:
                continue
            pad = spare // 2
            narrow = _snap(text, max(0, start - pad), min(len(text), end + pad), anchor)
            candidate = _merge(chosen + [narrow])
            if _cost(candidate) > max_chars:
                # Snapping one edge outward can overrun; the anchor's own words may still fit.
                candidate = _merge(chosen + [_snap(text, start, end, anchor)])
            if _cost(candidate) > max_chars:
                continue
        chosen = candidate
        if _cost(chosen) >= max_chars - len(JOIN):
            break
    if not chosen:
        return _opening(text, max_chars)
    return chosen


def cut_excerpts(text: str | None, terms: Terms, max_chars: int, *,
                 window: int = config.EXCERPT_WINDOW_CHARS) -> list[str]:
    """Verbatim windows of `text`, whole words only, whose rendered length is at most max_chars."""
    if not text:
        return []
    chosen = cut_spans(text, terms, max_chars, window=window)
    return [text[s:e] for s, e in chosen]


def render_excerpts(excerpts: Sequence[str]) -> str:
    """The text a model sees: the excerpts in page order, joined by the gap marker."""
    return JOIN.join(excerpts)


def source_text(raw: str | None, snippet: str | None, terms: Terms,
                max_chars: int = config.SEARCH_RESULT_MAX_CHARS) -> str:
    """Excerpts of a source's raw text for synthesize, or its snippet when no raw text exists."""
    if raw:
        return render_excerpts(cut_excerpts(raw, terms, max_chars))
    return (snippet or "")[:max_chars]
