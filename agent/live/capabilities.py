"""The capabilities check: the first live step on Claude Haiku 5.5 (decision 69). Never run in tests.

One small call per node that runs on config.HAIKU in the mode, each built
with that node's exact live settings (models.live_model_kwargs: model, effort,
max_tokens, timeout, no retries) by the same DeclineAwareChatAnthropic the
graph uses, in this order:

- read_plate: v1 case E1's demo plate (config.CAPABILITIES_PHOTO) with the
  Extraction schema. It goes first because Anthropic's docs do not state
  image input for this model: if the photo is refused (a decline, or an
  HTTP 400 whose message names the image), the check says so and stops,
  and the record it writes turns plate reading off (preflight.plate_refusal),
  never onto another model. Any other 400 there stops the check with its own
  message and leaves image input unchecked, since this first call also
  carries the effort, schema and max_tokens settings every node shares;
- classifier: a synthetic symptom with the ClassifierVerdict schema;
- research: the research prompt with the real search and fetch tool
  definitions bound; no tool is ever run, the reply only has to parse;
- synthesize: schema_check's no_reliable_answer prompt with BriefDraft.

Each call confirms the request is accepted, the structured output (or the
tool call) parses, and says whether the reply began with a thinking block,
so a thinking first reply is seen to parse. Any failure stops the check, so
no further call is made. Every call is reserved before it is sent and charged
in the ledger under node config.CAPABILITIES_NODE, as schema_check does.

`check` prints the planned calls and their cost, reads one line and goes on
only on exactly "proceed". The result is written to
preflight.capabilities_path() (model, date, image input and each call's
outcome, with the model name each reply carried; never a prompt or a
reply). A reply that names a model other than config.HAIKU marks the check
STOPPED with its own STOP line, so a dated snapshot shows up at the batch's first
live step rather than when a paid run's recording is written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agent import config, models
from agent.declined import ModelDeclined
from agent.ledger import Ledger, estimate_input_tokens, price_usage
from agent.live import schema_check
from agent.live.preflight import (
    IMAGE_ACCEPTED,
    IMAGE_REFUSED,
    IMAGE_UNCHECKED,
    PROCEED,
    ask_to_proceed,
    capabilities_path,
)
from agent.nodes.classify import ClassifierVerdict, classifier_messages
from agent.nodes.read_plate import plate_messages
from agent.nodes.synthesize import NO_USAGE_NOTE, _timeout_errors
from agent.prompts import RESEARCH_PROMPT
from agent.research.tools import make_research_tools
from agent.schemas import BriefDraft, Extraction
from agent.state import RunContext

NODE = config.CAPABILITIES_NODE
ACCEPTED = "accepted"
REJECTED = "rejected"
DECLINED = "declined"
ERROR = "error"
NOT_RUN = "not run"
# A 400 message that names the photo, as opposed to effort, the schema or another shared setting.
IMAGE_WORDS = re.compile(r"\b(?:image|images|media|media_type|vision)\b", re.IGNORECASE)

CLASSIFIER_IDENTITY = {"manufacturer": "Sundance Spas", "model": "Optima 880"}
CLASSIFIER_SYMPTOM = "the heater does not come on"
CLASSIFIER_CAUSES = [{"code": "FLO", "evidence": "FLO means the flow switch did not close; check the filter."}]
RESEARCH_USER = "Find the owner's manual for a Sundance Spas Optima 880 hot tub. Use the search tool."


@dataclass
class Probe:
    """One planned call: the node, its messages, what it is parsed as, and its estimate inputs."""

    node: str
    messages: list[BaseMessage]
    estimate_messages: list[BaseMessage]
    schema: type | None = None  # None: a tool bearing call (research)
    tools: list[Any] = field(default_factory=list)
    images: int = 0


@dataclass
class ProbeResult:
    """What one call showed. No prompt or reply text is kept."""

    node: str
    status: str
    effort: str | None
    max_tokens: int
    parsed: bool = False
    thinking_first: bool = False
    stop_reason: str | None = None
    category: str | None = None
    http_status: int | None = None
    message: str = ""
    usd: float = 0.0
    reply_model: str | None = None  # the model name the reply carried (response_metadata), if any


@dataclass
class CapabilitiesResult:
    model: str
    mode: str
    checked_at: str
    image_input: str
    results: list[ProbeResult]

    @property
    def calls_passed(self) -> bool:
        return bool(self.results) and all(r.status == ACCEPTED and r.parsed for r in self.results)

    @property
    def other_reply_models(self) -> list[str]:
        """Model names replies carried that are not the model checked (a dated snapshot, say)."""
        return sorted({r.reply_model for r in self.results if r.reply_model and r.reply_model != self.model})

    @property
    def passed(self) -> bool:
        return self.calls_passed and not self.other_reply_models


class _NeverCalled:
    """A tool inner client for the research probe: the check never runs a tool."""

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the capabilities check never runs a tool")


def haiku_nodes(mode: str) -> list[str]:
    """The nodes that run on config.HAIKU in this mode, in check order."""
    return [node for node in models.NODES if config.MODEL_FOR[mode][node] == config.HAIKU]


def _research_tools(mode: str) -> list[Any]:
    ctx = RunContext(run_id="capabilities", mode=mode, ledger_path=Path(config.LEDGER_PATH),
                     registry_path=Path(config.REGISTRY_PATH), graph_path=Path(config.GRAPH_PATH),
                     pages_dir=Path(config.PAGES_DIR))
    return make_research_tools(ctx, search_inner=_NeverCalled(), fetch_inner=_NeverCalled())


def probes(mode: str, photo: Path | None = None) -> list[Probe]:
    """The planned calls for this mode."""
    out = []
    for node in haiku_nodes(mode):
        if node == "read_plate":
            sent, estimate = plate_messages(str(photo or config.CAPABILITIES_PHOTO))
            out.append(Probe(node, sent, estimate, schema=Extraction, images=1))
        elif node == "classifier":
            msgs = classifier_messages(CLASSIFIER_IDENTITY, CLASSIFIER_SYMPTOM, CLASSIFIER_CAUSES)
            out.append(Probe(node, msgs, msgs, schema=ClassifierVerdict))
        elif node == "research":
            msgs = [SystemMessage(RESEARCH_PROMPT), HumanMessage(RESEARCH_USER)]
            out.append(Probe(node, msgs, msgs, tools=_research_tools(mode)))
        else:
            msgs = schema_check.messages()
            schema_text = HumanMessage(json.dumps(BriefDraft.model_json_schema()))
            out.append(Probe(node, msgs, [*msgs, schema_text], schema=BriefDraft))
    return out


def estimate_tokens(probe: Probe) -> int:
    return estimate_input_tokens(probe.estimate_messages, model=config.HAIKU, tools=probe.tools or None,
                                 images=probe.images)


def planned_usd(probe: Probe, mode: str) -> tuple[float, float]:
    """(typical, worst case) for one call: the estimate with a short reply, and the full reservation."""
    tokens = estimate_tokens(probe)
    max_tokens = models.max_tokens_for(probe.node, mode)
    typical_out = min(max_tokens, config.typical_tokens(config.HAIKU, config.SCHEMA_CHECK_OUTPUT_TOKENS_TYPICAL))
    typical = price_usage(config.HAIKU, {"input_tokens": tokens, "output_tokens": typical_out})
    worst = price_usage(config.HAIKU, {"input_tokens": tokens, "output_tokens": max_tokens})
    return typical, worst


def plan_lines(mode: str, plan: Sequence[Probe], ledger: Ledger) -> list[str]:
    """The text printed before the first call."""
    spent = ledger.build_total()
    lines = [f"advisor check-capabilities: preflight, mode {mode}, LIVE (real money)",
             f"planned: {len(plan)} small calls on {config.HAIKU}, one per node it runs, each at that node's "
             "exact settings; no retries, no tool is run, and the first failure stops the check"]
    total_typical = total_worst = 0.0
    for probe in plan:
        kwargs = models.live_model_kwargs(probe.node, mode)
        typical, worst = planned_usd(probe, mode)
        total_typical += typical
        total_worst += worst
        what = "with the demo plate photo" if probe.images else ("with the search and fetch tools" if probe.tools
                                                                else f"with the {probe.schema.__name__} schema")
        lines.append(f"  {probe.node}: effort {kwargs.get('reasoning_effort')}, max_tokens {kwargs['max_tokens']}, "
                     f"{what}; typical {typical:.4f} USD, worst case {worst:.4f} USD")
    lines += [
        f"total estimated cost: typical {total_typical:.4f} USD, worst case {total_worst:.4f} USD; each call is "
        f"reserved and charged in the ledger under node {NODE}",
        f"build spend so far (live runs): {spent:.4f} USD of {config.BUILD_CAP_USD:.4f} USD",
        f"Type {PROCEED} to spend this; anything else cancels and spends nothing.",
    ]
    return lines


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _reply_model(message: Any) -> str | None:
    """The model name a reply carried in its response_metadata, as the recorder reads it."""
    meta = getattr(message, "response_metadata", None) or {}
    name = meta.get("model_name") or meta.get("model")
    return name if isinstance(name, str) and name else None


def _thinking_first(raw: Any) -> bool:
    content = getattr(raw, "content", None)
    return isinstance(content, list) and bool(content) and isinstance(content[0], dict) \
        and content[0].get("type") == "thinking"


def run_probe(probe: Probe, ledger: Ledger, mode: str, run_id: str,
              chat_factory: Callable[..., Any]) -> ProbeResult:
    """Reserve, make one call at the node's settings, settle it, and say what it showed."""
    kwargs = models.live_model_kwargs(probe.node, mode)
    result = ProbeResult(probe.node, ERROR, kwargs.get("reasoning_effort"), kwargs["max_tokens"])
    reservation = ledger.reserve(run_id, mode=mode, node=NODE, model=config.HAIKU,
                                 input_tokens_est=estimate_tokens(probe), max_tokens=kwargs["max_tokens"])
    try:
        chat = chat_factory(**kwargs)
        if probe.schema is None:
            raw = chat.bind_tools(probe.tools).invoke(probe.messages)
            out = {"raw": raw, "parsed": raw, "parsing_error": None}
        else:
            out = chat.with_structured_output(probe.schema, method="json_schema", include_raw=True).invoke(
                probe.messages)
    except ModelDeclined as exc:
        result.reply_model = _reply_model(exc.ai_message)
        usage = exc.ai_message.usage_metadata
        result.usd = ledger.charge(reservation, dict(usage)) if usage else ledger.charge_estimated(
            reservation, NO_USAGE_NOTE)
        result.status, result.stop_reason, result.category = DECLINED, "refusal", exc.category
        result.message = f"the model declined (category {exc.category or 'none given'})"
        return result
    except _timeout_errors() as exc:
        result.usd = ledger.charge_timeout(reservation)
        result.message = f"timed out or lost the connection: {type(exc).__name__}"
        return result
    except Exception as exc:
        billed = getattr(exc, "ai_message", None)
        usage = getattr(billed, "usage_metadata", None) if billed is not None else None
        if usage:
            result.usd = ledger.charge(reservation, dict(usage))
        elif billed is not None:
            result.usd = ledger.charge_estimated(reservation, NO_USAGE_NOTE)
        else:
            ledger.release(reservation)
        result.http_status = _http_status(exc)
        result.status = REJECTED if result.http_status == 400 else ERROR
        result.message = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"
        return result
    raw = out.get("raw")
    usage = getattr(raw, "usage_metadata", None)
    result.usd = ledger.charge(reservation, dict(usage)) if usage else ledger.charge_estimated(
        reservation, NO_USAGE_NOTE)
    result.status = ACCEPTED
    result.reply_model = _reply_model(raw)
    result.stop_reason = (getattr(raw, "response_metadata", None) or {}).get("stop_reason")
    result.thinking_first = _thinking_first(raw)
    if probe.schema is None:
        result.parsed = isinstance(raw, AIMessage) and bool(raw.tool_calls or raw.text)
    else:
        result.parsed = out.get("parsing_error") is None and out.get("parsed") is not None
    result.message = "accepted; " + ("the reply parsed" if result.parsed else "the reply did not parse")
    return result


