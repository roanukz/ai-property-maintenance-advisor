"""The second PRD's tests are required node IDs in gate mode (gate_requirements.json, phase 6).

Phase 6 lists every test of SC12c, SC13, SC14, SC15, decision 69, the fixed test date and this
round (test_round2_*.py), so a gate run under ADVISOR_GATE=6 on a subset of files, or with -k or
--ignore, fails. The PRD 2 tests added to older files (PRD2_EXTRA_IDS) are listed too.
ADVISOR_GATE=6 also enforces phases 2 to 5; ADVISOR_GATE=5 is unchanged.

Offline. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent.tests.conftest import REQUIREMENTS_PATH, gate_requirements

TESTS_DIR = Path(__file__).resolve().parent
PRD2_PHASE = 6
PRD2_FILES = ("test_sc12c.py", "test_warranty_coverage.py", "test_warranty_dates.py",
              "test_property_page_warranty.py", "test_sweep.py", "test_sweep_review.py", "test_sweep_declines.py",
              "test_sweep_sc7b.py", "test_product_recalls.py", "test_product_recalls_cli.py",
              "test_product_recalls_review.py", "test_test_date_pin.py")
PRD2_GLOBS = ("test_haiku55_*.py", "test_round2_*.py")
# PRD 2 tests added to files that hold older tests too, so the files themselves are not listed above
# (review of round 2, R2). The seed test was renamed when decision 77 added the seventh appliance,
# the synthetic water softener, and covers that decision.
PRD2_EXTRA_IDS = (
    "test_registry.py::test_optima_terms_load_as_three_coverage_rows_in_months",
    "test_registry.py::test_schema_checks_warranty_coverage_rows",
    "test_registry.py::test_seed_coverage_rows_must_be_synthetic_and_may_be_absent",
    "test_registry.py::test_seed_loads_two_properties_seven_appliances_all_synthetic",
    "test_render.py::test_coverage_line_is_drawn_in_v2_only_and_labels_synthetic_rows",
    "test_prompts.py::test_warranty_rule_says_code_writes_every_warranty_date",
)


def prd2_files() -> list[Path]:
    files = [TESTS_DIR / name for name in PRD2_FILES]
    for pattern in PRD2_GLOBS:
        files += sorted(TESTS_DIR.glob(pattern))
    return files


def functions_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {f"{path.name}::{node.name}" for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")}


def short(key: str) -> str:
    """'<abs path>::name' as 'file::name'."""
    path, _, name = key.partition("::")
    return f"{Path(path).name}::{name}"


def test_phase_6_requires_every_prd2_test_and_nothing_missing() -> None:
    """Every test function in the PRD 2 files is required at phase 6 and not before; every phase 6
    node ID names a test function that exists. Mutation: gate_requirements_prd2_test_dropped."""
    at6 = {short(k) for k in gate_requirements(REQUIREMENTS_PATH, PRD2_PHASE)[0]}
    at5 = {short(k) for k in gate_requirements(REQUIREMENTS_PATH, PRD2_PHASE - 1)[0]}
    expected: set[str] = set()
    for path in prd2_files():
        assert path.is_file(), path.name
        expected |= functions_in(path)
    assert expected, "no PRD 2 tests found"
    assert sorted(expected - at6) == [], "PRD 2 tests the gate would not require"
    assert expected & at5 == set(), "phase 6 tests are required at phase 5"
    phase6 = at6 - at5
    known = set().union(*(functions_in(p) for p in TESTS_DIR.glob("test_*.py")))
    assert sorted(phase6 - known) == [], "phase 6 names tests that do not exist"
    for group in ("test_sc12c.py::", "test_warranty_coverage.py::", "test_sweep.py::", "test_product_recalls.py::",
                  "test_haiku55_config.py::", "test_round2_sweep_cap.py::"):
        assert any(node.startswith(group) for node in phase6), group


def test_phase_6_requires_the_prd2_tests_added_to_older_files() -> None:
    """Each PRD 2 test that lives in a file of older tests (SC13's registry, render and prompt tests,
    among them success criterion 1's coverage line) is required at phase 6 and not before, and names
    a test function that exists. Mutation: gate_requirements_prd2_extra_test_dropped."""
    at6 = {short(k) for k in gate_requirements(REQUIREMENTS_PATH, PRD2_PHASE)[0]}
    at5 = {short(k) for k in gate_requirements(REQUIREMENTS_PATH, PRD2_PHASE - 1)[0]}
    assert sorted(set(PRD2_EXTRA_IDS) - at6) == [], "PRD 2 tests in older files the gate would not require"
    assert set(PRD2_EXTRA_IDS) & at5 == set()
    for node in PRD2_EXTRA_IDS:
        name, _, _ = node.partition("::")
        assert node in functions_in(TESTS_DIR / name), node
