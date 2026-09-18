"""SC1b: v1's TypeScript validator against v2's parity layer (PLAN 2.3, 5, 6.2).

One Node child (started through `spawn_offline_child`, so the network guard
is loaded) runs v1 `validateBrief` from V1_BRIEFCASE_DIR over every payload;
`agent/rules/v1_parity.validate_brief_v1` runs over the same payloads with the
`budget_stopped` flag off. For each payload the decision, the reason code and
the normalized brief must agree. Differences are allowed only by rows in
`fixtures/sc1b_approved_differences.json`, which exists only after Roanuk
approves them (decision 21). Without Node 23.6 or later, or without the v1
source, every case skips with the reason, and gate mode fails the session.

Each test names, in a comment above it, a code change that turns it red.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent import config
from agent.rules import v1_parity
from agent.rules.v1_parity import ParityError, reason_for_v1_error, validate_brief_v1
from agent.tests.helpers import (
    SC1B_FIXTURES,
    SC1B_PENDING,
    TESTS_DIR,
    resolve_node_bin,
    sc1b_payloads,
    sc1b_v1_payload_file,
    spawn_offline_child,
)

pytestmark = pytest.mark.sc1b

HARNESS = TESTS_DIR / "harness" / "v1_validate.mjs"
APPROVED_FILE = SC1B_FIXTURES / "sc1b_approved_differences.json"
V1_VALIDATOR = Path("lib") / "brief-validate.ts"
MIN_NODE = (23, 6)
APPROVED_KEYS = {"payload_id", "json_path", "v1_value", "v2_value", "approved_on"}
DECISION_PATH = "$decision"
REASON_PATH = "$reason_code"
ABSENT = "<absent>"

PAYLOADS = sc1b_payloads()
BY_ID = {row["id"]: row for row in PAYLOADS}


# ---------------------------------------------------------------------------
# Running each side
# ---------------------------------------------------------------------------


def _skip_reason() -> str | None:
    """Why SC1b cannot run here, or None when Node and the v1 source are present."""
    v1_dir = os.environ.get(config.ENV_V1_DIR)
    if not v1_dir:
        return f"SC1b skipped: {config.ENV_V1_DIR} is not set (v1 source absent)"
    if not (Path(v1_dir) / V1_VALIDATOR).is_file():
        return f"SC1b skipped: {V1_VALIDATOR} not found under {config.ENV_V1_DIR}"
    try:
        resolve_node_bin()
    except FileNotFoundError as exc:
        return f"SC1b skipped: {exc}"
    proc = spawn_offline_child(["--version"], kind="node", timeout=30)
    match = re.search(r"v(\d+)\.(\d+)", proc.stdout)
    if not match or (int(match.group(1)), int(match.group(2))) < MIN_NODE:
        return f"SC1b skipped: node {proc.stdout.strip() or '?'} is older than {MIN_NODE[0]}.{MIN_NODE[1]}"
    return None


def reason_table() -> dict:
    """The Python side's message to reason code table, as the harness reads it."""
    return {
        "messages": dict(v1_parity.V1_MESSAGE_TO_REASON),
        "invalid_status_prefix": v1_parity.INVALID_STATUS_PREFIX,
        "invalid_status": v1_parity.INVALID_STATUS,
        "type_error": v1_parity.V1_TYPE_ERROR,
    }


def run_v1(rows: list[dict], work: Path) -> dict[str, dict]:
    """Run v1 validateBrief over rows of {id, payload}; results by payload id."""
    work.mkdir(parents=True, exist_ok=True)
    payload_file = work / "payloads.json"
    reasons_file = work / "reasons.json"
    payload_file.write_text(json.dumps([{"id": r["id"], "payload": r["payload"]} for r in rows],
                                       allow_nan=False), encoding="utf-8")
    reasons_file.write_text(json.dumps(reason_table()), encoding="utf-8")
    proc = spawn_offline_child(
        [HARNESS, payload_file, reasons_file],
        kind="node",
        env_extra={config.ENV_V1_DIR: os.environ[config.ENV_V1_DIR]},
    )
    assert proc.returncode == 0, proc.stderr
    assert "advisor offline guard: guard loaded" in proc.stderr
    results = v1_parity.loads_strict(proc.stdout)
    return {row["id"]: row for row in results}


Validator = Callable[..., Any]


def run_v2(payload: Any, validator: Validator = validate_brief_v1) -> dict:
    """The parity layer's outcome in the harness's row shape (budget_stopped flag off)."""
    try:
        normalized = validator(copy.deepcopy(payload), accept_budget_stopped=False)
    except ParityError as exc:
        return {"decision": "reject", "reason_code": exc.reason, "normalized": None}
    return {"decision": "accept", "reason_code": None, "normalized": normalized}


