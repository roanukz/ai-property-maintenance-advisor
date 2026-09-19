"""synthesize: one structured output call that drafts the brief (PLAN 8.3).

The model sees numbered sources (title, host, verbatim excerpts), the
identity, the symptom, the appliance's service records with their record IDs,
dates and symptoms, its registry dates and warranty terms (Phase 3; PLAN 8.3,
8.8 rules 8 and 9) and, on a retry, the errors the previous draft failed
validation with. Sources are truncated in code to
config.SYNTH_SOURCE_TOKEN_BUDGET. The call goes through ledger.reserve and
ledger.charge; include_raw keeps the raw message so a reply that fails the
BriefDraft schema is still charged, and that failure is recorded as a
validation error, never as an empty ok brief (v1 guardrail test 13).

This module also holds the paid call helper read_plate uses.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent import config
from agent.ledger import BudgetExceeded, Ledger, estimate_input_tokens
from agent.models import make_model, max_tokens_for
from agent.prompts import build_synthesis_user_prompt, synthesis_system_prompt
from agent.redaction import scrub_exception
from agent.schemas import BriefDraft
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "synthesize"
APPLIANCE_KIND = "appliance"  # history_hits entry for the registry dates (agent/nodes/history.py)
PARSE_ERROR_PREFIX = "synthesize: the reply did not match the BriefDraft schema"
EXCERPT_JOIN = "\n...\n"


# ---------------------------------------------------------------------------
# Paid call plumbing (shared with read_plate)
# ---------------------------------------------------------------------------


def _timeout_errors() -> tuple[type[BaseException], ...]:
    """Exceptions meaning the request may have been sent and billed (PLAN 8.9)."""
    from langchain_core.exceptions import ModelConnectionError, ModelTimeoutError

    errors: list[type[BaseException]] = [ModelTimeoutError, ModelConnectionError, TimeoutError]
    try:
        import anthropic

        errors += [anthropic.APITimeoutError, anthropic.APIConnectionError]
    except ImportError:  # the replay path never needs it
        pass
    return tuple(errors)


def open_ledger(ctx: RunContext) -> Ledger:
    """The run's ledger. Only a replay ledger that is not the live one is created on demand.

    The one copy every node and the retry gate use: a live run never
    recreates a deleted ledger (decision 52), and a replay context pointed at
    config.LEDGER_PATH never creates the live one.
    """
    create = ctx.mode == "replay" and Path(ctx.ledger_path).resolve() != config.LEDGER_PATH.resolve()
    return Ledger(ctx.ledger_path, create=create)


def _thread_id() -> str | None:
    try:
        from langgraph.config import get_config

        return (get_config().get("configurable") or {}).get("thread_id")
    except RuntimeError:  # called outside a graph run, as in unit tests
        return None


def paid_structured_call(
    ctx: RunContext,
    node: str,
    schema: type,
    messages: Sequence[BaseMessage],
    *,
    estimate_messages: Sequence[BaseMessage] | None = None,
    images: int = 0,
) -> tuple[dict[str, Any], float]:
    """Reserve, call with structured output (include_raw), charge in full.

    Returns ({"raw", "parsed", "parsing_error"}, charged usd). Raises
    BudgetExceeded before the call when the reservation does not fit. A call
    that timed out after it was sent is charged at its reservation; any other
    failure releases the reservation, unless the error carries a billed
    `.ai_message`, which is charged its usage (or, with no usage on it, the
    reservation, marked estimated). A reply that reports no usage is charged
    the same way, never at 0. Errors leave with the run's key values redacted.
    """
    model_id = config.MODEL_FOR[ctx.mode][node]
    est = estimate_input_tokens(list(estimate_messages or messages), model=model_id, images=images)
    ledger = open_ledger(ctx)
    reservation = ledger.reserve(
        ctx.run_id,
        mode=ctx.mode,
        node=node,
        model=model_id,
        input_tokens_est=est,
        max_tokens=max_tokens_for(node, ctx.mode),
        thread_id=_thread_id(),
        run_cap_usd=(ctx.caps or {}).get("run_cap_usd"),
    )
    try:
        # Built after the reservation (a refused call never builds its model)
        # and inside the try, so a model that fails to build releases the hold.
        runnable = make_model(node, ctx).with_structured_output(schema, method="json_schema", include_raw=True)
        out = runnable.invoke(list(messages))
    except BaseException as exc:
        if isinstance(exc, _timeout_errors()):
            ledger.charge_timeout(reservation)
        else:
            billed = getattr(exc, "ai_message", None)
            if billed is None:
                ledger.release(reservation)
            elif getattr(billed, "usage_metadata", None):
                ledger.charge(reservation, dict(billed.usage_metadata))
            else:
                ledger.charge_estimated(reservation, NO_USAGE_NOTE)
        clean = scrub_exception(exc, ctx.secrets)
        if clean is exc:
            raise
        raise clean from None
    return out, charge_reply(ledger, reservation, out.get("raw"))


NO_USAGE_NOTE = "a reply with no usage, charged at reservation"


def charge_reply(ledger: Ledger, reservation: Any, raw: Any) -> float:
    """Charge a reply's reported usage, or its whole reservation (estimated) when it reports none."""
    usage = getattr(raw, "usage_metadata", None)
    if not usage:
        return ledger.charge_estimated(reservation, NO_USAGE_NOTE)
    return ledger.charge(reservation, dict(usage))