def photo_refused(outcome: ProbeResult) -> bool:
    """True when the plate probe's failure is about the photo: a decline, or an HTTP 400 whose message
    names the image. The plate probe goes first and carries every setting shared by the Haiku calls
    (effort, the json_schema output format, max_tokens), so any other 400 is not blamed on the photo:
    it leaves image input unchecked and stops the check with its own message."""
    if outcome.status == DECLINED:
        return True
    return outcome.status == REJECTED and bool(IMAGE_WORDS.search(outcome.message or ""))


def run_check(ledger: Ledger, mode: str, run_id: str, *, chat_factory: Callable[..., Any] | None = None,
              photo: Path | None = None) -> CapabilitiesResult:
    """Every planned call in order, stopping at the first that is not accepted and parsed."""
    factory = chat_factory or models.ChatAnthropic
    plan = probes(mode, photo)
    results: list[ProbeResult] = []
    image = IMAGE_UNCHECKED
    for probe in plan:
        outcome = run_probe(probe, ledger, mode, run_id, factory)
        results.append(outcome)
        if probe.node == "read_plate":
            image = IMAGE_ACCEPTED if outcome.status == ACCEPTED else (
                IMAGE_REFUSED if photo_refused(outcome) else IMAGE_UNCHECKED)
        if outcome.status != ACCEPTED or not outcome.parsed:
            break
    for probe in plan[len(results):]:
        kwargs = models.live_model_kwargs(probe.node, mode)
        results.append(ProbeResult(probe.node, NOT_RUN, kwargs.get("reasoning_effort"), kwargs["max_tokens"],
                                   message="not run: an earlier call failed"))
    return CapabilitiesResult(model=config.HAIKU, mode=mode, checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
                              image_input=image, results=results)


