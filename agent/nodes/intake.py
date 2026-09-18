"""intake: validate the run's input and set up state (PLAN 8.3).

The graph's input uses AdvisorState keys: symptom, identity (a typed identity
dict, or None), photo ({"path": ...} or None), property_id, appliance_id and
observed_code (the --code flag, or None). A code guessed from the symptom goes
to observed_code_candidate, never to observed_code: only the owner confirms a
code (--code, or the confirm_identity resume). `intake_input` builds that input
from CLI values. The node validates it with schemas.Intake, so a non string
identity field fails here instead of deep in a prompt builder (v1's ".trim is
not a function"), and it never puts image bytes in state.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent import config
from agent.registry import Registry
from agent.schemas import Intake
from agent.state import AdvisorState, RunContext

NODE = "intake"
IDENTITY_FIELDS = ("manufacturer", "model", "serial", "manufacture_date")

_KEYWORDS = ("shows", "showing", "displays", "displaying", "code", "error")
_TOKEN = rf"[A-Z0-9]{{{config.OBSERVED_CODE_MIN_CHARS},{config.OBSERVED_CODE_MAX_CHARS}}}"
# The keyword is matched in any case; the token itself must be capitals or digits.
# A lookahead, so a skipped token ("ERROR CODE E3") can still be the next keyword.
_AFTER_KEYWORD = re.compile(
    rf"\b(?i:{'|'.join(_KEYWORDS)})\b(?=[\s:=#]*[\"'“‘]?({_TOKEN})(?![A-Za-z0-9]))"
)
_IN_QUOTES = re.compile(rf"[\"'“‘]({_TOKEN})[\"'”’]")


def observed_code_candidate(symptom: str) -> str | None:
    """A code the symptom reports, or None (PLAN 8.3).

    A token of 2 to 6 capital letters or digits after "shows", "displays",
    "code" or "error", or in quotes. A token that is itself one of those words
    (for example "ERROR CODE E3") is skipped so the real code is found.
    """
    for match in _AFTER_KEYWORD.finditer(symptom):
        token = match.group(1)
        if token.lower() not in _KEYWORDS:
            return token
    for match in _IN_QUOTES.finditer(symptom):
        token = match.group(1)
        if token.lower() not in _KEYWORDS:
            return token
    return None


def intake_input(
    *,
    symptom: str,
    photo_path: str | None = None,
    identity: dict[str, Any] | None = None,
    property_id: str | None = None,
    appliance_id: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """The graph input for one `advisor ask`, in AdvisorState keys."""
    return {
        "symptom": symptom,
        "photo": {"path": photo_path} if photo_path is not None else None,
        "identity": identity,
        "property_id": property_id,
        "appliance_id": appliance_id,
        "observed_code": code,
    }


def _photo_path(photo: Any) -> str | None:
    if photo is None:
        return None
    if isinstance(photo, str):
        return photo
    if isinstance(photo, dict) and isinstance(photo.get("path"), str):
        return photo["path"]
    raise ValueError("photo must be a path or {'path': ...}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _appliance(ctx: RunContext, appliance_id: str, property_id: str | None) -> dict[str, Any]:
    found = Registry(ctx.registry_path).get_appliance(appliance_id)
    if found is None:
        raise ValueError(f"unknown appliance {appliance_id!r}")
    if property_id is not None and found["property_id"] != property_id:
        raise ValueError(f"appliance {appliance_id!r} is not at property {property_id!r}")
    return found


def intake(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Validate the input; set run_id, mode, photo, identity, the --code value and the code candidate."""
    started = time.perf_counter()
    ctx = runtime.context
    if state.get("mode") not in (None, ctx.mode):
        raise ValueError(f"state mode {state.get('mode')!r} differs from the run mode {ctx.mode!r}")
    data = Intake(
        symptom=state.get("symptom"),
        mode=ctx.mode,
        photo_path=_photo_path(state.get("photo")),
        identity=state.get("identity"),
        property_id=state.get("property_id"),
        appliance_id=state.get("appliance_id"),
        code=state.get("observed_code"),
    )

    photo = None
    if data.photo_path is not None:
        path = Path(data.photo_path).expanduser().resolve()
        if path.suffix.lower() not in config.PHOTO_MEDIA_TYPES:
            raise ValueError(f"photo type {path.suffix!r} is not one of {sorted(config.PHOTO_MEDIA_TYPES)}")
        if not path.is_file():
            raise ValueError(f"photo not found: {path}")
        photo = {"path": str(path), "sha256": _sha256(path)}

    identity = data.identity.model_dump() if data.identity is not None else None
    property_id = data.property_id
    if data.appliance_id is not None:
        appliance = _appliance(ctx, data.appliance_id, data.property_id)
        property_id = appliance["property_id"]
        if identity is None and photo is None:
            # The registry is the owner's own record, so it stands in for a typed identity.
            identity = {
                "manufacturer": appliance.get("manufacturer") or None,
                "model": appliance.get("model") or None,
                "serial": appliance.get("serial") or None,
                "manufacture_date": None,
            }

    code = data.code.strip() if data.code else None
    return {
        "run_id": ctx.run_id,
        "mode": ctx.mode,
        "symptom": data.symptom,
        "photo": photo,
        "identity": identity,
        "property_id": property_id,
        "appliance_id": data.appliance_id,
        # Only --code is a confirmed code; a guess from the symptom waits for
        # the owner at confirm_identity (decision 7).
        "observed_code": code,
        "observed_code_candidate": None if code else observed_code_candidate(data.symptom),
        "identity_confirmed": False,
        "confirm_prompt": None,
        "status": "running",
        "latency": {NODE: time.perf_counter() - started},
    }