@pytest.fixture(scope="module")
def v1_results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)
    return run_v1(PAYLOADS, tmp_path_factory.mktemp("sc1b"))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _kind(value: Any) -> str:
    # JSON has one number type, so 2 and 2.0 compare equal, but a boolean is
    # never a number here (Python's True == 1 would hide the true index trap).
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def json_diff(v1: Any, v2: Any, path: str = "$") -> list[dict]:
    """Every JSON path where the two values differ; a key absent on one side is a difference."""
    if _kind(v1) != _kind(v2):
        return [{"json_path": path, "v1_value": v1, "v2_value": v2}]
    if isinstance(v1, dict):
        out = []
        for key in sorted(set(v1) | set(v2)):
            sub = f"{path}.{key}"
            if key not in v1 or key not in v2:
                out.append({"json_path": sub, "v1_value": v1.get(key, ABSENT), "v2_value": v2.get(key, ABSENT)})
            else:
                out += json_diff(v1[key], v2[key], sub)
        return out
    if isinstance(v1, list):
        if len(v1) != len(v2):
            return [{"json_path": f"{path}.length", "v1_value": len(v1), "v2_value": len(v2)}]
        return [d for i, (a, b) in enumerate(zip(v1, v2)) for d in json_diff(a, b, f"{path}[{i}]")]
    if v1 != v2:
        return [{"json_path": path, "v1_value": v1, "v2_value": v2}]
    return []


def load_approved() -> list[dict]:
    """Approved difference rows; the file exists only after Roanuk approves (decision 21)."""
    if not APPROVED_FILE.is_file():
        return []
    rows = v1_parity.loads_strict(APPROVED_FILE.read_text(encoding="utf-8"))
    for row in rows:
        assert set(row) == APPROVED_KEYS, row
        # One path per row: a row may never cover a whole payload.
        assert row["json_path"] not in ("$", "*", ""), row
    return rows


def compare(payload_id: str, v1_row: dict, v2_row: dict, approved: list[dict]) -> list[dict]:
    """Differences between v1 and v2 for one payload that no approved row covers."""
    if v1_row["decision"] != v2_row["decision"]:
        diffs = [{"json_path": DECISION_PATH, "v1_value": v1_row["decision"], "v2_value": v2_row["decision"]}]
    elif v1_row["decision"] == "reject":
        diffs = []
        if v1_row["reason_code"] != v2_row["reason_code"]:
            diffs = [{"json_path": REASON_PATH, "v1_value": v1_row["reason_code"],
                      "v2_value": v2_row["reason_code"]}]
    else:
        diffs = json_diff(v1_row["normalized"], v2_row["normalized"])

    def is_approved(diff: dict) -> bool:
        return any(
            row["payload_id"] == payload_id and row["json_path"] == diff["json_path"]
            and not json_diff(row["v1_value"], diff["v1_value"]) and not json_diff(row["v2_value"], diff["v2_value"])
            for row in approved
        )

    return [{"payload_id": payload_id, **d} for d in diffs if not is_approved(d)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _params() -> list:
    params = [pytest.param(row["id"], id=row["id"]) for row in PAYLOADS]
    if sc1b_v1_payload_file() is None:
        params.append(pytest.param(None, id="v1_guardrail_payloads",
                                   marks=pytest.mark.skip(reason=SC1B_PENDING)))
    return params


# Mutations: sc1b_true_index_accepted (v2 accepts a source_index of true),
# sc1b_unknown_who_rejected (v2 rejects an unknown who instead of normalizing),
# sc1b_dump_adds_absent_detail (v2 writes a default for an absent key),
# sc1b_budget_flag_on (the harness turns the budget_stopped flag on), and
# sc1b_harness_type_error_unmapped (the harness stops mapping TypeErrors).
@pytest.mark.parametrize("payload_id", _params())
def test_v1_and_v2_decisions_match(payload_id: str, v1_results: dict[str, dict]) -> None:
    v1_row = v1_results[payload_id]
    if v1_row["decision"] == "reject":
        # Every v1 error maps to a code, and the harness's mapping agrees with Python's.
        assert v1_row["reason_code"] is not None, v1_row
        assert v1_row["reason_code"] == reason_for_v1_error(
            v1_row["error_message"], error_name=v1_row["error_name"]
        ), v1_row
    v2_row = run_v2(BY_ID[payload_id]["payload"])
    diffs = compare(payload_id, v1_row, v2_row, load_approved())
    assert diffs == [], json.dumps(diffs, indent=2)


# Mutation: sc1b_compare_one_side (compare() reads the decision from the v1
# row on both sides, so a planted divergence is never reported).
def test_harness_reports_a_planted_divergence(tmp_path: Path) -> None:
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)
    rejected_id = "v1_test_05" if "v1_test_05" in BY_ID else "edge_ok_empty_sources"
    accepted_id = "edge_ok_baseline"
    rows = [BY_ID[rejected_id], BY_ID[accepted_id]]
    v1 = run_v1(rows, tmp_path / "planted")
    assert v1[rejected_id]["decision"] == "reject" and v1[accepted_id]["decision"] == "accept"

    def accept_all(payload: Any, **_: Any) -> Any:
        return payload

    def reject_all(payload: Any, **_: Any) -> Any:
        raise ParityError("ok_empty_sources", "stub")

    stub_accept = {pid: compare(pid, v1[pid], run_v2(BY_ID[pid]["payload"], accept_all), []) for pid in v1}
    assert [d["json_path"] for d in stub_accept[rejected_id]] == [DECISION_PATH]
    assert stub_accept[rejected_id][0]["v1_value"] == "reject"
    assert stub_accept[rejected_id][0]["v2_value"] == "accept"
    stub_reject = {pid: compare(pid, v1[pid], run_v2(BY_ID[pid]["payload"], reject_all), []) for pid in v1}
    assert [d["json_path"] for d in stub_reject[accepted_id]] == [DECISION_PATH]
    # The real parity layer agrees on both, so the comparison is not simply always red.
    real = {pid: compare(pid, v1[pid], run_v2(BY_ID[pid]["payload"]), []) for pid in v1}
    assert real == {rejected_id: [], accepted_id: []}
    # An approved row covers exactly its own path and values, nothing wider.
    row = {"payload_id": rejected_id, "json_path": DECISION_PATH, "v1_value": "reject",
           "v2_value": "accept", "approved_on": "test"}
    assert compare(rejected_id, v1[rejected_id], run_v2(BY_ID[rejected_id]["payload"], accept_all), [row]) == []
    assert compare(accepted_id, v1[accepted_id], run_v2(BY_ID[accepted_id]["payload"], reject_all), [row]) != []


