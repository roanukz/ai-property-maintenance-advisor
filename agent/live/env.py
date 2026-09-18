"""The .env loader for live commands (PRD Part A rules 1 and 8, PLAN 8.13).

Live commands (`advisor ask --live`, `advisor resume --live`, `advisor
check-schema --live`) read ANTHROPIC_API_KEY and TAVILY_API_KEY, and
LANGSMITH_* only when ADVISOR_TRACING=1, from the environment or from the
repo's gitignored .env (config.ENV_FILE). Replay never calls anything here.

Rules, all tested in agent/tests/test_env_loader.py:

- Format: one NAME=value per line; blank lines and lines starting with # are
  skipped; a leading "export " is allowed; a value in matching single or
  double quotes is taken verbatim between them (a # inside stays); an
  unquoted value ends at " #" (an inline comment).
- Only the names above are read. Any other line in .env is ignored, so a
  stray variable there can never reach the process.
- Precedence: a name already present in the environment is never overridden
  by .env, even when it is set to an empty string (it then counts as
  missing). This is the documented behavior; there is no override switch.
- Values never leave this module except into the environment the clients
  read. Reports carry names and where each came from, never values. A
  malformed line is reported by its line number only.
- `keys_in_environ` puts the loaded values in the environment for one
  command and takes them out afterwards; `RedactingStream` replaces any
  loaded value in printed text with "[redacted]".
"""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from agent import config

REDACTED = "[redacted]"
SOURCE_ENVIRONMENT = "environment"
SOURCE_DOTENV = ".env"
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPORT = "export "
_INLINE_COMMENT = " #"


class DotenvError(ValueError):
    """A .env line is not NAME=value. The message names the line number, never its text."""


@dataclass
class KeyReport:
    """Which wanted names were found and where; never the values."""

    sources: dict[str, str] = field(default_factory=dict)  # name -> "environment" or ".env"
    missing: list[str] = field(default_factory=list)
    ignored_dotenv_names: list[str] = field(default_factory=list)  # present in .env, not read


def _unquote(value: str) -> str:
    """The text between matching quotes (anything after the closing quote is dropped), or
    the unquoted value up to an inline comment."""
    quote = value[:1]
    if quote in ("'", '"'):
        end = value.find(quote, 1)
        if end > 0:
            return value[1:end]
        value = value[1:]  # an opening quote with no close: keep the text, drop the quote
    return value.split(_INLINE_COMMENT, 1)[0].rstrip()


def parse_dotenv(text: str, source: str = ".env") -> dict[str, str]:
    """Every NAME=value in a .env text, with comments, quotes and export handled."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(_EXPORT):
            line = line[len(_EXPORT):].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _NAME_RE.match(name):
            raise DotenvError(f"{source}: line {number} is not NAME=value")
        values[name] = _unquote(value.strip())
    return values


def read_dotenv(path: Path) -> dict[str, str]:
    """parse_dotenv of a file; a missing file is an empty mapping."""
    path = Path(path)
    if not path.is_file():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"), str(path))


def wanted_names(found: Sequence[str], *, tracing: bool) -> list[str]:
    """The names a live command reads: the two keys, plus LANGSMITH_* when tracing is opted in."""
    names = list(config.LIVE_KEY_NAMES)
    if tracing:
        names += sorted({n for n in found if n.startswith(config.TRACING_KEY_PREFIX)} - set(names))
    return names


def load_live_keys(
    environ: MutableMapping[str, str],
    path: Path,
    *,
    need: Sequence[str],
    tracing: bool,
) -> tuple[KeyReport, dict[str, str]]:
    """Find the wanted keys; return the report and the values .env adds to the environment.

    Nothing is written to `environ` here: `keys_in_environ` applies the
    returned values for the length of one command. A name already present
    in `environ` wins over .env, even when empty.
    """
    from_file = read_dotenv(path)
    names = wanted_names([*environ, *from_file], tracing=tracing)
    report = KeyReport()
    additions: dict[str, str] = {}
    for name in names:
        if name in environ:
            report.sources[name] = SOURCE_ENVIRONMENT
        elif name in from_file:
            additions[name] = from_file[name]
            report.sources[name] = SOURCE_DOTENV
    effective = {n: environ.get(n, "") for n, src in report.sources.items() if src == SOURCE_ENVIRONMENT}
    effective.update(additions)
    report.missing = [n for n in need if not effective.get(n)]
    report.ignored_dotenv_names = sorted(set(from_file) - set(names))
    return report, additions


def secret_values(environ: Mapping[str, str], additions: Mapping[str, str], names: Sequence[str]) -> list[str]:
    """Every loaded key value, for redaction; empty values are skipped."""
    values = {environ.get(n, "") for n in names} | set(additions.values())
    return sorted((v for v in values if v), key=len, reverse=True)


def redact(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret value in text."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def redact_value(value: Any, secrets: Sequence[str]) -> Any:
    """redact applied to every string in a JSON native value, keys included."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [redact_value(v, secrets) for v in value]
    if isinstance(value, dict):
        return {redact(str(k), secrets): redact_value(v, secrets) for k, v in value.items()}
    return value


