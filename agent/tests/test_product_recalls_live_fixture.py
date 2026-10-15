"""The first live product recall check, read back offline (SC15; PRD 2 success criterion 3).

The three live_check_20261014_*.json fixtures are the checks store_check
wrote on 13 October 2026 at 22:21 US Eastern (02:21 UTC on 14 October; run
record recalls-b37be1f2a56b49fd), copied byte for byte. Each test copies them
into a temporary store under the names the store gave them and reads them
back through agent/product_recalls.py's own reader, matcher and statements,
so the recorded result (no product recall names any of the three models
exactly) is asserted on the code as it is now. Nothing reaches the network.
Each test names, in its docstring, the planted bugs in mutations.toml that
turn it red.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from agent import product_recalls as pr

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cpsc"
README = FIXTURES / "README.md"
SEED = json.loads((Path(__file__).resolve().parents[2] / "seed" / "registry_seed.json").read_text(encoding="utf-8"))
APPLIANCES = {a["id"]: a for a in SEED["appliances"]}
FORBIDDEN = ("no recalls", "is affected", "is recalled")

# What each stored check holds, as recorded: the store's file name, its sha256,
# and per request (Manufacturer, Importer, Distributor) the recall count and the body's sha256.
LIVE = {
    "appl-xr16": {
        "fixture": "live_check_20261014_trane.json",
        "stored_as": "20261014T022149Z-001-d9caffc8.json",
        "sha256": "d9caffc82625f1a14ba9396bed14239aac0d6ac89c2aee4af9cd3770d04f3e36",
        "checked_at": "2026-10-14T02:21:49+00:00",
        "bodies": [(8, "789153a8f4f5e985a1219eb8827f6071c7d0ca52c98e3676ed5589d90109a02a"),
                   (1, "2044ae15baa5e9dde89312497bdc4d6982007275b66aa0fc3571fa8cd83cc6c8"),
                   (3, "f617609a2526a127b5f178ff5ab61ea734e128e68156e432606d9f9ecaaa84ad")],
        "recalls": 12,
        "searched": "model number 4TTR6036 or model on record XR16 4TTR6036",
    },
    "appl-waterheater": {
        "fixture": "live_check_20261014_rheem.json",
        "stored_as": "20261014T022151Z-001-b48526ee.json",
        "sha256": "b48526ee69b7e30c4b1d15fddd8ba2467737f69e0f0555402284aa1ad546c94e",
        "checked_at": "2026-10-14T02:21:51+00:00",
        "bodies": [(5, "3d18ad2f036fd20768cf5a51970ab1538e5e650491cc4d8e96152a0bbb9590d9"),
                   (1, "48f6eae2fd3ea52829aed8b87a061a75e8f8e880fed070284c5bf6bd9a88cd4d"),
                   (1, "6ab621a1b94c80399883ff6a369d612d3b3230648685e83551451d12e32ca04d")],
        "recalls": 6,
        "searched": "PRO+E50 M2 RH92 CL",
    },
    "appl-optima880": {
        "fixture": "live_check_20261014_sundance.json",
        "stored_as": "20261014T022148Z-001-1959255c.json",
        "sha256": "1959255cfd3c43435ecf1e4837d2d0acaeef71328d0da68f4a5ce232a5b8f0ad",
        "checked_at": "2026-10-14T02:21:48+00:00",
        "bodies": [(0, "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
                   (1, "e9cacfbe7f7d912f1d549222863eafd67a90d0536eb0cc20318dc0ad9f994999"),
                   (0, "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945")],
        "recalls": 1,
        "searched": "Optima 880",
    },
}
# The one recall found under more than one firm role in the three checks: Rheem's 16177.
FOUND_TWICE = {"16177": ["Manufacturer", "Distributor"]}


def _store(tmp_path: Path) -> Path:
    """A temporary store holding the three live checks under the names store_check gave them."""
    for appliance_id, live in LIVE.items():
        folder = tmp_path / appliance_id
        folder.mkdir()
        shutil.copyfile(FIXTURES / live["fixture"], folder / live["stored_as"])
    return tmp_path


def _readme_entry(name: str) -> str:
    """The README's bullet for one fixture file, whitespace collapsed."""
    text = README.read_text(encoding="utf-8")
    start = text.index(f"- `{name}`:")
    end = text.find("\n- `", start + 1)
    return " ".join(text[start:end if end != -1 else len(text)].split())


def _recalls(check: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(req["field"], recall) for req in check["requests"] for recall in pr.parse_response(req["body"])]