def write_result(result: CapabilitiesResult, path: Path | None = None) -> Path:
    """The record preflight.plate_refusal reads."""
    target = Path(path) if path is not None else capabilities_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"model": result.model, "mode": result.mode, "checked_at": result.checked_at,
              "image_input": result.image_input, "passed": result.passed,
              "results": [asdict(r) for r in result.results]}
    target.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return target


def report_lines(result: CapabilitiesResult) -> list[str]:
    """What the check prints after its calls."""
    lines = [f"capabilities check on {result.model}: {'passed' if result.passed else 'STOPPED'}"]
    for r in result.results:
        thinking = "began with a thinking block" if r.thinking_first else "no thinking block first"
        detail = f", {thinking}, stop_reason {r.stop_reason}" if r.status == ACCEPTED else ""
        lines.append(f"  {r.node}: {r.status}{detail}; {r.message}; {r.usd:.4f} USD")
    if result.image_input == IMAGE_REFUSED:
        lines.append(f"  STOP: {result.model} refused the plate photo. Plate reading is now off in live modes "
                     "(it refuses with a message and never moves to another model). Report this.")
    elif not result.calls_passed:
        lines.append("  STOP: a call was not accepted or did not parse. Report this before any live run.")
    if result.other_reply_models:
        lines.append(f"  STOP: replies named {', '.join(result.other_reply_models)}, not {result.model}. Live "
                     f"runs are priced and recorded as {result.model}, and a recording leaves out a reply model "
                     "with no price in config.PRICES_PER_MTOK. Report this before any live run.")
    if result.passed and not any(r.thinking_first for r in result.results):
        lines.append("  note: no reply began with a thinking block, so this check did not see one parse; "
                     "the offline tests cover that shape")
    return lines


def check(ledger: Ledger, mode: str, *, stdin: IO[str], stdout: IO[str], run_id: str,
          chat_factory: Callable[..., Any] | None = None, photo: Path | None = None,
          result_path: Path | None = None) -> CapabilitiesResult | None:
    """Print the plan, read the typed "proceed", run the calls, print and record the result.

    None when the typed line was not exactly "proceed": nothing was spent.
    """
    if mode not in config.LIVE_MODES:
        raise ValueError(f"the capabilities check makes paid calls; {mode!r} is not a live mode")
    plan = probes(mode, photo)
    if not ask_to_proceed(plan_lines(mode, plan, ledger), stdin, stdout):
        print('canceled: the typed confirmation was not exactly "proceed"; nothing was spent.', file=stdout)
        return None
    result = run_check(ledger, mode, run_id, chat_factory=chat_factory, photo=photo)
    write_result(result, result_path)
    for line in report_lines(result):
        print(line, file=stdout)
    return result
