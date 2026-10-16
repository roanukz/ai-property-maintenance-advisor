"""The advisor as a local MCP server over stdio (decision 97).

Claude Desktop, Claude Code or any MCP client can ask for a service brief, read
a property's status and run the product recall check through four tools, while
every guarantee stays in the advisor's own code. The tools start and resume the
same graph through the same pieces `advisor ask` and `advisor resume` use
(agent/cli.py: intake_input, run_context, the checkpointer and LiveSession), so
the ledger and its caps, the citations, the refusals, Jev's safety check and
the code written warranty dates apply unchanged. This module adapts arguments
and answers; it decides nothing about a brief.

Tools
- get_service_brief: a symptom and exactly one of a plate photo path, a
  registry appliance id or a typed identity (`advisor ask`).
- resume_brief: a paused thread and the confirmed identity (`advisor resume`).
- property_status: one property's warranty coverage, its "Plan a replacement"
  rows and its stored product recall checks; offline and free, from the code
  behind `advisor property show`.
- check_product_recalls: the CPSC check of `advisor property recalls --live`.

Resources: advisor://briefs/{run_id} is the HTML brief a run wrote under
config.BRIEFS_OUT_DIR; advisor://property/{property_id} is the page `advisor
property show` writes, rendered when it is read and never written.

Questions put to the person. A run asks two kinds: the identity confirmation
at confirm_identity's pause, and, before any paid or outbound call, the
existing preflight (planned calls and cost) followed by the equivalent of a
typed "proceed".
- A client that declared form elicitation is asked by elicitation: on a
  handshake era connection (protocol 2025-11-25 or earlier) as a request in the
  middle of the call (ctx.elicit), and on a 2026-07-28 connection by returning
  the question (InputRequiredResult), which the client answers by calling the
  same tool again with the answer attached.
- Otherwise the tool returns the paused thread with the identity read and what
  is missing, to finish with resume_brief as `advisor resume` would, or the
  preflight with a confirmation id, to answer on a second call with
  confirm="proceed" and that id.
A confirmation answers one preflight. It is kept in this process only, bound to
the tool, its arguments and the exact preflight lines, used once and dropped
after CONFIRMATION_TTL_S seconds. When the lines differ by the time it is used
(the build spend moved), the preflight is shown again and nothing is sent. Only
"proceed" exactly, read as preflight.confirmed reads a typed line, confirms.

Modes. ADVISOR_MODE picks the mode as it does for `advisor`; a live mode also
needs the server started with --live, and every live call opens the existing
ledger (never creating one), loads the keys and checks the caps as a live
`advisor ask` does. Replay needs a cassette, from --cassette or
ADVISOR_CASSETTE, and spends nothing. The product recall check, free and
keyless but sent to an outside service, reads no ADVISOR_MODE and opens no
ledger, as `advisor property recalls --live` does, and waits for the same
confirmation after product_recalls.plan_lines.

Runs and checks hold one lock, so one runs at a time in this process (PLAN risk
R17): LiveSession puts keys in the environment and swaps the printed streams
for the length of a run, and a paused thread being confirmed in one call cannot
be resumed by another.
"""

from __future__ import annotations

# agent.cli first: importing it scrubs the tracing variables before anything
# can import langchain, langgraph or langsmith (its module docstring). Those
# load only inside functions here, as they do in agent/cli.py.
from agent import cli, config  # isort: skip

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio.to_thread
from mcp.server.elicitation import render_elicitation_schema
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)
from mcp.types.version import is_version_at_least
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

SERVER_NAME = "property-maintenance-advisor"
SERVER_TITLE = "Property Maintenance Advisor"
TOOL_BRIEF = "get_service_brief"
TOOL_RESUME = "resume_brief"
TOOL_STATUS = "property_status"
TOOL_RECALLS = "check_product_recalls"
BRIEF_URI = "advisor://briefs/{run_id}"
PROPERTY_URI = "advisor://property/{property_id}"
HTML = "text/html"

# How long a shown preflight or a returned question can be answered, in seconds.
CONFIRMATION_TTL_S = 600
# Identity questions one call asks before it hands back the paused thread.
MAX_IDENTITY_ASKS = 3
# The first protocol revision whose tools return questions instead of asking mid call.
RETURNED_QUESTIONS_VERSION = "2026-07-28"
# Keys of the questions a returned InputRequiredResult carries.
KEY_IDENTITY = "identity"
KEY_PROCEED = "proceed"
# Thread, run and property ids are short tokens (t-<hex>, prop-a, appl-xr16).
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

STAGE_ASK = "ask"
STAGE_RESUME = "resume"
STAGE_RECALLS = "recalls"

PROCEED_FIELD = "Type proceed to go ahead. Anything else cancels, and nothing is sent or spent."
NOT_CONFIRMED = 'The confirmation was not exactly "proceed"; nothing was sent or spent.'
PREFLIGHT_CHANGED = ("The preflight changed after it was shown (spend or credits moved), so it is shown again; "
                     "nothing was sent or spent.")
REPLAY_NEEDS_CASSETTE = (f"replay needs a cassette: start the server with --cassette PATH or set "
                         f"{config.ENV_CASSETTE}. Replay spends nothing.")
ONE_SOURCE = "give exactly one of photo_path, appliance_id or identity"

# What the assistant must do when a brief pauses for the unit: the server cannot tell who supplied the fields
# resume_brief receives, so it asks the assistant to leave them to the owner (decision 105).
ASK_OWNER = ("Show the owner what was read and ask them to confirm or correct it; never fill in or change a field "
             "yourself.")

INSTRUCTIONS = (
    "Property Maintenance Advisor v2. A brief comes only from documented sources the advisor's code checks, and "
    "a no_reliable_answer brief is a success state, not an error. Report safety flagged steps first and word for "
    "word, with their sources. Paid or outbound calls (a live brief, the CPSC product recall check) always show a "
    "preflight first; pass confirm=\"proceed\" only after the person has read that preflight and typed proceed "
    "themselves, and never decide it for them. When a brief pauses for the owner to confirm the unit: "
    + ASK_OWNER + " property_status is offline and free."
)


# ---------------------------------------------------------------------------
# Tool inputs and the structured content every tool returns
# ---------------------------------------------------------------------------


class TypedIdentity(BaseModel):
    """A unit's identity typed from its data plate; leave out what the plate does not print."""

    model_config = ConfigDict(extra="forbid")

    manufacturer: str | None = Field(None, description="Manufacturer, as printed on the data plate")
    model: str | None = Field(None, description="Model, exactly as printed on the data plate")
    serial: str | None = Field(None, description="Serial number")
    manufacture_date: str | None = Field(None, description="Manufacture date, as printed")


class Source(BaseModel):
    number: int = Field(description="The citation number the brief page prints as [number]")
    url: str
    title: str | None = None
    tier: str | None = None
    host: str | None = None
    retrieved_at: str | None = None


class Step(BaseModel):
    position: int
    step: str
    detail: str | None = None
    safety_flag: bool
    raised_by: str | None = Field(None, description="Which check set the safety flag: writer, word, jev or "
                                                    "word_fallback; none when the step is not flagged")
    source: Source | None = None


class SafetyFlag(BaseModel):
    position: int = Field(description="The step's position in try_first")
    step: str
    detail: str | None = None
    raised_by: str | None = None
    source: Source | None = None


class Candidate(BaseModel):
    code: str | None = None
    confirmed: bool
    documented_meaning: str
    documented_action: str
    who: str | None = None
    why_shown: str | None = None
    evidence: str | None = None
    source: Source | None = None


class Caution(BaseModel):
    text: str
    source: Source | None = None


class Warranty(BaseModel):
    age_statement: str | None = None
    coverage_statement: str | None = Field(None, description="The warranty coverage line, written by code")
    coverage_as_of: str | None = None
    coverage: list[dict[str, Any]] = Field(default_factory=list)
    terms: str | None = None
    record_id: str | None = None
    cautions: list[Caution] = Field(default_factory=list)
    verify: list[str] = Field(default_factory=list)


class NoReliableAnswer(BaseModel):
    searched: list[str] = Field(default_factory=list)
    found: list[str] = Field(default_factory=list)
    why_insufficient: str | None = None


class SafetyCheck(BaseModel):
    status: str | None = None
    reason: str | None = None
    notice: str | None = Field(None, description="The notice line the brief shows when the automatic check failed")


class Brief(BaseModel):
    status: str
    stop_reason: str | None = None
    refusal_origin: str | None = None
    observed_code: Any = None
    matched_identity: Any = None
    try_first: list[Step] = Field(default_factory=list)
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    safety_check: SafetyCheck = Field(default_factory=SafetyCheck)
    candidates: list[Candidate] = Field(default_factory=list)
    warranty_caution: Caution | None = None
    warranty: Warranty | None = None
    no_reliable_answer: NoReliableAnswer | None = None
    upgrade_options: list[dict[str, Any]] = Field(default_factory=list)
    maintenance_due: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    full: dict[str, Any] = Field(description="The brief exactly as the run recorded it")


class Pause(BaseModel):
    identity: dict[str, str | None] = Field(description="What the owner is asked to confirm: read from the plate, "
                                                         "typed, or the last answer")
    extraction: dict[str, Any] | None = Field(None, description="What the plate photo read, with confidence")
    missing: list[str]
    code_shown: str | None = None
    prompt: str


class Preflight(BaseModel):
    stage: Literal["ask", "resume", "recalls"]
    lines: list[str] = Field(description="The preflight, exactly as the advisor command prints it")
    confirmation_id: str | None = None
    expires_at: str | None = None
    sends: list[str] = Field(default_factory=list, description="What else goes out with these calls")


class Cost(BaseModel):
    ledger: Literal["live", "replay"]
    run_total_usd: float
    tavily_credits: int
    run_cap_usd: float | None = None
    real_money: bool
    searches: int | None = None
    fetches: int | None = None
    sc11: dict[str, Any] | None = None


class Link(BaseModel):
    path: str
    file_uri: str
    resource_uri: str


