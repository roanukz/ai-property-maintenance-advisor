"""refuse node: a forced NO RELIABLE ANSWER (PLAN 8.3 and 8.4).

Reached when validation has failed twice. Code builds the refusal: no
candidates, steps, upgrades or maintenance, `searched` from the code built
search trail, the validation errors' reason codes as the reason, and
`refusal_origin="forced"`, so a forced refusal is never counted as the model
refusing (decision 33). Nothing the model wrote reaches this brief.

`build_budget_stopped_brief` builds the brief for a run that ends
`budget_stopped` (decision 9 clearing; the stop reason in place of an answer).
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from agent.rules.clearing import searched_from_trail
from agent.rules.tiers import apply_tiers
from agent.schemas import HTTP_URL, Brief
from agent.state import AdvisorState, RunContext

NODE = "refuse"
FORCED_WHY = "The drafted answer failed validation twice, so nothing documented can be shown."
BUDGET_WHY = "The run stopped at its spending limit before an answer could be checked."


def _empty_brief(status: str, state: AdvisorState, block: dict[str, Any] | None,
                 sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    brief = {
        "status": status,
        "sources": sources or [],
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


def error_reasons(errors: list[str]) -> list[str]:
    """The reason code of each error ("code_not_grounded"), in order, without repeats.

    An error message quotes the rejected draft (a code not found in its
    source, for example), and nothing the model wrote may reach a forced
    refusal, so only the reason codes are shown.
    """
    out: list[str] = []
    for error in errors:
        reason = error.split(":", 1)[0].strip()
        if reason and reason not in out:
            out.append(reason)
    return out


def build_forced_refusal_brief(state: AdvisorState) -> dict[str, Any]:
    """The NO RELIABLE ANSWER brief for a run whose drafts failed validation twice."""
    errors = [e for e in state.get("validation_errors") or [] if isinstance(e, str)]
    why = FORCED_WHY + (" Last errors: " + "; ".join(error_reasons(errors)) if errors else "")
    block = {"searched": searched_from_trail(state.get("search_trail")), "found": [],
             "why_insufficient": why}
    return _empty_brief("no_reliable_answer", state, block)


def build_budget_stopped_brief(state: AdvisorState) -> dict[str, Any]:
    """The brief for a budget stop: sources so far, the trail and stop reason, nothing answered.

    No model proposed a tier for these sources, so each gets the default
    (forum) lowered by its host ceiling, as the tier rule does.
    """
    reason = state.get("stop_reason")
    block = {
        "searched": searched_from_trail(state.get("search_trail")),
        "found": [],
        "why_insufficient": BUDGET_WHY + (f" Stop reason: {reason}." if reason else ""),
    }
    identity = state.get("identity") or {}
    retrieved = [
        {k: s[k] for k in ("url", "title", "host", "retrieved_at", "origin") if k in s}
        for s in state.get("sources") or []
        if isinstance(s.get("url"), str) and HTTP_URL.match(s["url"])
    ]
    sources = apply_tiers(retrieved, manufacturer=identity.get("manufacturer"), page_texts=None)
    return _empty_brief("budget_stopped", state, block, sources)


def refuse(state: AdvisorState, runtime: Runtime[RunContext]) -> dict:
    """Write the forced refusal and end the answer path."""
    started = time.perf_counter()
    brief = build_forced_refusal_brief(state)
    return {
        "brief": brief,
        "status": "no_reliable_answer",
        "refusal_origin": "forced",
        "latency": {NODE: time.perf_counter() - started},
    }
