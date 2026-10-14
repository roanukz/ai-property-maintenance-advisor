"""Product recall check against CPSC's recall service (PRD Phase C; SC15; decision 71).

The U.S. Consumer Product Safety Commission's Recall Retrieval web service
(https://www.saferproducts.gov/RestWebServices/Recall, no key) is queried by
maker, and code decides whether any recall it returns names the unit's exact
model. Pages call this a "product recall" check, because they already use
"recall" for the retrieval metric.

CPSC's guide to the service, v1.4 of 17 September 2018, governs (decision 71). Its
search parameters are the 23 field names in V14_PARAMETERS plus `format`; it
has no model parameter (the 2017 guide's ProductModel is gone), and a made up
parameter name returns every recall in the database. So `build_url` refuses
any name outside the whitelist before anything is sent, and always adds
format=json. The guide's search is a case insensitive wildcard ("contains")
match on each field.

Requests. For each maker, three requests: Manufacturer, Importer and
Distributor, each carrying only the maker name as the registry writes it
(the recorded recall 17057 lists its firm under Importers only, with
Manufacturers empty). Recalls are merged by RecallNumber (RecallID when a
recall has no number), keeping the roles each was found under.

Matching, in code, exact:
- The targets (`model_targets`) are kg.norm_model of the appliance's model
  number, when its record holds one, and of its model on record, the same
  component kg.model_key builds for the appliance's IS_MODEL edge. Each is
  compared on its own, exactly. The Trane unit's model on record is
  "XR16 4TTR6036" (product line and model number together) and its model
  number is "4TTR6036", so a recall matches when it prints "4TTR6036" as a
  whole run, or "XR16 4TTR6036" ("XR16-4TTR6036") as one. The product line
  "XR16" alone never matches, and neither does "4TTR6036A" or "4TTR603". A
  model number that normalizes like the model on record (the Rheem unit's) is
  one target, the model's, so that unit matches exactly as before.
- Each hit records which string matched: "matched" is "model_number" or
  "model" (the model on record), and "searched" is that string as recorded.
  A model number hit printed inside a model on record hit in the same field
  ("4TTR6036" within "XR16 4TTR6036") is left out, since the longer hit
  already names the unit.
- Text is cut into segments at list separators (comma, semicolon, slash,
  vertical bar, colon, a line break, and a period that ends a sentence; a
  comma grouping thousands, as in "36,000", stays inside its number), each
  segment is split on whitespace, and every contiguous run of tokens is
  normalized with kg.norm_model and compared whole with the target. No
  prefix, substring or family match: "PRO+E50" alone is not the Rheem unit
  "PRO+E50 M2 RH92 CL", and "XR16, 4TTR6036" is a list of two names, not the
  Trane unit. A printing may merge the recorded string's tokens but may not
  split where the recorded string does not (`recorded_breaks`): "XR16-4TTR6036"
  and "PRO+E50M2RH92CL" match, while "4TTR60 36", "XR1 64TTR6036" and
  "PRO+E50 M2 RH9 2CL" spell the target's characters across other tokens and
  do not. In free text a run followed by a suffix like token (one to three
  capital letters or digits, SUFFIX_TOKEN) is not a match, since
  "XR16 4TTR6036 A" prints a longer model. A product's Model field lists
  models, so there each whole segment must be the model: "XR16 4TTR6036 A"
  there names another model.
- Wildcard family notation ("4TTR6036*", WILDCARD) normalizes like the model
  it starts, so it is kept as a hit, but it names a series that may or may
  not hold the unit: SERIES_SENTENCE says so and asks the reader to check.
- Fields searched: each product's Model, Name and Description, then the
  recall's Description and Title. Model numbers often appear only in the
  recall's Description (recall 17057 lists its five model numbers there and
  leaves Products[].Model empty), and whole run equality keeps free text from
  matching on a fragment. Each match records the field ("Products[0].Model",
  "Description", "Title") and the run as printed.

Results, written by code (`evaluate`, then `text_lines` or `html_lines`):
- a recall names the model: its number, title, hazard, remedy, the CPSC link
  (a link only when it matches ^https?://), where the model was found, and
  SERIAL_SENTENCE. When no hit is in a product's Model field, MENTION_ONLY
  says the model appears only in the text, which may exclude it, and a hit
  printed with a wildcard adds SERIES_SENTENCE. Nothing says the unit itself
  is in the recall.
- no recall names this exact model: NO_MATCH, naming what was searched (the
  model on record, or "model number 4TTR6036 or model on record
  XR16 4TTR6036" when the record holds a distinct model number), then the
  maker searched and the UTC date checked. Never "no recalls". A match found
  by the model number alone names it the same way in its first sentence, and
  says "The model number appears" where it was found.
- a service error (timeout, HTTP error, network error, redirect, non JSON,
  oversized, unexpected shape): NOT_RUN with the reason code. When another of
  the maker's requests found a match, the match is shown with PARTIAL.

Which appliances are checked: every registry appliance except an invented
one. The registry's `synthetic` flag cannot tell them apart (every seeded row
is synthetic, including the three real makers), and no other code rule for
invented makers exists, so the seed's naming rule is used: an appliance whose
maker or model contains the word "synthetic" ("Synthetic Appliance Co.",
"SX-100 (synthetic)") is invented, CPSC is never queried for it, and its
section says NOT_APPLICABLE.

Storage: `store_check` writes each check (every raw response body with its
URL, parameters and retrieved_at, UTC) to <data_dir>/<appliance_id>/, one
JSON file per check, never overwriting: the name is the check's UTC second, a
sequence number within that second and a hash of the content
(20261013T120000Z-001-1a2b3c4d.json), the file is written aside and linked
into place, and a name already taken moves to the next number, so two checks
in the same second each keep their own file. `latest_check` reads the newest
back (by second, then sequence number; a file named before the sequence
number existed counts as number 0), and `evaluate` gives the same result
from it as from the live check. A check records the model number it searched
(CHECK_VERSION 2); one stored before that (version 1) has none and is
evaluated on its model on record alone.
`run_record_entry` is the check's run record entry (cost 0: the service is
free and keyless), and `run_record` the record of one live run. `plan_lines`
is what `advisor property recalls --live` prints before anything is sent:
the request count, the host, the parameter names and each maker value.

Network: stdlib urllib only, through an injectable `fetch(url, *, timeout,
max_bytes) -> bytes` that raises FetchError. The default refuses any URL but
the endpoint, follows no redirect, and caps time and size. Tests always
inject a fetch; sockets are blocked there.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from agent.kg import norm_model
from agent.render.brief_html import esc, safe_url

ENDPOINT = "https://www.saferproducts.gov/RestWebServices/Recall"
# The search parameters of CPSC's guide to the Recall Retrieval Web Services,
# v1.4 (17 September 2018), and its one non field parameter, format.
V14_PARAMETERS = frozenset({
    "RecallID", "RecallNumber", "RecallDateStart", "RecallDateEnd", "LastPublishDateStart",
    "LastPublishDateEnd", "RecallURL", "RecallTitle", "ConsumerContact", "RecallDescription",
    "ProductName", "InconjunctionURL", "ImageURL", "Injury", "Manufacturer", "Retailer", "Importer",
    "Distributor", "ManufacturerCountry", "UPC", "Hazard", "Remedy", "RemedyOption", "format",
})
FORMAT = "json"
# The firm roles a maker can be listed under; one request each.
MAKER_FIELDS = ("Manufacturer", "Importer", "Distributor")
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 5_000_000
USER_AGENT = "property-maintenance-advisor product recall check"
CHECK_VERSION = 2  # 2 adds model_number
RUN_RECORD_CHECK = "product_recalls"
RUN_RECORD_KIND = "product_recall_check"

# Reason codes for a check that did not run.
TIMEOUT = "timeout"
HTTP_ERROR = "http_error"
NETWORK_ERROR = "network_error"
REDIRECT = "redirect"
OVERSIZED = "oversized"
NOT_JSON = "not_json"
UNEXPECTED_SHAPE = "unexpected_shape"
STORED_UNREADABLE = "stored_unreadable"

# Which string a hit matched.
MODEL_NUMBER = "model_number"
MODEL_ON_RECORD = "model"

# Result statuses.
MATCHED = "matched"
NO_MATCH = "no_match"
NOT_RUN = "not_run"
NOT_APPLICABLE = "not_applicable"
NOT_CHECKED = "not_checked"

SECTION_TITLE = "Product recalls"
SERIAL_SENTENCE = "Check whether your serial number is in the affected range."
MATCH_HEAD = "CPSC product recall {number} names the model {model}: {title}"
MATCH_WHERE = "The model appears in the product recall's {field}: \"{text}\"."
MATCH_WHERE_NUMBER = "The model number appears in the product recall's {field}: \"{text}\"."
# What was searched, in words, when the record holds a model number apart from the model on record.
SEARCHED_BOTH = "model number {number} or model on record {model}"
# Follows "names the model " in MATCH_HEAD for a match on the model number alone.
NUMBER_ONLY = "number {number} (model on record {model})"
MENTION_ONLY = ("The model appears only in the product recall's text, not its model list; read the product "
                "recall to see whether this model is included.")
SERIES_SENTENCE = ("The product recall prints this model as part of a series (\"{text}\"); read it to see "
                   "whether your unit is included.")
NO_MATCH_SENTENCE = "No CPSC product recall names this exact model: {model}."
SEARCHED = ("Searched product recalls that list \"{maker}\" as manufacturer, importer or distributor, checked "
            "{date} (UTC). Matching is exact, so a product recall that prints the model another way is not "
            "shown.")
NOT_RUN_SENTENCE = ("The product recall check did not run (reason: {reason}), so nothing is said here "
                    "about product recalls for this model.")
PARTIAL_SENTENCE = ("Part of the product recall check did not run (reason: {reason}), so a product recall "
                    "listed under another firm role may be missing.")
NOT_APPLICABLE_SENTENCE = ("The product recall check does not apply to a synthetic maker: {maker} is "
                           "invented test data, so CPSC was not queried.")
NOT_CHECKED_SENTENCE = "The product recall check has not run."
NOT_STATED = "not stated in the product recall"

SYNTHETIC_WORD = re.compile(r"\bsynthetic\b", re.IGNORECASE)
# Where a run of tokens must stop: list separators, a line break, a sentence end. A comma that groups
# thousands ("36,000": a digit, the comma, then exactly three digits) is part of its number, not a break.
SEGMENT_BREAK = re.compile(r"[;/|:\n\r]|(?<!\d),|,(?!\d{3}(?![0-9A-Za-z]))|\.(?=\s|$)")
# Wildcard family notation ("4TTR6036*"): the printed model stands for a series, not one model.
WILDCARD = re.compile(r"[*?%]")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# A stored check's file name: UTC second, sequence number (absent in the first format), content hash.
STORED_NAME = re.compile(r"(\d{8}T\d{6}Z)-(?:(\d{3,})-)?([0-9a-f]{8})\.json")
# A token that reads as a model suffix: one to three capital letters or digits.
SUFFIX_TOKEN = re.compile(r"[A-Z0-9]{1,3}")
FIELD_LABELS = {"Model": "product model field", "Name": "product name", "Description": "description",
                "Title": "title"}

Fetch = Callable[..., bytes]


class UnsupportedParameter(ValueError):
    """A request named a parameter the v1.4 guide does not document; nothing was sent."""


class FetchError(Exception):
    """A request that failed; `reason` is one of the reason codes above."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def build_url(params: Mapping[str, str]) -> str:
    """The request URL for `params` plus format=json; raises before sending anything undocumented."""
    unknown = sorted(name for name in params if name not in V14_PARAMETERS)
    if unknown:
        raise UnsupportedParameter(f"not a v1.4 parameter: {', '.join(unknown)}")
    if params.get("format", FORMAT) != FORMAT:
        raise UnsupportedParameter(f"format must be {FORMAT!r}")
    query = {name: value for name, value in params.items() if name != "format"}
    for name, value in query.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non empty string, got {value!r}")
    query["format"] = FORMAT
    return ENDPOINT + "?" + urlencode(query, quote_via=quote)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        raise FetchError(REDIRECT, f"HTTP {code}")


