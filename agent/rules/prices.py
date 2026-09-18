"""Rule 10, prices (PLAN 8.5 rule 10; decision 28; PRD:119).

The tool never estimates a price. A currency amount may appear in model
written text only when the cited source states it: the amount, exactly as the
text prints it, must be inside the entry's own `evidence` quote, that quote
must pass the full span check of PLAN 8.11 against the cited source's raw
page text with the amount as the target, and the amount must stand whole
where the quote sits in the page (`amount_is_whole`): twelve dollars cut
from a printed 129, or one dollar cut from a printed 1,299 or 1.25, does
not count (test_prices.py has the cases).

- Upgrade options (`summary`, `successor_model`, `successor_manufacturer`)
  and maintenance entries (`task`, `interval`): an entry with an unquoted
  amount is dropped.
- v1 fields: an unquoted amount fails validation (error `price_unquoted`).
  The fields are decision 28's list (`why_shown`, `detail`, happened_before
  `summary`, warranty `age_statement`) plus every other model written text
  the page prints: step text, a candidate's documented meaning and action,
  caution text, the warranty "verify" items, `matched_identity`, and the
  refusal block's `found` items and `why_insufficient`. A refusal is not an
  answer, but its text is still shown, so an estimate there (what a new
  pump usually costs) would still be a price the tool made up. Only a
  candidate carries an evidence quote, so an amount anywhere else always
  fails. A failing refusal is retried and then replaced by the forced
  refusal, which code builds with no model text (refuse.py).

What counts as an amount (`CURRENCY`): a currency sign (dollar, euro,
pound, yen) or code (USD, US$, CAD, AUD, EUR, GBP) before a number, or a
number before a currency code or the word dollars or euros. "Pounds" is not
matched on its own because it is also a weight ("5 pounds of refrigerant").
"""

from __future__ import annotations

import re
from typing import Any

from agent.rules.evidence import normalize_for_span, verify_span
from agent.rules.upgrades import cited_url

PRICE_UNQUOTED = "price_unquoted"
DROP_PRICE = "price_not_in_verified_quote"

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CODES = r"USD|US\$|CAD|C\$|AUD|A\$|EUR|GBP"
CURRENCY = re.compile(
    rf"(?:(?:{_CODES}|[$\u20ac\u00a3\u00a5])\s?{_NUMBER})"
    rf"|(?:\b{_NUMBER}\s?(?:USD|CAD|AUD|EUR|GBP|dollars?|euros?)\b)",
    re.IGNORECASE,
)

ENTRY_FIELDS = {
    "upgrade_options": ("summary", "successor_model", "successor_manufacturer"),
    "maintenance_due": ("task", "interval"),
}


def amounts(text: Any) -> list[str]:
    """Every currency amount in `text`, as printed, in order."""
    if not isinstance(text, str):
        return []
    return [m.group(0).strip() for m in CURRENCY.finditer(text)]


_ALNUM = re.compile(r"[^\W_]")
_DIGIT = re.compile(r"\d")


def _joins(outer: str, digit: str) -> bool:
    """True when `outer` is a letter or digit, or a separator (. or ,) with a digit beyond it."""
    return bool(_ALNUM.match(outer)) or (outer in (".", ",") and bool(_DIGIT.match(digit)))


def amount_is_whole(evidence: str, page_text: str, amount: str) -> bool:
    """True when, at some place the normalized quote occurs in the normalized page,
    an occurrence of the amount inside it is not part of a longer number or word:
    no letter or digit directly beside it, and no "," or "." followed (after it)
    or preceded (before it) by a digit."""
    norm_quote, norm_page, wanted = (normalize_for_span(t) for t in (evidence, page_text, amount))
    if not norm_quote or not wanted:
        return False
    offsets = [i for i in range(len(norm_quote) - len(wanted) + 1) if norm_quote.startswith(wanted, i)]
    start = norm_page.find(norm_quote)
    while start != -1:
        for offset in offsets:
            i, j = start + offset, start + offset + len(wanted)
            before = norm_page[i - 1] if i > 0 else ""
            before2 = norm_page[i - 2] if i > 1 else ""
            after = norm_page[j] if j < len(norm_page) else ""
            after2 = norm_page[j + 1] if j + 1 < len(norm_page) else ""
            if not _joins(before, before2) and not _joins(after, after2):
                return True
        start = norm_page.find(norm_quote, start + 1)
    return False


