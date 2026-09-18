"""The verbatim evidence span check (PLAN 8.11, SC6).

A span is accepted only when all five conditions of section 8.11 hold:

1. it is checked against the page's raw text (`raw_content` from search with
   raw content, or from extract), never against a snippet; with no raw text
   there is nothing to check against, so the span is rejected;
2. after `normalize_for_span`, applied identically to both, the span is a
   contiguous substring of the page text;
3. it does not contain the snippet joiner "[...]";
4. its normalized length is 20 to 400 characters;
5. it contains the edge target as printed (the code, part number, successor
   model or family name), compared case sensitively after the same
   normalization.

Normalization is exactly: Unicode NFC, then every run of the whitespace
characters PLAN 8.11 lists (space, tab, newline, carriage return, no break
space U+00A0) becomes one space, then leading and trailing spaces are removed.
Nothing else: no case folding, no quote, dash, ligature, soft hyphen or
punctuation changes.

Condition 4 is read as measured after normalization: a span whose raw form is
longer only because of whitespace runs is measured by its normalized length.

`target_is_token` is a separate check, not one of the five: it asks whether
the target stands as its own token (no letter or digit directly before or
after it) where the span sits in the page, so a quote cut off inside a longer
code ("shows E1" taken from "shows E10") does not support the shorter code.
Code grounding and HAS_CODE edges use it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from agent import config

# The whitespace characters PLAN 8.11 names, and only those.
_SPAN_WHITESPACE = re.compile("[ \t\n\r\u00a0]+")

OK = "verified"
NO_SPAN = "no_span"
NO_PAGE_TEXT = "no_page_text"
SNIPPET_JOINER = "snippet_joiner"
TOO_SHORT = "too_short"
TOO_LONG = "too_long"
NO_TARGET = "no_target"
TARGET_MISSING = "target_not_in_span"
NOT_IN_PAGE = "not_in_page_text"
TARGET_NOT_TOKEN = "target_not_a_token"

# A letter or digit: what may not sit directly beside a target token.
_ALNUM = re.compile(r"[^\W_]")


@dataclass(frozen=True)
class SpanResult:
    """The outcome of one span check; `reason` is OK or the first failed condition."""

    ok: bool
    reason: str


def normalize_for_span(text: str) -> str:
    """Section 8.11 normalization: NFC, listed whitespace runs to one space, trimmed."""
    return _SPAN_WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip(" ")


def evidence_sha256(evidence: str) -> str:
    """The hash stored beside an edge's evidence: sha256 of its UTF-8 bytes, as stored."""
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


def check_span_shape(span: str | None, *, target: str | None) -> SpanResult:
    """Conditions 3 to 5, which need no page text: joiner, length, target in span."""
    if not isinstance(span, str) or not normalize_for_span(span):
        return SpanResult(False, NO_SPAN)
    norm = normalize_for_span(span)
    if config.SNIPPET_JOINER in norm:
        return SpanResult(False, SNIPPET_JOINER)
    if len(norm) < config.EVIDENCE_MIN_CHARS:
        return SpanResult(False, TOO_SHORT)
    if len(norm) > config.EVIDENCE_MAX_CHARS:
        return SpanResult(False, TOO_LONG)
    wanted = normalize_for_span(target) if isinstance(target, str) else ""
    if not wanted:
        return SpanResult(False, NO_TARGET)
    if wanted not in norm:
        return SpanResult(False, TARGET_MISSING)
    return SpanResult(True, OK)


def verify_span(span: str | None, page_text: str | None, *, target: str | None) -> SpanResult:
    """Conditions 1 to 5 of PLAN 8.11 against a page's raw text."""
    shape = check_span_shape(span, target=target)
    if not shape.ok:
        return shape
    if not isinstance(page_text, str) or not normalize_for_span(page_text):
        return SpanResult(False, NO_PAGE_TEXT)
    if normalize_for_span(span) not in normalize_for_span(page_text):
        return SpanResult(False, NOT_IN_PAGE)
    return SpanResult(True, OK)


def target_is_token(span: str, page_text: str, *, target: str) -> bool:
    """True when, at some place the normalized span occurs in the normalized page,
    an occurrence of the target inside it has no letter or digit directly before
    or after it in the page."""
    norm_span, norm_page, wanted = (normalize_for_span(t) for t in (span, page_text, target))
    if not norm_span or not wanted:
        return False
    offsets = [i for i in range(len(norm_span) - len(wanted) + 1) if norm_span.startswith(wanted, i)]
    start = norm_page.find(norm_span)
    while start != -1:
        for offset in offsets:
            i, j = start + offset, start + offset + len(wanted)
            before = norm_page[i - 1] if i > 0 else ""
            after = norm_page[j] if j < len(norm_page) else ""
            if not _ALNUM.match(before) and not _ALNUM.match(after):
                return True
        start = norm_page.find(norm_span, start + 1)
    return False


def anchor_to_target(span: str | None, page_text: str | None, *, target: str | None) -> str | None:
    """Widen a verbatim quote that lacks the target so it starts at the target, or None.

    Applies only when the quote is verbatim in the page and the target is
    printed as its own token on the same raw line, at most
    config.EVIDENCE_ANCHOR_MAX_GAP_CHARS characters before the quote starts.
    The result runs from the target through the end of the quote, is a
    contiguous span of the normalized page, and passes verify_span; when it
    would be longer than config.EVIDENCE_MAX_CHARS it is cut at the first
    sentence end after the target, or refused. The model's text is never
    trusted: every character of the result comes from the page.
    """
    if not all(isinstance(t, str) for t in (span, page_text, target)):
        return None
    quote, wanted = normalize_for_span(span), normalize_for_span(target)
    if not quote or not wanted or wanted in quote or quote not in normalize_for_span(page_text):
        return None
    head = quote[:min(len(quote), 60)]
    for raw_line in page_text.split("\n"):
        line = normalize_for_span(raw_line)
        at = line.find(head)
        if at < 0:
            continue
        window_start = max(0, at - config.EVIDENCE_ANCHOR_MAX_GAP_CHARS - len(wanted))
        t = line.rfind(wanted, window_start, at)
        while t >= 0:
            before = line[t - 1] if t > 0 else ""
            after = line[t + len(wanted)] if t + len(wanted) < len(line) else ""
            if not _ALNUM.match(before) and not _ALNUM.match(after):
                break
            t = line.rfind(wanted, window_start, t)
        if t < 0:
            continue
        widened = line[t:at] + quote
        if len(widened) > config.EVIDENCE_MAX_CHARS:
            cut = widened.find(". ", len(line[t:at]) + len(wanted))
            widened = widened[:cut + 1] if 0 <= cut < config.EVIDENCE_MAX_CHARS else ""
        if widened and verify_span(widened, page_text, target=target).ok \
                and target_is_token(widened, page_text, target=target):
            return widened
    return None
