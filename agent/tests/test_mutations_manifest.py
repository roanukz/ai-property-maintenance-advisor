"""mutations.toml stays in step with the tree (PLAN section 5).

The mutation gate itself (agent/tests/mutate.py) copies the repo and runs
pytest once per mutation, so it is a script run at each gate, not a test. These
offline checks catch drift between gates: a search text that no longer matches
exactly once, a node ID that points at a renamed test, or a new test that no
mutation proves can fail.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest

from agent.tests import mutate

TESTS_DIR = Path(__file__).resolve().parent
MUTATIONS = mutate.load_mutations()


@cache
def _test_functions() -> frozenset[str]:
    """Every top level test function, as "agent/tests/<file>::<name>"."""
    names = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(mutate.REPO_ROOT).as_posix()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                names.add(f"{rel}::{node.name}")
    return frozenset(names)


@pytest.mark.parametrize("mutation", MUTATIONS, ids=[m.id for m in MUTATIONS])
def test_mutation_applies_cleanly_to_the_tree(mutation: mutate.Mutation) -> None:
    """Mutation: change a search text in the tree (or in mutations.toml) so it
    matches zero or two times; or rename a listed test."""
    path = mutate.REPO_ROOT / mutation.file
    if mutation.search is None:
        assert not path.exists(), f"{mutation.file} would be created but already exists"
    else:
        text = path.read_text(encoding="utf-8")
        searches = mutation.search if isinstance(mutation.search, list) else [mutation.search]
        replaces = mutation.replace if isinstance(mutation.replace, list) else [mutation.replace]
        for search, replace in zip(searches, replaces, strict=True):
            assert text.count(search) == 1, f"{mutation.id}: search text does not match exactly once"
            text = text.replace(search, replace)
    for rel in mutation.create:
        assert not (mutate.REPO_ROOT / rel).exists(), f"{rel} would be created but already exists"
    known = _test_functions()
    for node_id in mutation.tests:
        assert node_id.split("[")[0] in known, f"{mutation.id}: {node_id} is not a test in agent/tests"


def test_every_test_has_a_mutation() -> None:
    """Mutation: add a test function that no mutations.toml entry lists."""
    covered = {node_id.split("[")[0] for m in MUTATIONS for node_id in m.tests}
    missing = sorted(_test_functions() - covered)
    assert not missing, "tests no mutation proves can fail:\n" + "\n".join(missing)


def test_apply_fails_loudly_unless_search_matches_once(tmp_path: Path) -> None:
    """Mutation: relax the count check in mutate.apply to `count == 0`, or drop it."""
    (tmp_path / "f.py").write_text("x = 1\ny = 1\nx = 1\n", encoding="utf-8")
    for search in ("x = 1", "z = 1"):
        m = mutate.Mutation(id="t", file="f.py", tests=["t"], search=search, replace="x = 2")
        with pytest.raises(mutate.MutationError):
            mutate.apply(m, tmp_path)
    m = mutate.Mutation(id="t", file="f.py", tests=["t"], search="y = 1", replace="y = 2")
    mutate.apply(m, tmp_path)
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "x = 1\ny = 2\nx = 1\n"


A = "agent/tests/test_a.py::test_one"
B = "agent/tests/test_a.py::test_two"


def test_classify_counts_only_real_failures_of_every_listed_test() -> None:
    """Mutations: treat any non-zero exit as KILLED again; accept an ERROR line
    as a kill; or accept one FAILED listed test as enough."""
    failed_both = f"FAILED {A} - AssertionError: x\nFAILED {B}[p1] - assert 0\n1 failed"
    assert mutate.classify(1, failed_both, [A, B])[0] == "KILLED"
    # A module that no longer imports: collection error, exit 2 (or 4 for usage).
    collection = f"ERROR agent/tests/test_a.py - ModuleNotFoundError: No module named 'x'\n1 error"
    for rc in (2, 3, 4):
        assert mutate.classify(rc, collection, [A])[0] == "ERROR"
    # A setup error on the listed test is not a failure of its assertion.
    assert mutate.classify(1, f"ERROR {A} - sqlite3.IntegrityError\n1 error", [A])[0] == "ERROR"
    # One listed test stayed green while the other went red.
    verdict, detail = mutate.classify(1, f"FAILED {A} - boom\n1 failed, 1 passed", [A, B])
    assert verdict == "SURVIVED" and B in detail
    assert mutate.classify(0, "2 passed", [A, B])[0] == "SURVIVED"
    assert mutate.classify(5, "no tests ran", [A])[0] == "ERROR"


def test_mutation_copies_never_include_secrets() -> None:
    """Mutation mutate_copies_secrets: the temp trees get the repo's .env or a key file."""
    from agent.tests import mutate

    for name in (".env", ".env.local", "APIKEYS.rtf", "my_keys.txt"):
        assert mutate.is_secret_name(name), name
    for name in (".env.example", "agent", "pyproject.toml", "README.md"):
        assert not mutate.is_secret_name(name), name
    ignore = mutate._ignore(mutate.REPO_ROOT.resolve())
    skipped = ignore(str(mutate.REPO_ROOT), [".env", "APIKEYS.rtf", ".env.example", "agent"])
    assert {".env", "APIKEYS.rtf"} <= skipped and not {".env.example", "agent"} & skipped
