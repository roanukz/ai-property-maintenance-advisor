"""The MCP server's tests are required node IDs in gate mode (gate_requirements.json, phase 7).

Phase 7 lists every test in test_mcp_server.py and in this file, so a gate run under
ADVISOR_GATE=7 on a subset of files, or with -k or --ignore, fails. ADVISOR_GATE=7 also
enforces phases 2 to 6; ADVISOR_GATE=6 is unchanged.

Offline. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent.tests.conftest import REQUIREMENTS_PATH, gate_requirements

TESTS_DIR = Path(__file__).resolve().parent
MCP_PHASE = 7
MCP_FILES = ("test_mcp_server.py", "test_mcp_gate.py")


def functions_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {f"{path.name}::{node.name}" for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")}


def short(key: str) -> str:
    """'<abs path>::name' as 'file::name'."""
    path, _, name = key.partition("::")
    return f"{Path(path).name}::{name}"


def test_phase_7_requires_every_mcp_test_and_nothing_missing() -> None:
    """Every test function in the MCP files is required at phase 7 and not before; every phase 7
    node ID names a test function that exists. Mutation: gate_requirements_mcp_test_dropped."""
    at7 = {short(k) for k in gate_requirements(REQUIREMENTS_PATH, MCP_PHASE)[0]}
    at6 = {short(k) for k in gate_requirements(REQUIREMENTS_PATH, MCP_PHASE - 1)[0]}
    expected: set[str] = set()
    for name in MCP_FILES:
        path = TESTS_DIR / name
        assert path.is_file(), name
        expected |= functions_in(path)
    assert len(expected) > len(MCP_FILES), "no MCP tests found"
    assert sorted(expected - at7) == [], "MCP tests the gate would not require"
    assert expected & at6 == set(), "phase 7 tests are required at phase 6"
    phase7 = at7 - at6
    known = set().union(*(functions_in(p) for p in TESTS_DIR.glob("test_*.py")))
    assert sorted(phase7 - known) == [], "phase 7 names tests that do not exist"
