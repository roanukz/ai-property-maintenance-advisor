"""safety_check: one Jev Noul per try_first step of the current draft (safety step flagging).

Wired synthesize -> safety_check -> validate. For each distinct step of the
draft (keyed by `step_key`, the hash of the normalized step and detail) it asks
the run's SafetyJudge (`models.make_safety_judge`) once, in order, with the
state filtered to {"appliance", "step", "detail"}, and writes `safety_signals`,
its only key besides latency. The rules read the signals in validate and never
call anything (`safety.raise_flags`).

- JEV_SAFETY_ENABLED is False: status "disabled", no call.
- Status `budget_stopped`, or no draft (synthesize's reply failed its schema):
  status "skipped", no call. A draft with no steps is skipped the same way.
- Every call answered: "ran". Some failed: "partial"; all failed: "failed".
  A failure (SafetyCheckError of any kind but "not_recorded") leaves that step
  with no Jev probability, so the writer's flag still applies, the word rule
  backs it up (as a production layer, or as the fallback of decision 68), the
  run record says why, and the brief shows the notice line.
- A replay whose cassette recorded no answer for any step: "not_recorded".
  Missing from a cassette is not a failure and shows no notice line.

A node, not an edge, so its output is checkpointed and a resumed thread never
asks Jev twice. A validation retry drafts again and so passes through here
again; the upgrade pass revalidates the same draft and reuses these signals.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent import config, models
from agent.nodes.synthesize import open_ledger
from agent.prompts import SAFETY_STEP_NOUL
from agent.rules.step_text import step_key
from agent.safety_judge import SafetyCheckError, question_hash
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "safety_check"
STATUSES = ("ran", "partial", "failed", "not_recorded", "skipped", "disabled")
# Only these mean a call was attempted and failed, and only these show the notice line.
NOTICE_STATUSES = ("partial", "failed")
NOT_RECORDED = "not_recorded"
# Used when neither the registry nor the identity gives the appliance's category.
UNKNOWN_APPLIANCE = "appliance"

REASON_DISABLED = "JEV_SAFETY_ENABLED is False"
REASON_BUDGET_STOPPED = "the run is budget_stopped"
REASON_NO_DRAFT = "synthesize returned no draft"
REASON_NO_STEPS = "the draft has no try_first steps"
REASON_RAN = "Jev answered every step"
REASON_NOT_RECORDED = "the cassette recorded no safety check for this draft"


def notice_needed(signals: dict[str, Any] | None) -> bool:
    """True when the brief shows the notice line: Jev enabled, and a call was attempted and failed."""
    return bool(config.JEV_SAFETY_ENABLED) and (signals or {}).get("status") in NOTICE_STATUSES


def _category(value: Any) -> str | None:
    """A registry category as words ("hot_tub" becomes "hot tub"), or None."""
    if not isinstance(value, str):
        return None
    return value.replace("_", " ").strip() or None


def _maker(value: Any) -> str | None:
    """A manufacturer compared as the registry compares it: case and runs of whitespace ignored."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()).lower() or None


def appliance_category(state: AdvisorState, ctx: RunContext) -> str:
    """The appliance's category ("hot tub"): its registry row, else the one registered model the identity
    names, else the one category the registry holds for the identity's maker (a model written another way,
    such as "XR16 (4TTR6036)" for the registered "XR16 4TTR6036")."""
    if ctx.registry_path is None or not Path(ctx.registry_path).is_file():
        return UNKNOWN_APPLIANCE
    from agent.registry import Registry

    registry = Registry(ctx.registry_path)
    appliance_id = state.get("appliance_id")
    if appliance_id:
        row = registry.get_appliance(appliance_id)
        if row is not None and _category(row.get("category")):
            return _category(row["category"])
    identity = state.get("identity") or {}
    if isinstance(identity.get("model"), str) and identity["model"].strip():
        found = {_category(r.get("category")) for r in registry.find_by_model(identity.get("manufacturer"),
                                                                              identity["model"])}
        found.discard(None)
        if len(found) == 1:
            return found.pop()
    maker = _maker(identity.get("manufacturer"))
    if maker is not None:
        found = {_category(r.get("category")) for r in registry.list_appliances()
                 if _maker(r.get("manufacturer")) == maker}
        found.discard(None)
        if len(found) == 1:
            return found.pop()
    return UNKNOWN_APPLIANCE


