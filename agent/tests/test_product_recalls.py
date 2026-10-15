"""The product recall check against CPSC (SC15; decision 71), offline.

Every response comes from agent/tests/fixtures/cpsc/ through an injected
fetch; sockets are blocked, and no test builds the default fetch's opener.
The synthetic fixtures are invented recalls; live_17057.json is a real CPSC
response recorded on 13 October 2026. Each test names, in its docstring, the
planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agent import kg
from agent import product_recalls as pr

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cpsc"
LIVE = FIXTURES / "live_17057.json"
LIVE_SHA256 = "68f11440306425457b2b6e0ff070bf2103b497206d18f62d2bf6148569d16d99"
MATCHES = json.loads((FIXTURES / "synthetic_matches.json").read_text(encoding="utf-8"))
NEAR_MISSES = json.loads((FIXTURES / "synthetic_near_misses.json").read_text(encoding="utf-8"))
SEED = json.loads((Path(__file__).resolve().parents[2] / "seed" / "registry_seed.json").read_text(encoding="utf-8"))
APPLIANCES = {a["id"]: a for a in SEED["appliances"]}
TRANE = APPLIANCES["appl-xr16"]
RHEEM = APPLIANCES["appl-waterheater"]
NOON = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
FORBIDDEN = ("no recalls", "is affected", "is recalled")


class FakeFetch:
    """Answers each request from {field: body bytes or exception}; records every URL."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> bytes:
        self.urls.append(url)
        query = parse_qs(urlsplit(url).query)
        field = next(name for name in query if name != "format")
        answer = self.answers.get(field, b"[]")
        if isinstance(answer, Exception):
            raise answer
        return answer


def _body(recalls: list[dict[str, Any]]) -> bytes:
    return json.dumps(recalls).encode("utf-8")


def _check(appliance: dict[str, Any], answers: dict[str, Any]) -> tuple[dict[str, Any], FakeFetch]:
    fetch = FakeFetch(answers)
    return pr.check_appliance(appliance, fetch=fetch, now=lambda: NOON), fetch


def _never(url: str, **_: Any) -> bytes:
    raise AssertionError(f"CPSC was queried: {url}")


def _all_text(result: dict[str, Any]) -> str:
    return "\n".join(pr.statements(result) + pr.text_lines(result) + pr.html_lines(result))


# ---------------------------------------------------------------------------
# Requests: the v1.4 whitelist, three queries per maker, the merge
# ---------------------------------------------------------------------------


def test_whitelist_is_the_2018_guide_and_a_model_parameter_raises() -> None:
    """Mutations: product_recalls_whitelist_allows_product_model (the 2017 guide's
    ProductModel joins the whitelist), product_recalls_build_url_skips_whitelist (no check)."""
    assert pr.V14_PARAMETERS == {
        "RecallID", "RecallNumber", "RecallDateStart", "RecallDateEnd", "LastPublishDateStart",
        "LastPublishDateEnd", "RecallURL", "RecallTitle", "ConsumerContact", "RecallDescription",
        "ProductName", "InconjunctionURL", "ImageURL", "Injury", "Manufacturer", "Retailer", "Importer",
        "Distributor", "ManufacturerCountry", "UPC", "Hazard", "Remedy", "RemedyOption", "format",
    }
    for name in ("Model", "ProductModel", "brand", "product"):
        with pytest.raises(pr.UnsupportedParameter):
            pr.build_url({"Manufacturer": "Trane", name: "XR16"})
    with pytest.raises(pr.UnsupportedParameter):
        pr.build_url({"Manufacturer": "Trane", "format": "xml"})
    url = pr.build_url({"Manufacturer": "Sundance Spas"})
    assert url == pr.ENDPOINT + "?Manufacturer=Sundance%20Spas&format=json"


