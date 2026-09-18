"""read_plate: read the identity off a data plate photo (PLAN 8.3, SC4).

Skipped when the owner typed an identity (or there is no photo). Otherwise one
vision call with structured output for schemas.Extraction, reserved and charged
through the ledger with the image counted at config.IMAGE_TOKENS_ESTIMATE.
Code then sets every field the model marked "unreadable" to null, even when
the model guessed text for it. This is one of the two rules decision 27 keeps
outside validate (PRD:142).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent import config
from agent.ledger import BudgetExceeded
from agent.nodes.synthesize import paid_structured_call
from agent.prompts import EXTRACT_SYSTEM, EXTRACT_USER
from agent.schemas import Extraction
from agent.state import AdvisorState, RunContext

NODE = "read_plate"
FIELDS = ("manufacturer", "model", "serial", "manufacture_date")


def null_unreadable(extraction: dict[str, Any]) -> dict[str, Any]:
    """Copy of an extraction with every "unreadable" field set to None."""
    out = dict(extraction)
    confidence = dict(out.get("confidence") or {})
    for field in FIELDS:
        if confidence.get(field) == "unreadable":
            out[field] = None
    out["confidence"] = confidence
    return out


def plate_messages(photo_path: str) -> tuple[list[Any], list[Any]]:
    """(messages sent, messages for the estimate); the image is counted separately."""
    path = Path(photo_path)
    media_type = config.PHOTO_MEDIA_TYPES[path.suffix.lower()]
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    sent = [
        SystemMessage(EXTRACT_SYSTEM),
        HumanMessage(content=[
            {"type": "image", "base64": data, "mime_type": media_type},
            {"type": "text", "text": EXTRACT_USER},
        ]),
    ]
    return sent, [SystemMessage(EXTRACT_SYSTEM), HumanMessage(EXTRACT_USER)]


def read_plate(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Extract the identity from the photo, or do nothing when it was typed."""
    started = time.perf_counter()
    photo = state.get("photo")
    if state.get("identity") is not None or not photo:
        return {"latency": {NODE: time.perf_counter() - started}}
    ctx = runtime.context
    sent, estimate = plate_messages(photo["path"])
    try:
        out, usd = paid_structured_call(ctx, NODE, Extraction, sent, estimate_messages=estimate, images=1)
    except BudgetExceeded as exc:
        return {
            "status": "budget_stopped",
            "stop_reason": exc.reason,
            "latency": {NODE: time.perf_counter() - started},
        }
    parsed = out.get("parsed")
    extraction = null_unreadable(parsed.model_dump(mode="json")) if parsed is not None else None
    return {
        "extraction": extraction,
        "cost_usd": usd,
        "latency": {NODE: time.perf_counter() - started},
    }