def urllib_fetch(url: str, *, timeout: float = TIMEOUT_SECONDS, max_bytes: int = MAX_RESPONSE_BYTES,
                 opener: Any = None) -> bytes:
    """GET `url` (the endpoint only) with no redirects; the body, at most `max_bytes`."""
    if not url.startswith(ENDPOINT + "?"):
        raise ValueError(f"only {ENDPOINT} is ever requested, not {url!r}")
    opener = opener or urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise FetchError(HTTP_ERROR, f"HTTP {status}")
            body = response.read(max_bytes + 1)
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        raise FetchError(HTTP_ERROR, f"HTTP {exc.code}") from exc
    except TimeoutError as exc:
        raise FetchError(TIMEOUT) from exc
    except urllib.error.URLError as exc:
        reason = TIMEOUT if isinstance(exc.reason, TimeoutError) else NETWORK_ERROR
        raise FetchError(reason, str(exc.reason)) from exc
    except http.client.HTTPException as exc:  # a truncated body or a bad status line; not an OSError
        raise FetchError(NETWORK_ERROR, f"{type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        raise FetchError(NETWORK_ERROR, str(exc)) from exc
    if len(body) > max_bytes:
        raise FetchError(OVERSIZED, f"more than {max_bytes} bytes")
    return body


def _now() -> datetime:
    return datetime.now(UTC)


def query_maker(maker: str, *, fetch: Fetch | None = None,
                now: Callable[[], datetime] | None = None) -> list[dict[str, Any]]:
    """The maker's three requests (Manufacturer, Importer, Distributor), each with its raw response.

    A request carries only the maker name. Each entry holds the field, the
    parameters, the URL, retrieved_at (UTC), the raw body as text (None when
    the request failed) with its sha256, and the reason code of a failure.
    """
    fetch = fetch or urllib_fetch
    now = now or _now
    out = []
    for field in MAKER_FIELDS:
        params = {field: maker.strip()}
        url = build_url(params)
        entry: dict[str, Any] = {"field": field, "params": {**params, "format": FORMAT}, "url": url,
                                 "retrieved_at": None, "body": None, "body_sha256": None, "error": None}
        try:
            raw = fetch(url, timeout=TIMEOUT_SECONDS, max_bytes=MAX_RESPONSE_BYTES)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise FetchError(OVERSIZED, f"more than {MAX_RESPONSE_BYTES} bytes")
            entry["body_sha256"] = hashlib.sha256(raw).hexdigest()
            try:
                entry["body"] = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FetchError(NOT_JSON, "body is not UTF-8") from exc
        except FetchError as exc:
            entry["error"] = exc.reason
        except (OSError, http.client.HTTPException) as exc:
            entry["error"] = NETWORK_ERROR
            entry["error_detail"] = f"{type(exc).__name__}: {exc}"
        entry["retrieved_at"] = now().astimezone(UTC).isoformat(timespec="seconds")
        out.append(entry)
    return out


def parse_response(body: str) -> list[dict[str, Any]]:
    """The recalls in one response body: a JSON list of objects, else FetchError."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise FetchError(NOT_JSON, str(exc)) from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise FetchError(UNEXPECTED_SHAPE, "expected a JSON list of recall objects")
    for item in data:
        products = item.get("Products")
        if products is not None and (not isinstance(products, list)
                                     or not all(isinstance(p, dict) for p in products)):
            raise FetchError(UNEXPECTED_SHAPE, "Products is not a list of objects")
    return data


def recall_key(recall: Mapping[str, Any]) -> str:
    """The merge key: RecallNumber, else RecallID, else the recall's own hash."""
    number = recall.get("RecallNumber")
    if isinstance(number, str | int) and str(number).strip():
        return str(number).strip()
    rid = recall.get("RecallID")
    if isinstance(rid, str | int) and str(rid).strip():
        return f"id:{str(rid).strip()}"
    return "sha:" + hashlib.sha256(json.dumps(recall, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def merge_recalls(responses: Iterable[tuple[str, Sequence[Mapping[str, Any]]]]) -> list[dict[str, Any]]:
    """One entry per recall across (field, recalls) responses, merged by RecallNumber.

    Each entry is {"key", "recall", "found_as": [fields]}, in first seen order.
    """
    merged: dict[str, dict[str, Any]] = {}
    for field, recalls in responses:
        for recall in recalls:
            key = recall_key(recall)
            entry = merged.setdefault(key, {"key": key, "recall": dict(recall), "found_as": []})
            if field not in entry["found_as"]:
                entry["found_as"].append(field)
    return list(merged.values())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def model_target(model: str) -> str:
    """What the matcher compares: kg.norm_model of the registry model (its model_key component)."""
    return norm_model(model)


def _norm_or_empty(token: str) -> str:
    try:
        return norm_model(token)
    except ValueError:
        return ""


def _suffix_like(token: str) -> bool:
    """True for a token that reads as a model suffix ("A", "B2", "LP"), not as a word."""
    return bool(SUFFIX_TOKEN.fullmatch(token.strip("()[]{}\"'")))


def recorded_breaks(recorded: str) -> frozenset[int]:
    """Where the recorded string breaks between letters or digits, as offsets into its kg.norm_model:
    "XR16 4TTR6036" gives {4}, "4TTR6036" gives none. A printing may merge recorded tokens but may not
    split where the recorded string does not."""
    breaks: set[int] = set()
    count, pending = 0, False
    for ch in unicodedata.normalize("NFKC", recorded).casefold():
        if ch.isalnum():
            if pending and count:
                breaks.add(count)
            pending = False
            count += 1
        else:
            pending = True
    return frozenset(breaks)


def _splits_allowed(norms: Sequence[str], allowed: frozenset[int] | None) -> bool:
    """True when every token boundary of a run falls where the recorded string breaks too (None: unchecked)."""
    if allowed is None:
        return True
    offset, splits = 0, set()
    for norm in norms[:-1]:
        offset += len(norm)
        splits.add(offset)
    return splits <= allowed


def find_model(text: Any, target: str, recorded: str | None = None) -> list[str]:
    """Every run of tokens in `text` whose kg.norm_model equals `target`, as printed.

    A run followed in its segment by a suffix like token is not a match:
    "XR16 4TTR6036 A" prints a longer model, as "4TTR6036A" does. Given the
    string as `recorded`, a run split where the recorded string is not
    ("4TTR60 36", "XR1 64TTR6036") is not a match either. A wildcard token
    right after the run ("4TTR6036 *") is printed with it.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    allowed = recorded_breaks(recorded) if recorded is not None else None
    found: list[str] = []
    for segment in SEGMENT_BREAK.split(text):
        tokens = segment.split()
        norms = [_norm_or_empty(t) for t in tokens]
        for i in range(len(tokens)):
            if not norms[i]:
                continue
            joined = ""
            for j in range(i, len(tokens)):
                joined += norms[j]
                if len(joined) > len(target):
                    break
                if joined == target:
                    run = " ".join(tokens[i:j + 1])
                    longer = j + 1 < len(tokens) and _suffix_like(tokens[j + 1])
                    split = not _splits_allowed(norms[i:j + 1], allowed)
                    if j + 1 < len(tokens) and not norms[j + 1] and WILDCARD.search(tokens[j + 1]):
                        run += " " + tokens[j + 1]
                    if norm_model(run) == target and not longer and not split and run not in found:
                        found.append(run)
                    break
    return found


def find_model_field(text: Any, target: str, recorded: str | None = None) -> list[str]:
    """The segments of a product model field that are the model whole: a model field lists models, so
    a segment that prints anything more ("XR16 4TTR6036 A") names another model. Given the string as
    `recorded`, a segment split where the recorded string is not ("XR1 64TTR6036") is another model."""
    if not isinstance(text, str) or not text.strip():
        return []
    allowed = recorded_breaks(recorded) if recorded is not None else None
    printed = (" ".join(segment.split()) for segment in SEGMENT_BREAK.split(text))
    return list(dict.fromkeys(
        seg for seg in printed if seg and _norm_or_empty(seg) == target
        and _splits_allowed([_norm_or_empty(t) for t in seg.split()], allowed)))


def is_model_field(field: str) -> bool:
    """True for a product's Model field ("Products[0].Model")."""
    return field.startswith("Products[") and field.endswith(".Model")


def searched_fields(recall: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """(field path, text) for each field the matcher searches, in order."""
    out: list[tuple[str, Any]] = []
    for n, product in enumerate(recall.get("Products") or []):
        for name in ("Model", "Name", "Description"):
            out.append((f"Products[{n}].{name}", product.get(name)))
    out.append(("Description", recall.get("Description")))
    out.append(("Title", recall.get("Title")))
    return out


def match_recall(recall: Mapping[str, Any], target: str, recorded: str | None = None) -> list[dict[str, str]]:
    """Where the recall names the model: [{"field", "text"}], empty when it does not. `recorded` is the
    string as the registry holds it, so a printing that splits it elsewhere is not a match."""
    return [{"field": field, "text": run} for field, text in searched_fields(recall)
            for run in (find_model_field if is_model_field(field) else find_model)(text, target, recorded)]


def _number_apart(model: Any, model_number: Any) -> str | None:
    """The model number as recorded when it differs from the model on record once normalized, else None."""
    number = model_number.strip() if isinstance(model_number, str) else ""
    if not number or _norm_or_empty(number) == _norm_or_empty(str(model or "")):
        return None
    return number


def model_targets(model: str, model_number: str | None = None) -> list[dict[str, str]]:
    """What the matcher compares, each exactly: the model number apart from the model on record, if
    any, then the model on record. Each is {"matched", "searched" (as recorded), "target"}; a model
    number that normalizes to nothing raises, as a model does."""
    targets = []
    number = _number_apart(model, model_number)
    if number:
        targets.append({"matched": MODEL_NUMBER, "searched": number, "target": model_target(number)})
    targets.append({"matched": MODEL_ON_RECORD, "searched": model.strip(), "target": model_target(model)})
    return targets


def _within(inner: str, outer: str) -> bool:
    """True when `inner` is a whole token run of `outer`."""
    return f" {' '.join(inner.split())} " in f" {' '.join(outer.split())} "


def match_targets(recall: Mapping[str, Any], targets: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    """Where the recall names any target: [{"field", "text", "matched", "searched"}], in field order.

    A model number hit inside a model on record hit in the same field is
    left out ("4TTR6036" within "XR16 4TTR6036"): the longer hit names the unit.
    """
    order = {field: n for n, (field, _) in enumerate(searched_fields(recall))}
    hits = [{**hit, "matched": t["matched"], "searched": t["searched"]}
            for t in targets for hit in match_recall(recall, t["target"], t["searched"])]
    on_record = [h for h in hits if h["matched"] == MODEL_ON_RECORD]
    kept = [h for h in hits if h["matched"] == MODEL_ON_RECORD
            or not any(h["field"] == o["field"] and _within(h["text"], o["text"]) for o in on_record)]
    return sorted(kept, key=lambda h: order[h["field"]])


def searched_words(model: Any, model_number: Any) -> str:
    """What was searched, in words: the model on record, or both strings when the number differs."""
    number = _number_apart(model, model_number)
    return SEARCHED_BOTH.format(number=number, model=model) if number else str(model)


def field_label(field: str) -> str:
    """A field path in words: "Products[0].Model" gives "product model field"."""
    if field.startswith("Products[") and field.endswith(".Description"):
        return "product description"
    name = field.rsplit(".", 1)[-1]
    return FIELD_LABELS.get(name, name)


# ---------------------------------------------------------------------------
# Which appliances, and the check
# ---------------------------------------------------------------------------


def is_synthetic_maker(appliance: Mapping[str, Any]) -> bool:
    """True for an invented maker or model: the word "synthetic" in either (the seed's naming rule)."""
    return any(SYNTHETIC_WORD.search(str(appliance.get(k) or "")) for k in ("manufacturer", "model"))


def check_appliance(appliance: Mapping[str, Any], *, fetch: Fetch | None = None,
                    now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Run the check for one registry appliance: the raw record `store_check` saves.

    An invented maker is never queried: the record says so and holds no
    requests. Otherwise the maker's three requests are made.
    """
    maker = str(appliance.get("manufacturer") or "").strip()
    model = str(appliance.get("model") or "").strip()
    number = appliance.get("model_number")
    number = (number.strip() or None) if isinstance(number, str) else None
    record: dict[str, Any] = {"check_version": CHECK_VERSION, "appliance_id": appliance.get("id"),
                              "maker": maker, "model": model, "model_number": number, "applies": True,
                              "checked_at": None, "requests": []}
    if is_synthetic_maker(appliance):
        record["applies"] = False
        return record
    model_targets(model, number)  # a model or number that normalizes to nothing raises here, before any request
    record["requests"] = query_maker(maker, fetch=fetch, now=now)
    record["checked_at"] = record["requests"][0]["retrieved_at"]
    return record


def _joined(items: Any, key: str) -> str:
    names = [str(i.get(key)).strip() for i in items or [] if isinstance(i, dict) and str(i.get(key) or "").strip()]
    return " ".join(names) if names else NOT_STATED


def evaluate(check: Mapping[str, Any]) -> dict[str, Any]:
    """The result of a check, from its stored responses alone (live or read back)."""
    result: dict[str, Any] = {
        "appliance_id": check.get("appliance_id"), "maker": check.get("maker"), "model": check.get("model"),
        "model_number": check.get("model_number"), "checked_at": check.get("checked_at"),
        "status": NOT_APPLICABLE, "matches": [], "errors": [],
        "counts": {"requests": 0, "recalls": 0, "matches": 0}, "requests": [],
    }
    if not check.get("applies"):
        return result
    targets = model_targets(str(check.get("model") or ""), check.get("model_number"))
    responses = []
    for req in check.get("requests") or []:
        error, recalls = req.get("error"), []
        if error is None:
            try:
                recalls = parse_response(req.get("body"))
            except FetchError as exc:
                error = exc.reason
        if error is not None:
            result["errors"].append({"field": req.get("field"), "reason": error})
        else:
            responses.append((req.get("field"), recalls))
        result["requests"].append({"field": req.get("field"), "params": req.get("params"), "url": req.get("url"),
                                   "retrieved_at": req.get("retrieved_at"), "error": error,
                                   "recalls": len(recalls)})
    merged = merge_recalls(responses)
    for entry in merged:
        recall = entry["recall"]
        hits = match_targets(recall, targets)
        if not hits:
            continue
        url = recall.get("URL")
        result["matches"].append({
            "recall_number": entry["key"], "title": str(recall.get("Title") or "").strip() or NOT_STATED,
            "hazard": _joined(recall.get("Hazards"), "Name"), "remedy": _joined(recall.get("Remedies"), "Name"),
            "url": url if isinstance(url, str) else None, "link": safe_url(url), "hits": hits,
            "found_as": entry["found_as"],
        })
    result["matches"].sort(key=lambda m: m["recall_number"])
    result["counts"] = {"requests": len(result["requests"]), "recalls": len(merged),
                        "matches": len(result["matches"])}
    if result["matches"]:
        result["status"] = MATCHED
    elif result["errors"] or not result["requests"]:
        result["status"] = NOT_RUN
    else:
        result["status"] = NO_MATCH
    return result


# ---------------------------------------------------------------------------
# What the reader sees
# ---------------------------------------------------------------------------


def _reasons(result: Mapping[str, Any]) -> str:
    reasons: list[str] = []
    for e in result.get("errors") or []:
        if e.get("reason") not in reasons:
            reasons.append(e.get("reason"))
    return ", ".join(str(r) for r in reasons) or NETWORK_ERROR


def _searched(result: Mapping[str, Any]) -> str:
    return SEARCHED.format(maker=result.get("maker"), date=str(result.get("checked_at") or "")[:10])


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


def match_sentences(match: Mapping[str, Any], model: str) -> list[str]:
    """One matched recall in sentences, without its link (the renderers add it)."""
    title = str(match.get("title") or NOT_STATED).rstrip(".")
    hits = match.get("hits") or []
    named = model
    if hits and all(hit.get("matched") == MODEL_NUMBER for hit in hits):
        named = NUMBER_ONLY.format(number=hits[0].get("searched"), model=model)
    out = [MATCH_HEAD.format(number=match.get("recall_number"), model=named, title=title) + "."]
    for hit in hits:
        where = MATCH_WHERE_NUMBER if hit.get("matched") == MODEL_NUMBER else MATCH_WHERE
        out.append(where.format(field=field_label(hit["field"]), text=hit["text"]))
    if hits and not any(is_model_field(hit["field"]) for hit in hits):
        out.append(MENTION_ONLY)
    for text in dict.fromkeys(hit["text"] for hit in hits if WILDCARD.search(hit["text"])):
        out.append(SERIES_SENTENCE.format(text=text))
    out.append(_sentence(f"Hazard: {match.get('hazard')}"))
    out.append(_sentence(f"Remedy: {match.get('remedy')}"))
    out.append(SERIAL_SENTENCE)
    return out


def statements(result: Mapping[str, Any]) -> list[str]:
    """The result in plain sentences, links left out (the renderers add them)."""
    status, model = result.get("status"), result.get("model")
    if status == NOT_APPLICABLE:
        return [NOT_APPLICABLE_SENTENCE.format(maker=result.get("maker"))]
    if status == NOT_CHECKED:
        return [NOT_CHECKED_SENTENCE]
    if status == NOT_RUN:
        return [NOT_RUN_SENTENCE.format(reason=_reasons(result))]
    if status == NO_MATCH:
        return [NO_MATCH_SENTENCE.format(model=searched_words(model, result.get("model_number"))),
                _searched(result)]
    out: list[str] = []
    for match in result.get("matches") or []:
        out += match_sentences(match, model)
    out.append(_searched(result))
    if result.get("errors"):
        out.append(PARTIAL_SENTENCE.format(reason=_reasons(result)))
    return out


def text_lines(result: Mapping[str, Any]) -> list[str]:
    """Plain text lines for the terminal."""
    lines = [f"{SECTION_TITLE}: {result.get('maker')} {result.get('model')}"]
    if result.get("status") != MATCHED:
        return lines + [f"  {s}" for s in statements(result)]
    for match in result.get("matches") or []:
        lines += [f"  {s}" for s in match_sentences(match, result.get("model"))]
        lines.append(f"  CPSC page: {match.get('url') or 'not given'}")
    lines.append(f"  {_searched(result)}")
    if result.get("errors"):
        lines.append(f"  {PARTIAL_SENTENCE.format(reason=_reasons(result))}")
    return lines


def _page_link(match: Mapping[str, Any]) -> str:
    url = match.get("url")
    if safe_url(url):
        return f'CPSC page: <a href="{esc(url)}">{esc(url)}</a>'
    return f"CPSC page: {esc(url) if url else 'not given'}"


def html_lines(result: Mapping[str, Any]) -> list[str]:
    """Escaped HTML lines for an appliance's "Product recalls" block: a heading, then html_body."""
    return [f"  <h3>{SECTION_TITLE}</h3>", *html_body(result)]


def html_body(result: Mapping[str, Any]) -> list[str]:
    """Escaped HTML lines for one appliance's result, without a heading (the property page's entries)."""
    lines: list[str] = []
    if result.get("status") != MATCHED:
        return lines + [f"  <p>{esc(s)}</p>" for s in statements(result)]
    lines.append('  <ul class="items">')
    for match in result.get("matches") or []:
        body = " ".join(esc(s) for s in match_sentences(match, result.get("model")))
        lines.append(f"    <li>{body} {_page_link(match)}</li>")
    lines.append("  </ul>")
    lines.append(f"  <p>{esc(_searched(result))}</p>")
    if result.get("errors"):
        lines.append(f"  <p>{esc(PARTIAL_SENTENCE.format(reason=_reasons(result)))}</p>")
    return lines


def compared_line(appliance: Mapping[str, Any]) -> str:
    """The preflight's line naming what each product recall is compared with for one appliance, in order."""
    try:
        targets = model_targets(str(appliance.get("model") or ""), appliance.get("model_number"))
    except ValueError:
        return "    compared: nothing, since the model on record has no letter or digit to compare"
    names = {MODEL_NUMBER: "model number", MODEL_ON_RECORD: "model on record"}
    return "    compared exactly in each product recall: " + ", then ".join(
        f"{names[t['matched']]} \"{t['searched']}\"" for t in targets)


def plan_lines(appliances: Sequence[Mapping[str, Any]], store: Path | str) -> list[str]:
    """What `advisor property recalls --live` prints before anything is sent, ending on the typed prompt.

    Every planned request is listed by appliance with the one maker value it
    carries and the model strings each product recall will be compared with,
    exactly (model_targets); an invented maker is listed as not queried.
    """
    checked = [a for a in appliances if not is_synthetic_maker(a)]
    lines = [
        "advisor property recalls: preflight, LIVE (requests to an outside service)",
        f"planned: {len(checked) * len(MAKER_FIELDS)} requests to {urlsplit(ENDPOINT).hostname} (CPSC's Recall "
        f"Retrieval service), {len(MAKER_FIELDS)} per appliance",
        f"  parameters: one of {', '.join(MAKER_FIELDS)} per request, set to the maker value below, plus "
        f"format={FORMAT}; no other parameter is sent",
    ]
    for a in appliances:
        unit = f"{a.get('id')} {a.get('manufacturer')} {a.get('model')}"
        if is_synthetic_maker(a):
            lines.append(f"  {unit}: not queried (invented maker)")
        else:
            lines.append(f"  {unit}: maker value \"{str(a.get('manufacturer') or '').strip()}\"")
            lines.append(compared_line(a))
    lines += [
        "estimated cost: 0.0000 USD; the service is free and needs no key, so nothing is reserved in the ledger",
        f"each response is stored under {store} with its retrieved date (UTC), and the run is logged",
        "Type proceed to send these requests; anything else cancels and sends nothing.",
    ]
    return lines


def run_record(run_id: str, checks: Sequence[Mapping[str, Any]], stored: Sequence[Path | str],
               build: str) -> dict[str, Any]:
    """The run record of one live product recall run: an entry per check, the stored files, cost 0."""
    return {"kind": RUN_RECORD_KIND, "run_id": run_id, "build_id": build,
            "generated_at": _now().isoformat(timespec="seconds"),
            "checks": [run_record_entry(c) for c in checks], "stored": [str(p) for p in stored],
            "cost_usd": 0.0}


def run_record_entry(check: Mapping[str, Any]) -> dict[str, Any]:
    """The check's run record entry: requests, counts, matches, errors and cost (0: free and keyless)."""
    result = evaluate(check)
    return {
        "check": RUN_RECORD_CHECK, "appliance_id": result["appliance_id"], "maker": result["maker"],
        "model": result["model"], "model_number": result["model_number"], "status": result["status"],
        "checked_at": result["checked_at"], "requests": result["requests"], "counts": result["counts"],
        "matches": [{"recall_number": m["recall_number"], "field": h["field"], "text": h["text"],
                     "matched": h.get("matched")}
                    for m in result["matches"] for h in m["hits"]],
        "errors": result["errors"], "cost_usd": 0.0,
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _appliance_dir(data_dir: Path | str, appliance_id: Any) -> Path:
    if not isinstance(appliance_id, str) or not SAFE_ID.match(appliance_id):
        raise ValueError(f"unsafe appliance id {appliance_id!r}")
    return Path(data_dir) / appliance_id


def store_check(data_dir: Path | str, check: Mapping[str, Any]) -> Path:
    """Write one check (raw responses, URLs, parameters, retrieved_at) as a new file; never overwrites."""
    if not check.get("requests"):
        raise ValueError("nothing was requested, so there is nothing to store")
    folder = _appliance_dir(data_dir, check.get("appliance_id"))
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(check), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    stamp = datetime.fromisoformat(str(check["checked_at"])).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    fd, tmp_name = tempfile.mkstemp(prefix=".check-", suffix=".tmp", dir=folder)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        seq = _next_seq(folder, stamp)
        while True:
            path = folder / f"{stamp}-{seq:03d}-{digest}.json"
            try:
                os.link(tmp, path)  # never replaces a file already there
            except FileExistsError:
                seq += 1
                continue
            return path
    finally:
        tmp.unlink(missing_ok=True)


def _check_order(path: Path) -> tuple[str, int, str]:
    """Sort key of a stored check: its UTC second, then its sequence number (0 in the first format).
    A file not named by store_check sorts first, so it is never taken for the newest."""
    m = STORED_NAME.fullmatch(path.name)
    return (m.group(1), int(m.group(2) or 0), m.group(3)) if m else ("", 0, path.name)


def _next_seq(folder: Path, stamp: str) -> int:
    """One past the highest sequence number stored for this UTC second (1 for the first)."""
    taken = [_check_order(p)[1] for p in folder.glob(f"{stamp}-*.json")]
    return max(taken, default=0) + 1


def latest_check(data_dir: Path | str, appliance_id: str) -> dict[str, Any] | None:
    """The newest stored check for the appliance, or None when none is stored."""
    folder = _appliance_dir(data_dir, appliance_id)
    files = sorted(folder.glob("*.json"), key=_check_order) if folder.is_dir() else []
    if not files:
        return None
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("appliance_id") != appliance_id:
        raise ValueError(f"{files[-1]} is not a product recall check for {appliance_id}")
    return data


def stored_result(data_dir: Path | str | None, appliance: Mapping[str, Any]) -> dict[str, Any]:
    """What a page shows for an appliance: not applicable, not checked, or its latest stored check.

    `data_dir` None means no store was given, so nothing has run.
    """
    base = {"appliance_id": appliance.get("id"), "maker": appliance.get("manufacturer"),
            "model": appliance.get("model"), "model_number": appliance.get("model_number"),
            "checked_at": None, "matches": [], "errors": [],
            "counts": {"requests": 0, "recalls": 0, "matches": 0}, "requests": []}
    if is_synthetic_maker(appliance):
        return {**base, "status": NOT_APPLICABLE}
    if data_dir is None:
        return {**base, "status": NOT_CHECKED}
    try:
        check = latest_check(data_dir, appliance.get("id"))
        if check is None:
            return {**base, "status": NOT_CHECKED}
        return evaluate(check)
    except (OSError, ValueError):
        return {**base, "status": NOT_RUN, "errors": [{"field": None, "reason": STORED_UNREADABLE}]}