def test_every_request_carries_only_the_maker_and_format_json() -> None:
    """Mutations: product_recalls_query_sends_extra_parameter (each request adds a
    documented ProductName beside the maker), product_recalls_drops_format_json (format
    left to the XML default), product_recalls_skips_distributor."""
    for appliance in (TRANE, RHEEM, APPLIANCES["appl-optima880"]):
        _, fetch = _check(appliance, {})
        assert len(fetch.urls) == 3
        for url in fetch.urls:
            parts = urlsplit(url)
            assert f"{parts.scheme}://{parts.netloc}{parts.path}" == pr.ENDPOINT
            query = parse_qs(parts.query, keep_blank_values=True)
            assert set(query) <= pr.V14_PARAMETERS
            assert query.pop("format") == ["json"]
            assert len(query) == 1 and list(query.values())[0] == [appliance["manufacturer"]]


def test_three_queries_per_maker_merged_by_recall_number() -> None:
    """Mutations: product_recalls_skips_distributor (two roles queried, a recall listed
    only under Distributors is lost), product_recalls_merge_keeps_duplicates (a recall
    found under two roles counted twice)."""
    check, fetch = _check(TRANE, {"Manufacturer": _body(MATCHES[:2]), "Importer": _body(MATCHES[:1]),
                                  "Distributor": _body([NEAR_MISSES[0]])})
    fields = [next(k for k in parse_qs(urlsplit(u).query) if k != "format") for u in fetch.urls]
    assert fields == ["Manufacturer", "Importer", "Distributor"]
    result = pr.evaluate(check)
    assert result["counts"] == {"requests": 3, "recalls": 3, "matches": 2}
    assert [r["recalls"] for r in result["requests"]] == [2, 1, 1]
    found = {m["recall_number"]: m["found_as"] for m in result["matches"]}
    assert found == {"SYN001": ["Manufacturer", "Importer"], "SYN002": ["Manufacturer"]}
    merged = pr.merge_recalls([("Manufacturer", MATCHES[:2]), ("Importer", MATCHES[:1]),
                               ("Distributor", [MATCHES[2]])])
    assert [e["key"] for e in merged] == ["SYN001", "SYN002", "SYN003"]
    assert merged[2]["found_as"] == ["Distributor"]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_trane_target_is_the_registry_model_key_never_the_family() -> None:
    """Mutation product_recalls_target_family_only (the target is the first word of the
    model, the family "XR16")."""
    target = pr.model_target(TRANE["model"])
    assert f"model:{kg.norm_maker('Trane')}:{target}" == kg.model_key(TRANE["manufacturer"], TRANE["model"])
    assert target == "xr164ttr6036"
    assert pr.find_model("Model XR16 4TTR6036 outdoor unit", target) == ["XR16 4TTR6036"]
    assert pr.find_model("The XR16 line", target) == []


@pytest.mark.parametrize(("model", "text", "expected"), [
    ("XR16 4TTR6036", "XR16 4TTR6036", ["XR16 4TTR6036"]),
    ("XR16 4TTR6036", "Model numbers: XR16-4TTR6036, 4TTR6048.", ["XR16-4TTR6036"]),
    ("XR16 4TTR6036", "units (XR16 4TTR6036) sold", ["(XR16 4TTR6036)"]),
    ("XR16 4TTR6036", "4TTR6036A", []),
    ("XR16 4TTR6036", "4TTR603", []),
    ("XR16 4TTR6036", "XR16", []),
    ("XR16 4TTR6036", "4TTR6036", []),
    ("XR16 4TTR6036", "XR16 4TTR6036A", []),
    ("XR16 4TTR6036", "XR16 4TTR603", []),
    ("XR16 4TTR6036", "XR16, 4TTR6036", []),
    ("XR16 4TTR6036", "XR16/4TTR6036", []),
    ("XR16 4TTR6036", "The XR16 series. 4TTR6036 parts", []),
    ("PRO+E50 M2 RH92 CL", "PRO+E50 M2 RH92 CL", ["PRO+E50 M2 RH92 CL"]),
    ("PRO+E50 M2 RH92 CL", "PRO+E50", []),
    ("PRO+E50 M2 RH92 CL", "PRO+E50 M2 RH92 CLX", []),
    ("PRO+E50 M2 RH92 CL", "XPRO+E50 M2 RH92 CL", []),
])
def test_match_is_exact_on_whole_token_runs(model: str, text: str, expected: list[str]) -> None:
    """Mutations: product_recalls_prefix_match (a run that starts the target counts),
    product_recalls_substring_match (a target inside one token counts),
    product_recalls_no_list_separators (a list or sentence break joins two names)."""
    assert pr.find_model(text, pr.model_target(model)) == expected