@pytest.mark.parametrize("appliance_id", list(LIVE))
def test_live_check_body_hashes_match_the_readme(appliance_id: str) -> None:
    """Mutation: product_recalls_live_readme_hash_wrong (the README lists another body's
    sha256 for Trane's Manufacturer response)."""
    live = LIVE[appliance_id]
    raw = (FIXTURES / live["fixture"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == live["sha256"]
    assert live["stored_as"].split("-")[-1] == live["sha256"][:8] + ".json"  # the store's name for these bytes
    check = json.loads(raw.decode("utf-8"))
    entry = _readme_entry(live["fixture"])
    assert f"`{live['stored_as']}` (file sha256 {live['sha256']})" in entry
    assert [req["field"] for req in check["requests"]] == list(pr.MAKER_FIELDS)
    for req, (count, digest) in zip(check["requests"], live["bodies"], strict=True):
        assert hashlib.sha256(req["body"].encode("utf-8")).hexdigest() == req["body_sha256"] == digest
        assert len(pr.parse_response(req["body"])) == count
        noun = "recall" if count == 1 else "recalls"
        assert f"{req['field']}: {count} {noun}, body sha256 {digest}" in entry


@pytest.mark.parametrize("appliance_id", list(LIVE))
def test_live_check_reads_back_with_its_recorded_counts(appliance_id: str, tmp_path: Path) -> None:
    """Mutation: product_recalls_live_found_as_first_role_only (a recall found under a second
    firm role keeps only the first, so Rheem's recall 16177 loses Distributor)."""
    live, appliance = LIVE[appliance_id], APPLIANCES[appliance_id]
    store = _store(tmp_path)
    check = pr.latest_check(store, appliance_id)
    assert check == json.loads((FIXTURES / live["fixture"]).read_text(encoding="utf-8"))
    assert check["check_version"] == pr.CHECK_VERSION and check["applies"] is True
    assert (check["maker"], check["model"], check["model_number"]) == (
        appliance["manufacturer"], appliance["model"], appliance.get("model_number"))
    assert check["checked_at"] == live["checked_at"]
    for req in check["requests"]:
        assert req["error"] is None and req["params"] == {req["field"]: check["maker"], "format": "json"}
        assert req["url"] == pr.build_url({req["field"]: check["maker"]})
    result = pr.stored_result(store, appliance)
    assert result == pr.evaluate(check)
    assert result["status"] == pr.NO_MATCH and result["matches"] == [] and result["errors"] == []
    assert result["counts"] == {"requests": 3, "recalls": live["recalls"], "matches": 0}
    assert [r["recalls"] for r in result["requests"]] == [count for count, _ in live["bodies"]]
    merged = pr.merge_recalls((field, [recall]) for field, recall in _recalls(check))
    assert len(merged) == live["recalls"] and all(entry["found_as"] for entry in merged)
    twice = {entry["key"]: entry["found_as"] for entry in merged if len(entry["found_as"]) > 1}
    assert twice == (FOUND_TWICE if appliance_id == "appl-waterheater" else {})


@pytest.mark.parametrize("appliance_id", list(LIVE))
def test_no_live_product_recall_names_the_exact_model(appliance_id: str, tmp_path: Path) -> None:
    """Mutation: product_recalls_live_target_last_token (the model on record is matched on its
    last token, so Sundance recall 23068's series list "680, 780, 880 and 980" names Optima 880)."""
    check = pr.latest_check(_store(tmp_path), appliance_id)
    targets = pr.model_targets(check["model"], check["model_number"])
    assert [t["searched"] for t in targets] == {
        "appl-xr16": ["4TTR6036", "XR16 4TTR6036"],
        "appl-waterheater": ["PRO+E50 M2 RH92 CL"],
        "appl-optima880": ["Optima 880"],
    }[appliance_id]
    recalls = _recalls(check)
    assert recalls
    for _, recall in recalls:
        assert pr.match_targets(recall, targets) == [], recall["RecallNumber"]
        for t in targets:
            assert pr.match_recall(recall, t["target"], t["searched"]) == [], recall["RecallNumber"]
    numbers = {recall["RecallNumber"]: recall for _, recall in recalls}
    if appliance_id == "appl-optima880":  # a real near miss: the series number without the model
        assert "Sundance Spas 680, 780, 880 and 980 Series spas" in numbers["23068"]["Description"]
    if appliance_id == "appl-xr16":  # another Trane series, never the 4TTR6036
        assert "4TTM3036A1000A*" in numbers["14737"]["Description"]


@pytest.mark.parametrize("appliance_id", list(LIVE))
def test_live_check_says_no_product_recall_names_the_exact_model(appliance_id: str, tmp_path: Path) -> None:
    """Mutations: product_recalls_live_statement_drops_model_number (the Trane sentence names
    only the model on record), product_recalls_live_statement_names_model_as_maker (the maker
    searched is replaced by the model)."""
    live, appliance = LIVE[appliance_id], APPLIANCES[appliance_id]
    result = pr.stored_result(_store(tmp_path), appliance)
    said = pr.statements(result)
    assert said == [
        f"No CPSC product recall names this exact model: {live['searched']}.",
        f"Searched product recalls that list \"{appliance['manufacturer']}\" as manufacturer, importer or "
        "distributor, checked 2026-10-14 (UTC). Matching is exact, so a product recall that prints the model "
        "another way is not shown.",
    ]
    assert pr.text_lines(result) == [f"{pr.SECTION_TITLE}: {appliance['manufacturer']} {appliance['model']}",
                                     *(f"  {s}" for s in said)]
    assert pr.html_body(result) == [f"  <p>{pr.esc(s)}</p>" for s in said]
    text = "\n".join(said + pr.text_lines(result) + pr.html_lines(result)).lower()
    assert not any(f in text for f in FORBIDDEN)
