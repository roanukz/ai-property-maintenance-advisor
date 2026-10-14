"""A model that declined a request (decision 69).

Claude Haiku 5.5 runs safety classifiers that can answer a request with HTTP
200, stop_reason "refusal" and a stop_details.category ("cyber", "bio",
"frontier_llm", "general_harms", or none at all). There is no server side
fallback for this model, and v2 never asks another model instead:

- the call is charged its usage like any reply (its reservation, marked
  estimated, when it reports none);
- the run record names the decline and its category in stop_reason, with
  refusal_origin "model_declined";
- the run ends with a brief code writes, saying the model declined. Nothing
  the model wrote reaches it, partial output included.

ModelDeclined is raised at the model boundary: by
models.DeclineAwareChatAnthropic on a live reply and by ReplayChatModel on a
recorded refusal. It carries the billed message as `.ai_message`, which is
how paid_structured_call and the research SpendCap charge a billed error.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage

from agent.rules.clearing import searched_from_trail
from agent.schemas import Brief

REFUSAL_STOP_REASON = "refusal"
REFUSAL_ORIGIN = "model_declined"
NO_CATEGORY = "no category given"


class ModelDeclined(Exception):
    """The model answered with stop_reason "refusal". Never retried, on this model or another."""

    def __init__(self, *, model: str, category: str | None, ai_message: AIMessage) -> None:
        self.model = model
        self.category = category
        self.ai_message = ai_message
        super().__init__(f"{model} declined the request (stop_reason refusal, category {category or NO_CATEGORY})")


def declined_from(message: AIMessage, model: str, metadata: Mapping[str, Any] | None = None) -> ModelDeclined | None:
    """The decline in one reply, or None. Read from stop_reason, never from stop_details.

    `metadata` is the reply's llm_output (ChatAnthropic merges it into
    response_metadata only after _generate returns); the message's own
    response_metadata is read too. stop_details may be null on a refusal.
    """
    meta = {**(message.response_metadata or {}), **(metadata or {})}
    if meta.get("stop_reason") != REFUSAL_STOP_REASON:
        return None
    details = meta.get("stop_details")
    category = details.get("category") if isinstance(details, Mapping) else None
    return ModelDeclined(model=model, category=category if isinstance(category, str) and category else None,
                         ai_message=message)


def declined_stop_reason(node: str, exc: ModelDeclined) -> str:
    """The run record's stop_reason for a decline: "model_declined: cyber (synthesize)"."""
    return f"{REFUSAL_ORIGIN}: {exc.category or NO_CATEGORY} ({node})"


def declined_why(exc: ModelDeclined) -> str:
    """The brief's code written reason."""
    return (f"The model declined this request (safety category: {exc.category or NO_CATEGORY}), so there is "
            "no answer. The request was not sent to another model.")


def declined_brief(state: Mapping[str, Any], exc: ModelDeclined) -> dict[str, Any]:
    """The no_reliable_answer brief for a decline: the searches so far, the reason, nothing else."""
    block = {"searched": searched_from_trail(state.get("search_trail")), "found": [],
             "why_insufficient": declined_why(exc)}
    brief = {
        "status": "no_reliable_answer",
        "sources": [],
        "matched_identity": None,
        "observed_code": state.get("observed_code"),
        "warranty_caution": None,
        "happened_before": None,
        "try_first": [],
        "candidates": [],
        "warranty": None,
        "no_reliable_answer": block,
        "upgrade_options": [],
        "maintenance_due": [],
    }
    return Brief.model_validate(brief).model_dump(mode="json")


def declined_update(state: Mapping[str, Any], exc: ModelDeclined, node: str) -> dict[str, Any]:
    """The state update a node returns when its model declined: the run ends on this brief."""
    return {
        "status": "no_reliable_answer",
        "refusal_origin": REFUSAL_ORIGIN,
        "stop_reason": declined_stop_reason(node, exc),
        "draft": None,
        "brief": declined_brief(state, exc),
    }


def is_declined(state: Mapping[str, Any]) -> bool:
    """True once a node has ended the run on a decline; the graph sends it straight to render."""
    return state.get("refusal_origin") == REFUSAL_ORIGIN