def test_near_miss_fixtures_never_match_the_real_units() -> None:
    """Mutations: product_recalls_prefix_match, product_recalls_no_list_separators."""
    for appliance in (TRANE, RHEEM):
        target = pr.model_target(appliance["model"])
        for recall in NEAR_MISSES:
            assert pr.match_recall(recall, target) == [], (appliance["id"], recall["RecallNumber"])
    check, _ = _check(dict(TRANE, model_number=None), {"Manufacturer": _body(NEAR_MISSES)})
    result = pr.evaluate(check)
    assert result["status"] == pr.NO_MATCH and result["counts"]["recalls"] == 3
    # SYN102 prints "4TTR6036" alone, the Trane unit's model number, so with the model number on
    # record (13 October 2026) it matches on that alone; the other two stay near misses.
    check, _ = _check(TRANE, {"Manufacturer": _body(NEAR_MISSES)})
    result = pr.evaluate(check)
    assert [m["recall_number"] for m in result["matches"]] == ["SYN102"]
    assert {h["matched"] for h in result["matches"][0]["hits"]} == {pr.MODEL_NUMBER}


def test_each_match_records_its_field_and_text() -> None:
    """Mutations: product_recalls_model_field_only (Description and Title not searched),
    product_recalls_field_label_lost (every hit attributed to the model field)."""
    trane, rheem = pr.model_target(TRANE["model"]), pr.model_target(RHEEM["model"])
    assert pr.match_recall(MATCHES[0], trane) == [{"field": "Products[0].Model", "text": "XR16 4TTR6036"}]
    assert pr.match_recall(MATCHES[1], trane) == [{"field": "Description", "text": "XR16-4TTR6036"}]
    assert pr.match_recall(MATCHES[2], rheem) == [{"field": "Title", "text": "PRO+E50 M2 RH92 CL"}]
    assert pr.field_label("Products[0].Model") == "product model field"
    assert pr.field_label("Products[1].Description") == "product description"
    assert pr.field_label("Description") == "description"


# ---------------------------------------------------------------------------
# What the reader sees
# ---------------------------------------------------------------------------


def test_match_names_the_recall_and_asks_for_the_serial_number() -> None:
    """Mutations: product_recalls_drops_serial_sentence, product_recalls_links_any_url
    (a javascript: URL becomes a link), product_recalls_says_unit_is_affected."""
    check, _ = _check(RHEEM, {"Distributor": _body([MATCHES[2]])})
    result = pr.evaluate(check)
    assert result["status"] == pr.MATCHED
    text = "\n".join(pr.text_lines(result))
    for part in ("SYN003", "PRO+E50 M2 RH92 CL", "Synthetic water heater PRO+E50 M2 RH92 CL (test fixture)",
                 "Hazard: not stated in the product recall.", "Remedy: Third synthetic remedy text.",
                 "title: \"PRO+E50 M2 RH92 CL\"", pr.SERIAL_SENTENCE, "CPSC page: javascript:alert(1)"):
        assert part in text, part
    html = "\n".join(pr.html_lines(result))
    assert "<a " not in html and "CPSC page: javascript:alert(1)" in html
    check, _ = _check(TRANE, {"Manufacturer": _body(MATCHES[:1])})
    html = "\n".join(pr.html_lines(pr.evaluate(check)))
    assert '<a href="https://example.com/synthetic-recall-001">' in html
    assert html.count(pr.SERIAL_SENTENCE) == 1
    for result in (pr.evaluate(check), pr.evaluate(_check(RHEEM, {"Distributor": _body([MATCHES[2]])})[0])):
        assert not any(f in _all_text(result).lower() for f in FORBIDDEN)