def scrub_files(paths: Sequence[Path], secrets: Sequence[str]) -> list[Path]:
    """Rewrite any of these text files that holds a secret value, redacted; return the ones changed.

    A backstop for text a client library might echo into an error message
    that a run then logs (the lookup log keeps Tavily's error text verbatim).
    """
    changed = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        clean = redact(text, secrets)
        if clean != text:
            path.write_text(clean, encoding="utf-8")
            changed.append(path)
    return changed


def _mask(secret: str) -> bytes:
    """A same length stand in: a checkpoint blob is msgpack, so its string lengths must not change."""
    return b"*" * len(secret.encode("utf-8"))


def scrub_sqlite(path: Path, secrets: Sequence[str]) -> bool:
    """Mask every secret value in a sqlite file's text and blob cells; True if one was found.

    A backstop for the checkpoint database behind the source redaction in
    agent/redaction.py. Each hit is overwritten with asterisks of the same
    byte length (so msgpack blobs stay readable), then the WAL is folded back
    and the file vacuumed, so no page keeps the old bytes.
    """
    import sqlite3

    path = Path(path)
    wanted = [s.encode("utf-8") for s in secrets if s]
    files = [path, Path(f"{path}-wal")]
    if not wanted or not path.is_file():
        return False
    if not any(w in f.read_bytes() for f in files if f.is_file() for w in wanted):
        return False
    conn = sqlite3.connect(path, timeout=30)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            quoted = ", ".join(f'"{c}"' for c in cols)
            try:
                rows = conn.execute(f'SELECT rowid, {quoted} FROM "{table}"').fetchall()
            except sqlite3.OperationalError:  # a WITHOUT ROWID table; the checkpointer has none
                continue
            for rowid, *values in rows:
                for col, value in zip(cols, values, strict=True):
                    raw = value.encode("utf-8") if isinstance(value, str) else value
                    if not isinstance(raw, bytes) or not any(w in raw for w in wanted):
                        continue
                    for w in wanted:
                        raw = raw.replace(w, _mask(w.decode("utf-8")))
                    new = raw.decode("utf-8") if isinstance(value, str) else raw
                    conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?', (new, rowid))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return True


@contextlib.contextmanager
def keys_in_environ(additions: Mapping[str, str], environ: MutableMapping[str, str]) -> Iterator[None]:
    """Put the .env values where the Anthropic and Tavily clients read them; restore afterwards.

    load_live_keys never returns a name the environment already holds, so
    this only ever adds names, and removes them again on the way out.
    """
    saved = {name: environ.get(name) for name in additions}
    environ.update(additions)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                environ.pop(name, None)
            else:
                environ[name] = value


class RedactingStream:
    """A text stream wrapper that redacts secret values before writing."""

    def __init__(self, inner: IO[str], secrets: Sequence[str]) -> None:
        self._inner = inner
        self._secrets = list(secrets)

    def write(self, text: str) -> int:
        self._inner.write(redact(text, self._secrets))
        return len(text)

    def flush(self) -> None:
        self._inner.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@contextlib.contextmanager
def redacted_output(secrets: Sequence[str]) -> Iterator[None]:
    """Redact secret values from everything printed to stdout and stderr in this block."""
    out, err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = RedactingStream(out, secrets), RedactingStream(err, secrets)
    try:
        yield
    finally:
        sys.stdout, sys.stderr = out, err
