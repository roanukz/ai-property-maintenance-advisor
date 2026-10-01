"""Which build of the advisor produced a result (decision 35).

Every run record carries this fingerprint, so a published number can always
be traced to the code that produced it, and a result from a superseded build
is never reported as a finding.

The fingerprint covers the code that decides what the advisor does: every
.py, .sql, .json and .toml file under agent/, less the tests and less
EXCLUDED, the files that never run inside the advisor. The recorded demo's
builder (agent/replay/build_demo.py) reads this same build_id(), so a run
record and the demo agree on what "the current build" means.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path

from agent import config

# Paths relative to agent/ that are left out of the fingerprint, because a
# change to them does not change what the advisor does. An entry ending in "/"
# leaves out everything under that folder.
EXCLUDED = frozenset({
    # The recorded demo's tooling: it builds and checks the page from recorded
    # runs and never runs inside the advisor, so installing or editing it does
    # not make the runs it shows look like another build.
    "replay/build_demo.py",
    "replay/check_demo.py",
    "replay/demo_selection.json",
    # The safety evaluation harness and its committed data (SC12a and SC12b):
    # the scorer, the pools, the wordings and labels.json, which changes
    # whenever readers' labels are imported. A question (ask and resume, the
    # graph and its nodes) never imports it, and neither does the SC7b first
    # lookup filter (agent/live/eval_sc7b.py; a test holds that). The SC12a
    # and SC12b commands do (agent/cli.py, agent/live/sc12a.py and
    # agent/live/sc12b.py): it words SC12a's Jev calls and shapes SC12b's live
    # inputs, so an edit here can change what those two commands send without
    # changing this fingerprint.
    "safety_eval/",
})

SUFFIXES = (".py", ".sql", ".json", ".toml")


def is_excluded(rel: str, excluded: frozenset[str] = EXCLUDED) -> bool:
    """True when `rel` (a path relative to agent/, with forward slashes) is left out."""
    return any(rel.startswith(entry) if entry.endswith("/") else rel == entry for entry in excluded)


def fingerprint(root: Path) -> str:
    """The fingerprint of the advisor package at `root` (normally agent/): tests and EXCLUDED left out."""
    digest = hashlib.sha256()
    tests = root / "tests"
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_relative_to(tests) or "__pycache__" in path.parts:
            continue
        if path.suffix not in SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if is_excluded(rel):
            continue
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


@functools.lru_cache(maxsize=1)
def build_id() -> str:
    """The advisor's build fingerprint for decision 35: fingerprint() of the repo's agent/.

    Cached per process: the code a process runs does not change under it.
    """
    return fingerprint(Path(config.REPO_ROOT) / "agent")