def test_no_match_says_exactly_that_with_model_maker_and_date() -> None:
    """Mutations: product_recalls_no_match_says_no_recalls (the sentence becomes "No recalls
    found"), product_recalls_no_match_drops_date (the date checked left out)."""
    near = [r for r in NEAR_MISSES if r["RecallNumber"] != "SYN102"]  # SYN102 prints the model number
    check, _ = _check(TRANE, {"Manufacturer": _body(near), "Importer": LIVE.read_bytes()})
    result = pr.evaluate(check)
    assert result["status"] == pr.NO_MATCH
    said = pr.statements(result)
    assert said[0] == ("No CPSC product recall names this exact model: model number 4TTR6036 or model on "
                       "record XR16 4TTR6036.")
    assert "\"Trane\" as manufacturer, importer or distributor" in said[1]
    assert "checked 2026-10-13 (UTC)" in said[1]
    assert not any(f in _all_text(result).lower() for f in FORBIDDEN)


@pytest.mark.parametrize(("answer", "reason"), [
    (pr.FetchError(pr.TIMEOUT), "timeout"),
    (pr.FetchError(pr.HTTP_ERROR, "HTTP 503"), "http_error"),
    (b"<html>Service Unavailable</html>", "not_json"),
    (b"\xff\xfe not utf-8", "not_json"),
    (b'{"Recalls": []}', "unexpected_shape"),
    (b"[1, 2]", "unexpected_shape"),
    (b'[{"RecallNumber": "1", "Products": "XR16"}]', "unexpected_shape"),
    (b"[" + b" " * pr.MAX_RESPONSE_BYTES + b"]", "oversized"),
])
def test_a_service_error_says_the_check_did_not_run(answer: Any, reason: str) -> None:
    """Mutations: product_recalls_error_counts_as_empty (a failed request read as no
    recalls, so the page says no recall names the model), product_recalls_parse_accepts_any_json,
    product_recalls_no_size_cap_on_injected_fetch."""
    check, _ = _check(TRANE, {"Manufacturer": answer})
    result = pr.evaluate(check)
    assert result["status"] == pr.NOT_RUN
    assert result["errors"] == [{"field": "Manufacturer", "reason": reason}]
    said = pr.statements(result)
    assert said == [f"The product recall check did not run (reason: {reason}), so nothing is said here "
                    "about product recalls for this model."]
    assert not any(f in _all_text(result).lower() for f in FORBIDDEN)


def test_a_partial_failure_keeps_the_match_and_says_part_did_not_run() -> None:
    """Mutation product_recalls_partial_hides_errors (the failed request is not mentioned
    beside a match)."""
    check, _ = _check(TRANE, {"Manufacturer": _body(MATCHES[:1]), "Importer": pr.FetchError(pr.TIMEOUT)})
    result = pr.evaluate(check)
    assert result["status"] == pr.MATCHED
    assert pr.statements(result)[-1] == ("Part of the product recall check did not run (reason: timeout), so "
                                         "a product recall listed under another firm role may be missing.")
    assert pr.html_lines(result)[-1].startswith("  <p>Part of the product recall check did not run")


def test_html_escapes_every_recall_value() -> None:
    """Mutation product_recalls_html_unescaped (the recall sentences go out unescaped)."""
    hostile = dict(MATCHES[0], Title="<script>alert(1)</script>", URL="https://example.com/a\"onmouseover=\"x")
    check, _ = _check(TRANE, {"Manufacturer": _body([hostile])})
    html = "\n".join(pr.html_lines(pr.evaluate(check)))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="https://example.com/a&quot;onmouseover=&quot;x"' in html


# ---------------------------------------------------------------------------
# Which appliances are checked
# ---------------------------------------------------------------------------


def test_synthetic_makers_are_never_queried() -> None:
    """Mutation product_recalls_queries_synthetic_makers (the synthetic rule never fires)."""
    synthetic = [a for a in APPLIANCES.values() if a["manufacturer"].startswith("Synthetic")]
    assert len(synthetic) == 4
    for appliance in synthetic:
        check = pr.check_appliance(appliance, fetch=_never, now=lambda: NOON)
        assert check["applies"] is False and check["requests"] == []
        result = pr.evaluate(check)
        assert result["status"] == pr.NOT_APPLICABLE
        assert pr.statements(result) == [f"The product recall check does not apply to a synthetic maker: "
                                         f"{appliance['manufacturer']} is invented test data, so CPSC was not "
                                         "queried."]
        assert pr.stored_result("/nonexistent", appliance)["status"] == pr.NOT_APPLICABLE
    real = [a for a in APPLIANCES.values() if not pr.is_synthetic_maker(a)]
    assert sorted(a["manufacturer"] for a in real) == ["Rheem", "Sundance Spas", "Trane"]


