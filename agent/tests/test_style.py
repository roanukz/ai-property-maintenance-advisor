"""House style over agent/ and seed/: no en or em dashes, American spelling.

Raw dash characters and their escapes (JSON or Python \\u2013 and \\u2014,
\\U00002014, \\N{EM DASH}) both count, because an escape renders as a dash.
Escapes are allowed in cassette text under agent/tests/cassettes/, which
keeps v1 model output verbatim (decision 17). A file that mentions an escape
or a spelling in order to check for it or plant it is on MENTION_ALLOWLIST
with its reason. A raw dash character is allowed nowhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent import config

ROOTS = ("agent", "seed")
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".json", ".toml", ".md", ".mjs", ".js", ".sql", ".txt", ".html", ".yaml", ".yml"}

DASH_CHARS = re.compile("[" + chr(0x2013) + chr(0x2014) + "]")
DASH_ESCAPES = re.compile(r"\\u201[34]|\\U0000201[34]|\\N\{E[MN] DASH\}")
# Escapes allowed here: verbatim v1 model text in cassettes (decision 17).
ESCAPE_EXEMPT_DIRS = ("agent/tests/cassettes/",)

BRITISH = (
    "colour", "behaviour", "favour", "honour", "labour", "neighbour", "flavour", "humour",
    "analyse", "catalogue", "centre", "licence", "defence", "cheque", "programme",
    "travelled", "cancelled", "modelling", "labelled", "fulfil", "judgement", "ageing",
    "artefact", "aluminium", "sceptical", "grey", "tyre", "mould", "enrol", "instalment",
)
# -ise verbs, written as stems so every form (-ise, -ised, -ising, -isation) matches.
BRITISH_ISE = (
    "organis", "realis", "recognis", "optimis", "summaris", "normalis", "initialis",
    "serialis", "minimis", "maximis", "prioritis", "utilis", "customis", "authoris",
    "categoris", "emphasis", "characteris", "standardis", "sanitis", "tokenis", "finalis",
    "visualis", "localis", "synchronis",
)
BRITISH_RE = re.compile(
    r"\b(?:(?:" + "|".join(BRITISH) + r")(?:s|d|ed|es|ing|r|rs)?"
    r"|(?:" + "|".join(BRITISH_ISE) + r")(?:e|es|ed|er|ers|ing|ation|ations))\b",
    re.IGNORECASE,
)
MENTION_ALLOWLIST = {
    "agent/tests/test_style.py": "holds the escapes and spellings it checks for",
    "agent/tests/mutations.toml": "its TOML escapes encode the dashes that mutations plant",
}


def scanned_files() -> list[Path]:
    files = []
    for root in ROOTS:
        for path in sorted((config.REPO_ROOT / root).rglob("*")):
            if path.is_file() and path.suffix in TEXT_SUFFIXES and not SKIP_DIRS & set(path.parts):
                files.append(path)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(config.REPO_ROOT).as_posix()


def dash_hits(path: Path) -> list[str]:
    rel = _rel(path)
    hits = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if DASH_CHARS.search(line):
            hits.append(f"{rel}:{n}: dash character")
        if DASH_ESCAPES.search(line) and not rel.startswith(ESCAPE_EXEMPT_DIRS) \
                and rel not in MENTION_ALLOWLIST:
            hits.append(f"{rel}:{n}: dash escape")
    return hits


def spelling_hits(path: Path) -> list[str]:
    rel = _rel(path)
    if rel in MENTION_ALLOWLIST:
        return []
    return [
        f"{rel}:{n}: {m.group()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for m in BRITISH_RE.finditer(line)
    ]


def test_scan_covers_agent_and_seed() -> None:
    """Mutation: scan only .py files, or skip seed/; a folder's text goes unchecked."""
    rels = {_rel(p) for p in scanned_files()}
    for expected in ("agent/config.py", "agent/tests/mutations.toml", "agent/tests/harness/netguard.mjs",
                     "seed/registry_seed.json", "agent/registry_schema.sql"):
        assert expected in rels, expected


def test_no_en_or_em_dashes_in_agent_or_seed() -> None:
    """Mutation: plant a dash, or a \\u2014 escape, in any scanned file outside cassettes."""
    hits = [hit for path in scanned_files() for hit in dash_hits(path)]
    assert hits == []


def test_american_spelling_in_agent_or_seed() -> None:
    """Mutation: plant a British spelling in any scanned file."""
    hits = [hit for path in scanned_files() for hit in spelling_hits(path)]
    assert hits == []


def test_checks_see_what_they_ban() -> None:
    """Mutation: drop the escape pattern, the dash class or a suffix form; its sample passes."""
    sample = config.REPO_ROOT / "agent" / "tests" / "cassettes" / "x.json"
    assert _rel(sample).startswith(ESCAPE_EXEMPT_DIRS)
    dash = chr(0x2014)
    for text in (f"a {dash} b", "a \\u2014 b", "a \\u2013 b", "a \\U00002014 b", "a \\N{EM DASH} b"):
        assert DASH_CHARS.search(text) or DASH_ESCAPES.search(text), text
    for word in ("Colour", "organised", "behaviours", "cancelled", "normalisation"):
        assert BRITISH_RE.search(word), word
    for word in ("color", "organized", "CancelledError", "analyzer", "parameter", "center",
                 "emphasis", "otherwise"):
        assert not BRITISH_RE.search(word), word
