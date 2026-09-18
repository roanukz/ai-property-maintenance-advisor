"""Test session setup: environment scrub, tracing off, markers and gate mode.

The scrub runs at import, before any test module imports langgraph or
langchain (PLAN section 8.14). langsmith itself is already imported by then,
because langsmith 0.12.6 registers a pytest11 plugin that pytest loads before
any conftest, so the scrub also clears langsmith's cached env lookups and
turns tracing off through `langsmith.configure(enabled=False)`.

Gate mode (PLAN sections 5 and 10): with ADVISOR_GATE=N, the session fails
if any required test was skipped, xfailed, deselected, or not collected. The
required tests are every v1_inventory.json node ID whose phase is <= N (a row
may give single node IDs an earlier phase in "node_phases", for the halves of
a later row that an earlier phase delivers), every node ID that
gate_requirements.json lists for a phase <= N (the gate's own tests, so a run
on a subset of files, or with --ignore or -k, cannot pass a gate), and every
test marked `sc1b`. The session also fails when it collects fewer sc1b cases
than the requirements' min_sc1b. Node IDs are "file::function" paths relative
to the directory of the file that lists them. N is the
phase whose gate is being run, so the Phase 2 gate is ADVISOR_GATE=2 (the
PLAN text says ADVISOR_GATE=1 throughout; that wording is out of date). The
terminal summary always prints how many node IDs the gate enforced, so a
gate that enforced nothing is visible.
"""

from __future__ import annotations

import os

from agent.tests.helpers import scrub_environ

scrub_environ(os.environ)

import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from agent import config  # noqa: E402

INVENTORY_PATH = Path(__file__).resolve().with_name("v1_inventory.json")
REQUIREMENTS_PATH = Path(__file__).resolve().with_name("gate_requirements.json")
# Let the gate mode tests point the hook at a synthetic inventory and requirements.
ENV_GATE_INVENTORY = config.ENV_GATE_INVENTORY
ENV_GATE_REQUIREMENTS = config.ENV_GATE_REQUIREMENTS
LIVE_MARKER_ERROR = "live evaluations are CLI commands, never pytest tests"
GATE_FAILURE_TITLE = "ADVISOR_GATE failed"
GATE_SUMMARY = "gate phase {phase}: {required} required node IDs, {sc1b} sc1b"

_PARAM_SUFFIX = re.compile(r"\[.*\]$")


def _turn_tracing_off() -> None:
    """Clear langsmith's cached env reads and disable tracing globally."""
    try:
        from langsmith import utils as ls_utils
    except ImportError:
        return
    for name in ("get_env_var", "get_tracer_project"):
        cached = getattr(ls_utils, name, None)
        if hasattr(cached, "cache_clear"):
            cached.cache_clear()
    import langsmith

    langsmith.configure(enabled=False)


_turn_tracing_off()


NO_DOTENV_NAME = "no.env"


