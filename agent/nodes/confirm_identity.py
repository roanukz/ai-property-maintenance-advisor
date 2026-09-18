"""confirm_identity: the one human pause (PLAN 8.3, SC4, SC5).

With an extraction from a photo, or a typed identity missing its model, the
node calls interrupt() once. On resume LangGraph reruns the node from the top,
so nothing before the interrupt may cost money or write anywhere; this node
makes no paid call. If the model is still missing after a resume, the node
stores a re-prompt in confirm_prompt and `next_after_confirm` loops back here
through a conditional edge, one interrupt per node run.

A typed identity with a model (--identity, or --appliance from the registry)
needs no pause only when there is no unconfirmed code: either --code was given
or the symptom names no code. A code intake guessed from the symptom text
(observed_code_candidate) is never enforced until the owner confirms it here
(PLAN 8.3, decision 7), so a typed run with a guessed code pauses too.

Interrupt payload: {"extraction", "identity", "observed_code_candidate",
"missing", "prompt"}. Resume value: {"identity": {four fields},
"observed_code": str | None}. A resume without the observed_code key confirms
the code shown in the payload; an explicit null clears it.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from agent.schemas import IntakeIdentity
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "confirm_identity"
FIELDS = ("manufacturer", "model", "serial", "manufacture_date")

FIRST_PROMPT = (
    "Confirm or correct the unit's identity and the code shown on its display, if any. "
    "The model is required."
)
MISSING_MODEL_PROMPT = (
    "The model is still missing. Type the model exactly as printed on the data plate; "
    "the run stays paused until it is given."
)
BAD_RESUME_PROMPT = "The confirmation could not be read: {error}. Send the identity again."


def _blank_identity() -> dict[str, Any]:
    return {field: None for field in FIELDS}


def proposed_identity(state: AdvisorState) -> dict[str, Any]:
    """What the owner is asked to confirm: the last edit, a typed identity, or the extraction."""
    identity = state.get("identity")
    if identity is not None:
        return {field: identity.get(field) for field in FIELDS}
    extraction = state.get("extraction")
    if extraction is not None:
        return {field: extraction.get(field) for field in FIELDS}
    return _blank_identity()


def missing_fields(identity: dict[str, Any]) -> list[str]:
    return [field for field in FIELDS if not identity.get(field)]


def _stripped(identity: dict[str, Any]) -> dict[str, Any]:
    """Surrounding whitespace dropped, so a blank typed model counts as missing."""
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in identity.items()}


def _clean_code(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("observed_code must be text or null")
    return value.strip() or None


def shown_code(state: AdvisorState) -> str | None:
    """The code the owner is asked to confirm: a --code value, else the symptom guess."""
    return state.get("observed_code") or state.get("observed_code_candidate")


def unconfirmed_code(state: AdvisorState) -> bool:
    """True when the symptom gave a code guess that no one has confirmed."""
    return state.get("observed_code") is None and bool(state.get("observed_code_candidate"))


def interrupt_payload(state: AdvisorState) -> dict[str, Any]:
    identity = proposed_identity(state)
    return {
        "extraction": state.get("extraction"),
        "identity": identity,
        "observed_code_candidate": shown_code(state),
        "missing": missing_fields(identity),
        "prompt": state.get("confirm_prompt") or FIRST_PROMPT,
    }


def confirm_identity(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Pause for the owner, or pass a complete typed identity with no unconfirmed code through."""
    started = time.perf_counter()

    def timed(update: dict[str, Any]) -> dict[str, Any]:
        return {**update, **latency_entry(state, NODE, time.perf_counter() - started)}

    typed = state.get("identity")
    if (
        typed is not None
        and typed.get("model")
        and state.get("extraction") is None
        and not state.get("confirm_prompt")
        and not unconfirmed_code(state)
    ):
        return timed({
            "identity": {field: typed.get(field) for field in FIELDS},
            "identity_confirmed": True,
            "confirm_prompt": None,
        })

    answer = interrupt(interrupt_payload(state))

    try:
        if not isinstance(answer, dict):
            raise ValueError("the resume value must be an object")
        identity = _stripped(IntakeIdentity.model_validate(answer.get("identity") or {}).model_dump())
        # The owner saw the shown code in the payload; resuming without the
        # key confirms it as shown.
        code = _clean_code(answer["observed_code"]) if "observed_code" in answer else shown_code(state)
    except (ValidationError, ValueError) as exc:
        first = str(exc).strip().splitlines()[0]
        return timed({
            "identity_confirmed": False,
            "confirm_prompt": BAD_RESUME_PROMPT.format(error=first),
        })
    if not identity.get("model"):
        return timed({
            "identity": identity,
            "identity_confirmed": False,
            "observed_code": code,
            "observed_code_candidate": None,
            "confirm_prompt": MISSING_MODEL_PROMPT,
        })
    return timed({
        "identity": identity,
        "identity_confirmed": True,
        "observed_code": code,
        "observed_code_candidate": None,
        "confirm_prompt": None,
    })


def next_after_confirm(state: AdvisorState) -> str:
    """Conditional edge: loop back until the identity is confirmed, then route."""
    return "route" if state.get("identity_confirmed") else NODE
