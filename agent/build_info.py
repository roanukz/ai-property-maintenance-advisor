"""Which build of the advisor produced a result (decision 35).

Every run record carries this fingerprint, so a published number can always
be traced to the code that produced it, and a result from a superseded build
is never reported as a finding.
"""

from __future__ import annotations

import functools
import hashlib

from agent import config


@functools.lru_cache(maxsize=1)
def build_id() -> str:
    """A fingerprint of the advisor's code (agent/ without its tests), for decision 35.

    Cached per process: the code a process runs does not change under it.
    """
    digest = hashlib.sha256()
    root = config.REPO_ROOT / "agent"
    tests = root / "tests"
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_relative_to(tests) or "__pycache__" in path.parts:
            continue
        if path.suffix not in (".py", ".sql", ".json", ".toml"):
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]