@pytest.fixture(autouse=True)
def _never_read_the_real_dotenv(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point config.ENV_FILE away from the repo's .env for every test (review finding M7).

    Once the real .env exists for Phase 5, a test (or a mutation run) that got
    past a refusal would otherwise load the real keys into this process. A
    test that needs a .env sets its own path, which wins over this one.
    """
    monkeypatch.setattr(config, "ENV_FILE", tmp_path_factory.getbasetemp() / NO_DOTENV_NAME)


def gate_phase() -> int | None:
    """The phase number from ADVISOR_GATE, or None outside gate mode."""
    raw = os.environ.get(config.ENV_GATE, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise pytest.UsageError(f"{config.ENV_GATE} must be a phase number, got {raw!r}")


def _resolve_inventory_id(node_id: str, base: Path) -> str:
    file_part, _, rest = node_id.partition("::")
    return f"{(base / file_part).resolve()}::{rest}"


def required_node_ids(inventory_path: Path, phase: int) -> dict[str, str]:
    """Map each required inventory node ID (resolved) to the row that needs it."""
    rows = json.loads(inventory_path.read_text(encoding="utf-8"))
    base = inventory_path.resolve().parent
    required: dict[str, str] = {}
    for row in rows:
        for node_id in row["node_ids"]:
            node_phase = node_phase_of(row, node_id)
            if node_phase > phase:
                continue
            required[_resolve_inventory_id(node_id, base)] = (
                f"v1 test {row['v1_test']} (phase {node_phase}): {node_id}"
            )
    return required


def node_phase_of(row: dict, node_id: str) -> int:
    """The phase a row's node ID is due: its own entry in node_phases, else the row's phase."""
    return int((row.get("node_phases") or {}).get(node_id, row["phase"]))


def gate_requirements(requirements_path: Path, phase: int) -> tuple[dict[str, str], int]:
    """(resolved node ID -> why, fewest sc1b cases) for every requirements phase <= phase."""
    if not requirements_path.is_file():
        return {}, 0
    doc = json.loads(requirements_path.read_text(encoding="utf-8"))
    base = requirements_path.resolve().parent
    required: dict[str, str] = {}
    min_sc1b = 0
    for key, block in sorted(doc.get("phases", {}).items(), key=lambda kv: int(kv[0])):
        if int(key) > phase:
            continue
        min_sc1b = max(min_sc1b, int(block.get("min_sc1b", 0)))
        for node_id in block.get("node_ids", []):
            required[_resolve_inventory_id(node_id, base)] = f"phase {key} gate requirement: {node_id}"
    return required, min_sc1b


def _item_key(item: pytest.Item) -> str:
    """Resolved "abs_path::name" for an item, with any parameter suffix cut."""
    _, _, rest = item.nodeid.partition("::")
    return f"{Path(item.path).resolve()}::{_PARAM_SUFFIX.sub('', rest)}"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "sc1b: SC1b differential case; a skip fails every gate"
    )
    config.addinivalue_line(
        "markers", f"live: reserved; collection fails if used ({LIVE_MARKER_ERROR})"
    )
    phase = gate_phase()
    if phase is None or config.option.collectonly:
        return
    inventory = Path(os.environ.get(ENV_GATE_INVENTORY) or INVENTORY_PATH)
    requirements = Path(os.environ.get(ENV_GATE_REQUIREMENTS) or REQUIREMENTS_PATH)
    listed, min_sc1b = gate_requirements(requirements, phase)
    required = {**listed, **required_node_ids(inventory, phase)}
    config.pluginmanager.register(GatePlugin(phase, required, min_sc1b=min_sc1b), "advisor-gate")


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    live = [item.nodeid for item in items if item.get_closest_marker("live")]
    if live:
        raise pytest.UsageError(
            f"{LIVE_MARKER_ERROR}; marked live: " + ", ".join(sorted(live))
        )


class GatePlugin:
    """Tracks required tests through one gate session and fails it on a gap."""

    def __init__(self, phase: int, required: dict[str, str], *, min_sc1b: int = 0) -> None:
        self.phase = phase
        self.min_sc1b = min_sc1b
        self.required = required  # resolved "abs_path::name" -> why it is required
        self.inventory_count = len(required)
        self.sc1b: set[str] = set()
        self.collected: dict[str, list[str]] = {}
        self.deselected: set[str] = set()
        self.key_of: dict[str, str] = {}  # nodeid -> resolved key
        self.violations: list[str] = []

    def _note(self, item: pytest.Item) -> None:
        key = _item_key(item)
        self.key_of[item.nodeid] = key
        if item.nodeid not in self.collected.setdefault(key, []):
            self.collected[key].append(item.nodeid)
        if item.get_closest_marker("sc1b"):
            self.required.setdefault(key, f"sc1b case: {item.nodeid}")
            self.sc1b.add(key)

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            self._note(item)

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        for item in items:
            self._note(item)
            self.deselected.add(item.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        why = self.required.get(self.key_of.get(report.nodeid, ""))
        if why is None:
            return
        if hasattr(report, "wasxfail"):
            self.violations.append(f"xfailed: {report.nodeid} [{why}]")
        elif report.skipped:
            self.violations.append(f"skipped: {report.nodeid} [{why}]")

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        for key, why in sorted(self.required.items()):
            nodeids = self.collected.get(key, [])
            if not nodeids:
                self.violations.append(f"not collected: {why}")
            elif all(nodeid in self.deselected for nodeid in nodeids):
                self.violations.append(f"deselected: {', '.join(nodeids)} [{why}]")
        if len(self.sc1b) < self.min_sc1b:
            self.violations.append(
                f"sc1b: {len(self.sc1b)} cases collected; this gate needs at least {self.min_sc1b}"
            )
        if self.violations and session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter) -> None:
        terminalreporter.write_line(
            GATE_SUMMARY.format(phase=self.phase, required=self.inventory_count, sc1b=len(self.sc1b))
        )
        if not self.violations:
            return
        terminalreporter.section(f"{GATE_FAILURE_TITLE} (phase {self.phase})", red=True)
        for line in self.violations:
            terminalreporter.line(line, red=True)
