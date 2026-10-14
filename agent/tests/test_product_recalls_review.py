"""SC15 review findings C1 to C4: the product recall check, tightened, offline.

- C1: a reply urllib raises as http.client.HTTPException (a truncated body, a bad status line) is a
  network error for that request, never a crash; and `property recalls --live` writes its run record
  with the checks stored so far even when something unexpected stops it.
- C2: a model printed with a spaced suffix ("XR16 4TTR6036 A") is not a match, and a product's Model
  field must hold the model whole.
- C3: a match found only in a product recall's text says so, since the text may exclude the model.
- C4: user facing text says "product recall", never a bare "recall", for the CPSC feature.

Every recall below is invented test data with example.com links; the fake fetch and the fake
opener open no socket. Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import http.client
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agent import cli
from agent import product_recalls as pr
from agent.render.brief_html import esc
from agent.render.property_page import RECALLS_INTRO
from agent.tests.test_product_recalls import MATCHES, NOON, TRANE, FakeFetch, _body, _check, _Opener, _Response
from agent.tests.test_product_recalls_cli import install_fetch, recalls_cmd, run_records, stored_files
from agent.tests.test_sweep import sweep_data  # noqa: F401  (sweep_data is a fixture)

TARGET = pr.model_target(TRANE["model"])


def invented(number: str, *, model: str = "", description: str = "", title: str = "") -> dict[str, Any]:
    """An invented recall (not CPSC data) with one product."""
    return {"RecallID": 990000 + int(number[-2:]), "RecallNumber": number,
            "Description": "SYNTHETIC TEST FIXTURE, not a real recall. " + description,
            "URL": f"https://example.com/synthetic-notice-{number.lower()}",
            "Title": title or "Synthetic review fixture (test data)",
            "Products": [{"Name": "Synthetic outdoor unit", "Description": "", "Model": model}],
            "Hazards": [{"Name": "Synthetic hazard."}], "Remedies": [{"Name": "Synthetic remedy."}]}


# ---------------------------------------------------------------------------
# C1: http.client failures
# ---------------------------------------------------------------------------


class _TruncatedResponse(_Response):
    def read(self, *args: Any) -> bytes:
        raise http.client.IncompleteRead(b"[{\"Recall", 4000)


class _ReadOpener(_Opener):
    def open(self, request: Any, timeout: float) -> Any:
        self.seen.append((request.full_url, timeout))
        return _TruncatedResponse(b"")


@pytest.mark.parametrize("opener", [
    _Opener(http.client.IncompleteRead(b"partial", 100)), _Opener(http.client.BadStatusLine("HTTP/9 ???")),
    _Opener(http.client.LineTooLong("header line")), _ReadOpener(None),
], ids=["incomplete_read_on_open", "bad_status_line", "line_too_long", "incomplete_read_on_read"])
def test_the_default_fetch_maps_an_http_client_failure_to_a_network_error(opener: Any) -> None:
    """IncompleteRead, BadStatusLine and LineTooLong subclass HTTPException, not OSError. Mutation:
    product_recalls_http_exception_escapes_fetch."""
    with pytest.raises(pr.FetchError) as exc:
        pr.urllib_fetch(pr.build_url({"Manufacturer": "Trane"}), opener=opener)
    assert exc.value.reason == pr.NETWORK_ERROR
    assert type(exc.value.__cause__).__name__ in exc.value.args[0]


def test_an_injected_fetch_raising_an_http_client_failure_is_a_failed_request() -> None:
    """query_maker records the failure on that request and keeps the maker's other responses, so the
    check says part of it did not run. Mutation: product_recalls_query_maker_misses_http_exception."""
    check, fetch = _check(TRANE, {"Manufacturer": _body(MATCHES[:1]),
                                  "Importer": http.client.IncompleteRead(b"partial", 100)})
    assert len(fetch.urls) == 3
    errors = [(r["field"], r["error"]) for r in check["requests"]]
    assert errors == [("Manufacturer", None), ("Importer", pr.NETWORK_ERROR), ("Distributor", None)]
    assert check["requests"][1]["error_detail"].startswith("IncompleteRead")
    result = pr.evaluate(check)
    assert result["status"] == pr.MATCHED and result["errors"] == [{"field": "Importer", "reason": "network_error"}]


class _CountingFetch(FakeFetch):
    """Answers "[]" until request `fail_at` (1 based), which raises `error`."""

    def __init__(self, fail_at: int, error: BaseException) -> None:
        super().__init__({})
        self.fail_at, self.error = fail_at, error

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> bytes:
        self.urls.append(url)
        if len(self.urls) == self.fail_at:
            raise self.error
        return b"[]"


def test_a_live_run_stopped_by_a_failure_still_writes_its_run_record(
        sweep_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A truncated reply on the fifth request (the Trane Importer request) is stored as a failed request
    beside the other responses; an unexpected error there stops the run, but the run record still logs
    the check stored before it, and the command exits 4 with no traceback. Mutations:
    cli_recalls_unexpected_error_escapes, cli_recalls_record_only_on_success."""
    install_fetch(monkeypatch, {})
    fetch = _CountingFetch(5, http.client.IncompleteRead(b"partial", 100))
    monkeypatch.setattr(pr, "urllib_fetch", fetch)
    code, out, err = recalls_cmd(monkeypatch, capsys, "prop-a", "--live", typed="proceed\n")
    assert code == cli.EXIT_LIVE_ERROR, out + err
    assert len(fetch.urls) == 6 and [p.parent.name for p in stored_files()] == ["appl-optima880", "appl-xr16"]
    [record] = run_records(sweep_data)
    errors = {c["appliance_id"]: c["errors"] for c in record["checks"]}
    assert errors == {"appl-optima880": [], "appl-xr16": [{"field": "Importer", "reason": "network_error"}]}

    fetch = _CountingFetch(5, RuntimeError("synthetic failure"))
    monkeypatch.setattr(pr, "urllib_fetch", fetch)
    monkeypatch.setattr(pr, "_now", lambda: NOON + timedelta(hours=1))  # a later run: a new stored file
    before = {p.name for p in (sweep_data / "runs").glob("recalls-*.json")}
    code, out, err = recalls_cmd(monkeypatch, capsys, "prop-a", "--live", typed="proceed\n")
    assert code == cli.EXIT_LIVE_ERROR, out + err
    assert "the check failed: RuntimeError: synthetic failure" in err and "Traceback" not in err
    (new,) = [p for p in (sweep_data / "runs").glob("recalls-*.json") if p.name not in before]
    record = json.loads(new.read_text(encoding="utf-8"))
    assert [c["appliance_id"] for c in record["checks"]] == ["appl-optima880"]
    assert len(record["stored"]) == 1 and Path(record["stored"][0]).exists()
    assert f"run record: {new}" in out