def _signals(status: str, reason: str, qhash: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"status": status, "reason": reason, "question_hash": qhash, "model": config.JEV_MODEL,
            "steps": steps or []}


def _outcome(entries: list[dict[str, Any]]) -> tuple[str, str]:
    """(status, reason) for the calls made."""
    errors = [e["error"] for e in entries if e["error"] is not None]
    failed = [kind for kind in errors if kind != NOT_RECORDED]
    answered = len(entries) - len(errors)
    if failed:
        kinds = ", ".join(sorted(set(failed)))
        status = "failed" if answered == 0 else "partial"
        return status, f"Jev failed on {len(failed)} of {len(entries)} steps ({kinds})"
    if answered == 0:
        return NOT_RECORDED, REASON_NOT_RECORDED
    if errors:
        return "ran", f"Jev answered {answered} of {len(entries)} steps; the cassette recorded no others"
    return "ran", REASON_RAN


def check_draft(state: AdvisorState, ctx: RunContext) -> dict[str, Any]:
    """The safety_signals for the state's current draft."""
    if not config.JEV_SAFETY_ENABLED:
        return _signals("disabled", REASON_DISABLED, _configured_hash())
    if state.get("status") == "budget_stopped":
        return _signals("skipped", REASON_BUDGET_STOPPED, _configured_hash())
    draft = state.get("draft")
    if not isinstance(draft, dict):
        return _signals("skipped", REASON_NO_DRAFT, _configured_hash())
    steps = [s for s in draft.get("try_first") or [] if isinstance(s, dict)]
    if not steps:
        return _signals("skipped", REASON_NO_STEPS, _configured_hash())
    judge = models.make_safety_judge(ctx)
    appliance = appliance_category(state, ctx)
    entries: list[dict[str, Any]] = []
    asked: set[str] = set()
    for step in steps:
        key = step_key(step.get("step"), step.get("detail"))
        if key in asked:  # the same step twice is one question
            continue
        asked.add(key)
        payload = {"appliance": appliance, "step": step.get("step") or "", "detail": step.get("detail") or ""}
        try:
            reply = judge.ask(key, payload)
        except SafetyCheckError as exc:
            entries.append({"step_sha256": key, "noul": None, "model": None, "request_id": None,
                            "input_tokens": None, "error": exc.kind})
            continue
        entries.append({"step_sha256": key, "noul": float(reply.noul), "model": reply.model,
                        "request_id": reply.request_id, "input_tokens": reply.input_tokens, "error": None})
    status, reason = _outcome(entries)
    return _signals(status, reason, judge.question_hash, entries)


def _configured_hash() -> str:
    return question_hash(SAFETY_STEP_NOUL["instructions"], SAFETY_STEP_NOUL.get("criteria"))


def safety_check(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Ask Jev about each step of the draft and record the answers in safety_signals."""
    started = time.perf_counter()
    ctx = runtime.context
    live = ctx.mode in config.LIVE_MODES
    before = open_ledger(ctx).run_total(ctx.run_id) if live else 0.0
    signals = check_draft(state, ctx)
    out: dict[str, Any] = {"safety_signals": signals}
    if live and signals["steps"]:
        # The state mirror of spend; the ledger, charged by the judge, is the truth.
        out["cost_usd"] = open_ledger(ctx).run_total(ctx.run_id) - before
    out.update(latency_entry(state, NODE, time.perf_counter() - started))
    return out
