"""Rule 13, no model written warranty dates (SC13; modeled on rule 10, prices).

Code writes every warranty date: the age statement (rule 8) and each
coverage end date and status (`agent/rules/coverage.py`). A date in the
model's own warranty text could contradict them, or state an end of coverage
nobody computed, so any date there fails validation (error
`warranty_date_written`), which sends the draft back for its retry; a draft
that fails twice becomes the forced refusal, which carries no model text.

Fields checked: `warranty_caution.text`, each `warranty.cautions[].text` and
each `warranty.verify` item, on every status, since a refusal shows its
warranty block too (decision 9). The age statement and the coverage line are
code output and are not checked. A warranty date quoted from a source is not
an exception, unlike a price: no coverage date from a page can be checked
against this unit's own dates.

What counts as a date (`DATES`), so ordinary warranty wording passes
("purchasing through an authorized dealer", "2 years from the install date",
"within 60 days", model numbers such as 4TTR6036, a price after a dollar sign):

- a numeric date, year first or last: 2021-06-10, 2021-06, 2021/06/10,
  2021/06, 6/10/2021, 6/10/21, 6-10-2021, 06/2021, 10.06.2021;
- a bare year: a four digit number from 1900 to 2099 standing alone ("Fall
  2027", "since 2021", "from 2021 to 2026"). That includes a number that is
  not a year, such as "2000 series": the trade is deliberate, since a false
  positive costs only a retry. A number joined to a letter, a digit, a
  hyphen, a slash, a period or a dollar sign (SX-2000, 4TTR6036, a price) is
  not one;
- a month name (or its abbreviation) with a day or a four digit year:
  "June 10", "10 June", "June 2027", "Jun. 10, 2027"; "May" only beside a
  year, because it is also a verb;
- one of the words until, till, through, thru, expires, expire, expired,
  expiring, expiry, expiration, ends, ending, ended, lapses or lapsed, then a
  space or a colon and, within four words, a four digit year or a full month
  name: "until 2027", "Expires: June", "expiring in June", "through the end
  of 2026".
"""

from __future__ import annotations

import re
from typing import Any

WARRANTY_DATE_WRITTEN = "warranty_date_written"

# "May" is also a verb ("coverage may be reduced", "clause 4 may apply"), so it
# counts as a month only beside a four digit year.
_MONTHS_BUT_MAY = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|june?|july?|aug(?:ust)?"
                   r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_MONTH = rf"(?:{_MONTHS_BUT_MAY}|may)"
# After a keyword only a full month name other than May counts, so an
# abbreviation such as "mar", or the verb "may", a few words later never does.
_FULL_MONTH = (r"(?:january|february|march|april|june|july|august|september|october|november"
               r"|december)")
_YEAR = r"(?:19|20)\d{2}"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_KEYWORD = (r"(?:until|till|through|thru|expir(?:es|e|ed|ing|y|ation)|end(?:s|ing|ed)"
            r"|laps(?:es|ed))")
# Nothing that touches a date may be a letter, a digit, a hyphen, a slash or a
# period (part of a model, a fraction or a version number); before it, not a
# dollar sign either, so a price is never a year.
DATES = re.compile(
    rf"(?<![\w./$-])(?:"
    rf"{_YEAR}-\d{{1,2}}(?:-\d{{1,2}})?"  # 2021-06-10, 2021-06
    rf"|\d{{1,2}}/\d{{1,2}}/(?:\d{{4}}|\d{{2}})"  # 6/10/2021, 6/10/21
    rf"|\d{{1,2}}\.\d{{1,2}}\.{_YEAR}"  # 10.06.2021
    rf"|\d{{1,2}}/{_YEAR}"  # 06/2021
    rf"|\d{{1,2}}-\d{{1,2}}-(?:{_YEAR}|\d{{2}})"  # 06-10-2021, 6-10-21
    rf"|{_YEAR}/\d{{1,2}}(?:/\d{{1,2}})?"  # 2027/06/10, 2027/06
    rf"|{_MONTHS_BUT_MAY}\s+{_DAY}(?:,?\s+{_YEAR})?"  # June 10, June 10, 2027
    rf"|{_DAY}\s+(?:of\s+)?{_MONTHS_BUT_MAY}(?:,?\s+{_YEAR})?"  # 10 June, 10th of June 2027
    rf"|(?:{_DAY}\s+(?:of\s+)?)?{_MONTH}\s+(?:{_DAY},?\s+)?{_YEAR}"  # June 2027, May 10, 2027, 10 May 2027
    rf"|{_KEYWORD}[\s:]+(?:[\w,]+\s+){{0,4}}?"  # until 2027, Expires: June, ends June 10, 2027
    rf"(?:{_YEAR}|{_FULL_MONTH}(?:\s+{_DAY})?(?:,?\s+{_YEAR})?)"
    rf"|{_YEAR}"  # Fall 2027, since 2021, 2000 series
    rf")(?![\w/-])",
    re.IGNORECASE,
)


def dates(text: Any) -> list[str]:
    """Every date in `text`, as printed, in order."""
    if not isinstance(text, str):
        return []
    return [m.group(0).strip() for m in DATES.finditer(text)]


def _model_texts(brief: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    """(path, texts) for each model written warranty field group."""
    out: list[tuple[str, list[Any]]] = []
    caution = brief.get("warranty_caution")
    if isinstance(caution, dict):
        out.append(("warranty_caution", [caution.get("text")]))
    warranty = brief.get("warranty")
    if isinstance(warranty, dict):
        cautions = warranty.get("cautions")
        if isinstance(cautions, list):
            out.append(("warranty.cautions", [c.get("text") for c in cautions if isinstance(c, dict)]))
        verify = warranty.get("verify")
        if isinstance(verify, list):
            out.append(("warranty.verify", list(verify)))
    return out


def check_warranty_dates(brief: dict[str, Any]) -> list[str]:
    """One `warranty_date_written` error per model written warranty field group that holds a date."""
    errors = []
    for path, texts in _model_texts(brief):
        found = [d for text in texts for d in dates(text)]
        if found:
            errors.append(f"{WARRANTY_DATE_WRITTEN}: {path} states {', '.join(found)}; code writes every "
                          "warranty date, so leave dates out of warranty text")
    return errors