# ---------------------------------------------------------------------------
# C2: a spaced suffix and the Model field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("recall", "hits"), [
    (invented("REV01", model="XR16 4TTR6036 A"), []),
    (invented("REV02", model="XR16 4TTR6036 Series II"), []),
    (invented("REV03", description="Models XR16 4TTR6036 A and XR16 4TTR6036 B2 only."), []),
    (invented("REV04", description="Units (XR16 4TTR6036 LP) only."), []),
    (invented("REV05", model="XR16 4TTR6036, XR17 4TTR6048"),
     [{"field": "Products[0].Model", "text": "XR16 4TTR6036"}]),
    (invented("REV06", description="Models XR16 4TTR6036 and later."),
     [{"field": "Description", "text": "XR16 4TTR6036"}]),
], ids=["model_field_suffix", "model_field_series", "text_suffixes", "text_suffix_in_parens",
        "model_field_list", "text_word_after"])
def test_a_spaced_suffix_or_a_longer_model_field_is_not_a_match(recall: dict[str, Any],
                                                                 hits: list[dict[str, str]]) -> None:
    """Mutations: product_recalls_suffix_token_ignored (a run followed by "A" counts),
    product_recalls_model_field_as_free_text (a Model field segment that prints more than the model
    counts)."""
    assert pr.match_recall(recall, TARGET) == hits