def quoted(amount: str, evidence: Any, page_text: str | None) -> bool:
    """True when the amount is inside the evidence quote, the quote verifies against
    the page, and the amount stands whole where the quote sits in the page."""
    if not isinstance(evidence, str) or normalize_for_span(amount) not in normalize_for_span(evidence):
        return False
    if not verify_span(evidence, page_text, target=amount).ok:
        return False
    return amount_is_whole(evidence, page_text or "", amount)


def _unquoted(texts: list[Any], evidence: Any, page_text: str | None) -> list[str]:
    return [a for text in texts for a in amounts(text) if not quoted(a, evidence, page_text)]


def _drop_entries(brief: dict[str, Any], key: str, page_texts: dict[str, str],
                  report: list[dict[str, Any]] | None) -> None:
    kept = []
    for i, entry in enumerate(brief.get(key) or []):
        bad = _unquoted([entry.get(f) for f in ENTRY_FIELDS[key]], entry.get("evidence"),
                        page_texts.get(cited_url(brief, entry)))
        if bad:
            if report is not None:
                report.append({"list": key, "index": i, "amounts": bad, "reason": DROP_PRICE})
            continue
        kept.append(entry)
    brief[key] = kept


def _v1_texts(brief: dict[str, Any]) -> list[tuple[str, list[Any], Any, dict[str, Any] | None]]:
    """(path, texts, evidence quote or None, the citing entry or None) for each v1 field group."""
    out: list[tuple[str, list[Any], Any, dict[str, Any] | None]] = []
    matched = brief.get("matched_identity")
    if isinstance(matched, str):
        out.append(("matched_identity", [matched], None, None))
    for i, step in enumerate(brief.get("try_first") or []):
        out.append((f"try_first[{i}]", [step.get("step"), step.get("detail")], None, None))
    for i, cand in enumerate(brief.get("candidates") or []):
        out.append((f"candidates[{i}]", [cand.get("documented_meaning"), cand.get("documented_action"),
                                         cand.get("why_shown")], cand.get("evidence"), cand))
    hb = brief.get("happened_before")
    if isinstance(hb, dict):
        out.append(("happened_before", [hb.get("summary")], None, None))
    caution = brief.get("warranty_caution")
    if isinstance(caution, dict):
        out.append(("warranty_caution", [caution.get("text")], None, None))
    warranty = brief.get("warranty")
    if isinstance(warranty, dict):
        out.append(("warranty", [warranty.get("age_statement"),
                                 *[c.get("text") for c in warranty.get("cautions") or [] if isinstance(c, dict)]],
                    None, None))
        verify = warranty.get("verify")
        if isinstance(verify, list):
            out.append(("warranty.verify", list(verify), None, None))
    refusal = brief.get("no_reliable_answer")
    if isinstance(refusal, dict):
        found = refusal.get("found")
        if isinstance(found, list):
            out.append(("no_reliable_answer.found", list(found), None, None))
        out.append(("no_reliable_answer.why_insufficient", [refusal.get("why_insufficient")], None, None))
    return out


def check_prices(brief: dict[str, Any], *, page_texts: dict[str, str],
                 report: list[dict[str, Any]] | None = None, entries: bool = True) -> list[str]:
    """Drop upgrade and maintenance entries with an unquoted amount (in place, when
    `entries`); return one `price_unquoted` error per v1 field group that has one."""
    for key in ENTRY_FIELDS if entries else ():
        _drop_entries(brief, key, page_texts, report)
    errors = []
    for path, texts, evidence, entry in _v1_texts(brief):
        bad = _unquoted(texts, evidence, page_texts.get(cited_url(brief, entry)) if entry is not None else None)
        if bad:
            errors.append(f"{PRICE_UNQUOTED}: {path} states {', '.join(bad)} without a verified source quote")
    return errors