# ---------------------------------------------------------------------------
# Sources block
# ---------------------------------------------------------------------------


def source_text(source: dict[str, Any], pages_dir: Path | None) -> str:
    """The verbatim text synthesize sees for one source.

    Code cut excerpts when research stored them, else the stored page text
    (verbatim, cut to budget later), else the search snippet (PLAN 8.3: the
    snippet only when no raw text exists).
    """
    excerpts = source.get("excerpts")
    if isinstance(excerpts, list) and excerpts:
        return EXCERPT_JOIN.join(str(e) for e in excerpts)
    if isinstance(excerpts, str) and excerpts:
        return excerpts
    sha = source.get("text_sha256")
    if pages_dir is not None and isinstance(sha, str) and sha:
        page = Path(pages_dir) / f"{sha}.txt"
        if page.is_file():
            return page.read_text(encoding="utf-8")
    snippet = source.get("snippet")
    return snippet if isinstance(snippet, str) else ""


def _header(index: int, source: dict[str, Any]) -> str:
    title = source.get("title") or source.get("url") or "(untitled)"
    return f"[{index}] {title}\nHost: {source.get('host') or ''}\nExcerpts:\n"


def _fair_shares(lengths: list[int], budget: int) -> list[int]:
    """Split a character budget so short texts keep everything and long ones share the rest."""
    shares = [0] * len(lengths)
    remaining = max(budget, 0)
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    for n, i in enumerate(order):
        fair = remaining // (len(order) - n)
        shares[i] = min(lengths[i], fair)
        remaining -= shares[i]
    return shares


def cut_at_word(text: str, limit: int) -> str:
    """At most `limit` characters of `text`, never ending inside a word.

    The model is told to copy quotes exactly, so a text cut mid word reaches
    the brief cut mid word (the Phase 6 "deactivated and filte"). A cut that
    would split a word backs off to the whitespace before it.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if cut[-1].isalnum() and text[limit].isalnum():
        space = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
        cut = cut[:space] if space > 0 else cut  # one unbroken token: no boundary to use
    return cut.rstrip()


def build_sources_block(sources: Sequence[dict[str, Any]], pages_dir: Path | None) -> str:
    """Numbered sources, headers included, within SYNTH_SOURCE_TOKEN_BUDGET tokens.

    Every cut to fit the budget ends on a word boundary (cut_at_word).
    """
    if not sources:
        return ""
    budget = config.SYNTH_SOURCE_TOKEN_BUDGET * config.CHARS_PER_TOKEN_ESTIMATE
    headers = [_header(i, s) for i, s in enumerate(sources)]
    separators = 2 * (len(sources) - 1)
    texts = [source_text(s, pages_dir) for s in sources]
    shares = _fair_shares([len(t) for t in texts], budget - sum(map(len, headers)) - separators)
    blocks = [h + cut_at_word(t, n) for h, t, n in zip(headers, texts, shares)]
    return cut_at_word("\n\n".join(blocks), budget)


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def split_history(history_hits: Sequence[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """(service records, the appliance's registry entry) from history_hits.

    Service records carry their record_id, date and symptom (rule 9); the
    appliance entry carries the registry dates and warranty terms (rule 8,
    decision 29) and is shown on its own, never as a prior event.
    """
    records: list[dict[str, Any]] = []
    appliance: dict[str, Any] | None = None
    for hit in history_hits or []:
        if hit.get("kind") == APPLIANCE_KIND:
            appliance = hit
        else:
            records.append(hit)
    return records, appliance


def synthesis_messages(state: AdvisorState, ctx: RunContext) -> list[BaseMessage]:
    """The system and user messages for one synthesize attempt."""
    records, appliance = split_history(state.get("history_hits"))
    user = build_synthesis_user_prompt(
        identity=state.get("identity"),
        symptom=state.get("symptom") or "",
        sources_block=build_sources_block(state.get("sources") or [], ctx.pages_dir),
        history_hits=records,
        prior_errors=state.get("validation_errors") or [],
        registry=appliance,
        observed_code=state.get("observed_code"),
    )
    return [SystemMessage(synthesis_system_prompt(_today())), HumanMessage(user)]


def synthesize(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Draft the brief, or record why the reply could not be used."""
    started = time.perf_counter()
    ctx = runtime.context
    messages = synthesis_messages(state, ctx)
    try:
        out, usd = paid_structured_call(ctx, NODE, BriefDraft, messages)
    except BudgetExceeded as exc:
        return {
            "status": "budget_stopped",
            "stop_reason": exc.reason,
            "draft": None,
            **latency_entry(state, NODE, time.perf_counter() - started),
        }
    update: dict[str, Any] = {"cost_usd": usd, **latency_entry(state, NODE, time.perf_counter() - started)}
    parsed = out.get("parsed")
    if out.get("parsing_error") is not None or parsed is None:
        detail = str(out.get("parsing_error") or "no parsed output").strip().splitlines()[0]
        update["draft"] = None
        update["validation_errors"] = [f"{PARSE_ERROR_PREFIX}: {detail}"]
    else:
        update["draft"] = parsed.model_dump(mode="json")
    return update
