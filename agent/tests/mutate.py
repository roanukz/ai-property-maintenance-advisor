"""Mutation gate (PLAN section 5): prove every listed test can go red.

For each entry in mutations.toml this script copies the repo to a temporary
directory (leaving out .venv, data, .git and caches), applies one text
replacement, runs the listed pytest node IDs in the copy with this venv's
Python, and reports KILLED (the tests went red) or SURVIVED (they stayed
green, so the test is too weak or the mutation is wrong).

It is a script, not a test module: pytest never collects it.

    .venv/bin/python agent/tests/mutate.py            # every mutation
    .venv/bin/python agent/tests/mutate.py --only ledger_no_run_cap canary_in_process
    .venv/bin/python agent/tests/mutate.py --jobs 6

Each copy imports its own agent package: pytest's `pythonpath = ["."]` and the
`-m pytest` working directory both put the copy's root ahead of the editable
install, and child processes get the copy's root on PYTHONPATH through
`spawn_offline_child` (config.REPO_ROOT resolves inside the copy). The
`canary_*` entries check both paths: each changes only the copy and must be
KILLED. PYTHONDONTWRITEBYTECODE keeps a stale .pyc from masking a mutation.

mutations.toml format, one [[mutation]] table each:
    id      unique name
    file    path relative to the repo root
    search  text that must occur exactly once in `file` (omit to create `file`);
            a list of texts makes several edits to the same file, in order
    replace replacement text (or the new file's content when `search` is
            omitted); a list when `search` is a list, of the same length
    tests   pytest node IDs expected to go red; KILLED needs every one of
            them on a FAILED line of a run that exited 1, so an import
            error, a collection error or a setup ERROR never counts
    create  optional table of {path = content}: extra files the mutation adds
            (for a change that needs a second file, such as a test under a
            newly collected folder)
    env     optional table of extra environment variables for the pytest run
    why     optional note
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MUTATIONS_FILE = Path(__file__).resolve().with_name("mutations.toml")
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
TOP_LEVEL_EXCLUDES = {".venv", "data", ".git"}
# Never copy secrets into the temp trees: the repo's .env (and any .env.* but the
# example), and any top level file whose name mentions a key.
SECRET_EXAMPLE = ".env.example"


def is_secret_name(name: str) -> bool:
    lowered = name.lower()
    if name == SECRET_EXAMPLE:
        return False
    return name == ".env" or name.startswith(".env.") or "key" in lowered
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".hypothesis", "node_modules", ".DS_Store"}
# The key and tracing variables conftest scrubs; a mutation may plant them on purpose.
SCRUB_PREFIXES = ("ANTHROPIC_", "LANGSMITH_", "LANGCHAIN_")
SCRUB_NAMES = ("TAVILY_API_KEY",)
RUN_TIMEOUT_S = 600
_SUMMARY_LINE = re.compile(r"^(FAILED|ERROR) (\S+)")


class MutationError(Exception):
    """A mutation entry is malformed or its search text does not match exactly once."""


@dataclass
class Mutation:
    id: str
    file: str
    tests: list[str]
    replace: str | list[str]
    search: str | list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    create: dict[str, str] = field(default_factory=dict)
    why: str = ""


@dataclass
class Outcome:
    mutation: Mutation
    verdict: str  # KILLED, SURVIVED, or ERROR
    returncode: int | None
    seconds: float
    detail: str = ""


def load_mutations(path: Path = MUTATIONS_FILE) -> list[Mutation]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    mutations = []
    seen: set[str] = set()
    for raw in data.get("mutation", []):
        unknown = set(raw) - {"id", "file", "search", "replace", "tests", "env", "create", "why"}
        if unknown:
            raise MutationError(f"{raw.get('id')}: unknown keys {sorted(unknown)}")
        m = Mutation(**raw)
        if m.id in seen:
            raise MutationError(f"duplicate mutation id {m.id}")
        if not m.tests:
            raise MutationError(f"{m.id}: no tests listed")
        if isinstance(m.search, list) != isinstance(m.replace, list) or (
            isinstance(m.search, list) and len(m.search) != len(m.replace)
        ):
            raise MutationError(f"{m.id}: search and replace lists must match")
        seen.add(m.id)
        mutations.append(m)
    return mutations


def _ignore(root: Path):
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {n for n in names if n in CACHE_DIRS}
        if Path(directory).resolve() == root:
            skipped |= {n for n in names if n in TOP_LEVEL_EXCLUDES or is_secret_name(n)}
        return skipped

    return ignore


def copy_repo(dest: Path) -> Path:
    target = dest / "repo"
    shutil.copytree(REPO_ROOT, target, ignore=_ignore(REPO_ROOT.resolve()), symlinks=True)
    return target


def apply(mutation: Mutation, root: Path) -> None:
    """Apply one mutation in the copy, failing loudly unless it matches exactly once."""
    for rel, content in mutation.create.items():
        extra = root / rel
        if extra.exists():
            raise MutationError(f"{mutation.id}: {rel} already exists")
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text(content, encoding="utf-8")
    path = root / mutation.file
    if mutation.search is None:
        if path.exists():
            raise MutationError(f"{mutation.id}: {mutation.file} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mutation.replace, encoding="utf-8")
        return
    if not path.exists():
        raise MutationError(f"{mutation.id}: {mutation.file} not found")
    searches = mutation.search if isinstance(mutation.search, list) else [mutation.search]
    replaces = mutation.replace if isinstance(mutation.replace, list) else [mutation.replace]
    text = path.read_text(encoding="utf-8")
    for n, (search, replace) in enumerate(zip(searches, replaces, strict=True), start=1):
        count = text.count(search)
        if count != 1:
            raise MutationError(
                f"{mutation.id}: search text {n} found {count} times in {mutation.file}, expected 1"
            )
        mutated = text.replace(search, replace)
        if mutated == text:
            raise MutationError(f"{mutation.id}: replacement {n} leaves {mutation.file} unchanged")
        text = mutated
    path.write_text(text, encoding="utf-8")


def child_env(extra: dict[str, str]) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in SCRUB_NAMES and not k.startswith(SCRUB_PREFIXES) and k != "PYTHONPATH"
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("ADVISOR_GATE", None)
    env.update(extra)
    return env


def run_tests(root: Path, tests: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VENV_PYTHON), "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "--no-header", *tests],
        cwd=root,
        env=child_env(env),
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
        check=False,
    )


def _tail(proc: subprocess.CompletedProcess[str], lines: int = 3) -> str:
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return " | ".join(out[-lines:])


def _base(node_id: str) -> str:
    """The node ID with any parameter suffix cut (a parameter id may hold spaces)."""
    return node_id.split("[", 1)[0]


def classify(returncode: int, stdout: str, tests: list[str]) -> tuple[str, str]:
    """(verdict, detail) for one mutated run of the listed tests.

    KILLED only when pytest exited 1 (tests failed) and every listed node ID,
    parameter suffix cut, is on a FAILED summary line. Exit 2, 3 or 4 means
    an interrupted run, an internal error or a usage error such as a module
    that no longer imports, and a listed test that only shows ERROR failed in
    setup or collection: all of those are ERROR, not proof. A listed test that
    stayed green while another went red SURVIVED.
    """
    if returncode == 0:
        return "SURVIVED", "every listed test passed"
    if returncode == 5:
        return "ERROR", "no tests collected: a node ID is wrong"
    if returncode != 1:
        return "ERROR", f"pytest exit {returncode}: interrupted, internal, usage or collection error"
    failed: set[str] = set()
    errored: set[str] = set()
    for line in stdout.splitlines():
        m = _SUMMARY_LINE.match(line)
        if m:
            (failed if m.group(1) == "FAILED" else errored).add(_base(m.group(2)))
    missing = [t for t in tests if _base(t) not in failed]
    if not missing:
        return "KILLED", " | ".join(sorted(failed))[:300]
    only_errored = [t for t in missing if _base(t) in errored]
    if only_errored:
        return "ERROR", "errored, not failed: " + ", ".join(only_errored)
    return "SURVIVED", "stayed green: " + ", ".join(missing)


def run_one(mutation: Mutation) -> Outcome:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"mutate-{mutation.id}-") as tmp:
        root = copy_repo(Path(tmp))
        try:
            apply(mutation, root)
        except MutationError as exc:
            return Outcome(mutation, "ERROR", None, time.monotonic() - start, str(exc))
        proc = run_tests(root, mutation.tests, mutation.env)
    seconds = time.monotonic() - start
    verdict, detail = classify(proc.returncode, proc.stdout, mutation.tests)
    if verdict != "KILLED":
        detail = f"{detail} | {_tail(proc)}"
    return Outcome(mutation, verdict, proc.returncode, seconds, detail)


def baseline(mutations: list[Mutation]) -> subprocess.CompletedProcess[str]:
    """Every listed node ID must pass on an unmutated copy, or no verdict means anything."""
    nodes = sorted({t for m in mutations for t in m.tests})
    with tempfile.TemporaryDirectory(prefix="mutate-baseline-") as tmp:
        root = copy_repo(Path(tmp))
        return run_tests(root, nodes, {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", metavar="ID", help="run only these mutation ids")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the failure line for KILLED too")
    args = parser.parse_args(argv)

    mutations = load_mutations()
    if args.only:
        wanted = set(args.only)
        missing = wanted - {m.id for m in mutations}
        if missing:
            print(f"unknown mutation ids: {sorted(missing)}", file=sys.stderr)
            return 2
        mutations = [m for m in mutations if m.id in wanted]

    if not args.skip_baseline:
        base = baseline(mutations)
        if base.returncode != 0:
            print("baseline failed: the listed tests are not green on an unmutated copy")
            print(base.stdout[-4000:] + base.stderr[-2000:])
            return 2
        print(f"baseline green: {_tail(base, 1)}")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        outcomes = list(pool.map(run_one, mutations))

    width = max(len(o.mutation.id) for o in outcomes)
    for o in outcomes:
        tests = ", ".join(t.split("/")[-1] for t in o.mutation.tests)
        print(f"{o.verdict:<8} {o.mutation.id:<{width}}  rc={o.returncode}  {o.seconds:5.1f}s  {tests}")
        if o.verdict != "KILLED" or args.verbose:
            print(f"         {o.detail}")
    counts = {v: sum(o.verdict == v for o in outcomes) for v in ("KILLED", "SURVIVED", "ERROR")}
    print(f"{counts['KILLED']} killed, {counts['SURVIVED']} survived, {counts['ERROR']} errors "
          f"of {len(outcomes)} mutations")
    return 0 if counts["KILLED"] == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
