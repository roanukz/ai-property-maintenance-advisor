"""No API key, auth header or key shaped token in any file under agent/ or seed/.

What counts as a hit:
- a known key prefix followed by at least 8 key characters: Anthropic
  ("sk" "-ant-"), LangSmith ("lsv2" "_"), Tavily ("tvly" "-"). A bare prefix,
  as in prose or a pattern definition, is not a key.
- an Authorization or x-api-key header whose value is a quoted literal, or a
  raw header line ("Authorization: Bearer ...") with a 16+ character value.
- a high entropy token: 32 or more characters from [A-Za-z0-9_+/=-], with at
  least one letter and one digit, and Shannon entropy of at least 4.3 bits per
  character. Random base64 keys of this length score about 4.6 to 5.5; English
  identifiers and slugs score below 4. Inside a URL only the query values, the
  fragment and path segments that are one opaque run (no "-", "_" or ".") are
  tested: a readable path such as a maker's PDF name built from hyphenated
  words can score near 5 over its whole length and is not a secret, while a key
  pasted into a query string still is. This matches the cassette privacy diff
  (agent/replay/privacy_diff.py), so a cassette that passes the diff also
  passes this scan once it is promoted.

Exclusions, all deliberate:
- agent/PLAN.md, which discusses the key formats.
- __pycache__, .pytest_cache and .hypothesis folders, and binary files
  (anything with a NUL byte or not valid UTF-8).
- hash digests labeled as hashes: a token that is a sha256 digest (64 hex
  characters, or 43 to 44 base64 characters) on a line that says sha256,
  hash or digest. Hex digests never reach the entropy threshold anyway (hex
  tops out at 4 bits per character); the label rule is what lets base64 ones
  through.
This file builds its own prefixes and fake keys by concatenation, so it holds
no match and is scanned like any other file.

Mutations that turn these red:
- a file under agent/ or seed/ containing a fake key such as the prefix
  "sk" "-ant-api03-" plus 20 random characters: the main test lists it.
- any pattern or the entropy check removed, or the threshold raised above 5:
  test_scanner_catches_each_kind fails.
- the labeled hash exclusion removed: test_scanner_allows_prose_and_labeled_digests fails.
- URL query values dropped from the entropy candidates: test_scanner_catches_each_kind
  fails on the key in a query string. URLs scanned whole again (the URL strip
  removed): test_scanner_allows_prose_and_labeled_digests fails on the
  readable manual path.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from agent import config

ROOTS = (config.REPO_ROOT / "agent", config.REPO_ROOT / "seed")
EXCLUDED_FILES = {config.REPO_ROOT / "agent" / "PLAN.md"}
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".hypothesis"}

KEY_CHARS = r"[A-Za-z0-9_\-]"
PREFIX_PATTERNS = {
    "Anthropic key": re.compile("sk" + "-ant-" + KEY_CHARS + "{8,}"),
    "LangSmith key": re.compile("lsv2" + "_" + KEY_CHARS + "{8,}"),
    "Tavily key": re.compile("tvly" + "-" + KEY_CHARS + "{8,}"),
}
HEADER_NAMES = "(?:author" + "ization|x-api" + "-key)"
HEADER_PATTERNS = {
    "auth header literal": re.compile(
        HEADER_NAMES
        + r"""["']?\s*[:=]\s*["'](?:bearer\s+|basic\s+)?[^"'\s{}$<>]{8,}["']""",
        re.IGNORECASE,
    ),
    "auth header line": re.compile(
        r"^\s*" + HEADER_NAMES + r"\s*:\s*(?:bearer\s+|basic\s+)?[A-Za-z0-9._\-+/=]{16,}",
        re.IGNORECASE,
    ),
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_+/=\-]{32,}")
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
ENTROPY_MIN_BITS = 4.3
HASH_LABEL_RE = re.compile(r"sha256|hash|digest", re.IGNORECASE)
DIGEST_RE = re.compile(r"^(?:[0-9a-fA-F]{64}|[A-Za-z0-9+/_\-]{43}=?)$")


