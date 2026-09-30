"""Privacy diff for everything waiting in staging (PLAN section 8.10).

Run as `python -m agent.replay.privacy_diff`. It reads every JSON file under
config.STAGING_DIR (the cassettes and the SC1b payload file written by
build_cassettes), and:

- prints every copied string with the v1 JSON path it came from (from the
  copy logs the builder writes next to the staged files);
- scans every string, copied or synthetic, and every object key and long
  integer, against the local denylist (STAGING_DIR / "denylist.txt",
  gitignored, created as a commented template if absent) and against
  patterns for emails, phone numbers, street addresses, 5 digit numbers
  outside URLs, key prefixes, auth headers and high entropy tokens;
- strips tracking and session parameters from URLs in the staged files;
- rejects any referenced plate image, or any image in staging, that carries
  a JPEG APP1 (EXIF or XMP) segment;
- holds a cassette's recorded Jev answers ("safety_check") to their shape:
  hashes, a model name, a request ID and numbers, never step or page text;
- writes a readable report to STAGING_DIR / "privacy_diff.md".

It never writes into agent/tests/cassettes/: nothing leaves staging until
Roanuk approves the report. No regex can recognize a private name, so with an
empty denylist the result is INCOMPLETE, never CLEAN. Exit status 0 means a
filled denylist and no blocking hit.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agent import config
from agent.replay.cassettes import REQUEST_ID_RE, CassetteError, check_safety_check_entries

# Staging layout, shared with build_cassettes.
CASSETTES_SUBDIR = "cassettes"
COPY_LOG_SUBDIR = "copy_logs"
SC1B_FILE = "sc1b_payloads.json"
DENYLIST_FILE = "denylist.txt"
REPORT_FILE = "privacy_diff.md"
DEMO_ASSETS_DIR = config.REPO_ROOT / "demo-assets"  # read only

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\d\-])(?:\+?1[\s.\-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?![\d\-])")
# Ten digits with no separators, or eleven after a +1. Checked outside URLs,
# and never inside a longer run of letters and digits such as a hash.
BARE_PHONE_RE = re.compile(r"(?<![\w+.\-])(?:\+?1)?\d{10}(?![\w\-])")
STREET_SUFFIX = (
    "Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl"
    "|Terrace|Ter|Circle|Cir|Highway|Hwy|Parkway|Pkwy|Trail|Trl|Square|Sq"
)
# Suffixes long enough to match in any case as whole words. "way", "place"
# and the abbreviations stay capitalized only: "2 filters in place" is prose.
STREET_SUFFIX_ANY_CASE = (
    "street|avenue|road|boulevard|lane|drive|court|terrace|circle|highway|parkway|trail|square"
)
# A house number may carry a letter and a comma: "12B, Lakeview Dr".
HOUSE_NUMBER = r"\b\d{1,6}[A-Za-z]?,?\s+"
STREET_RE = re.compile(
    HOUSE_NUMBER + r"(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z']+\s+){1,3}(?:" + STREET_SUFFIX + r")\b\.?"
    r"|\b(?:P\.?\s?O\.?\s+Box)\s+\d+"
    r"|" + HOUSE_NUMBER + r"(?:[A-Za-z][A-Za-z']+\s+){1,3}(?i:" + STREET_SUFFIX_ANY_CASE + r")\b",
)
# A ZIP code stands alone. Digits inside a longer word, such as a random hex
# run ID ("t-3be90749debc4812"), are an identifier, not an address.
FIVE_DIGIT_RE = re.compile(r"(?<![\w.,])\d{5}(?:-\d{4})?(?![\w])")

# Key prefixes are split so this file never holds a key shaped string.
KEY_CHARS = r"[A-Za-z0-9_\-]"
KEY_PREFIXES = {
    "Anthropic key prefix": "sk" + "-ant-",
    "LangSmith key prefix": "lsv2" + "_",
    "Tavily key prefix": "tvly" + "-",
    # TypeSafe keys begin "apikey_" (checked against a real key's shape, not its value, on 29 September 2026).
    "TypeSafe key prefix": "apikey" + "_",
}
KEY_RES = {name: re.compile(re.escape(prefix) + KEY_CHARS + "{8,}") for name, prefix in KEY_PREFIXES.items()}
# Any mention of an auth header name in staged data fails closed.
HEADER_RE = re.compile(r"author" + r"ization|x-api" + r"-key", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-z0-9_+/=\-]{32,}")
ENTROPY_MIN_BITS = 4.3

TRACKING_EXACT = {
    "gclid", "fbclid", "msclkid", "dclid", "gbraid", "wbraid", "yclid", "twclid",
    "mc_cid", "mc_eid", "_ga", "_gl", "igshid", "si", "ref_src",
    "sid", "sessid", "sessionid", "session_id", "jsessionid", "phpsessid", "aspsessionid",
}
TRACKING_PREFIXES = ("utm_", "aspsessionid")
PATH_SESSION_RE = re.compile(r";(?:jsessionid|sessionid|sid)=[^/?#]*", re.IGNORECASE)
PRIVATE_HOST_RE = re.compile(r"^(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.0\.0\.0|\[?::1\]?)")

DASHES = (chr(0x2013), chr(0x2014))  # en and em dash

# JPEG markers, written in decimal because the config price scanner reads a
# hex literal holding an "e" as a float.
SOI = b"\xff\xd8"
MARKER_PREFIX = 255
APP1 = 225  # EXIF and XMP live here
SOS, EOI = 218, 217
RST0, RST7, TEM = 208, 215, 1


def shannon_entropy(token: str) -> float:
    counts = Counter(token)
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def is_secret_shaped(token: str) -> bool:
    """32+ characters with a letter and a digit at 4.3+ bits per character."""
    return (
        len(token) >= 32
        and re.search(r"[A-Za-z]", token) is not None
        and re.search(r"\d", token) is not None
        and shannon_entropy(token) >= ENTROPY_MIN_BITS
    )


def _entropy_candidates(text: str) -> list[str]:
    """Tokens to test for high entropy.

    Outside URLs, every long token. Inside a URL, query values and the
    fragment, plus path segments that are one opaque run: a readable path such
    as a PDF name built from hyphenated words scores above the threshold but
    is not a secret, while a key pasted into a query string still is.
    """
    tokens = [m.group() for m in TOKEN_RE.finditer(URL_RE.sub(" ", text))]
    for url in URL_RE.findall(text):
        parts = urlsplit(url)
        tokens += [value for _, value in parse_qsl(parts.query, keep_blank_values=True)]
        tokens.append(parts.fragment)
        tokens += [seg for seg in parts.path.split("/") if not re.search(r"[-_.]", seg)]
    return [t for t in tokens if len(t) >= 32]


DENYLIST_TEMPLATE = """\
# Privacy diff denylist (gitignored, local only). One entry per line, matched
# case insensitively anywhere in a staged string. Blank lines and lines
# starting with # are ignored. Fill it before approving a privacy diff: with
# no entries the result is INCOMPLETE, because no pattern can recognize a name.
#
# Add, for example: the property's name and town, the owner's and guests'
# names, the street, and the cleaner's or technician's names.
"""


def load_denylist(path: Path) -> list[str]:
    """Denylist entries, one per line; blank lines and # comments ignored."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DENYLIST_TEMPLATE, encoding="utf-8")
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line.lower())
    return entries


