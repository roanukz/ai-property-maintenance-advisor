"""SC1: every v1 guardrail test is ported or approved obsolete (PLAN 6.1, decision 40).

Rows of tests/v1_inventory.json at or below the build phase (config.BUILD_PHASE,
or ADVISOR_GATE at a gate) must name test functions that exist in agent/tests.
An obsolete row names no test; at a gate it must also carry the date Roanuk
approved it. Outside a gate a pending approval is allowed and listed, since
approval is a gate decision. That the named tests ran and passed, not skipped,
is enforced by the conftest gate hook, which reads the same file.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from agent import config
from agent.tests.conftest import node_phase_of

TESTS_DIR = Path(__file__).resolve().parent
INVENTORY = TESTS_DIR / "v1_inventory.json"
VERDICTS = ("port", "port_with_changes", "obsolete")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def collect_test_functions() -> set[str]:
    """Every top level test function in agent/tests, as "file.py::name"."""
    names: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names |= {f"{path.name}::{node.name}" for node in tree.body
                  if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")}
    return names


def inventory_problems(rows: list[dict], phase: int, known: set[str], *, gate: bool) -> tuple[list[str], list[str]]:
    """(problems, obsolete rows still awaiting approval) for rows at or below phase."""
    problems: list[str] = []
    pending: list[str] = []
    numbers = [row["v1_test"] for row in rows]
    if sorted(numbers) != list(range(1, 18)):
        problems.append(f"inventory must list v1 tests 1 to 17 once each, got {sorted(numbers)}")
    for row in rows:
        label = f"v1 test {row['v1_test']} ({row['name']})"
        if row["verdict"] not in VERDICTS:
            problems.append(f"{label}: unknown verdict {row['verdict']!r}")
        for node_id, node_phase in (row.get("node_phases") or {}).items():
            if node_id not in row["node_ids"]:
                problems.append(f"{label}: node_phases names {node_id}, which is not in node_ids")
            elif not isinstance(node_phase, int) or node_phase > row["phase"]:
                problems.append(f"{label}: {node_id} phase {node_phase!r} must be an integer <= {row['phase']}")
        if row["phase"] > phase:
            # A later row may still have halves due now (node_phases).
            for node_id in row["node_ids"]:
                if node_phase_of(row, node_id) <= phase and node_id.split("[")[0] not in known:
                    problems.append(f"{label}: {node_id} is not a test function in agent/tests")
            continue
        if row["verdict"] == "obsolete":
            if row["node_ids"]:
                problems.append(f"{label}: an obsolete row names no tests")
            approved = row.get("approved_obsolete_on")
            if approved is None:
                (problems if gate else pending).append(f"{label}: obsolete, approval date missing")
            elif not ISO_DATE.match(str(approved)):
                problems.append(f"{label}: approval date {approved!r} is not YYYY-MM-DD")
            continue
        if not row["node_ids"]:
            problems.append(f"{label}: ported but maps to no test")
        for node_id in row["node_ids"]:
            if node_id.split("[")[0] not in known:
                problems.append(f"{label}: {node_id} is not a test function in agent/tests")
    return problems, pending


def current_phase() -> tuple[int, bool]:
    raw = os.environ.get(config.ENV_GATE, "").strip()
    return (int(raw), True) if raw.isdigit() else (config.BUILD_PHASE, False)


def test_every_v1_guardrail_is_ported_or_approved_obsolete() -> None:
    """Mutations: inventory_names_a_missing_test (a phase 2 row names a renamed
    test); inventory_obsolete_check_skipped (the checker ignores obsolete rows
    without approval at a gate); inventory_node_phases_ignored (a Phase 2 half
    of a phase 4 row goes unchecked at the Phase 2 gate)."""
    rows = json.loads(INVENTORY.read_text(encoding="utf-8"))
    phase, gate = current_phase()
    known = collect_test_functions()
    problems, pending = inventory_problems(rows, phase, known, gate=gate)
    assert problems == []
    assert any(row["phase"] <= phase and row["verdict"] != "obsolete" for row in rows)
    if pending:
        print("awaiting approval at the gate: " + "; ".join(pending))

    # The checker itself: a gate refuses an unapproved obsolete row and a
    # missing test, and accepts an approved one.
    obsolete = next(row for row in rows if row["verdict"] == "obsolete")
    ported = next(row for row in rows if row["verdict"] != "obsolete" and row["phase"] <= phase)
    bad = [dict(r) for r in rows]
    for row in bad:
        if row["v1_test"] == obsolete["v1_test"]:
            row["approved_obsolete_on"] = None
        if row["v1_test"] == ported["v1_test"]:
            row["node_ids"] = ["test_nowhere.py::test_missing"]
    found, _ = inventory_problems(bad, max(phase, obsolete["phase"], ported["phase"]), known, gate=True)
    assert any("approval date missing" in p for p in found)
    assert any("test_nowhere.py::test_missing" in p for p in found)
    for row in bad:
        if row["v1_test"] == obsolete["v1_test"]:
            row["approved_obsolete_on"] = "2026-09-18"
    found, _ = inventory_problems(bad, max(phase, obsolete["phase"]), known, gate=True)
    assert not any(f"v1 test {obsolete['v1_test']} " in p and "approval" in p for p in found)

    # A node ID due earlier than its row (node_phases) is checked at its own phase.
    split = next(row for row in rows if row.get("node_phases"))
    early_id, early_phase = next(iter(split["node_phases"].items()))
    renamed = [dict(r) for r in rows]
    for row in renamed:
        if row["v1_test"] == split["v1_test"]:
            row["node_ids"] = [n if n != early_id else "test_nowhere.py::test_renamed" for n in row["node_ids"]]
            row["node_phases"] = {"test_nowhere.py::test_renamed": early_phase}
    found, _ = inventory_problems(renamed, early_phase, known, gate=True)
    assert any("test_nowhere.py::test_renamed" in p for p in found)
    later = next(n for n in split["node_ids"] if n not in split["node_phases"])
    found, _ = inventory_problems(rows, early_phase, known, gate=True)
    assert not any(later in p for p in found)  # the row's own phase is not yet due