# ---------------------------------------------------------------------------
# C3: a match in the text only
# ---------------------------------------------------------------------------


def test_a_match_in_the_text_only_says_the_text_may_exclude_the_model() -> None:
    """A Description that names the model to exclude it still names it, so the match is shown, followed
    by the code written sentence that it appears only in the text; a Model field match has no such
    sentence. Mutations: product_recalls_mention_only_dropped, product_recalls_mention_only_always."""
    exclusion = invented("REV07", description="The models XR16 4TTR6036 are not included in this recall.")
    check, _ = _check(TRANE, {"Manufacturer": _body([exclusion])})
    result = pr.evaluate(check)
    assert result["status"] == pr.MATCHED
    said = pr.statements(result)
    where = 'The model appears in the product recall\'s description: "XR16 4TTR6036".'
    assert said[said.index(where) + 1] == pr.MENTION_ONLY
    assert pr.MENTION_ONLY in "\n".join(pr.text_lines(result))
    assert esc(pr.MENTION_ONLY) in "\n".join(pr.html_lines(result))

    check, _ = _check(TRANE, {"Manufacturer": _body(MATCHES[:1])})
    assert pr.MENTION_ONLY not in "\n".join(pr.statements(pr.evaluate(check)))


# ---------------------------------------------------------------------------
# C4: "product recall", never a bare "recall"
# ---------------------------------------------------------------------------

BARE_RECALL = re.compile(r"(?<!product )\brecalls?\b(?! Retrieval)", re.IGNORECASE)


def test_user_facing_text_never_says_a_bare_recall(tmp_path: Path) -> None:
    """Every sentence the check prints or renders, for every status, and the plan and the page intro.
    Mutations: product_recalls_match_where_bare_recall, product_recalls_not_stated_bare_recall,
    product_recalls_searched_bare_recall, product_recalls_partial_bare_recall,
    product_recalls_plan_bare_recall, property_page_recalls_intro_bare_recall."""
    no_hazard = dict(invented("REV08", model="XR16 4TTR6036"), Hazards=[])  # its own words hold no "recall"
    results = [
        pr.evaluate(_check(TRANE, {"Manufacturer": _body([no_hazard]), "Importer": pr.FetchError(pr.TIMEOUT)})[0]),
        pr.evaluate(_check(TRANE, {})[0]),
        pr.evaluate(_check(TRANE, {"Manufacturer": pr.FetchError(pr.TIMEOUT)})[0]),
        pr.stored_result(None, TRANE),
        pr.stored_result(tmp_path, {"id": "appl-x", "manufacturer": "Synthetic Co.", "model": "SX-1 (synthetic)"}),
    ]
    assert {r["status"] for r in results} == {pr.MATCHED, pr.NO_MATCH, pr.NOT_RUN, pr.NOT_CHECKED,
                                              pr.NOT_APPLICABLE}
    texts = [line for r in results for line in pr.statements(r) + pr.text_lines(r) + pr.html_body(r)]
    texts += pr.plan_lines([TRANE], tmp_path / "store") + [RECALLS_INTRO]  # a store path with no such word
    for text in texts:
        bare = BARE_RECALL.findall(text.replace("advisor property recalls", ""))
        assert not bare, text
    joined = "\n".join(texts)
    for wording in ("not stated in the product recall", "so a product recall that prints the model another way",
                    "so a product recall listed under another firm role", "Recall Retrieval service",
                    "Code shows a product recall only when", "appears in the product recall's"):
        assert wording in joined, wording