def scan_string(text: str, denylist: list[str] = ()) -> list[tuple[str, str]]:
    """Return (kind, matched text) for every privacy or key hit in one string."""
    hits: list[tuple[str, str]] = []
    lowered = text.lower()
    for entry in denylist:
        if entry in lowered:
            hits.append(("denylist", entry))
    hits += [("email", m.group()) for m in EMAIL_RE.finditer(text)]
    hits += [("phone", m.group()) for m in PHONE_RE.finditer(text)]
    hits += [("street address", m.group()) for m in STREET_RE.finditer(text)]
    outside_urls = URL_RE.sub(" ", text)
    hits += [("phone", m.group()) for m in BARE_PHONE_RE.finditer(outside_urls)]
    hits += [("5 digit number", m.group()) for m in FIVE_DIGIT_RE.finditer(outside_urls)]
    for name, pattern in KEY_RES.items():
        hits += [(name, m.group()[:12] + "...") for m in pattern.finditer(text)]
    hits += [("auth header name", m.group()) for m in HEADER_RE.finditer(text)]
    for token in _entropy_candidates(text):
        if is_secret_shaped(token):
            hits.append(("high entropy token", token[:12] + "..."))
    for url in URL_RE.findall(text):
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            hits.append(("non web URL", url))
        elif PRIVATE_HOST_RE.match(parts.hostname or ""):
            hits.append(("private or local host", url))
    if text.lower().startswith("file:"):
        hits.append(("non web URL", text[:40]))
    return hits