# ---------------------------------------------------------------------------
# The default fetch, with a fake opener (no socket is opened)
# ---------------------------------------------------------------------------


class _Response(io.BytesIO):
    status = 200


class _Opener:
    def __init__(self, outcome: Any) -> None:
        self.outcome, self.seen = outcome, []

    def open(self, request: Any, timeout: float) -> Any:
        self.seen.append((request.full_url, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _Response(self.outcome)


def test_default_fetch_caps_size_maps_errors_and_requests_only_the_endpoint() -> None:
    """Mutation product_recalls_default_fetch_no_size_cap (the whole body is read and kept)."""
    url = pr.build_url({"Manufacturer": "Trane"})
    opener = _Opener(b"[]")
    assert pr.urllib_fetch(url, opener=opener) == b"[]"
    assert opener.seen == [(url, pr.TIMEOUT_SECONDS)]
    with pytest.raises(pr.FetchError) as exc:
        pr.urllib_fetch(url, max_bytes=10, opener=_Opener(b"[" + b" " * 20 + b"]"))
    assert exc.value.reason == pr.OVERSIZED
    for error, reason in ((TimeoutError(), pr.TIMEOUT), (urllib.error.URLError(TimeoutError()), pr.TIMEOUT),
                          (urllib.error.URLError("refused"), pr.NETWORK_ERROR),
                          (urllib.error.HTTPError(url, 500, "x", {}, None), pr.HTTP_ERROR)):
        with pytest.raises(pr.FetchError) as exc:
            pr.urllib_fetch(url, opener=_Opener(error))
        assert exc.value.reason == reason
    with pytest.raises(ValueError):
        pr.urllib_fetch("https://example.com/Recall?Manufacturer=Trane", opener=_Opener(b"[]"))


# ---------------------------------------------------------------------------
# Storage, the run record and the recorded response
# ---------------------------------------------------------------------------


def test_storage_round_trip_and_latest_check(tmp_path: Path) -> None:
    """Mutations: product_recalls_latest_reads_oldest (the first stored check is read back),
    product_recalls_store_overwrites (the same check stored again in its second replaces the first)."""
    first, _ = _check(TRANE, {"Manufacturer": _body(NEAR_MISSES)})
    later_fetch = FakeFetch({"Manufacturer": _body(MATCHES[:1])})
    later = pr.check_appliance(TRANE, fetch=later_fetch, now=lambda: datetime(2026, 10, 14, 9, 30, tzinfo=UTC))
    assert pr.latest_check(tmp_path, TRANE["id"]) is None
    assert pr.stored_result(tmp_path, TRANE)["status"] == pr.NOT_CHECKED
    p1, p2 = pr.store_check(tmp_path, first), pr.store_check(tmp_path, later)
    assert p1.parent == p2.parent == tmp_path / TRANE["id"] and p1 != p2
    stored = json.loads(p1.read_text(encoding="utf-8"))
    for req in stored["requests"]:
        assert req["retrieved_at"] == "2026-10-13T12:00:00+00:00"
        assert req["url"] == pr.build_url({req["field"]: "Trane"})
        assert req["params"] == {req["field"]: "Trane", "format": "json"}
        assert hashlib.sha256(req["body"].encode("utf-8")).hexdigest() == req["body_sha256"]
    p3 = pr.store_check(tmp_path, first)  # the same check again: its own file, the first kept
    assert p3 not in (p1, p2) and p3.read_text(encoding="utf-8") == p1.read_text(encoding="utf-8")
    assert sorted(p.name for p in p1.parent.iterdir()) == sorted([p1.name, p2.name, p3.name])
    latest = pr.latest_check(tmp_path, TRANE["id"])
    assert latest == later
    assert pr.evaluate(latest) == pr.evaluate(later) == pr.stored_result(tmp_path, TRANE)
    assert pr.evaluate(latest)["status"] == pr.MATCHED
    for bad in ("../escape", "", "a/b"):
        with pytest.raises(ValueError):
            pr.store_check(tmp_path, dict(first, appliance_id=bad))
    with pytest.raises(ValueError):
        pr.store_check(tmp_path, pr.check_appliance(APPLIANCES["appl-sx100"], fetch=_never))


def test_run_record_entry_counts_matches_errors_and_costs_nothing() -> None:
    """Mutations: product_recalls_run_record_counts_before_merge (recalls counted per
    request), product_recalls_merge_keeps_duplicates."""
    check, _ = _check(TRANE, {"Manufacturer": _body(MATCHES[:2]), "Importer": _body(MATCHES[:1]),
                              "Distributor": pr.FetchError(pr.HTTP_ERROR)})
    entry = pr.run_record_entry(check)
    assert entry["check"] == "product_recalls" and entry["cost_usd"] == 0
    assert entry["counts"] == {"requests": 3, "recalls": 2, "matches": 2}
    assert entry["matches"] == [{"recall_number": "SYN001", "field": "Products[0].Model", "text": "XR16 4TTR6036",
                                 "matched": "model"},
                                {"recall_number": "SYN002", "field": "Description", "text": "XR16-4TTR6036",
                                 "matched": "model"}]
    assert entry["model_number"] == "4TTR6036"
    assert entry["errors"] == [{"field": "Distributor", "reason": "http_error"}]
    assert [r["field"] for r in entry["requests"]] == list(pr.MAKER_FIELDS)
    assert all("body" not in r for r in entry["requests"])
    json.dumps(entry)


def test_recorded_live_response_parses_and_matches_from_its_description() -> None:
    """Mutation product_recalls_model_field_only (recall 17057's model numbers sit only in
    its Description, so they are lost)."""
    raw = LIVE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LIVE_SHA256
    assert LIVE_SHA256 in (FIXTURES / "README.md").read_text(encoding="utf-8")
    recalls = pr.parse_response(raw.decode("utf-8"))
    assert len(recalls) == 1
    recall = recalls[0]
    assert recall["RecallNumber"] == "17057" and recall["Manufacturers"] == []
    assert recall["Importers"][0]["Name"].startswith("Aria Child")
    assert [p["Model"] for p in recall["Products"]] == [""]
    stroller = {"id": "appl-test", "manufacturer": "Aria Child", "model": "10AW1G-RAS2U"}
    check, _ = _check(stroller, {"Importer": raw})
    result = pr.evaluate(check)
    assert result["status"] == pr.MATCHED
    assert result["matches"][0]["hits"] == [{"field": "Description", "text": "10AW1G-RAS2U", "matched": "model",
                                             "searched": "10AW1G-RAS2U"}]
    assert result["matches"][0]["found_as"] == ["Importer"]
    assert result["matches"][0]["link"] == "https://www.cpsc.gov/Recalls/2017/Aria-Child-Recalls-Strollers"
    near = dict(stroller, model="10AW1G")
    assert pr.evaluate(_check(near, {"Importer": raw})[0])["status"] == pr.NO_MATCH


def test_fixture_folder_readme_names_every_file_and_marks_the_synthetic_ones() -> None:
    """Mutation product_recalls_readme_drops_a_fixture (a fixture file goes undescribed)."""
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in FIXTURES.iterdir() if p.suffix == ".json")
    assert files == ["live_17057.json", "live_check_20261014_rheem.json", "live_check_20261014_sundance.json",
                     "live_check_20261014_trane.json", "synthetic_matches.json", "synthetic_near_misses.json"]
    for name in files:
        assert f"`{name}`" in readme, name
    for recall in MATCHES + NEAR_MISSES:
        assert recall["Description"].startswith("SYNTHETIC TEST FIXTURE, not a real recall.")
        assert urlsplit(recall["URL"]).netloc in ("example.com", "")
