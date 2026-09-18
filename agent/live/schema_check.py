"""`advisor check-schema --live`: does the API accept the BriefDraft schema? (decision 22)

One minimal synthesize call with structured output (method "json_schema",
include_raw=True) on the synthesize model of the mode, with a tiny fixed
prompt that asks for a no_reliable_answer draft about the fabricated Aquarest
ZX-9000 Pro and gives no sources. max_tokens is config.SCHEMA_CHECK_MAX_TOKENS,
sized for that short reply. The call is reserved and charged through the
same ledger code as every node, under node "schema_check":

- a reply (parsed or not): charged at its reported usage; the schema was
  accepted;
- HTTP 400: the schema, or the request, was rejected. This is the Phase 5 stop
  condition. It carries no usage, so the reservation is released, as
  paid_structured_call does for any unbilled error;
- a billed error (an exception carrying `.ai_message`): charged its usage,
  or the whole reservation (marked estimated) when it carries none;
- a reply with no usage: charged the whole reservation, marked estimated,
  never recorded as free;
- a timeout or lost connection after sending: charged at the reservation,
  marked estimated (PLAN 8.9).

Replay cannot run this check: ReplayChatModel never compiles a grammar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agent import config, models
from agent.ledger import Ledger, estimate_input_tokens, price_usage
from agent.nodes.synthesize import NO_USAGE_NOTE, _timeout_errors, charge_reply
from agent.schemas import BriefDraft

NODE = config.SCHEMA_CHECK_NODE
ACCEPTED = "accepted"
REJECTED = "rejected"
ERROR = "error"

SYSTEM = (
    "You fill in a service brief draft for a property owner. No sources are provided. "
    "Answer only through the provided schema."
)
USER = (
    "Appliance: Aquarest ZX-9000 Pro spa. Symptom: not heating. No sources were found for this model. "
    'Return status "no_reliable_answer". In no_reliable_answer, set found to an empty list and '
    "why_insufficient to one short sentence. Set matched_identity, warranty_caution, happened_before "
    "and warranty to null, and every list to empty."
)


@dataclass
class SchemaCheckResult:
    """What the check found and what it cost."""

    status: str  # accepted, rejected or error
    http_status: int | None
    message: str
    parsed_status: str | None
    usd: float
    ledger_outcome: str  # charged, charged_estimated or released


def model_id(mode: str) -> str:
    return config.MODEL_FOR[mode]["synthesize"]


def messages() -> list[BaseMessage]:
    return [SystemMessage(SYSTEM), HumanMessage(USER)]


def estimate_tokens(mode: str) -> int:
    """The reservation's input estimate: the prompt plus the schema text, with the model's margin.

    The schema is counted as input on purpose; how the API bills a compiled
    schema is not documented, so the estimate errs high.
    """
    schema_text = HumanMessage(json.dumps(BriefDraft.model_json_schema()))
    return estimate_input_tokens([*messages(), schema_text], model=model_id(mode))


def typical_and_worst_usd(mode: str) -> tuple[float, float]:
    """(typical, worst case): the reservation with a typical reply, and the full reservation."""
    prices = config.PRICES_PER_MTOK[model_id(mode)]
    tokens = estimate_tokens(mode)
    typical = price_usage(prices, {"input_tokens": tokens, "output_tokens": config.SCHEMA_CHECK_OUTPUT_TOKENS_TYPICAL})
    worst = price_usage(prices, {"input_tokens": tokens, "output_tokens": config.SCHEMA_CHECK_MAX_TOKENS})
    return typical, worst


def model_kwargs(mode: str) -> dict[str, Any]:
    """The synthesize constructor arguments of this mode, with the schema check's own max_tokens."""
    kwargs = models.live_model_kwargs("synthesize", mode)
    kwargs["max_tokens"] = config.SCHEMA_CHECK_MAX_TOKENS
    kwargs["timeout"] = config.model_timeout_s(config.SCHEMA_CHECK_MAX_TOKENS)
    return kwargs


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _message(exc: BaseException) -> str:
    text = getattr(exc, "message", None)
    return str(text if isinstance(text, str) and text else exc)


def run_schema_check(ledger: Ledger, mode: str, run_id: str) -> SchemaCheckResult:
    """Reserve, make the one call, settle it in the ledger, and classify the outcome."""
    reservation = ledger.reserve(
        run_id, mode=mode, node=NODE, model=model_id(mode),
        input_tokens_est=estimate_tokens(mode), max_tokens=config.SCHEMA_CHECK_MAX_TOKENS,
    )
    try:
        # Built after the reservation, inside the try, as paid_structured_call does.
        chat = models.ChatAnthropic(**model_kwargs(mode))
        runnable = chat.with_structured_output(BriefDraft, method="json_schema", include_raw=True)
        out = runnable.invoke(messages())
    except _timeout_errors() as exc:
        usd = ledger.charge_timeout(reservation)
        return SchemaCheckResult(ERROR, _http_status(exc), f"timed out or lost the connection: {_message(exc)}",
                                 None, usd, "charged_estimated")
    except Exception as exc:
        billed = getattr(exc, "ai_message", None)
        usage = getattr(billed, "usage_metadata", None) if billed is not None else None
        if usage:
            usd, outcome = ledger.charge(reservation, dict(usage)), "charged"
        elif billed is not None:
            usd, outcome = ledger.charge_estimated(reservation, NO_USAGE_NOTE), "charged_estimated"
        else:
            ledger.release(reservation)
            usd, outcome = 0.0, "released"
        status = _http_status(exc)
        verdict = REJECTED if status == 400 else ERROR
        return SchemaCheckResult(verdict, status, f"{type(exc).__name__}: {_message(exc)}", None, usd, outcome)
    raw = out.get("raw")
    usd = charge_reply(ledger, reservation, raw)
    parsed = out.get("parsed")
    if out.get("parsing_error") is not None or parsed is None:
        detail = str(out.get("parsing_error") or "no parsed output").strip().splitlines()[0]
        return SchemaCheckResult(ACCEPTED, None, f"the API accepted the schema, but the reply did not parse: {detail}",
                                 None, usd, _charged(raw))
    return SchemaCheckResult(ACCEPTED, None, "the API accepted the schema and the reply parsed as BriefDraft",
                             parsed.status, usd, _charged(raw))


def _charged(raw: Any) -> str:
    return "charged" if getattr(raw, "usage_metadata", None) else "charged_estimated"


def report_lines(result: SchemaCheckResult) -> list[str]:
    """What check-schema prints after the call."""
    lines = [f"schema check: {result.status}"]
    if result.http_status is not None:
        lines.append(f"  HTTP {result.http_status}")
    lines.append(f"  {result.message}")
    if result.parsed_status is not None:
        lines.append(f"  draft status: {result.parsed_status}")
    lines.append(f"  ledger: {result.ledger_outcome}, {result.usd:.4f} USD")
    if result.status == REJECTED:
        lines.append("  STOP: an HTTP 400 on the schema is the Phase 5 stop condition (decision 22). "
                     "Do not start the cheap run; report this error.")
    elif result.status == ERROR:
        lines.append("  STOP: the check did not complete, so schema acceptance is unknown. Report this error.")
    return lines