# A recorded Jev answer's request ID is TypeSafe's opaque identifier for the
# call: high entropy by design and no secret. Only that kind of hit, only at
# that path, only for one token of request ID characters, is exempt; a key
# prefix or anything else found there still blocks.
_REQUEST_ID_PATH = re.compile(r"^safety_check\[\d+\]\.request_id$")
EXEMPT_REQUEST_ID_KIND = "high entropy token"


def is_exempt_hit(path: str, kind: str, text: str) -> bool:
    """True for the one hit the diff does not count: a high entropy recorded request ID."""
    return (kind == EXEMPT_REQUEST_ID_KIND and _REQUEST_ID_PATH.match(path) is not None
            and REQUEST_ID_RE.match(text) is not None)


def scan_safety_check(data: Any) -> list[dict[str, str]]:
    """A hit when a cassette's safety_check list is not in its fixed, text free shape."""
    if not isinstance(data, dict) or "safety_check" not in data:
        return []
    try:
        check_safety_check_entries(data["safety_check"])
    except CassetteError as exc:
        return [{"path": "safety_check", "kind": "safety_check shape", "match": str(exc).split(": ", 1)[-1][:80]}]
    return []


def strip_tracking(url: str) -> tuple[str, list[str]]:
    """Remove tracking and session parameters; keep content ones like page=80."""
    parts = urlsplit(url)
    removed: list[str] = []
    path = parts.path
    for m in PATH_SESSION_RE.finditer(path):
        removed.append(m.group().lstrip(";").split("=")[0])
    path = PATH_SESSION_RE.sub("", path)
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        low = key.lower()
        if low in TRACKING_EXACT or low.startswith(TRACKING_PREFIXES):
            removed.append(key)
        else:
            kept.append((key, value))
    if not removed:
        return url, []
    query = urlencode(kept, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment)), removed


def jpeg_has_app1(data: bytes) -> bool:
    """True when a JPEG carries an APP1 segment (EXIF or XMP) before its scan data."""
    if not data.startswith(SOI):
        return False
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != MARKER_PREFIX:
            return False  # not a marker where one must be: stop, nothing found
        marker = data[pos + 1]
        if marker == MARKER_PREFIX:  # fill byte
            pos += 1
            continue
        if marker == APP1:
            return True
        if marker in (SOS, EOI):  # scan data follows, or the image ends
            return False
        if RST0 <= marker <= RST7 or marker == TEM:
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        pos += 2 + length
    return False


def iter_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield (json path, string) for every string value, depth first."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from iter_strings(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from iter_strings(item, f"{path}[{i}]")


# Integers under these keys are token counts, not addresses or phone numbers.
NUMERIC_EXEMPT_KEYS = ("usage",)
NUMERIC_EXEMPT_SUFFIX = "_tokens"
LONG_INT_MIN = 10_000  # five digits or more


def _numeric_exempt(path: str) -> bool:
    keys = [key for key, _ in _PATH_TOKEN.findall(path) if key]
    return any(k in NUMERIC_EXEMPT_KEYS for k in keys) or bool(keys and keys[-1].endswith(NUMERIC_EXEMPT_SUFFIX))


def scan_keys_and_numbers(value: Any, denylist: list[str] = (), path: str = "") -> list[dict[str, str]]:
    """Hits in what iter_strings skips: object keys, and integers of 5+ digits.

    A ZIP stored as the integer 90210 or a name used as a key would otherwise
    never be read. Token counts under usage are exempt.
    """
    hits: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            kpath = f"{path}.{key}" if path else str(key)
            for kind, match in scan_string(str(key), denylist):
                hits.append({"path": f"{kpath} (key)", "kind": kind, "match": match})
            hits += scan_keys_and_numbers(item, denylist, kpath)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            hits += scan_keys_and_numbers(item, denylist, f"{path}[{i}]")
    elif isinstance(value, int) and not isinstance(value, bool):
        if abs(value) >= LONG_INT_MIN and not _numeric_exempt(path):
            hits.append({"path": path, "kind": "number of 5 or more digits", "match": str(value)})
    return hits


_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def get_path(data: Any, path: str) -> Any:
    """Read a value by a path like 'synthesize[0].draft.try_first[1].step'."""
    node = data
    for key, index in _PATH_TOKEN.findall(path):
        node = node[int(index)] if index else node[key]
    return node


def set_path(data: Any, path: str, value: Any) -> None:
    tokens = _PATH_TOKEN.findall(path)
    node = data
    for key, index in tokens[:-1]:
        node = node[int(index)] if index else node[key]
    key, index = tokens[-1]
    if index:
        node[int(index)] = value
    else:
        node[key] = value


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------