class BriefResponse(BaseModel):
    status: Literal["ok", "no_reliable_answer", "budget_stopped", "paused", "confirmation_required", "not_confirmed"]
    message: str
    mode: str
    build_id: str
    thread_id: str | None = None
    run_id: str | None = None
    brief: Brief | None = None
    pause: Pause | None = None
    preflight: Preflight | None = None
    cost: Cost | None = None
    html: Link | None = None
    next_step: str | None = None
    notes: list[str] = Field(default_factory=list, description="What a live run printed after it ended (SC11)")


class ApplianceStatus(BaseModel):
    appliance_id: str
    category: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    model_number: str | None = None
    serial: str | None = None
    install_date: str | None = None
    purchase_date: str | None = None
    record_id: str | None = None
    synthetic: bool
    warranty: dict[str, Any] = Field(description="Coverage rows with end dates and statuses computed by code, and "
                                                 "the coverage line a brief would print")
    replacement: dict[str, Any] = Field(description="The Plan a replacement row: age and verified facts")
    recalls: dict[str, Any] = Field(description="The latest stored product recall check, as the page shows it")


class PropertyStatusResponse(BaseModel):
    property_id: str
    label: str | None = None
    synthetic: bool
    as_of: str = Field(description="The one UTC date every status here is computed for")
    graph: str
    appliances: list[ApplianceStatus]
    page: Link
    message: str


class RecallResult(BaseModel):
    appliance_id: str
    status: str
    lines: list[str]
    stored: str | None = None
    errors: list[Any] = Field(default_factory=list)


class RecallsResponse(BaseModel):
    status: Literal["checked", "confirmation_required", "not_confirmed"]
    message: str
    properties: list[str]
    preflight: Preflight | None = None
    run_id: str | None = None
    run_record: str | None = None
    complete: bool | None = None
    results: list[RecallResult] = Field(default_factory=list)
    cost_usd: float = 0
    next_step: str | None = None


class Proceed(BaseModel):
    """The typed "proceed" a preflight waits for."""

    confirmation: str = Field(description=PROCEED_FIELD)


# ---------------------------------------------------------------------------
# Questions: who can be asked, and the confirmations kept between calls
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Asker:
    """How this call can put a question to the person.

    `pushes`: a handshake era client that declared form elicitation and can
    take a request mid call. `returns`: a 2026-07-28 client that declared it,
    asked by returning the question. Neither: the tool returns what it needs.
    The capability rule is the SDK resolver's: a bare elicitation capability
    counts as form support, a url only one does not.
    """

    pushes: bool = False
    returns: bool = False

    @classmethod
    def of(cls, ctx: Context | None) -> Asker:
        if ctx is None:
            return cls()
        capabilities = ctx.client_capabilities
        elicitation = capabilities.elicitation if capabilities is not None else None
        if elicitation is None or (elicitation.form is None and elicitation.url is not None):
            return cls()
        version = ctx.protocol_version
        if version is not None and is_version_at_least(version, RETURNED_QUESTIONS_VERSION):
            return cls(returns=True)
        return cls(pushes=bool(ctx.session.can_send_request))


@dataclass
class Pending:
    """A preflight shown or a question returned, waiting for its one answer."""

    kind: str  # STAGE_ASK, STAGE_RESUME, STAGE_RECALLS (a preflight) or KEY_IDENTITY (a question)
    tool: str
    digest: str  # of the call's arguments, the confirmation arguments left out
    lines: tuple[str, ...] = ()
    thread_id: str | None = None
    resume: dict[str, Any] | None = None  # an accepted identity answer waiting for its resume preflight
    question: str | None = None  # digest of the identity question asked
    asks: int = 0  # identity questions this flow has asked
    expires: float = 0
    expires_at: str = ""


class PendingAnswers:
    """Confirmations and returned questions, in this process only: used once, dropped when they expire."""

    def __init__(self, *, ttl_s: float = CONFIRMATION_TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._items: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def add(self, pending: Pending) -> str:
        ident = "c-" + secrets.token_hex(12)
        pending.expires = self.clock() + self.ttl_s
        pending.expires_at = (datetime.now(UTC) + timedelta(seconds=self.ttl_s)).isoformat(timespec="seconds")
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v.expires > self.clock()}
            self._items[ident] = pending
        return ident

    def take(self, ident: Any, *, tool: str, digest: str, kinds: Sequence[str]) -> Pending | None:
        """The pending answer for `ident`, removed, when it is unexpired and was made for this call."""
        if not isinstance(ident, str):
            return None
        with self._lock:
            pending = self._items.pop(ident, None)
        if pending is None or pending.expires <= self.clock():
            return None
        if pending.tool != tool or pending.digest != digest or pending.kind not in kinds:
            return None
        return pending

    def expires_at(self, ident: str) -> str | None:
        with self._lock:
            pending = self._items.get(ident)
        return pending.expires_at if pending is not None else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


# What the person did with a question, for the messages that report it.
ACTION_WORDS = {"accept": "accepted", "decline": "declined", "cancel": "canceled"}


def asked_result(answer: Any) -> str:
    action = getattr(answer, "action", None)
    return ACTION_WORDS.get(action, "not answered") if isinstance(action, str) else "not answered"


