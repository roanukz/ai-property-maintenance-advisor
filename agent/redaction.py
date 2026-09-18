"""Key redaction at the source (PRD Part A rule 8; review findings M5 and T1).

A live run carries its loaded key values on RunContext.secrets. The paid call
sites (paid_structured_call, the research SpendCap and the Tavily wrappers)
pass every error through `scrub_exception` before it leaves them, so a
client error that echoes a key reaches neither the lookup log, nor a
ToolMessage the research model reads next, nor the checkpoint (langgraph
stores a failed task's error as its repr). Replay runs carry no secrets, and
then nothing here changes anything.

Stdlib only: the nodes import it, and agent.live must not be imported by them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

REDACTED = "[redacted]"
# Attributes client errors keep their text in besides args (the anthropic SDK's
# APIError has message and body).
_TEXT_ATTRS = ("message", "body")


def redact(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret value in text."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def redact_any(value: Any, secrets: Sequence[str]) -> Any:
    """redact applied to every string in a str, list, tuple or dict value."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, (list, tuple)):
        return type(value)(redact_any(v, secrets) for v in value)
    if isinstance(value, dict):
        return {k: redact_any(v, secrets) for k, v in value.items()}
    return value


def _holds(text: str, secrets: Sequence[str]) -> bool:
    return any(secret and secret in text for secret in secrets)


class RedactedError(RuntimeError):
    """Stands in for an error whose text still held a key after redaction in place."""


def _scrub_in_place(exc: BaseException, secrets: Sequence[str]) -> None:
    try:
        exc.args = tuple(redact_any(arg, secrets) for arg in exc.args)
    except (AttributeError, TypeError):
        pass
    for name in _TEXT_ATTRS:
        value = getattr(exc, name, None)
        if isinstance(value, (str, list, tuple, dict)):
            try:
                setattr(exc, name, redact_any(value, secrets))
            except (AttributeError, TypeError):
                pass


def scrub_exception(exc: BaseException, secrets: Sequence[str]) -> BaseException:
    """The exception to re-raise: exc with every secret redacted, or a stand in.

    The text is redacted in place (args, message, body, and the same for its
    cause and context), which keeps its type, so retry rules and billing
    branches still see the original class. If str or repr still holds a
    secret (a class that builds its text some other way), a RedactedError
    with the redacted text replaces it; raise that `from None`.
    """
    if not secrets or not isinstance(exc, Exception):
        return exc
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        _scrub_in_place(link, secrets)
        link = link.__cause__ or link.__context__
    try:
        leaked = _holds(str(exc), secrets) or _holds(repr(exc), secrets)
    except Exception:  # a __str__ that fails on the new args
        leaked = True
    if not leaked:
        return exc
    try:
        text = f"{type(exc).__name__}: {exc}"
    except Exception:
        text = type(exc).__name__
    replacement = RedactedError(redact(text, secrets))
    replacement.__suppress_context__ = True
    return replacement