@dataclass
class FileResult:
    name: str
    copies: list[dict[str, Any]] = field(default_factory=list)
    hashed: list[dict[str, Any]] = field(default_factory=list)
    strings: int = 0
    hits: list[dict[str, str]] = field(default_factory=list)
    stripped: list[dict[str, Any]] = field(default_factory=list)
    dash_notes: list[str] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)


def _demo_hashes(demo_dir: Path) -> dict[str, Path]:
    out = {}
    if demo_dir.is_dir():
        for path in sorted(demo_dir.iterdir()):
            if path.is_file():
                out[hashlib.sha256(path.read_bytes()).hexdigest()] = path
    return out


def _check_image(path: Path, result: FileResult, label: str) -> None:
    data = path.read_bytes()
    if jpeg_has_app1(data):
        result.hits.append({"path": label, "kind": "EXIF or APP1 segment", "match": path.name})
        result.images.append({"ref": label, "file": path.name, "verdict": "rejected: APP1 segment"})
    else:
        result.images.append({"ref": label, "file": path.name, "verdict": "no APP1 segment"})


def diff_file(path: Path, staging: Path, denylist: list[str], demo_hashes: dict[str, Path]) -> FileResult:
    """Scan one staged JSON file, strip tracking parameters in place, and report."""
    rel = path.relative_to(staging).as_posix()
    result = FileResult(name=rel)
    data = json.loads(path.read_text(encoding="utf-8"))
    log_path = staging / COPY_LOG_SUBDIR / (path.stem + ".copies.json")
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {"copies": [], "hashed": []}
    result.copies = log.get("copies", [])
    result.hashed = log.get("hashed", [])
    if not log_path.exists():
        result.hits.append({"path": "", "kind": "missing copy log", "match": log_path.name})

    changed = False
    for spath, text in list(iter_strings(data)):
        result.strings += 1
        if URL_RE.fullmatch(text):
            clean, removed = strip_tracking(text)
            if removed:
                set_path(data, spath, clean)
                changed = True
                result.stripped.append({"path": spath, "removed": removed, "url": clean})
                for entry in result.copies:
                    if entry.get("dest") == spath:
                        entry["value"] = clean
                text = clean
        for kind, match in scan_string(text, denylist):
            if not is_exempt_hit(spath, kind, text):
                result.hits.append({"path": spath, "kind": kind, "match": match})
        if any(d in text for d in DASHES):
            result.dash_notes.append(spath)
    result.hits += scan_keys_and_numbers(data, denylist)
    result.hits += scan_safety_check(data)

    # The copy log must describe the file exactly.
    for entry in result.copies:
        try:
            actual = get_path(data, entry["dest"])
        except (KeyError, IndexError, TypeError, ValueError):
            actual = "<missing>"
        if actual != entry.get("value"):
            result.hits.append({"path": entry["dest"], "kind": "copy log mismatch", "match": str(actual)[:60]})

    plate = data.get("input", {}).get("plate_sha256") if isinstance(data, dict) else None
    if plate:
        match = demo_hashes.get(plate)
        if match is None:
            result.hits.append({"path": "input.plate_sha256", "kind": "unresolved image", "match": plate})
        else:
            _check_image(match, result, "input.plate_sha256")

    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if log_path.exists():
            log["copies"] = result.copies
            log_path.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    return result


def git_status() -> str:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "agent/tests/cassettes", "agent/tests/fixtures"],
            cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git status unavailable: {exc}"
    return out.stdout.strip() or "(no changes under agent/tests/cassettes or agent/tests/fixtures)"


def _md(text: str) -> str:
    return text.replace("`", "'").replace("\n", " ")


def verdict(total_hits: int, denylist_size: int) -> str:
    """BLOCKED on any hit; INCOMPLETE when no private name was looked for."""
    if total_hits:
        return "BLOCKED"
    if denylist_size == 0:
        return "INCOMPLETE: denylist empty, private names not checked"
    return "CLEAN"


