"""The repository holds no key, and .gitignore keeps the key files out (decision 107).

Anyone can clone this repository and connect a copy of the MCP server of their
own, so no key of mine may ever be committed. The scanner below reads every
file `git ls-files` lists, tracked or new and not ignored (what `git add -A`
would commit), and fails on any string shaped like a key: Anthropic, Tavily,
TypeSafe (its keys begin "apikey_", the shape agent/replay/privacy_diff.py
checks), LangSmith, GitHub, AWS, Google and Slack keys, a private key block,
an API key, secret, token or password field holding a long value, and a bearer
token. A key that begins with a fixed prefix (Anthropic, Tavily, TypeSafe,
LangSmith) is also found split across two lines, as prose wrapped at a hyphen
or a string literal broken in two leaves it, and inside base64 text, standard
or URL safe, as in a recorded request body or a Basic authorization header. A
hit is reported by file, line and kind, never by its text. It never opens an
ignored file, so it never reads .env. `git check-ignore` then confirms that
the repository's own .gitignore, with no other ignore rules, ignores .env and
.env.local and not .env.example.

test_privacy.py already scans agent/ and seed/ with shorter prefixes and an
entropy check, which also catch these split and base64 shapes there; the two
passes here extend that to every other file git lists, such as README.md,
docs/, index.html, tool.html, briefs/ and src/.

Every key prefix below is split, and every sample is built at run time, so no
tracked file, this one and mutations.toml included, holds a key shaped
string. The scan of the repository skips cleanly when git or the .git folder
is unavailable (a downloaded archive); mutate.py's copies leave .git out and
name the repository's own .git in ADVISOR_GIT_DIR, which is only read.

Offline. Each test names the planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent import config
from agent.tests.helpers import spawn_offline_child

ROOT = config.REPO_ROOT
# Set by mutate.py for its copies, which leave .git out: the repository's own .git, read only.
ENV_GIT_DIR = "ADVISOR_GIT_DIR"
SKIP_NO_GIT = "git or the repository's .git folder is unavailable"

# What may follow a key prefix, and what a field or bearer value is made of. A
# value must hold a digit and a letter, so a field set to a code name such as
# os.environ or session.secrets is not a hit.
KEY_CHARS = r"[A-Za-z0-9_\-]"
VALUE = r"(?=[A-Za-z0-9_\-+/]*\d)(?=[A-Za-z0-9_\-+/]*[A-Za-z])[A-Za-z0-9_\-+/]{20,}=*"
FIELD_NAME = r"(?:api[_\-]?key|secret|token|passw(?:or)?d)"
KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    "Anthropic key": re.compile(re.escape("sk" + "-ant-") + KEY_CHARS + "{16,}"),
    "Tavily key": re.compile(re.escape("tvly" + "-") + KEY_CHARS + "{16,}"),
    "TypeSafe key": re.compile(re.escape("apikey" + "_") + KEY_CHARS + "{16,}"),
    "LangSmith key": re.compile(re.escape("lsv2" + "_") + r"(?:pt|sk)_" + KEY_CHARS + "{16,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]" + "_" + r"[A-Za-z0-9]{30,}|github" + "_pat_" + r"[A-Za-z0-9_]{30,})"),
    "AWS key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
                          r"|(?i:aws_secret_access" + "_key" + r")[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}"),
    "Google key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    "Slack token": re.compile(r"\bxox[abposr]-[0-9A-Za-z\-]{10,}"),
    "Private key block": re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE" + r" KEY-----"),
    "API key field": re.compile(r"(?i:\b[A-Za-z0-9_\-]*" + FIELD_NAME + r"[A-Za-z0-9_\-]*)[\"']?[ \t]*[:=][ \t]*[\"']?"
                                + VALUE),
    "Bearer token": re.compile(r"(?i:\bbearer)[ \t]+" + VALUE),
}
# Kinds whose keys begin with a fixed prefix: these are also looked for across a line break and inside base64.
PREFIXED_KINDS = ("Anthropic key", "Tavily key", "TypeSafe key", "LangSmith key")
# What a break inside a key leaves around it: spaces, quotes, a plus, a backslash or an opening parenthesis at the
# end of the first line, and spaces, quotes or a plus at the start of the second.
SPLIT_END = " \t\"'+\\("
SPLIT_START = " \t\"'+"
SPLIT = " (split across two lines)"
BASE64 = " (base64)"
BASE64_RUN = re.compile(r"[A-Za-z0-9+/_\-]{40,}={0,2}")


@dataclass(frozen=True)
class Hit:
    """One key shaped string: where it is and what kind, never its text."""

    path: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}"


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, kind) for every key shaped string in text: on one line, and for a prefixed kind, also split
    across two lines (reported at the first) or inside base64 text."""
    lines = text.splitlines()
    found = []
    for number, line in enumerate(lines, start=1):
        for kind, pattern in KEY_PATTERNS.items():
            if pattern.search(line):
                found.append((number, kind))
    found += split_hits(lines, found)
    found += base64_hits(lines)
    return found