# Mutation: sc1b_diff_bool_is_number (json_diff compares True == 1 as Python
# does) or sc1b_diff_ignores_absent_keys (a key absent on one side is skipped).
def test_json_diff_is_strict_about_booleans_and_absent_keys() -> None:
    assert json_diff({"i": 2}, {"i": 2.0}) == []
    assert json_diff({"i": True}, {"i": 1}) == [{"json_path": "$.i", "v1_value": True, "v2_value": 1}]
    assert json_diff({"a": [{"x": 1}]}, {"a": [{"x": 1, "detail": None}]}) == [
        {"json_path": "$.a[0].detail", "v1_value": ABSENT, "v2_value": None}
    ]
    assert json_diff([1], [1, 2]) == [{"json_path": "$.length", "v1_value": 1, "v2_value": 2}]


# PLAN 6.2 last paragraph: every check the guardrail tests never exercised,
# and every parity trap, has an edge payload.
# Mutation: sc1b_edge_true_index_untagged (the only candidate true index
# payload loses its trap tag, as if it were dropped).
def test_edge_payloads_cover_untested_checks_and_traps() -> None:
    doc = v1_parity.loads_strict((SC1B_FIXTURES / "sc1b_edge_payloads.json").read_text(encoding="utf-8"))
    assert doc["provenance"]["source"].startswith("authored")
    covered = {check for row in doc["payloads"] for check in row["checks"]}
    untested = {"1", "2", "3a", "3b", "4", "7", "8", "11", "12", "13", "14", "16", "17", "18", "19", "20",
                "22", "23", "24", "25", "27", "29", "30", "31"}
    traps = {"true_index", "float_index", "string_index", "unknown_who", "bare_string", "optional",
             "null", "primitive", "extra", "case", "whitespace", "code", "any_type", "order", "falsy"}
    assert untested - covered == set()
    assert traps - covered == set()
    # The true index trap on each index kind: step, candidate, caution, caution entry.
    true_index = {c for r in doc["payloads"] if "true_index" in r["checks"] for c in r["checks"]}
    assert {"15", "21", "23", "28"} <= true_index
    text = json.dumps(doc)
    urls = re.findall(r"[a-zA-Z]+://([^/\"]*)", text)
    assert urls and all(host == "example.com" or host.endswith(".example.com") for host in urls if host), urls