def write_report(results: list[FileResult], staging: Path, denylist_size: int,
                 status_text: str = "") -> str:
    total_hits = sum(len(r.hits) for r in results)
    lines = [
        "# Privacy diff",
        "",
        f"Staging folder: `{staging}`. Nothing here is in the working tree until Roanuk approves.",
        "",
        "## Summary",
        "",
        f"- Files scanned: {len(results)}",
        f"- Copied strings (listed below with their source path): {sum(len(r.copies) for r in results)}",
        f"- Strings scanned in all (copied and synthetic): {sum(r.strings for r in results)}",
        f"- Denylist entries: {denylist_size}",
        f"- URLs with tracking or session parameters stripped: {sum(len(r.stripped) for r in results)}",
        f"- Images checked: {sum(len(r.images) for r in results)}",
        f"- Strings holding an en or em dash (style note, not a privacy hit): "
        f"{sum(len(r.dash_notes) for r in results)}",
        f"- Blocking hits: {total_hits}",
        f"- Result: {verdict(total_hits, denylist_size)}",
        "",
        "## Working tree",
        "",
        "```",
        status_text,
        "```",
        "",
    ]
    for r in results:
        lines += [f"## {r.name}", ""]
        lines.append(f"Hits: {len(r.hits)}. Copied strings: {len(r.copies)}. Strings scanned: {r.strings}.")
        lines.append("")
        if r.hits:
            lines.append("### Hits")
            lines += [f"- `{_md(h['path'])}`: {h['kind']}: `{_md(h['match'])}`" for h in r.hits]
            lines.append("")
        if r.stripped:
            lines.append("### Tracking parameters stripped")
            lines += [f"- `{s['path']}`: removed {', '.join(s['removed'])}" for s in r.stripped]
            lines.append("")
        if r.images:
            lines.append("### Images")
            lines += [f"- `{i['ref']}` = {i['file']}: {i['verdict']}" for i in r.images]
            lines.append("")
        if r.hashed:
            lines.append("### Read to hash only, never copied")
            lines += [f"- {h['source']}: sha256 {h['sha256']} ({h.get('matches', 'no match')})" for h in r.hashed]
            lines.append("")
        if r.dash_notes:
            lines.append("### Strings with an en or em dash (verbatim v1 model text)")
            lines += [f"- `{p}`" for p in r.dash_notes]
            lines.append("")
        lines.append("### Copied fields")
        if not r.copies:
            lines.append("- none")
        for c in r.copies:
            value = c.get("value")
            shown = _md(value) if isinstance(value, str) else json.dumps(value)
            lines.append(f"- `{c['dest']}` from {c['source']}: {shown}")
        lines.append("")
    report = "\n".join(lines)
    (staging / REPORT_FILE).write_text(report, encoding="utf-8")
    return report


def staged_json_files(staging: Path) -> list[Path]:
    files = sorted((staging / CASSETTES_SUBDIR).glob("*.json")) if (staging / CASSETTES_SUBDIR).is_dir() else []
    sc1b = staging / SC1B_FILE
    if sc1b.exists():
        files.append(sc1b)
    return files


def run(
    staging: Path = config.STAGING_DIR,
    demo_dir: Path = DEMO_ASSETS_DIR,
    *,
    status: Callable[[], str] = git_status,
) -> tuple[list[FileResult], str]:
    """Diff every staged file and write the report. Returns the results and report text.

    `status` supplies the working tree section; tests pass a stub so they
    start no child process outside spawn_offline_child (PLAN 8.14).
    """
    if staging.resolve().is_relative_to((config.REPO_ROOT / "agent" / "tests").resolve()):
        raise ValueError("the privacy diff runs on staging, never inside agent/tests")
    staging.mkdir(parents=True, exist_ok=True)
    denylist = load_denylist(staging / DENYLIST_FILE)
    hashes = _demo_hashes(demo_dir)
    results = [diff_file(p, staging, denylist, hashes) for p in staged_json_files(staging)]
    # Any image sitting in staging is checked too.
    loose = FileResult(name="(images in staging)")
    for image in sorted(staging.rglob("*")):
        if image.suffix.lower() in (".jpg", ".jpeg"):
            _check_image(image, loose, image.relative_to(staging).as_posix())
    if loose.images:
        results.append(loose)
    return results, write_report(results, staging, len(denylist), status())


def main(argv: list[str] | None = None) -> int:
    results, _ = run()
    for r in results:
        print(f"== {r.name}")
        for c in r.copies:
            value = c.get("value")
            shown = value if isinstance(value, str) else json.dumps(value)
            print(f"  {c['dest']} <- {c['source']}: {shown}")
        for h in r.hits:
            print(f"  HIT {h['kind']} at {h['path']}: {h['match']}")
    hits = sum(len(r.hits) for r in results)
    result = verdict(hits, len(load_denylist(config.STAGING_DIR / DENYLIST_FILE)))
    print(f"\n{len(results)} file(s), {hits} blocking hit(s). Result: {result}. "
          f"Report: {config.STAGING_DIR / REPORT_FILE}")
    return 0 if result == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