def args_digest(tool: str, args: Mapping[str, Any]) -> str:
    """The digest that binds a confirmation to one tool and its exact arguments."""
    text = json.dumps({"tool": tool, "args": dict(args)}, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proceed_answered(answer: Any) -> bool:
    """True for an accepted answer whose confirmation is exactly "proceed" (preflight.confirmed)."""
    from agent.live.preflight import confirmed

    if isinstance(answer, ElicitResult):
        content = answer.content or {}
        return answer.action == "accept" and isinstance(content.get("confirmation"), str) and \
            confirmed(content["confirmation"])
    data = getattr(answer, "data", None)
    return getattr(answer, "action", None) == "accept" and isinstance(getattr(data, "confirmation", None), str) \
        and confirmed(data.confirmation)


def spend_message(lines: Sequence[str], sends: Sequence[str] = ()) -> str:
    """The elicitation text for a preflight: what else is sent, then the preflight word for word."""
    return "\n".join([*sends, *lines])


def proceed_request(lines: Sequence[str], sends: Sequence[str] = ()) -> ElicitRequest:
    return ElicitRequest(params=ElicitRequestFormParams(message=spend_message(lines, sends),
                                                        requested_schema=render_elicitation_schema(Proceed)))


# ---------------------------------------------------------------------------
# The identity question (confirm_identity's interrupt payload)
# ---------------------------------------------------------------------------

IDENTITY_FIELDS = ("manufacturer", "model", "serial", "manufacture_date")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def identity_form(payload: Mapping[str, Any]) -> type[BaseModel]:
    """The form for one pause: the proposed identity and the code shown, filled in, every field text."""
    proposed = payload.get("identity") or {}
    return create_model(
        "IdentityConfirmation",
        manufacturer=(str, Field(default=_text(proposed.get("manufacturer")),
                                 description="Manufacturer, as printed on the data plate")),
        model=(str, Field(default=_text(proposed.get("model")),
                          description="Model, exactly as printed on the data plate. Required.")),
        serial=(str, Field(default=_text(proposed.get("serial")),
                           description="Serial number; leave blank when it is not known")),
        manufacture_date=(str, Field(default=_text(proposed.get("manufacture_date")),
                                     description="Manufacture date; leave blank when it is not known")),
        code=(str, Field(default=_text(payload.get("observed_code_candidate")),
                         description="The code shown on the unit's display; leave blank when none is shown")),
    )


def _read_line(extraction: Mapping[str, Any]) -> str:
    confidence = extraction.get("confidence") or {}
    parts = [f"{name.replace('_', ' ')} {extraction.get(name) or 'not read'} ({confidence.get(name, 'unknown')})"
             for name in IDENTITY_FIELDS]
    return "Read from the plate photo: " + "; ".join(parts) + "."


def identity_message(payload: Mapping[str, Any], thread_id: str) -> str:
    """The question for one pause: the prompt, what was read, what is missing, and the thread to resume."""
    proposed = payload.get("identity") or {}
    lines = [str(payload.get("prompt") or "")]
    if isinstance(payload.get("extraction"), Mapping):
        lines.append(_read_line(payload["extraction"]))
    lines.append("To confirm: " + "; ".join(f"{name.replace('_', ' ')} {proposed.get(name) or 'not given'}"
                                            for name in IDENTITY_FIELDS) + ".")
    missing = list(payload.get("missing") or [])
    lines.append("Missing: " + (", ".join(m.replace("_", " ") for m in missing) if missing else "nothing") + ".")
    code = payload.get("observed_code_candidate")
    lines.append(f"Code shown on the display: {code}." if code else "No code is shown.")
    lines.append(f"The run is paused as thread {thread_id}. If this form is closed, the run stays paused and "
                 "can be resumed later with resume_brief.")
    return "\n".join(line for line in lines if line)


def identity_request(payload: Mapping[str, Any], thread_id: str) -> ElicitRequest:
    return ElicitRequest(params=ElicitRequestFormParams(
        message=identity_message(payload, thread_id),
        requested_schema=render_elicitation_schema(identity_form(payload))))


def question_digest(payload: Mapping[str, Any], thread_id: str) -> str:
    request = identity_request(payload, thread_id)
    text = json.dumps(request.params.model_dump(mode="json", by_alias=True), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resume_value_from(fields: Mapping[str, Any], proposed: Mapping[str, Any] | None) -> dict[str, Any]:
    """The resume value `advisor resume` builds (cli.resume_value) from confirmed fields; None keeps a field."""
    args = argparse.Namespace(manufacturer=fields.get("manufacturer"), model=fields.get("model"),
                              serial=fields.get("serial"), date=fields.get("manufacture_date"),
                              code=fields.get("code"))
    return cli.resume_value(args, proposed)


# ---------------------------------------------------------------------------
# A live session whose typed "proceed" is the confirmation the client gave
# ---------------------------------------------------------------------------


class PreflightChanged(Exception):
    """The preflight a run would print differs from the one confirmed; nothing was spent."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__("the preflight changed after it was confirmed")
        self.lines = lines


class McpLiveSession(cli.LiveSession):
    """cli.LiveSession with its typed "proceed" replaced by a confirmation of these exact lines.

    Every check LiveSession makes when it is built stays (full mode, keys,
    the Haiku date, registry drift); `confirm` builds the lines
    LiveSession.confirm prints and continues only when they equal the lines
    the person confirmed, so a run never reads stdin, which is the MCP wire.
    """

    approved: list[str] | None = None

    def preflight(self, plans: list, extra: Sequence[str] = ()) -> list[str]:
        """The lines LiveSession.confirm prints before it reads the typed line."""
        from agent.live.preflight import preflight_lines

        lines = preflight_lines(self.command, self.mode, plans, self.ledger)
        lines[-1:-1] = list(extra)
        keys = ", ".join(f"{name} from the {source}" if source == "environment" else f"{name} from .env"
                         for name, source in self.sources.items())
        lines.insert(1, f"keys: {keys} (values are never shown)")
        return lines

    def run_preflight(self, stage: str, *, photo: bool, run_id: str | None) -> list[str]:
        """The preflight `confirm_run` would show for one stage of one run."""
        from agent.live.preflight import run_plan

        spent = self.ledger.run_total(run_id) if run_id else 0.0
        return self.preflight([run_plan(self.mode, photo=photo, stage=stage, spent_in_run=spent)])

    def confirm(self, plans: list, extra: Sequence[str] = ()) -> None:
        lines = self.preflight(plans, extra)
        if self.approved is None or lines != self.approved:
            raise PreflightChanged(lines)


# ---------------------------------------------------------------------------
# What one run segment reached
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """One ask or resume segment: the thread's state, its pause if any, and the run context it used."""

    thread_id: str
    run_id: str
    values: dict[str, Any]
    interrupt: dict[str, Any] | None
    ctx: Any = None
    notes: list[str] = field(default_factory=list)

    @property
    def paused(self) -> bool:
        return self.interrupt is not None


@dataclass
class BriefArgs:
    symptom: str
    photo_path: str | None = None
    appliance_id: str | None = None
    identity: dict[str, Any] | None = None
    property_id: str | None = None
    code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"symptom": self.symptom, "photo_path": self.photo_path, "appliance_id": self.appliance_id,
                "identity": self.identity, "property_id": self.property_id, "code": self.code}


def _safe_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.match(value):
        raise cli.CliRefusal(f"{what} {value!r} is not an id this tool uses")
    return value


def _link(path: Path | str, resource_uri: str) -> Link:
    resolved = Path(path).expanduser().resolve()
    return Link(path=str(resolved), file_uri=resolved.as_uri(), resource_uri=resource_uri)


def _source(sources: Sequence[Any], index: Any) -> Source | None:
    """The cited source, computed only from a valid integer index, as the brief page computes its [n] label."""
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(sources):
        return None
    s = sources[index]
    if not isinstance(s, Mapping) or not isinstance(s.get("url"), str):
        return None
    return Source(number=index + 1, url=s["url"], title=s.get("title"), tier=s.get("tier"), host=s.get("host"),
                  retrieved_at=s.get("retrieved_at"))


def brief_view(brief: Mapping[str, Any], safety: Mapping[str, Any] | None, signals: Mapping[str, Any] | None,
               state: Mapping[str, Any]) -> Brief:
    """The brief as structured content: every step with its source and flag, every safety flag, every citation.

    `safety` is the run record's safety section (persist.safety_record), whose
    steps follow the brief's try_first order; `signals` are the run's
    safety_signals, which decide the notice line as the brief page decides it.
    """
    from agent.nodes.safety_check import notice_needed
    from agent.render.brief_html import SAFETY_NOTICE

    sources = list(brief.get("sources") or [])
    provenance = list((safety or {}).get("steps") or [])
    steps: list[Step] = []
    for n, s in enumerate(brief.get("try_first") or []):
        if not isinstance(s, Mapping):
            continue
        entry = provenance[n] if n < len(provenance) and isinstance(provenance[n], Mapping) else {}
        raised = entry.get("raised_by") if entry.get("step") == s.get("step") else None
        steps.append(Step(position=n + 1, step=str(s.get("step")), detail=s.get("detail"),
                          safety_flag=s.get("safety_flag") is True, raised_by=raised,
                          source=_source(sources, s.get("source_index"))))
    flags = [SafetyFlag(position=s.position, step=s.step, detail=s.detail, raised_by=s.raised_by, source=s.source)
             for s in steps if s.safety_flag]
    candidates = [
        Candidate(code=c.get("code") if isinstance(c.get("code"), str) else None, confirmed=c.get("confirmed") is True,
                  documented_meaning=str(c.get("documented_meaning")), documented_action=str(c.get("documented_action")),
                  who=c.get("who"), why_shown=c.get("why_shown"), evidence=c.get("evidence"),
                  source=_source(sources, c.get("source_index")))
        for c in brief.get("candidates") or [] if isinstance(c, Mapping)]
    caution = brief.get("warranty_caution")
    warranty = brief.get("warranty")
    refusal = brief.get("no_reliable_answer")
    return Brief(
        status=str(brief.get("status")),
        stop_reason=state.get("stop_reason"),
        refusal_origin=state.get("refusal_origin"),
        observed_code=brief.get("observed_code"),
        matched_identity=brief.get("matched_identity"),
        try_first=steps,
        safety_flags=flags,
        safety_check=SafetyCheck(status=(safety or {}).get("status"), reason=(safety or {}).get("reason"),
                                 notice=SAFETY_NOTICE if notice_needed(dict(signals or {})) else None),
        candidates=candidates,
        warranty_caution=Caution(text=str(caution.get("text")), source=_source(sources, caution.get("source_index")))
        if isinstance(caution, Mapping) else None,
        warranty=Warranty(
            age_statement=warranty.get("age_statement"), coverage_statement=warranty.get("coverage_statement"),
            coverage_as_of=warranty.get("coverage_as_of"), coverage=list(warranty.get("coverage") or []),
            terms=warranty.get("terms"), record_id=warranty.get("record_id"),
            cautions=[Caution(text=str(c.get("text")), source=_source(sources, c.get("source_index")))
                      for c in warranty.get("cautions") or [] if isinstance(c, Mapping)],
            verify=[str(v) for v in warranty.get("verify") or []],
        ) if isinstance(warranty, Mapping) else None,
        no_reliable_answer=NoReliableAnswer(
            searched=[str(v) for v in refusal.get("searched") or []], found=[str(v) for v in refusal.get("found") or []],
            why_insufficient=refusal.get("why_insufficient"),
        ) if isinstance(refusal, Mapping) else None,
        upgrade_options=[dict(u) for u in brief.get("upgrade_options") or [] if isinstance(u, Mapping)],
        maintenance_due=[dict(m) for m in brief.get("maintenance_due") or [] if isinstance(m, Mapping)],
        sources=[src for src in (_source(sources, i) for i in range(len(sources))) if src is not None],
        full=dict(brief),
    )


def counted(n: int, one: str, many: str) -> str:
    """"1 step", "2 steps": a count with its noun, singular when the count is 1."""
    return f"{n} {one if n == 1 else many}"


def brief_counts(steps: int, flagged: int, candidates: int) -> str:
    """The brief's counts as the result message says them, singular when a count is 1."""
    flags = "flagged as a safety step" if flagged == 1 else "flagged as safety steps"
    return (f"{counted(steps, 'step', 'steps')} to try first, {flagged} {flags}; "
            f"{counted(candidates, 'documented candidate', 'documented candidates')}")


def _cite(source: Source | None) -> str:
    return f" [{source.number}]" if source is not None else " (uncited)"


def brief_text(response: BriefResponse) -> str:
    """The brief as plain text for the model: every step, flag, citation, the coverage line, cost and run id."""
    brief = response.brief
    assert brief is not None
    full = brief.full
    lines = [f"Service brief, status {brief.status}, run {response.run_id}, {response.mode} mode."]
    if brief.matched_identity:
        lines.append(f"Matched documentation for: {brief.matched_identity}")
    if brief.no_reliable_answer is not None:
        block = brief.no_reliable_answer
        lines.append("No reliable answer. " + (block.why_insufficient or ""))
        if block.searched:
            lines.append("What was searched: " + "; ".join(block.searched))
        if block.found:
            lines.append("What was found: " + "; ".join(block.found))
    if brief.stop_reason:
        lines.append(f"Stop reason: {brief.stop_reason}")
    if brief.warranty_caution is not None:
        lines.append(f"Warranty caution: {brief.warranty_caution.text}{_cite(brief.warranty_caution.source)}")
    if brief.try_first:
        lines.append("Try this first:")
        if brief.safety_check.notice:
            lines.append(f"  {brief.safety_check.notice}")
        for s in brief.try_first:
            lines.append(f"  {s.position}. {'SAFETY: ' if s.safety_flag else ''}{s.step}{_cite(s.source)}")
            if s.detail:
                lines.append(f"     {s.detail}")
    if brief.candidates:
        lines.append("Documented candidates:")
        for c in brief.candidates:
            code = c.code or "no code"
            lines.append(f"  - {code}{' (confirmed)' if c.confirmed else ''}: {c.documented_meaning} "
                         f"Action: {c.documented_action} Who: {c.who}.{_cite(c.source)}")
    for u in brief.upgrade_options:
        lines.append(f"Upgrade option: {u.get('successor_manufacturer') or ''} {u.get('successor_model')} "
                     f"({u.get('reason')}): {u.get('summary')}{_cite(_source(full.get('sources') or [], u.get('source_index')))}")
    if brief.warranty is not None:
        w = brief.warranty
        lines.append(f"Warranty: {w.age_statement or ''}")
        if w.terms:
            lines.append(f"  Terms from property record {w.record_id}: {w.terms}")
        lines.append(f"  Coverage: {w.coverage_statement or 'not computed'}")
        for c in w.cautions:
            lines.append(f"  - {c.text}{_cite(c.source)}")
        for v in w.verify:
            lines.append(f"  Verify: {v}")
    for m in brief.maintenance_due:
        lines.append(f"Maintenance due: {m.get('task')}, interval {m.get('interval')}, due {m.get('due_date')}"
                     f"{_cite(_source(full.get('sources') or [], m.get('source_index')))}")
    if brief.sources:
        lines.append("Sources:")
        for src in brief.sources:
            lines.append(f"  [{src.number}] {src.tier or ''} {src.host or ''}: {src.title or src.url} {src.url}"
                         + (f" (retrieved {src.retrieved_at[:10]})" if src.retrieved_at else ""))
    lines += _cost_lines(response.cost)
    if response.html is not None:
        lines.append(f"HTML brief: {response.html.path} ({response.html.resource_uri})")
    lines += response.notes
    return "\n".join(lines)


def _cost_lines(cost: Cost | None) -> list[str]:
    if cost is None:
        return []
    where = "the live ledger (real money)" if cost.real_money else "the replay ledger (no money was spent)"
    cap = f", run cap {cli._usd(cost.run_cap_usd)}" if cost.run_cap_usd is not None else ""
    return [f"Cost: {cli._usd(cost.run_total_usd)} on {where}, {cost.tavily_credits} Tavily credits{cap}."]


def _result(response: BaseModel, text: str, links: Sequence[ResourceLink] = ()) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text), *links],
                          structured_content=response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# The application behind the tools