def split_hits(lines: list[str], found: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """A prefixed key that neither of two adjacent lines holds alone but the two do when joined, without the break
    and the quotes or plus around it; reported at the first line."""
    seen = set(found)
    hits = []
    for number, (first, second) in enumerate(zip(lines, lines[1:], strict=False), start=1):
        joined = first.rstrip(SPLIT_END) + second.lstrip(SPLIT_START)
        for kind in PREFIXED_KINDS:
            if (number, kind) in seen or (number + 1, kind) in seen:
                continue
            if KEY_PATTERNS[kind].search(joined):
                hits.append((number, kind + SPLIT))
    return hits


def base64_hits(lines: list[str]) -> list[tuple[int, str]]:
    """A prefixed key inside a run of base64 text on one line, decoded both as standard and as URL safe base64."""
    hits = []
    for number, line in enumerate(lines, start=1):
        kinds: set[str] = set()
        for run in BASE64_RUN.findall(line):
            padded = run + "=" * (-len(run) % 4)
            for decode in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    decoded = decode(padded).decode("utf-8", errors="replace")
                except (binascii.Error, ValueError):
                    continue
                kinds.update(kind for kind in PREFIXED_KINDS if KEY_PATTERNS[kind].search(decoded))
        hits += [(number, kind + BASE64) for kind in sorted(kinds)]
    return hits


def scan_files(root: Path, names: Iterable[str]) -> list[Hit]:
    """Hits in each named file under root, read as bytes, so a key inside any file type is found."""
    hits = []
    for name in names:
        path = root / name
        if not path.is_file():  # listed by the index but deleted in the working tree
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        hits += [Hit(name, number, kind) for number, kind in scan_text(text)]
    return hits


def git_env(*, isolated: bool = False) -> dict[str, str | None]:
    """The changes to a git child's environment: every inherited GIT_ variable unset, and with isolated, no global
    or system config either."""
    env: dict[str, str | None] = {k: None for k in os.environ if k.startswith("GIT_")}
    if isolated:
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def run_git(args: list[str], env: dict[str, str | None] | None = None) -> tuple[int, str, str]:
    """One git call through the shared child launcher: its exit code, output and error output."""
    proc = spawn_offline_child(args, kind="git", env_extra=env if env is not None else git_env())
    return proc.returncode, proc.stdout, proc.stderr


def git_command(root: Path) -> list[str] | None:
    """The git arguments that read root's repository, or None when git or its .git folder is unavailable."""
    if shutil.which("git") is None:
        return None
    base = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
    if (root / ".git").exists():
        return [*base, "-C", str(root)]
    named = os.environ.get(ENV_GIT_DIR)
    if named and Path(named).is_dir():
        return [*base, f"--git-dir={named}", f"--work-tree={root}", "-C", str(root)]
    return None


def listed_files(git: list[str], env: dict[str, str | None] | None = None) -> list[str]:
    """Every file git would commit: tracked, or new and not ignored."""
    code, out, err = run_git([*git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], env)
    if code != 0:
        raise RuntimeError(f"git ls-files failed: {err[-300:]}")
    return sorted({name for name in out.split("\0") if name})


def is_ignored(git: list[str], name: str, env: dict[str, str | None] | None = None) -> bool:
    """True when git's ignore rules ignore name, whether or not it is tracked."""
    code, _, err = run_git([*git, "check-ignore", "--no-index", "-q", name], env)
    if code not in (0, 1):
        raise RuntimeError(f"git check-ignore failed for {name}: {err[-300:]}")
    return code == 0


def is_secret_file(name: str) -> bool:
    """A .env file of any kind but the example, at any depth."""
    base = name.rsplit("/", 1)[-1]
    return base != ".env.example" and (base == ".env" or base.startswith(".env."))


def temp_repo(path: Path) -> list[str]:
    """An empty repository at path, with no global or system git config, and the command that reads it."""
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    path.mkdir(parents=True, exist_ok=True)
    code, _, err = run_git(["init", "-q", str(path)], git_env(isolated=True))
    assert code == 0, err
    return ["-C", str(path)]


def tail(seed: str, length: int = 40) -> str:
    """Key characters built at run time from a seed (lowercase hex), so no source file holds a key."""
    return hashlib.sha256(seed.encode()).hexdigest()[:length]


def key_samples() -> dict[str, list[str]]:
    """One or more strings of each key shape, built at run time; every prefix is split."""
    t = tail("sample")
    return {
        "Anthropic key": ["key " + "sk" + "-ant-" + "api03-" + tail("anthropic")],
        "Tavily key": ["tvly" + "-dev-" + tail("tavily")],
        "TypeSafe key": ["apikey" + "_" + tail("typesafe")],
        "LangSmith key": ["lsv2" + "_pt_" + tail("langsmith")],
        "GitHub token": ["gh" + "p_" + tail("github", 36), "github" + "_pat_" + tail("github pat", 60)],
        "AWS key": ["AK" + "IA" + tail("aws").upper()[:16],
                    "aws_secret_access" + "_key = " + (tail("aws secret") + tail("aws secret 2"))[:40]],
        "Google key": ["AI" + "za" + tail("google", 35)],
        "Slack token": ["xo" + "xb-" + tail("slack", 24)],
        "Private key block": ["-----BEGIN " + "RSA PRIVATE" + " KEY-----"],
        "API key field": ["api" + "_key = '" + t + "'",
                          "TYPESAFE" + "_API_KEY=" + t,
                          '{"x-api' + '-key": "' + t + '"}',
                          "pass" + "word: " + t,
                          "client" + "_secret=" + t,
                          "export GITHUB" + "_TOKEN=" + t],
        "Bearer token": ["Author" + "ization: Bear" + "er " + t],
    }


# Text that names keys without holding one: none of it may be a hit.
CLEAN = [
    "ANTHROPIC" + "_API_KEY=",
    "TAVILY" + "_API_KEY={FAKE_TAVILY}",
    "api" + '_key=os.environ["TAVILY' + '_API_KEY"]',
    "secrets = session.secrets_for_redaction",
    "Author" + "ization: Bearer something",
    '"max_tokens": 16000',
    "sk" + "-ant-",
    "tvly" + "-",
    "sha256 " + tail("digest", 64),
    "set TYPESAFE" + "_API_KEY in the copy's .env, never in an app's settings file",
]


def split_and_encoded_samples() -> dict[str, tuple[str, int, str]]:
    """Prefixed keys that no single line holds whole, built at run time: name -> (text, line, kind expected)."""
    anthropic = "sk" + "-ant-" + "api03-" + tail("anthropic split", 48)
    tavily = "tvly" + "-dev-" + tail("tavily split")
    typesafe_tail = tail("typesafe split")
    langsmith_tail = tail("langsmith split")

    def b64(text: str, *, urlsafe: bool = False) -> str:
        encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
        return encode(text.encode()).decode()

    return {
        "prose wrapped at a hyphen": (
            textwrap.fill("Paste the key " + anthropic + " into the clone's own file, never into a settings file.",
                          width=60), 1, "Anthropic key" + SPLIT),
        "implicit string concatenation": (
            'KEY = (\n    "' + tavily[:16] + '"\n    "' + tavily[16:] + '"\n)', 2, "Tavily key" + SPLIT),
        "split just after the prefix, joined with a plus": (
            'key = "' + "apikey" + "_" + '" + \\\n    "' + typesafe_tail + '"', 1, "TypeSafe key" + SPLIT),
        "split just after the prefix, two quoted lines": (
            '"' + "lsv2" + "_pt_" + '"\n"' + langsmith_tail + '"', 1, "LangSmith key" + SPLIT),
        "base64 of a recorded JSON body": (
            json.dumps({"body_b64": b64(json.dumps({"q": "x", "auth": tavily}))}), 1, "Tavily key" + BASE64),
        "a Basic authorization header": (
            "Author" + "ization: Basic " + b64("user:" + "apikey" + "_" + typesafe_tail), 1, "TypeSafe key" + BASE64),
        "URL safe base64 with its padding cut": (
            "body=" + b64("header: " + anthropic + "?>", urlsafe=True).rstrip("="), 1, "Anthropic key" + BASE64),
    }


# Text near a prefix that holds no key, split or encoded: none of it may be a hit.
CLEAN_SPLIT = [
    "Anthropic keys begin " + "sk" + "-ant-\nand Tavily keys begin " + "tvly" + "-.",
    "The prefix is " + '"' + "apikey" + "_" + '"\n"and then the rest of the key."',
    base64.b64encode(("sha256 " + tail("digest", 64)).encode()).decode(),
    base64.b64encode(b"the key names " + ("sk" + "-ant-").encode() + b" and nothing after it").decode(),
]


# ---------------------------------------------------------------------------
# The scanner, on samples built at run time
# ---------------------------------------------------------------------------


def test_scanner_flags_every_key_shape_by_line_and_kind_never_by_text() -> None:
    """Each sample is found as its own kind, on its own line, and the result never carries its text; text that
    only names keys is clean. Mutations: no_keys_drops_anthropic_pattern, no_keys_drops_tavily_pattern,
    no_keys_drops_typesafe_pattern, no_keys_drops_github_pattern, no_keys_drops_aws_pattern,
    no_keys_drops_api_key_field_pattern and no_keys_drops_bearer_pattern (that kind's sample comes back clean);
    no_keys_hit_carries_the_text (a hit reports the key it found)."""
    for kind, samples in key_samples().items():
        for sample in samples:
            found = scan_text("first line\n\n" + sample + "\nlast line")
            assert (3, kind) in found, (kind, found)
            assert sample not in repr(found) and sample[-12:] not in repr(found), kind
    for text in CLEAN:
        assert scan_text(text) == [], text
    assert set(key_samples()) == set(KEY_PATTERNS)


def test_scanner_finds_a_prefixed_key_split_across_two_lines_or_inside_base64() -> None:
    """A prefixed key that no single line holds whole is still found: prose wrapped at a hyphen, a string literal
    broken in two (by implicit concatenation, or just after the prefix with or without a plus), base64 of a recorded
    JSON body, a Basic authorization header and URL safe base64 with its padding cut; each is reported at its line
    with its kind and how it was hidden, never its text. Text that only names a prefix near a break, and base64 of
    text without a key, is clean. Mutations: no_keys_ignores_split_lines (the two line pass sees no lines);
    no_keys_ignores_base64 (the base64 pass sees no lines)."""
    for name, (text, line, kind) in split_and_encoded_samples().items():
        base_kind = kind.removesuffix(SPLIT).removesuffix(BASE64)
        assert not any(KEY_PATTERNS[base_kind].search(one) for one in text.splitlines()), f"{name}: not hidden"
        found = scan_text(text)
        assert (line, kind) in found, (name, found)
        assert all(text_line not in repr(found) for text_line in text.splitlines() if len(text_line) > 8), name
    for text in CLEAN_SPLIT:
        assert scan_text(text) == [], text


def test_scan_reads_every_file_git_would_commit_and_never_an_ignored_one(tmp_path: Path) -> None:
    """In a new repository: a key in a tracked file and in a new, unignored file is found; the ignored .env
    beside them, holding a key too, is never read. Mutations: no_keys_scans_no_files (nothing is listed);
    no_keys_scan_skips_new_files (only tracked files are read); no_keys_scan_reads_ignored_files (the ignored .env
    is read too)."""
    git = temp_repo(tmp_path / "repo")
    repo = tmp_path / "repo"
    env = git_env(isolated=True)
    key = key_samples()["Anthropic key"][0]
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("notes\n" + key + "\n", encoding="utf-8")
    (repo / "new.md").write_text(key + "\n", encoding="utf-8")
    (repo / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    (repo / ".env").write_text("ANTHROPIC" + "_API_KEY=" + key + "\n", encoding="utf-8")
    code, _, err = run_git([*git, "add", "tracked.txt", "clean.txt", ".gitignore"], env)
    assert code == 0, err
    names = listed_files(git, env)
    assert names == [".gitignore", "clean.txt", "new.md", "tracked.txt"]
    hits = scan_files(repo, names)
    assert {str(h) for h in hits} == {"tracked.txt:2: Anthropic key", "new.md:1: Anthropic key"}


def test_the_repository_gitignore_keeps_env_files_out_and_the_example_in(tmp_path: Path) -> None:
    """The repository's own .gitignore, alone in a new repository with no global, system or local ignore rules,
    ignores .env and .env.local (and any other .env file, at any depth) and not .env.example, by git
    check-ignore. Mutations: no_keys_ignore_check_always_true (the check answers ignored for any name);
    gitignore_drops_env (.env is no longer ignored); gitignore_ignores_env_example (the example is ignored, so it
    would drop out of the repository)."""
    git = temp_repo(tmp_path / "repo")
    env = git_env(isolated=True)
    shutil.copyfile(ROOT / ".gitignore", tmp_path / "repo" / ".gitignore")
    for name in (".env", ".env.local", ".env.production", "agent/.env"):
        assert is_ignored(git, name, env), f".gitignore does not ignore {name}"
    assert not is_ignored(git, ".env.example", env), ".gitignore ignores .env.example"
    assert not is_ignored(git, "README.md", env)


# ---------------------------------------------------------------------------
# The repository itself
# ---------------------------------------------------------------------------


def test_no_file_git_lists_holds_a_key_shaped_string() -> None:
    """Every file git would commit from this checkout, tracked or new and not ignored, is free of key shaped
    strings, and none is a .env file but the example. Skips when git or the .git folder is unavailable.
    Mutation: no_keys_scans_no_files (the listing is empty, so nothing is read)."""
    git = git_command(ROOT)
    if git is None:
        pytest.skip(SKIP_NO_GIT)
    names = listed_files(git)
    for expected in ("README.md", ".gitignore", ".env.example", "agent/mcp_server.py", "agent/tests/mutations.toml",
                     "agent/tests/test_no_keys.py", "index.html"):
        assert expected in names, f"git does not list {expected}"
    assert [name for name in names if is_secret_file(name)] == []
    hits = scan_files(ROOT, names)
    assert hits == [], "key shaped strings in files git would commit:\n" + "\n".join(str(h) for h in hits)