def shannon_entropy(token: str) -> float:
    counts = Counter(token)
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _is_secret_shaped(token: str, line: str) -> bool:
    if not (re.search(r"[A-Za-z]", token) and re.search(r"\d", token)):
        return False
    if shannon_entropy(token) < ENTROPY_MIN_BITS:
        return False
    if DIGEST_RE.match(token) and HASH_LABEL_RE.search(line):
        return False
    return True


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line, reason) for each key pattern found."""
    hits: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for reason, pattern in {**PREFIX_PATTERNS, **HEADER_PATTERNS}.items():
            if pattern.search(line):
                hits.append((number, reason))
        for token in _entropy_candidates(line):
            if _is_secret_shaped(token, line):
                hits.append((number, "high entropy token"))
                break
    return hits


def _entropy_candidates(line: str) -> list[str]:
    """Long tokens outside URLs, plus the opaque parts of each URL."""
    tokens = [m.group() for m in TOKEN_RE.finditer(URL_RE.sub(" ", line))]
    for url in URL_RE.findall(line):
        parts = urlsplit(url)
        tokens += [value for _, value in parse_qsl(parts.query, keep_blank_values=True)]
        tokens.append(parts.fragment)
        tokens += [seg for seg in parts.path.split("/") if not re.search(r"[-_.]", seg)]
    return [t for t in tokens if len(t) >= 32]


def _read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scanned_files() -> list[Path]:
    files = []
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path in EXCLUDED_FILES:
                continue
            if EXCLUDED_DIRS.intersection(path.relative_to(root).parts):
                continue
            files.append(path)
    return sorted(files)


def test_no_key_patterns_in_tracked_files() -> None:
    found = []
    for path in _scanned_files():
        text = _read_text(path)
        if text is None:
            continue
        for line, reason in scan_text(text):
            found.append(f"{path.relative_to(config.REPO_ROOT)}:{line}: {reason}")
    assert not found, "possible secrets:\n" + "\n".join(found)


def test_scanner_catches_each_kind() -> None:
    random_tail = "Q7vK2mX9pL4sT8wR3nB6yH1cF5"
    samples = {
        "Anthropic key": "key = '" + "sk" + "-ant-api03-" + random_tail + "'",
        "LangSmith key": "LANGSMITH_API_KEY=" + "lsv2" + "_pt_" + random_tail,
        "Tavily key": "TAVILY_API_KEY=" + "tvly" + "-dev-" + random_tail,
        "auth header literal": '{"' + "x-api" + '-key": "' + "abcd1234efgh5678" + '"}',
        "auth header line": "Author" + "ization: Bearer " + "abcdefgh12345678ijkl",
        "high entropy token": "token: " + "aZ3kP9qW2eR7tY4u" + "I1oP6sD8fG5hJ0lX3cV",
    }
    in_query = "https://example.com/api?key=" + "aZ3kP9qW2eR7tY4u" + "I1oP6sD8fG5hJ0lX3cV"
    assert "high entropy token" in [r for _, r in scan_text(in_query)]
    for reason, line in samples.items():
        reasons = [r for _, r in scan_text(line)]
        assert reason in reasons, (reason, reasons)


def test_scanner_allows_prose_and_labeled_digests() -> None:
    b64_digest = "n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg="
    # A readable path that scores above the threshold when taken whole.
    readable_manual_url = (
        "https://maker.example/wp-content/uploads/2019/02/"
        + "AR-OWNERS-MANUAL-DOMESTIC-ENGLISH-" + "HU2-378100-2-REV-S-.pdf"
    )
    assert _is_secret_shaped(readable_manual_url.split("//", 1)[1], "")
    clean = "\n".join(
        [
            "Keys start with " + "sk" + "-ant- and are never written down.",
            'headers = {"' + "x-api" + '-key": api_key}',
            "text_sha256: " + "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            '"text_sha256": "' + b64_digest + '"',
            "test_no_key_patterns_in_tracked_files_under_agent_and_seed",
            "https://www.example.com/manuals/sundance-880-series-owners-manual-2005.pdf",
            readable_manual_url,
        ]
    )
    assert scan_text(clean) == []
    # The same base64 digest without a hash label is flagged.
    assert scan_text("value: " + b64_digest) != []