# ---------------------------------------------------------------------------


class Advisor:
    """The state one server process keeps: its switches, the pending answers and the run lock."""

    def __init__(self, *, live: bool = False, cassette: str | None = None,
                 environ: MutableMapping[str, str] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.live = live
        self.cassette = cassette
        self.environ = environ if environ is not None else os.environ
        self.pending = PendingAnswers(clock=clock)
        self._run_lock = threading.Lock()
        self._busy: set[str] = set()
        self._busy_lock = threading.Lock()

    # -- shared plumbing ------------------------------------------------------

    async def _work(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run blocking work on a worker thread, one run or check at a time."""
        def locked() -> Any:
            with self._run_lock:
                return fn(*args, **kwargs)

        return await anyio.to_thread.run_sync(locked)

    def mode(self) -> str:
        return cli.resolve_mode(self.environ, live=self.live)

    def cassette_path(self, mode: str) -> Path | None:
        path = cli.resolve_cassette(self.cassette, self.environ, mode=mode)
        if mode == "replay" and path is None:
            raise cli.CliRefusal(REPLAY_NEEDS_CASSETTE)
        return path

    @contextlib.contextmanager
    def _busy_thread(self, thread_id: str):
        with self._busy_lock:
            if thread_id in self._busy:
                raise cli.CliRefusal(f"thread {thread_id} is waiting for an answer in another call; nothing was run")
            self._busy.add(thread_id)
        try:
            yield
        finally:
            with self._busy_lock:
                self._busy.discard(thread_id)

    def _refuse_busy(self, thread_id: str) -> None:
        with self._busy_lock:
            if thread_id in self._busy:
                raise cli.CliRefusal(f"thread {thread_id} is waiting for an answer in another call; nothing was run")

    # -- the checks `advisor ask` makes before anything runs -------------------

    def check_brief(self, req: BriefArgs, mode: str) -> None:
        """Refuse a request intake would refuse, before any preflight or run."""
        from agent.registry import Registry
        from agent.schemas import Intake

        given = [name for name, value in (("photo_path", req.photo_path), ("appliance_id", req.appliance_id),
                                          ("identity", req.identity)) if value]
        if len(given) != 1:
            raise cli.CliRefusal(f"{ONE_SOURCE}; got {', '.join(given) or 'none'}")
        try:
            Intake(symptom=req.symptom, mode=mode, photo_path=req.photo_path, identity=req.identity,
                   property_id=req.property_id, appliance_id=req.appliance_id, code=req.code)
        except ValidationError as exc:
            raise cli.CliRefusal(f"the request is not valid: {exc.errors()[0].get('msg')}") from None
        if req.photo_path:
            path = Path(req.photo_path).expanduser()
            if path.suffix.lower() not in config.PHOTO_MEDIA_TYPES:
                raise cli.CliRefusal(f"photo type {path.suffix!r} is not one of {sorted(config.PHOTO_MEDIA_TYPES)}")
            if not path.is_file():
                raise cli.CliRefusal(f"photo not found: {path}")
        if req.appliance_id:
            cli.ensure_registry()
            found = Registry(config.REGISTRY_PATH).get_appliance(req.appliance_id)
            if found is None:
                raise cli.CliRefusal(f"unknown appliance {req.appliance_id!r}")
            if req.property_id is not None and found["property_id"] != req.property_id:
                raise cli.CliRefusal(f"appliance {req.appliance_id!r} is not at property {req.property_id!r}")

    def live_session(self, command: str, mode: str) -> McpLiveSession:
        """The ledger and session a live `advisor` run opens, with every refusal it makes before its preflight.

        The ledger must already exist: this server never passes --new-ledger.
        """
        ledger = cli.open_live_ledger(mode, new_ledger=False, need=config.LIVE_KEY_NAMES, environ=self.environ)
        return McpLiveSession(command, mode, ledger, need=config.LIVE_KEY_NAMES, environ=self.environ,
                              uses_registry=True)

    def ask_preflight(self, req: BriefArgs, mode: str) -> tuple[McpLiveSession, list[str]]:
        from agent.live.preflight import plate_refusal

        if req.photo_path:
            refusal = plate_refusal(mode)
            if refusal:
                raise cli.CliRefusal(refusal)
        session = self.live_session(STAGE_ASK, mode)
        return session, session.run_preflight(STAGE_ASK, photo=bool(req.photo_path), run_id=None)

    def resume_preflight(self, thread_id: str, mode: str) -> tuple[McpLiveSession, list[str]]:
        view = self.thread(thread_id, mode)
        session = self.live_session(STAGE_RESUME, mode)
        return session, session.run_preflight(STAGE_RESUME, photo=bool(view.values.get("photo")), run_id=view.run_id)

    # -- the graph, run as `advisor ask` and `advisor resume` run it -----------

    def thread(self, thread_id: str, mode: str) -> Segment:
        """A thread's checkpointed state, refused as `advisor resume` refuses it; nothing runs."""
        from agent.graph import build_graph, open_checkpointer, thread_config

        _safe_id(thread_id, "thread")
        graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
        snapshot = graph.get_state(thread_config(thread_id))
        values = dict(snapshot.values or {})
        if not values:
            raise cli.CliRefusal(f"no thread {thread_id!r} in {config.CHECKPOINT_PATH}")
        if values.get("mode") != mode:
            raise cli.CliRefusal(f"thread {thread_id} ran in {values.get('mode')!r} mode, not {mode!r}")
        interrupts = list(snapshot.interrupts or ())
        return Segment(thread_id=thread_id, run_id=values.get("run_id") or thread_id, values=values,
                       interrupt=dict(interrupts[0].value) if interrupts else None)

    def _segment(self, graph: Any, payload: Any, ctx: Any, thread_id: str, session: McpLiveSession | None,
                 segment: str) -> Segment:
        """One graph run with what run_live and cmd_ask wrap around it: keys, capture, scrub and SC11."""
        from agent.graph import run_until_pause_or_end

        printed = io.StringIO()
        with contextlib.ExitStack() as stack:
            if session is not None:
                stack.enter_context(contextlib.redirect_stdout(printed))
                stack.enter_context(session.active())
            try:
                with cli._tracing(), cli._live_capture(session, thread_id, segment):
                    outcome = run_until_pause_or_end(graph, payload, ctx, thread_id)
            finally:
                if session is not None:
                    session.scrub(ctx)  # whatever happened, no key value stays in a file this run wrote
            if session is not None:
                session.finish(outcome, ctx)
        return Segment(thread_id=thread_id, run_id=ctx.run_id, values=dict(outcome.state),
                       interrupt=dict(outcome.interrupt) if outcome.paused and outcome.interrupt else None,
                       ctx=ctx, notes=[ln for ln in printed.getvalue().splitlines() if ln.strip()])

    @staticmethod
    def _refuse_unconfirmed(mode: str, session: McpLiveSession | None) -> None:
        """No live run starts without a session whose preflight the person confirmed."""
        if mode in config.LIVE_MODES and (session is None or session.approved is None):
            raise cli.CliRefusal("a live run starts only after its preflight is confirmed; nothing was sent or spent")

    def run_ask(self, req: BriefArgs, mode: str, cassette_path: Path | None,
                session: McpLiveSession | None = None) -> Segment:
        """cli.cmd_ask's run: a new thread, the same input, the same checkpointer, context and runner."""
        from agent.graph import build_graph, open_checkpointer
        from agent.nodes.intake import intake_input

        self._refuse_unconfirmed(mode, session)
        cassette = cli._load_cassette(cassette_path) if cassette_path is not None else None
        thread_id = cli.new_thread_id()
        html = cli.resolve_html_path(None, kind="brief", ident=thread_id)
        data = intake_input(symptom=req.symptom, photo_path=req.photo_path, identity=req.identity,
                            property_id=req.property_id, appliance_id=req.appliance_id, code=req.code)
        data["html_path"] = str(html)
        if session is not None:
            session.confirm_run(STAGE_ASK, photo=bool(req.photo_path), run_id=thread_id)
        graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
        ctx = cli.run_context(thread_id, mode, cassette, secrets=session.secrets if session is not None else ())
        return self._segment(graph, data, ctx, thread_id, session, STAGE_ASK)

    def run_resume(self, thread_id: str, value: dict[str, Any], mode: str, cassette_path: Path | None,
                   session: McpLiveSession | None = None) -> Segment:
        """cli.cmd_resume's run: the paused thread resumed with the confirmed value; a thread not paused is shown."""
        from langgraph.types import Command

        from agent.graph import build_graph, open_checkpointer

        view = self.thread(thread_id, mode)
        if not view.paused:
            return view
        self._refuse_unconfirmed(mode, session)
        cassette = cli._load_cassette(cassette_path) if cassette_path is not None else None
        graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
        if session is not None:
            session.confirm_run(STAGE_RESUME, photo=bool(view.values.get("photo")), run_id=view.run_id)
        ctx = cli.run_context(view.run_id, mode, cassette, secrets=session.secrets if session is not None else ())
        return self._segment(graph, Command(resume=value), ctx, thread_id, session, STAGE_RESUME)

    # -- answers ----------------------------------------------------------------

    def _cost(self, mode: str, run_id: str, ctx: Any = None, record: Mapping[str, Any] | None = None) -> Cost:
        from agent.ledger import Ledger, price_basis

        live = mode in config.LIVE_MODES
        path = config.LEDGER_PATH if live else config.REPLAY_LEDGER_PATH
        total, credits = 0.0, 0
        if path.exists():
            ledger = Ledger(path)
            total, credits = ledger.run_total(run_id), ledger.run_credits(run_id)
        cap = None
        if live:
            cap = config.RUN_CAP_USD.get(mode)
        elif ctx is not None:
            cap = Ledger.run_cap(price_basis(ctx.mode, ctx.cassette), (ctx.caps or {}).get("run_cap_usd"))
        record = record or {}
        return Cost(ledger="live" if live else "replay", run_total_usd=total, tavily_credits=credits, run_cap_usd=cap,
                    real_money=live, searches=record.get("searches"), fetches=record.get("fetches"),
                    sc11=record.get("sc11"))

    def finished(self, seg: Segment, mode: str, note: str | None = None) -> CallToolResult:
        """The brief a finished thread wrote, from its run record (what persist wrote) and its final state."""
        from agent.build_info import build_id
        from agent.nodes.persist import RUNS_DIRNAME

        values = seg.values
        report = values.get("persist_report") or {}
        path = Path(report.get("run_record") or (config.PAGES_DIR.parent / RUNS_DIRNAME / f"{seg.run_id}.json"))
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        brief = record.get("brief") or values.get("brief") or {}
        view = brief_view(brief, record.get("safety"), values.get("safety_signals"), values)
        status = view.status
        if status not in ("ok", "no_reliable_answer", "budget_stopped"):
            raise RuntimeError(f"thread {seg.thread_id} ended with brief status {status!r}")
        identity = values.get("identity") or {}
        unit = " ".join(str(v) for v in (identity.get("manufacturer"), identity.get("model")) if v) or "the unit"
        flagged = len(view.safety_flags)
        message = (f"Brief for {unit}: status {status}; "
                   f"{brief_counts(len(view.try_first), flagged, len(view.candidates))}. Run {seg.run_id}.")
        if note:
            message = f"{note} {message}"
        html_path = values.get("html_path")
        html = _link(html_path, BRIEF_URI.format(run_id=seg.run_id)) if html_path else None
        response = BriefResponse(status=status, message=message, mode=mode, build_id=record.get("build_id") or build_id(),
                                 thread_id=seg.thread_id, run_id=seg.run_id, brief=view,
                                 cost=self._cost(mode, seg.run_id, seg.ctx, record), html=html, notes=seg.notes)
        links = []
        if html is not None:
            links.append(ResourceLink(name=f"brief {seg.run_id}", uri=html.resource_uri, mime_type=HTML,
                                      description="The HTML brief this run wrote"))
        return _result(response, brief_text(response), links)

    def paused(self, seg: Segment, mode: str, note: str | None = None) -> CallToolResult:
        """The paused thread: the identity read, what is missing, and how to resume, as `advisor ask` prints."""
        from agent.build_info import build_id

        payload = seg.interrupt or {}
        pause = Pause(identity={k: (v if isinstance(v, str) else None) for k, v in (payload.get("identity") or {}).items()},
                      extraction=payload.get("extraction"), missing=list(payload.get("missing") or []),
                      code_shown=payload.get("observed_code_candidate"), prompt=str(payload.get("prompt") or ""))
        code = pause.code_shown
        step = (f"{ASK_OWNER} Then call {TOOL_RESUME} with thread_id \"{seg.thread_id}\" and the confirmed "
                "manufacturer and model (the "
                "model is required), with serial and manufacture_date when known. Leave code out to confirm "
                + (f"the code shown ({code})" if code else "that no code is shown")
                + ", or pass the code the display shows; an empty code clears it.")
        if mode in config.LIVE_MODES:
            step += " The resume shows its own preflight before it spends anything."
        message = (f"Paused for the owner to confirm the unit's identity, as `advisor ask` pauses; nothing more runs "
                   f"or is spent until it is resumed. Missing: {', '.join(pause.missing) or 'nothing'}.")
        if note:
            message = f"{note} {message}"
        response = BriefResponse(status="paused", message=message, mode=mode, build_id=build_id(),
                                 thread_id=seg.thread_id, run_id=seg.run_id, pause=pause,
                                 cost=self._cost(mode, seg.run_id, seg.ctx), next_step=step, notes=seg.notes)
        text = "\n".join([message, identity_message(payload, seg.thread_id), step, *_cost_lines(response.cost)])
        return _result(response, text)

    def confirmation_required(self, stage: str, lines: list[str], pending_id: str, mode: str, tool: str, *,
                              thread_id: str | None = None, run_id: str | None = None, sends: Sequence[str] = (),
                              note: str | None = None) -> CallToolResult:
        from agent.build_info import build_id

        preflight = Preflight(stage=stage, lines=lines, confirmation_id=pending_id,
                              expires_at=self.pending.expires_at(pending_id), sends=list(sends))
        step = (f"Show the person these preflight lines. Only if they type proceed, call {tool} again with the same "
                f"arguments plus confirm=\"proceed\" and confirmation_id=\"{pending_id}\". It can be used once, "
                f"within {CONFIRMATION_TTL_S // 60} minutes.")
        message = "Nothing has been sent or spent. This is a live run: it needs the person's confirmation first."
        if note:
            message = f"{note} {message}"
        response = BriefResponse(status="confirmation_required", message=message, mode=mode, build_id=build_id(),
                                 thread_id=thread_id, run_id=run_id, preflight=preflight, next_step=step)
        return _result(response, "\n".join([message, *sends, *lines, step]))

    def not_confirmed(self, stage: str, lines: list[str], mode: str, *, thread_id: str | None = None,
                      run_id: str | None = None) -> CallToolResult:
        from agent.build_info import build_id

        step = (f"Thread {thread_id} is still paused; call {TOOL_RESUME} with it when the person is ready."
                if thread_id else None)
        response = BriefResponse(status="not_confirmed", message=NOT_CONFIRMED, mode=mode, build_id=build_id(),
                                 thread_id=thread_id, run_id=run_id or thread_id,
                                 preflight=Preflight(stage=stage, lines=lines), next_step=step)
        return _result(response, "\n".join([NOT_CONFIRMED, *([step] if step else [])]))

    # -- the confirmation before a paid or outbound call --------------------------

    async def gate(self, ctx: Context | None, asker: Asker, *, stage: str, tool: str, digest: str, lines: list[str],
                   confirm: str | None, confirmation_id: str | None, thread_id: str | None = None,
                   resume: dict[str, Any] | None = None, sends: Sequence[str] = ()
                   ) -> tuple[bool, Any]:
        """(True, None) once the person confirmed these exact lines; else (False, the result to return).

        An elicitation that fails, or an answer that does not fit the form,
        confirms nothing.
        """
        if asker.pushes:
            try:
                answer = await ctx.elicit(spend_message(lines, sends), schema=Proceed)
            except (ValueError, MCPError):
                return False, None
            return (True, None) if proceed_answered(answer) else (False, None)
        if asker.returns:
            pending_id = self.pending.add(Pending(kind=stage, tool=tool, digest=digest, lines=tuple(lines),
                                                  thread_id=thread_id, resume=resume))
            return False, InputRequiredResult(input_requests={KEY_PROCEED: proceed_request(lines, sends)},
                                              request_state=pending_id)
        note = None
        if confirm is not None and confirmation_id:
            pending = self.pending.take(confirmation_id, tool=tool, digest=digest, kinds=(stage,))
            if pending is not None and pending.thread_id == thread_id:
                from agent.live.preflight import confirmed

                if not confirmed(confirm):
                    return False, None
                if list(pending.lines) == lines:
                    return True, None
                note = PREFLIGHT_CHANGED
        pending_id = self.pending.add(Pending(kind=stage, tool=tool, digest=digest, lines=tuple(lines),
                                              thread_id=thread_id))
        return False, ("required", pending_id, note)

    # -- get_service_brief ---------------------------------------------------------

    async def brief(self, ctx: Context | None, req: BriefArgs, *, confirm: str | None,
                    confirmation_id: str | None) -> CallToolResult | InputRequiredResult:
        mode = self.mode()
        cassette_path = self.cassette_path(mode)
        await self._work(self.check_brief, req, mode)
        asker = Asker.of(ctx)
        digest = args_digest(TOOL_BRIEF, req.as_dict())
        if asker.returns and ctx is not None and ctx.request_state:
            pending = self.pending.take(ctx.request_state, tool=TOOL_BRIEF, digest=digest,
                                        kinds=(STAGE_ASK, STAGE_RESUME, KEY_IDENTITY))
            if pending is not None:
                return await self.answered(ctx, asker, pending, mode, cassette_path, digest, req=req)
        session = None
        if mode in config.LIVE_MODES:
            session, lines = await self._work(self.ask_preflight, req, mode)
            sends = [f"This run sends the plate photo at {Path(req.photo_path).expanduser().resolve()} to the model."] \
                if req.photo_path else []
            approved, out = await self.gate(ctx, asker, stage=STAGE_ASK, tool=TOOL_BRIEF, digest=digest, lines=lines,
                                            confirm=confirm, confirmation_id=confirmation_id, sends=sends)
            if not approved:
                return self._gate_result(out, STAGE_ASK, lines, mode, TOOL_BRIEF, sends=sends)
            session.approved = lines
        seg = await self._run_live_or_replay(self.run_ask, req, mode, cassette_path, session=session)
        if isinstance(seg, CallToolResult):
            return seg
        return await self.after_segment(ctx, asker, seg, mode, cassette_path, digest, asks=0)

    def _gate_result(self, out: Any, stage: str, lines: list[str], mode: str, tool: str, *,
                     thread_id: str | None = None, run_id: str | None = None,
                     sends: Sequence[str] = ()) -> CallToolResult | InputRequiredResult:
        if isinstance(out, InputRequiredResult):
            return out
        if isinstance(out, tuple):
            _, pending_id, note = out
            return self.confirmation_required(stage, lines, pending_id, mode, tool, thread_id=thread_id,
                                              run_id=run_id, sends=sends, note=note)
        return self.not_confirmed(stage, lines, mode, thread_id=thread_id, run_id=run_id)

    async def _run_live_or_replay(self, fn: Callable[..., Segment], *args: Any,
                                  session: McpLiveSession | None = None) -> Segment | CallToolResult:
        """A run segment; a live failure is reported redacted, and a changed preflight is shown again."""
        try:
            return await self._work(fn, *args, session=session)
        except PreflightChanged:
            raise cli.CliRefusal(PREFLIGHT_CHANGED + " Call again to see the new preflight.") from None
        except (cli.CliRefusal, ToolError, MCPError):
            raise
        except Exception as exc:
            if session is None:
                raise
            from agent.live.env import redact

            raise ToolError(redact(f"the live call failed: {type(exc).__name__}: {exc}. Stop and report this "
                                   "(PRD Part A rule 7); every call made so far is in the ledger (advisor ledger).",
                                   session.secrets)) from None

    async def after_segment(self, ctx: Context | None, asker: Asker, seg: Segment, mode: str,
                            cassette_path: Path | None, digest: str, *, asks: int,
                            tool: str = TOOL_BRIEF) -> CallToolResult | InputRequiredResult:
        """A finished thread's brief, or its pause put to the person when they can be asked."""
        while seg.paused and asks < MAX_IDENTITY_ASKS:
            payload = seg.interrupt or {}
            if asker.returns:
                pending_id = self.pending.add(Pending(kind=KEY_IDENTITY, tool=tool, digest=digest,
                                                      thread_id=seg.thread_id, asks=asks + 1,
                                                      question=question_digest(payload, seg.thread_id)))
                return InputRequiredResult(input_requests={KEY_IDENTITY: identity_request(payload, seg.thread_id)},
                                           request_state=pending_id)
            if not asker.pushes:
                break
            with self._busy_thread(seg.thread_id):
                try:
                    answer = await ctx.elicit(identity_message(payload, seg.thread_id), schema=identity_form(payload))
                except (ValueError, MCPError):
                    return self.paused(seg, mode, note="The identity answer could not be read.")
                if answer.action != "accept":
                    return self.paused(seg, mode, note=f"The identity question was {asked_result(answer)}.")
                value = resume_value_from(answer.data.model_dump(), payload.get("identity"))
                session = None
                if mode in config.LIVE_MODES:
                    session, lines = await self._work(self.resume_preflight, seg.thread_id, mode)
                    approved, out = await self.gate(ctx, asker, stage=STAGE_RESUME, tool=tool, digest=digest,
                                                    lines=lines, confirm=None, confirmation_id=None,
                                                    thread_id=seg.thread_id)
                    if not approved:
                        return self._gate_result(out, STAGE_RESUME, lines, mode, tool, thread_id=seg.thread_id,
                                                 run_id=seg.run_id)
                    session.approved = lines
                result = await self._run_live_or_replay(self.run_resume, seg.thread_id, value, mode,
                                                        cassette_path, session=session)
            if isinstance(result, CallToolResult):
                return result
            seg, asks = result, asks + 1
        if seg.paused:
            return self.paused(seg, mode)
        return self.finished(seg, mode)

    async def answered(self, ctx: Context, asker: Asker, pending: Pending, mode: str, cassette_path: Path | None,
                       digest: str, *, req: BriefArgs | None = None) -> CallToolResult | InputRequiredResult:
        """A 2026-07-28 retry: the answer to the question this call returned last round.

        The retry carries the same arguments (the SDK binds the request state to
        them), so `req` is the request a returned ask preflight answers.
        """
        answers = ctx.input_responses or {}
        tool = pending.tool
        if pending.kind == KEY_IDENTITY:
            view = await self._work(self.thread, pending.thread_id, mode)
            if not view.paused or question_digest(view.interrupt or {}, view.thread_id) != pending.question:
                return await self.after_segment(ctx, asker, view, mode, cassette_path, digest, asks=pending.asks,
                                                tool=tool)
            answer = answers.get(KEY_IDENTITY)
            if not isinstance(answer, ElicitResult) or answer.action != "accept":
                return self.paused(view, mode, note=f"The identity question was {asked_result(answer)}.")
            try:
                fields = identity_form(view.interrupt or {}).model_validate(answer.content or {}).model_dump()
            except ValidationError:
                return self.paused(view, mode, note="The identity answer did not fit the form.")
            value = resume_value_from(fields, (view.interrupt or {}).get("identity"))
            session = None
            if mode in config.LIVE_MODES:
                session, lines = await self._work(self.resume_preflight, view.thread_id, mode)
                pending_id = self.pending.add(Pending(kind=STAGE_RESUME, tool=tool, digest=digest, lines=tuple(lines),
                                                      thread_id=view.thread_id, resume=value, asks=pending.asks))
                return InputRequiredResult(input_requests={KEY_PROCEED: proceed_request(lines)},
                                           request_state=pending_id)
            seg = await self._run_live_or_replay(self.run_resume, view.thread_id, value, mode,
                                                 cassette_path, session=session)
            if isinstance(seg, CallToolResult):
                return seg
            return await self.after_segment(ctx, asker, seg, mode, cassette_path, digest, asks=pending.asks,
                                            tool=tool)
        # A preflight returned as a question: the answer must be exactly "proceed", for the same lines.
        if not proceed_answered(answers.get(KEY_PROCEED)):
            return self.not_confirmed(pending.kind, list(pending.lines), mode, thread_id=pending.thread_id)
        if pending.kind == STAGE_ASK:
            if req is None:
                raise cli.CliRefusal("no brief request is waiting for this preflight; nothing was run")
            return await self._confirmed_ask(ctx, asker, pending, req, mode, cassette_path, digest)
        session, lines = await self._work(self.resume_preflight, pending.thread_id, mode)
        if lines != list(pending.lines):
            pending_id = self.pending.add(Pending(kind=STAGE_RESUME, tool=tool, digest=digest, lines=tuple(lines),
                                                  thread_id=pending.thread_id, resume=pending.resume,
                                                  asks=pending.asks))
            return InputRequiredResult(input_requests={KEY_PROCEED: proceed_request([PREFLIGHT_CHANGED, *lines])},
                                       request_state=pending_id)
        session.approved = lines
        value = pending.resume
        if value is None:  # resume_brief's own arguments carry the confirmed identity
            raise cli.CliRefusal("no confirmed identity is waiting for this preflight; nothing was run")
        seg = await self._run_live_or_replay(self.run_resume, pending.thread_id, value, mode,
                                             cassette_path, session=session)
        if isinstance(seg, CallToolResult):
            return seg
        follow = asker if tool == TOOL_BRIEF else Asker()
        return await self.after_segment(ctx, follow, seg, mode, cassette_path, digest, asks=pending.asks, tool=tool)

    async def _confirmed_ask(self, ctx: Context, asker: Asker, pending: Pending, req: BriefArgs, mode: str,
                             cassette_path: Path | None, digest: str) -> CallToolResult | InputRequiredResult:
        session, lines = await self._work(self.ask_preflight, req, mode)
        if lines != list(pending.lines):
            pending_id = self.pending.add(Pending(kind=STAGE_ASK, tool=TOOL_BRIEF, digest=digest, lines=tuple(lines)))
            return InputRequiredResult(input_requests={KEY_PROCEED: proceed_request([PREFLIGHT_CHANGED, *lines])},
                                       request_state=pending_id)
        session.approved = lines
        seg = await self._run_live_or_replay(self.run_ask, req, mode, cassette_path, session=session)
        if isinstance(seg, CallToolResult):
            return seg
        return await self.after_segment(ctx, asker, seg, mode, cassette_path, digest, asks=0)

    # -- resume_brief ------------------------------------------------------------

    async def resume(self, ctx: Context | None, thread_id: str, fields: dict[str, Any], *, confirm: str | None,
                     confirmation_id: str | None) -> CallToolResult | InputRequiredResult:
        mode = self.mode()
        cassette_path = self.cassette_path(mode)
        _safe_id(thread_id, "thread")
        self._refuse_busy(thread_id)
        asker = Asker.of(ctx)
        digest = args_digest(TOOL_RESUME, {"thread_id": thread_id, **fields})
        if asker.returns and ctx is not None and ctx.request_state:
            pending = self.pending.take(ctx.request_state, tool=TOOL_RESUME, digest=digest, kinds=(STAGE_RESUME,))
            if pending is not None:
                return await self.answered(ctx, asker, pending, mode, cassette_path, digest)
        view = await self._work(self.thread, thread_id, mode)
        if not view.paused:
            return self.finished(view, mode, note="This thread is not paused, so nothing was run; this is the brief "
                                                  "it already wrote.")
        value = resume_value_from(fields, (view.interrupt or {}).get("identity"))
        session = None
        if mode in config.LIVE_MODES:
            session, lines = await self._work(self.resume_preflight, thread_id, mode)
            approved, out = await self.gate(ctx, asker, stage=STAGE_RESUME, tool=TOOL_RESUME, digest=digest,
                                            lines=lines, confirm=confirm, confirmation_id=confirmation_id,
                                            thread_id=thread_id, resume=value)
            if not approved:
                return self._gate_result(out, STAGE_RESUME, lines, mode, TOOL_RESUME, thread_id=thread_id,
                                         run_id=view.run_id)
            session.approved = lines
        seg = await self._run_live_or_replay(self.run_resume, thread_id, value, mode, cassette_path,
                                             session=session)
        if isinstance(seg, CallToolResult):
            return seg
        # resume_brief is the answer to a pause, so it never asks the identity question itself.
        return await self.after_segment(ctx, Asker(), seg, mode, cassette_path, digest, asks=0, tool=TOOL_RESUME)

    # -- property_status ---------------------------------------------------------

    def property_status(self, property_id: str) -> CallToolResult:
        """What `advisor property show` puts on the page for warranty, replacement and recalls, as data."""
        from agent import product_recalls, sweep
        from agent.kg import KnowledgeGraph
        from agent.registry import Registry
        from agent.rules import coverage as coverage_rule

        cli.ensure_registry()
        registry = Registry(config.REGISTRY_PATH)
        prop = self._property(registry, property_id)
        graph_path = cli.property_graph_path(self.environ)
        kg = KnowledgeGraph.load(graph_path, registry=config.REGISTRY_PATH)
        as_of = cli._today()
        appliances = registry.list_appliances(property_id)
        out = []
        for a, row in zip(appliances, sweep.sweep_rows(kg, appliances, as_of), strict=True):
            rows = coverage_rule.coverage_rows(registry.warranty_coverage(a["id"]), a, as_of)
            terms = bool(a.get("warranty_terms"))
            recall = product_recalls.stored_result(config.RECALLS_DIR, a)
            out.append(ApplianceStatus(
                appliance_id=a["id"], category=a.get("category"), manufacturer=a.get("manufacturer"),
                model=a.get("model"), model_number=a.get("model_number"), serial=a.get("serial"),
                install_date=a.get("install_date"), purchase_date=a.get("purchase_date"), record_id=a.get("record_id"),
                synthetic=bool(a.get("synthetic")),
                warranty={"terms": a.get("warranty_terms"), "rows": rows,
                          "statement": coverage_rule.coverage_statement(rows, as_of, terms_on_file=terms),
                          "no_rows_statement": None if rows else coverage_rule.no_rows_statement(terms)},
                replacement={"age": row["age"], "facts": row["facts"], "statement": row["statement"]},
                recalls={"status": recall.get("status"), "checked_at": recall.get("checked_at"),
                         "lines": product_recalls.text_lines(recall), "matches": recall.get("matches") or [],
                         "errors": recall.get("errors") or []},
            ))
        page = Link(path=str(config.PROPERTY_OUT_DIR / f"{property_id}.html"),
                    file_uri=(config.PROPERTY_OUT_DIR / f"{property_id}.html").resolve().as_uri(),
                    resource_uri=PROPERTY_URI.format(property_id=property_id))
        message = (f"Property {property_id}: {counted(len(out), 'appliance', 'appliances')} as of {as_of.isoformat()} "
                   f"(UTC), from the registry, "
                   f"the graph at {graph_path} and the stored product recall checks. Nothing was sent or spent.")
        response = PropertyStatusResponse(property_id=property_id, label=prop.get("label"),
                                          synthetic=bool(prop.get("synthetic")), as_of=as_of.isoformat(),
                                          graph=str(graph_path), appliances=out, page=page, message=message)
        return _result(response, status_text(response),
                       [ResourceLink(name=f"property {property_id}", uri=page.resource_uri, mime_type=HTML,
                                     description="The property page advisor property show writes")])

    @staticmethod
    def _property(registry: Any, property_id: str) -> dict[str, Any]:
        known = registry.list_properties()
        prop = next((p for p in known if p.get("id") == property_id), None)
        if prop is None:
            ids = ", ".join(p["id"] for p in known) or "none"
            raise cli.CliRefusal(f"unknown property {property_id!r}; the registry holds {ids}.")
        return prop

    def property_page(self, property_id: str) -> str:
        """cli.cmd_property_show's page, rendered and returned, never written."""
        from agent.kg import KnowledgeGraph
        from agent.registry import Registry
        from agent.render.property_page import render_property_page

        cli.ensure_registry()
        registry = Registry(config.REGISTRY_PATH)
        self._property(registry, property_id)
        graph_path = cli.property_graph_path(self.environ)
        appliance_ids = [a["id"] for a in registry.list_appliances(property_id)]
        graph = KnowledgeGraph.load(graph_path, registry=config.REGISTRY_PATH)
        due = cli.maintenance_due_by_appliance(registry, appliance_ids, replay=graph_path == config.REPLAY_GRAPH_PATH)
        return render_property_page(property_id, registry, graph=graph, maintenance_due=due,
                                    generated_at=cli._today().isoformat(), recalls_dir=config.RECALLS_DIR)

    # -- check_product_recalls ---------------------------------------------------

    def recall_targets(self, property_id: str | None, all_properties: bool) -> tuple[list[str], list[dict[str, Any]]]:
        from agent.registry import Registry

        if bool(property_id) == bool(all_properties):
            raise cli.CliRefusal("give exactly one of property_id or all_properties=true")
        cli.ensure_registry()
        registry = Registry(config.REGISTRY_PATH)
        targets = cli.sweep_targets(registry, argparse.Namespace(all=all_properties, property_id=property_id))
        appliances = [a for pid in targets for a in registry.list_appliances(pid)]
        return targets, appliances

    def recalls_preflight(self, appliances: Sequence[Mapping[str, Any]]) -> list[str]:
        from agent import product_recalls

        cli.refuse_registry_drift()  # the stored checks keep the model numbers compared, so the seed must be loaded
        return product_recalls.plan_lines(appliances, config.RECALLS_DIR)

    def send_recall_checks(self, appliances: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """cli.recalls_live after its typed proceed: query CPSC per appliance, store each check, log the run."""
        from agent import product_recalls
        from agent.build_info import build_id
        from agent.nodes.persist import RUNS_DIRNAME

        run_id = f"{cli.RECALLS_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
        checks: list[dict[str, Any]] = []
        stored: list[Path] = []
        results: list[RecallResult] = []
        complete, failure = True, None
        try:
            for a in appliances:
                if product_recalls.is_synthetic_maker(a):
                    result = product_recalls.stored_result(None, a)
                    results.append(RecallResult(appliance_id=str(a.get("id")), status=result["status"],
                                                lines=product_recalls.text_lines(result)))
                    continue
                try:
                    check = product_recalls.check_appliance(a)
                except ValueError as exc:  # a model that normalizes to nothing: refused before any request
                    results.append(RecallResult(appliance_id=str(a.get("id")), status="not_checked",
                                                lines=[f"not checked: {exc}"], errors=[str(exc)]))
                    complete = False
                    continue
                stored.append(product_recalls.store_check(config.RECALLS_DIR, check))
                checks.append(check)
                result = product_recalls.evaluate(check)
                complete = complete and not result["errors"]
                results.append(RecallResult(appliance_id=str(a.get("id")), status=result["status"],
                                            lines=product_recalls.text_lines(result), stored=str(stored[-1]),
                                            errors=list(result["errors"])))
        except Exception as exc:  # requests went out: stop, but still log what ran (below)
            failure = f"the check failed: {type(exc).__name__}: {exc}"
            complete = False
        finally:
            # Written whatever happens, so every check stored so far is logged with the run.
            record = product_recalls.run_record(run_id, checks, stored, build_id())
            path = config.PAGES_DIR.parent / RUNS_DIRNAME / f"{run_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        return {"run_id": run_id, "run_record": str(path), "results": results, "complete": complete,
                "failure": failure}

    async def recalls(self, ctx: Context | None, property_id: str | None, all_properties: bool, *,
                      confirm: str | None, confirmation_id: str | None) -> CallToolResult | InputRequiredResult:
        targets, appliances = await self._work(self.recall_targets, property_id, all_properties)
        asker = Asker.of(ctx)
        digest = args_digest(TOOL_RECALLS, {"property_id": property_id, "all_properties": all_properties})
        lines = await self._work(self.recalls_preflight, appliances)
        approved = False
        if asker.returns and ctx is not None and ctx.request_state:
            pending = self.pending.take(ctx.request_state, tool=TOOL_RECALLS, digest=digest, kinds=(STAGE_RECALLS,))
            if pending is not None:
                if not proceed_answered((ctx.input_responses or {}).get(KEY_PROCEED)):
                    return self.recalls_not_confirmed(targets, lines)
                if list(pending.lines) == lines:
                    approved = True
                else:
                    pending_id = self.pending.add(Pending(kind=STAGE_RECALLS, tool=TOOL_RECALLS, digest=digest,
                                                          lines=tuple(lines)))
                    return InputRequiredResult(
                        input_requests={KEY_PROCEED: proceed_request([PREFLIGHT_CHANGED, *lines])},
                        request_state=pending_id)
        if not approved:
            approved, out = await self.gate(ctx, asker, stage=STAGE_RECALLS, tool=TOOL_RECALLS, digest=digest,
                                            lines=lines, confirm=confirm, confirmation_id=confirmation_id)
            if not approved:
                if isinstance(out, InputRequiredResult):
                    return out
                if isinstance(out, tuple):
                    return self.recalls_required(targets, lines, out[1], out[2])
                return self.recalls_not_confirmed(targets, lines)
        run = await self._work(self.send_recall_checks, appliances)
        return self.recalls_checked(targets, run)

    def recalls_required(self, targets: list[str], lines: list[str], pending_id: str,
                         note: str | None) -> CallToolResult:
        step =(f"Show the person these planned requests. Only if they type proceed, call {TOOL_RECALLS} again with "
                f"the same arguments plus confirm=\"proceed\" and confirmation_id=\"{pending_id}\". It can be used once, "
                f"within {CONFIRMATION_TTL_S // 60} minutes.")
        message = "Nothing has been sent. The product recall check sends requests to an outside service (CPSC)."
        if note:
            message = f"{note} {message}"
        response = RecallsResponse(status="confirmation_required", message=message, properties=targets,
                                   preflight=Preflight(stage=STAGE_RECALLS, lines=lines, confirmation_id=pending_id,
                                                       expires_at=self.pending.expires_at(pending_id)),
                                   next_step=step)
        return _result(response, "\n".join([message, *lines, step]))

    def recalls_not_confirmed(self, targets: list[str], lines: list[str]) -> CallToolResult:
        text = 'The confirmation was not exactly "proceed"; nothing was sent.'
        response = RecallsResponse(status="not_confirmed", message=text, properties=targets,
                                   preflight=Preflight(stage=STAGE_RECALLS, lines=lines))
        return _result(response, text)

    def recalls_checked(self, targets: list[str], run: dict[str, Any]) -> CallToolResult:
        results: list[RecallResult] = run["results"]
        message = (f"Product recall check run {run['run_id']} for {', '.join(targets)}: "
                   f"{'complete' if run['complete'] else 'not complete'}. The service is free, so nothing was spent; "
                   f"the run record is {run['run_record']}.")
        if run.get("failure"):
            message = f"{run['failure']}. Stop and report this; the checks stored so far are in the run record. " \
                      + message
        response = RecallsResponse(status="checked", message=message, properties=targets, run_id=run["run_id"],
                                   run_record=run["run_record"], complete=run["complete"], results=results)
        text = [message]
        for r in results:
            text.append(f"{r.appliance_id}: {r.status}")
            text += [f"  {line}" for line in r.lines]
        return _result(response, "\n".join(text))


def status_text(response: PropertyStatusResponse) -> str:
    """property_status as plain text: each appliance's coverage, replacement and recall lines."""
    lines = [response.message]
    for a in response.appliances:
        unit = f"{a.appliance_id} {a.manufacturer} {a.model}{' (synthetic record)' if a.synthetic else ''}"
        lines.append(unit)
        lines.append(f"  Warranty: {a.warranty['statement']}")
        for row in a.warranty["rows"]:
            lines.append(f"    {row['component']}: {row['term_months']} months from {row['start_event']} "
                         f"{row['start_date'] or 'not recorded'}, ends {row['end_date'] or 'unknown'}, "
                         f"{row['status']} ({row['record_id']}{', synthetic' if row['synthetic'] else ''})")
        lines.append(f"  Plan a replacement: {a.replacement['age']['statement']}")
        if a.replacement.get("statement"):
            lines.append(f"    {a.replacement['statement']}")
        for fact in a.replacement.get("facts") or []:
            lines.append(f"    {fact.get('reason')}: successor {fact.get('successor_model') or 'not named'}; "
                         f"source {fact.get('source_url')}")
        lines.append("  Product recalls:")
        lines += [f"    {line}" for line in a.recalls["lines"]]
    lines.append(f"Property page: {response.page.resource_uri}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


def _refusals(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn an advisor refusal into the tool error the model reads; nothing else changes."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except cli.CliRefusal as refusal:
            raise ToolError(f"refused: {refusal}") from None

    return wrapper


def build_server(*, live: bool = False, cassette: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> MCPServer:
    """The MCP server: four tools and two resources over one Advisor (server.advisor)."""
    from agent.build_info import build_id

    advisor = Advisor(live=live, cassette=cassette, clock=clock)
    server = MCPServer(SERVER_NAME, title=SERVER_TITLE, instructions=INSTRUCTIONS, version=build_id(),
                       log_level="WARNING")
    server.advisor = advisor  # type: ignore[attr-defined]

    @server.tool(name=TOOL_BRIEF, title="Get a service brief",
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=True))
    @_refusals
    async def get_service_brief(
        ctx: Context,
        symptom: Annotated[str, Field(description="What the owner sees or hears, in their words, such as "
                                                  "\"panel shows FLO\"")],
        photo_path: Annotated[str | None, Field(description="Path on this computer to a photo of the data plate")] = None,
        appliance_id: Annotated[str | None, Field(description="A registry appliance id, such as appl-optima880")] = None,
        identity: Annotated[TypedIdentity | None, Field(description="The identity typed from the data plate")] = None,
        property_id: Annotated[str | None, Field(description="The registry property the appliance is at")] = None,
        code: Annotated[str | None, Field(description="The code shown on the unit's display, if any")] = None,
        confirm: Annotated[str | None, Field(description="Live runs only: \"proceed\", passed only after the person "
                                                         "read the preflight and typed proceed")] = None,
        confirmation_id: Annotated[str | None, Field(description="Live runs only: the confirmation_id of the "
                                                                 "preflight being answered")] = None,
    ) -> Annotated[CallToolResult, BriefResponse] | InputRequiredResult:
        """Write a cited service brief for an appliance problem with the advisor's own graph, rules and ledger.

        Give the symptom and exactly one of photo_path, appliance_id or identity. A photo, or a symptom that names
        a code, pauses for the owner to confirm the unit: a client with form elicitation is asked directly;
        otherwise the result has status "paused" with a thread_id, finished with resume_brief. In live mode every
        paid call first returns status "confirmation_required" with the preflight (planned calls and cost); show
        it, and only if the person types proceed, call again with confirm="proceed" and the confirmation_id.
        The result lists every step to try first with its source, every safety flag, the documented candidates,
        the warranty coverage line, the cost and the run id, with a link to the HTML brief. A no_reliable_answer
        brief is a success state: the documentation found did not support an answer.
        """
        req = BriefArgs(symptom=symptom, photo_path=photo_path, appliance_id=appliance_id,
                        identity=identity.model_dump() if identity is not None else None,
                        property_id=property_id, code=code)
        return await advisor.brief(ctx, req, confirm=confirm, confirmation_id=confirmation_id)

    @server.tool(name=TOOL_RESUME, title="Resume a paused brief",
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=True))
    @_refusals
    async def resume_brief(
        ctx: Context,
        thread_id: Annotated[str, Field(description="The paused thread, from get_service_brief")],
        manufacturer: Annotated[str | None, Field(description="Confirmed manufacturer")] = None,
        model: Annotated[str | None, Field(description="Confirmed model, exactly as printed; required to go on")] = None,
        serial: Annotated[str | None, Field(description="Confirmed serial number; an empty value clears it")] = None,
        manufacture_date: Annotated[str | None, Field(description="Confirmed manufacture date")] = None,
        code: Annotated[str | None, Field(description="The code the display shows; leave out to confirm the code "
                                                      "shown in the pause, or pass an empty value to clear it")] = None,
        confirm: Annotated[str | None, Field(description="Live runs only: \"proceed\", passed only after the person "
                                                         "read the preflight and typed proceed")] = None,
        confirmation_id: Annotated[str | None, Field(description="Live runs only: the confirmation_id of the "
                                                                 "preflight being answered")] = None,
    ) -> Annotated[CallToolResult, BriefResponse] | InputRequiredResult:
        """Resume a thread paused at the identity confirmation with the owner's confirmed fields, as advisor resume does.

        A field left out keeps what the pause proposed. The run stays paused while the model is missing. In live
        mode the rest of the run shows its own preflight first and waits for confirm="proceed" with the
        confirmation_id, or for the person's answer by elicitation. A thread that is not paused is not run again:
        its brief is returned as it was written.
        """
        fields = {"manufacturer": manufacturer, "model": model, "serial": serial,
                  "manufacture_date": manufacture_date, "code": code}
        return await advisor.resume(ctx, thread_id, fields, confirm=confirm, confirmation_id=confirmation_id)

    @server.tool(name=TOOL_STATUS, title="Property status",
                 annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True))
    @_refusals
    async def property_status(
        property_id: Annotated[str, Field(description="A registry property id, such as prop-a")],
    ) -> Annotated[CallToolResult, PropertyStatusResponse]:
        """Warranty coverage, the Plan a replacement rows and the stored product recall checks of one property.

        Offline and free: it reads the registry, the knowledge graph and the stored checks with the code behind
        advisor property show, computes every date and status for today's UTC date, and sends nothing.
        """
        _safe_id(property_id, "property")
        return await advisor._work(advisor.property_status, property_id)

    @server.tool(name=TOOL_RECALLS, title="Check product recalls",
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=True))
    @_refusals
    async def check_product_recalls(
        ctx: Context,
        property_id: Annotated[str | None, Field(description="A registry property id, such as prop-a")] = None,
        all_properties: Annotated[bool, Field(description="Check every property in the registry")] = False,
        confirm: Annotated[str | None, Field(description="\"proceed\", passed only after the person read the planned "
                                                         "requests and typed proceed")] = None,
        confirmation_id: Annotated[str | None, Field(description="The confirmation_id of the planned requests being "
                                                                 "answered")] = None,
    ) -> Annotated[CallToolResult, RecallsResponse] | InputRequiredResult:
        """Check CPSC's recall service for each appliance's exact model, as advisor property recalls --live does.

        Free and keyless, but it sends requests to an outside service, so it first returns the planned requests
        (status "confirmation_required") and sends nothing until the person types proceed. Each check is stored
        with its date and the run is logged. A match never says the unit is affected: check the serial range.
        """
        if property_id is not None:
            _safe_id(property_id, "property")
        return await advisor.recalls(ctx, property_id, all_properties, confirm=confirm,
                                     confirmation_id=confirmation_id)

    @server.resource(BRIEF_URI, name="brief", title="Service brief", mime_type=HTML,
                     description="The HTML brief a run wrote, by its run id")
    def brief_page(run_id: str) -> str:
        if not isinstance(run_id, str) or not SAFE_ID.match(run_id):
            raise ResourceError(f"{run_id!r} is not a run id")
        root = config.BRIEFS_OUT_DIR.resolve()
        path = (root / f"{run_id}.html").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError(f"no brief for run {run_id} in {root}")
        return path.read_text(encoding="utf-8")

    @server.resource(PROPERTY_URI, name="property", title="Property page", mime_type=HTML,
                     description="The page advisor property show writes, rendered on read and never written")
    def property_page(property_id: str) -> str:
        if not isinstance(property_id, str) or not SAFE_ID.match(property_id):
            raise ResourceError(f"{property_id!r} is not a property id")
        with advisor._run_lock:
            try:
                return advisor.property_page(property_id)
            except cli.CliRefusal as refusal:
                raise ResourceError(str(refusal)) from None

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="advisor-mcp",
                                     description="Property Maintenance Advisor v2 as a local MCP server over stdio")
    parser.add_argument("--live", action="store_true",
                        help="allow live runs when ADVISOR_MODE names a live mode; every paid call still shows the "
                             "preflight and waits for a confirmation")
    parser.add_argument("--cassette", metavar="PATH",
                        help=f"replay only: the cassette every brief replays (or {config.ENV_CASSETTE})")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for `advisor-mcp` and `python -m agent.mcp_server`: serve over stdio until stdin closes."""
    cli.TRACING_OPTED_IN = cli.scrub_tracing_env()
    os.environ[cli.ENV_STRICT_MSGPACK] = "true"
    args = build_parser().parse_args(argv)
    try:
        mode = cli.resolve_mode(os.environ, live=args.live)
        cli.resolve_cassette(args.cassette, os.environ, mode=mode)
        cli.refuse_unapproved_full(mode)
    except cli.CliRefusal as refusal:
        print(f"advisor-mcp: refused: {refusal}", file=sys.stderr)
        return cli.EXIT_REFUSED
    build_server(live=args.live, cassette=args.cassette).run("stdio")
    return cli.EXIT_OK


if __name__ == "__main__":
    # Run as `python -m agent.mcp_server`, this file is __main__; delegating to the
    # imported module keeps one copy of its classes, as agent/cli.py does.
    from agent.mcp_server import main as _main

    sys.exit(_main())
