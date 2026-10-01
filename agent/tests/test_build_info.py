"""The build fingerprint (agent/build_info.py, decision 35).

A run record carries build_info.build_id(), and the recorded demo's builder
refuses any run whose build is not the current one. So the fingerprint must
change with every file that changes what the advisor does, and must not change
with the files that never run inside it: the demo tooling and the safety
evaluation under agent/safety_eval/ (build_info.EXCLUDED).

The change tests work on a small copy of agent/ under tmp_path, never on the
repo. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from agent import build_info, config

AGENT = Path(config.REPO_ROOT) / "agent"
# A few real files of each kind the fingerprint reads, copied into the fake tree.
COPIED = (
    "config.py",
    "registry_schema.sql",
    "replay/build_demo.py",
    "replay/check_demo.py",
    "replay/demo_selection.json",
    "replay/cassettes.py",
    "safety_eval/labels.json",
    "safety_eval/stats.py",
    "safety_eval/wordings.json",
)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "agent"
    for rel in COPIED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(AGENT / rel, root / rel)
    return root


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def test_build_id_is_the_fingerprint_of_the_repo_agent_folder() -> None:
    """Mutation build_info_build_id_hashes_elsewhere: build_id() stops hashing the repo's agent/."""
    assert build_info.build_id() == build_info.fingerprint(AGENT)
    assert len(build_info.build_id()) == 16


@pytest.mark.parametrize("rel", ["safety_eval/labels.json", "safety_eval/stats.py", "safety_eval/new_scorer.py",
                                 "safety_eval/sub/notes.toml"])
def test_a_change_under_safety_eval_leaves_the_build_unchanged(tree: Path, rel: str) -> None:
    """Mutation build_info_counts_safety_eval: agent/safety_eval/ drops out of EXCLUDED, so
    importing readers' labels or editing the scorer would look like a new build."""
    before = build_info.fingerprint(tree)
    _append(tree / rel, "\n")
    assert build_info.fingerprint(tree) == before


@pytest.mark.parametrize("rel", ["replay/build_demo.py", "replay/check_demo.py", "replay/demo_selection.json"])
def test_a_change_to_the_demo_tooling_leaves_the_build_unchanged(tree: Path, rel: str) -> None:
    """Mutation build_info_counts_demo_tooling: a demo tooling file drops out of EXCLUDED, so
    installing the demo tooling would make the runs it shows look like another build."""
    before = build_info.fingerprint(tree)
    _append(tree / rel, "\n")
    assert build_info.fingerprint(tree) == before


@pytest.mark.parametrize("rel", ["config.py", "registry_schema.sql", "replay/cassettes.py", "safety_eval.py",
                                 "nodes/new_node.py", "replay/demo_selection.toml", "safety_judge.py",
                                 "nodes/safety_check.py", "prompts.py", "live/eval_sc7b.py"])
def test_a_change_to_a_runtime_file_changes_the_build(tree: Path, rel: str) -> None:
    """Mutations build_info_excludes_too_much (a folder entry matches as a bare prefix, so
    agent/safety_eval.py would be left out with agent/safety_eval/),
    build_info_ignores_contents (only file names are hashed, so an edit is not a new build) and
    build_info_excludes_jev (the Jev safety check drops out of the build)."""
    before = build_info.fingerprint(tree)
    _append(tree / rel, "\n# changed\n")
    assert build_info.fingerprint(tree) != before


def test_tests_and_other_file_types_leave_the_build_unchanged(tree: Path) -> None:
    """Mutation build_info_counts_tests: agent/tests/ is hashed into the build."""
    before = build_info.fingerprint(tree)
    _append(tree / "tests" / "test_new.py", "x = 1\n")
    _append(tree / "replay" / "DEMO_NOTES.md", "notes\n")
    assert build_info.fingerprint(tree) == before


def test_excluded_is_exactly_the_demo_tooling_and_safety_eval() -> None:
    """Mutation build_info_excludes_jev: "safety_judge.py" joins EXCLUDED, so the Jev safety
    check could drop out of the build and an edit to it would not be a new build. Anything
    added to or removed from EXCLUDED is a change to what a build means (decision 35), so the
    set is pinned whole."""
    assert build_info.EXCLUDED == frozenset({
        "replay/build_demo.py", "replay/check_demo.py", "replay/demo_selection.json", "safety_eval/"})


def test_the_sc7b_first_lookup_filter_imports_nothing_the_build_leaves_out() -> None:
    """Mutation eval_sc7b_imports_safety_eval: the SC7b first lookup filter reads the SC12a
    replies folder through agent/safety_eval/harness again, so which run counts as SC7b's
    comparator would depend on code the fingerprint leaves out. The filter and the harness
    read the replies folder from the same config constant."""
    from agent.safety_eval import harness

    source = (AGENT / "live" / "eval_sc7b.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    left_out = sorted(name for name in imported if name.startswith("agent.")
                      and any(build_info.is_excluded(rel) for rel in _module_paths(name)))
    assert left_out == []
    assert harness.replies_dir() == config.EVAL_DIR / config.SC12A_REPLIES_SUBDIR


def _module_paths(module: str) -> list[str]:
    """The paths relative to agent/ that a dotted agent.* name could be: a module or a package."""
    rel = module.removeprefix("agent.").replace(".", "/")
    return [f"{rel}.py", f"{rel}/__init__.py"]
