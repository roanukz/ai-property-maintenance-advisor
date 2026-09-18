"""Pydantic models for the advisor (PLAN.md section 7).

Two model sets live here.

1. **Parity models** (`Extraction` through `Brief`): field names and value
   types follow v1 `lib/types.ts`; which fields may be missing follows the v1
   validator (`lib/brief-validate.ts`). They are lenient, use `extra="ignore"`,
   and are never sent to the API. `model_dump(exclude_unset=True)` leaves a key
   absent when v1 would leave it absent, and a validator sets a key explicitly
   wherever v1 writes it (`try_first`, `candidates`, `safety_flag`, `who`,
   `confirmed`, `matches`, caution `source_index`, `warranty.cautions`,
   `warranty.verify`, `no_reliable_answer.found`), so SC1b can compare dumps.

2. **`BriefDraft`**, the model facing schema (decision 22): every field
   required, no defaults, `""` meaning none for optional free text, nullable
   only where absence carries meaning. `draft_to_brief_dict` turns it into the
   parity shape and attaches the code built sources.

Which v1 checks live in this schema layer (shape and type):

- status is one of the allowed values (check 1; v2 adds `budget_stopped`)
- sources is a list; each source is an object whose url is text matching
  `^https?://` (scheme case insensitive), title is text or missing or null,
  tier is one of three exact values (checks 2, 3a to 3d, null element)
- try_first and candidates that are missing or not lists become `[]` (check 4)
- the refusal block's shape: `searched` a string list with no default, `found`
  a string list defaulting to `[]`, `why_insufficient` text (checks 6 to 8)
- step and candidate text fields, `detail`, `why_shown` text or missing
  (checks 12, 13, 16 to 18); `safety_flag`, `confirmed`, `matches` via
  `v is True` (checks 14, 20, 30); unknown `who` becomes technician (check 19)
- `source_index` on steps and candidates is an integer: bools and strings
  rejected, integral floats accepted (the type half of checks 15 and 21)
- caution text is text (checks 22, 27); a caution `source_index` that is not
  an integer becomes None (the type half of checks 23 and 28)
- warranty age statement text, cautions not a list become `[]`, a bare string
  caution becomes `{text, source_index: None}`, verify a string list
  defaulting to `[]` (checks 24 to 26, 29); happened_before summary text or
  missing (check 31)
- `code`, `matched_identity`, `observed_code` accept any JSON value

Which v1 checks live in `agent/rules/v1_parity.py` (Phase 2), because they
depend on status, on other fields, or mutate across fields:

- ok requires a non empty sources list (check 10)
- NRA requires the explanation block to be present (check 5)
- NRA clears try_first and candidates and returns early (check 9)
- ok nulls a filled refusal block (check 11)
- every `source_index` is in range of the sources list (the range half of
  checks 15, 21, 23, 28; a bad caution index becomes None there)
- first failure wins, and the v1 message to reason code mapping

Known limit of a status independent schema: v1 skips every per item check on
the NRA path and skips the refusal block on the ok path, while this schema
checks both. So `v1_parity.py` (what SC1b compares) is a plain dict port that
never goes through these models; the rules pipeline builds the final `Brief`
only after the parity layer and the v2 rules have run.

Phase 2 schema pass (decision 29): `Warranty.record_id` and `Warranty.terms`
are code written, and `DraftWarranty` has no `age_statement`, because code
computes the unit's age from the registry or the manufacture date.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr, field_validator, model_validator

from agent import config

# re.ASCII: Python's Unicode case folding lets U+017F match "s" and U+212A
# match "k"; JavaScript's /i (no u flag), which v1 uses, folds ASCII only.
HTTP_URL = re.compile(r"^https?://", re.IGNORECASE | re.ASCII)

SourceTier = Literal["manufacturer", "dealer", "forum"]
Confidence = Literal["high", "low", "unreadable"]
BriefStatus = Literal["ok", "no_reliable_answer", "budget_stopped"]
DraftStatus = Literal["ok", "no_reliable_answer"]
Who = Literal["anyone", "technician"]
UpgradeReason = Literal["discontinued", "parts_unavailable"]
SourceOrigin = Literal["search", "extract", "graph"]
Mode = Literal[config.MODES]

_WHO_VALUES = get_args(Who)


# ---------------------------------------------------------------------------
# Shared value rules (the parity traps of PLAN 6.2)
# ---------------------------------------------------------------------------


def _is_integral(value: Any) -> bool:
    """True for what JavaScript's Number.isInteger accepts from JSON."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def _strict_index(value: Any) -> int:
    # Python treats True as 1 and Pydantic lax mode turns "0" into 0; v1
    # rejects both, and accepts 2.0 because JSON has one number type.
    if not _is_integral(value):
        raise ValueError(f"source_index must be an integer, got {type(value).__name__}")
    return int(value)


def _lenient_index(value: Any) -> int | None:
    # A caution may be uncited: v1 nulls a bad index instead of rejecting.
    return int(value) if _is_integral(value) else None


def _is_true(value: Any) -> bool:
    # v1 uses `=== true`; lax coercion would turn "true" into True, the
    # unsafe direction for `confirmed`.
    return value is True


def _falsy_to_none(value: Any) -> Any:
    # v1 skips an object slot whose value is falsy (null, false, 0, "").
    if value is None or value is False or value == 0 or value == "":
        return None
    return value


def _lowercase(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


def _normalize_who(value: Any) -> str:
    return value if value in _WHO_VALUES else "technician"


StrictIndex = Annotated[int, BeforeValidator(_strict_index)]
LenientIndex = Annotated[int | None, BeforeValidator(_lenient_index)]
TrueOnly = Annotated[bool, BeforeValidator(_is_true)]


def _as_dict(data: Any) -> Any:
    """Copy a mapping so before validators never mutate caller data."""
    return dict(data) if isinstance(data, dict) else data


class _Parity(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# v1 parity models (types.ts), with the v2 additions of PLAN 7.3
# ---------------------------------------------------------------------------


class ExtractionConfidence(_Parity):
    manufacturer: Confidence
    model: Confidence
    serial: Confidence
    manufacture_date: Confidence


class Extraction(_Parity):
    """What read_plate returns; every field required, as v1's JSON schema had it."""

    manufacturer: StrictStr | None
    model: StrictStr | None
    serial: StrictStr | None
    manufacture_date: StrictStr | None
    confidence: ExtractionConfidence


class Identity(_Parity):
    """v1 identity: four strings, empty meaning unknown."""

    manufacturer: StrictStr
    model: StrictStr
    serial: StrictStr
    manufacture_date: StrictStr


class BriefSource(_Parity):
    url: StrictStr
    tier: SourceTier
    title: StrictStr | None = None
    # v2 additions, all optional so v1 payloads validate unchanged.
    host: StrictStr | None = None
    retrieved_at: StrictStr | None = None
    origin: SourceOrigin | None = None
    proposed_tier: SourceTier | None = None

    @field_validator("url")
    @classmethod
    def _web_url(cls, value: str) -> str:
        if not HTTP_URL.match(value):
            raise ValueError("source url is not a web URL")
        return value


class TryFirstStep(_Parity):
    step: StrictStr
    source_index: StrictIndex
    detail: StrictStr | None = None
    safety_flag: TrueOnly = False

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if isinstance(data, dict):
            data["safety_flag"] = _is_true(data.get("safety_flag"))
        return data


class Candidate(_Parity):
    documented_meaning: StrictStr
    documented_action: StrictStr
    source_index: StrictIndex
    code: Any = None
    who: Who = "technician"
    why_shown: StrictStr | None = None
    confirmed: TrueOnly = False
    evidence: StrictStr | None = None  # v2: verbatim quote from the cited source

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if isinstance(data, dict):
            data["who"] = _normalize_who(data.get("who"))
            data["confirmed"] = _is_true(data.get("confirmed"))
        return data


class TopWarrantyCaution(_Parity):
    """Brief.warranty_caution. v1 checks `.text` on it, so a bare string fails (check 22)."""

    text: StrictStr
    source_index: LenientIndex = None

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if isinstance(data, dict):
            data["source_index"] = _lenient_index(data.get("source_index"))
        return data


class WarrantyCaution(TopWarrantyCaution):
    """An entry of warranty.cautions, the one place v1 accepts a bare string (check 26)."""

    @model_validator(mode="before")
    @classmethod
    def _v1_bare_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            # Guardrail test 17: a bare string caution is uncited.
            return {"text": data, "source_index": None}
        return data


class Warranty(_Parity):
    age_statement: StrictStr  # v2: written by code (decision 29), never the model
    cautions: list[WarrantyCaution] = Field(default_factory=list)
    verify: list[StrictStr] = Field(default_factory=list)
    # v2 (decision 29): the registry record the age or terms come from, and the
    # warranty terms quoted from that record. Code fills both.
    record_id: StrictStr | None = None
    terms: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if isinstance(data, dict):
            if not isinstance(data.get("cautions"), list):
                data["cautions"] = []
            if data.get("verify") is None:
                data["verify"] = []
        return data


class HappenedBefore(_Parity):
    matches: TrueOnly = False
    summary: StrictStr | None = None
    record_id: StrictStr | None = None  # v2: the service record it cites

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if isinstance(data, dict):
            data["matches"] = _is_true(data.get("matches"))
        return data


class NoReliableAnswer(_Parity):
    searched: list[StrictStr]
    found: list[StrictStr] = Field(default_factory=list)
    why_insufficient: StrictStr

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if isinstance(data, dict) and data.get("found") is None:
            data["found"] = []
        return data


class UpgradeOption(_Parity):
    successor_model: StrictStr
    reason: UpgradeReason
    summary: StrictStr
    source_index: StrictIndex
    successor_manufacturer: StrictStr | None = None
    evidence: StrictStr | None = None


class MaintenanceItem(_Parity):
    task: StrictStr
    interval: StrictStr  # verbatim from the cited source
    source_index: StrictIndex
    evidence: StrictStr | None = None
    last_done_record_id: StrictStr | None = None
    last_done_on: StrictStr | None = None  # copied by code from that maintenance_log row
    due_date: StrictStr | None = None  # computed by code from the registry


def _list_or_empty(value: Any) -> Any:
    return value if isinstance(value, list) else []


class Brief(_Parity):
    status: BriefStatus
    sources: list[BriefSource]
    matched_identity: Any = None
    observed_code: Any = None
    warranty_caution: TopWarrantyCaution | None = None
    # A JSON array is a truthy object to v1: it sets `.matches` on it without
    # throwing, and JSON.stringify then drops that property, so v1 outputs the
    # array unchanged. Kept as is so the accept decision and the normalized
    # output both match; renderers treat it as no match.
    happened_before: HappenedBefore | list[Any] | None = None
    try_first: list[TryFirstStep] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    warranty: Warranty | None = None
    no_reliable_answer: NoReliableAnswer | None = None
    upgrade_options: list[UpgradeOption] = Field(default_factory=list)
    maintenance_due: list[MaintenanceItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _v1_writes(cls, data: Any) -> Any:
        data = _as_dict(data)
        if not isinstance(data, dict):
            return data
        # v1 lines 58 to 59 write both lists on every path.
        data["try_first"] = _list_or_empty(data.get("try_first"))
        data["candidates"] = _list_or_empty(data.get("candidates"))
        for key in ("warranty_caution", "happened_before", "warranty", "no_reliable_answer"):
            if key in data:
                data[key] = _falsy_to_none(data[key])
        return data


# ---------------------------------------------------------------------------
# Run wrapper and CLI intake
# ---------------------------------------------------------------------------


class RunResult(BaseModel):
    """Replaces v1 BriefResult; the brief plus how the run went."""

    model_config = ConfigDict(extra="forbid")

    brief: Brief
    thread_id: str
    mode: Mode
    route: list[str] = Field(default_factory=list)
    route_reason: str = ""
    generated_at: str
    all_forum: bool = False
    refusal_origin: Literal["model", "forced"] | None = None
    stop_reason: str | None = None
    cost_usd: float = 0.0
    tavily_credits: int = 0
    searches: int = 0
    fetches: int = 0
    latency_s: float = 0.0
    usage: dict[str, int] = Field(default_factory=dict)


def _text_or_none(field: str, value: Any) -> str | None:
    # Typed at the boundary so v1's ".trim is not a function" cannot recur.
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text or empty, got {type(value).__name__}")
    return value or None


class IntakeIdentity(BaseModel):
    """A typed identity: v1's "" means unknown, stored as None inside v2."""

    model_config = ConfigDict(extra="forbid")

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    manufacture_date: str | None = None

    @field_validator("manufacturer", "model", "serial", "manufacture_date", mode="before")
    @classmethod
    def _text(cls, value: Any, info: Any) -> str | None:
        return _text_or_none(info.field_name, value)


class Intake(BaseModel):
    """Validated CLI input for `advisor ask`."""

    model_config = ConfigDict(extra="forbid")

    symptom: str
    mode: Mode = "replay"
    photo_path: str | None = None
    identity: IntakeIdentity | None = None
    property_id: str | None = None
    appliance_id: str | None = None
    code: str | None = None

    @field_validator("symptom", mode="before")
    @classmethod
    def _symptom(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("symptom must be non empty text")
        return value

    @field_validator("photo_path", "property_id", "appliance_id", "code", mode="before")
    @classmethod
    def _optional_text(cls, value: Any, info: Any) -> str | None:
        return _text_or_none(info.field_name, value)

    @model_validator(mode="after")
    def _photo_or_identity(self) -> Intake:
        if self.photo_path is not None and self.identity is not None:
            raise ValueError("give a photo or a typed identity, not both")
        return self


# ---------------------------------------------------------------------------
# BriefDraft: the model facing schema (decision 22)
# ---------------------------------------------------------------------------
# Every field is required and has no default: transform_schema copies
# `required` unchanged, so a default would count as an optional parameter.
# Free text that may be absent is a required string where "" means none.

LowerStatus = Annotated[DraftStatus, BeforeValidator(_lowercase)]
LowerTier = Annotated[SourceTier, BeforeValidator(_lowercase)]
LowerReason = Annotated[UpgradeReason, BeforeValidator(_lowercase)]
LowerWho = Annotated[Who, BeforeValidator(lambda v: _normalize_who(_lowercase(v)))]


class _Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DraftCaution(_Draft):
    text: str
    source_index: LenientIndex = Field(description="Index of the provided source, or null when uncited.")


class DraftStep(_Draft):
    step: str
    detail: str = Field(description='Extra detail, or "" for none.')
    safety_flag: TrueOnly
    source_index: StrictIndex


class DraftCandidate(_Draft):
    code: str | None = Field(description="The documented code, or null when the candidate has none.")
    documented_meaning: str
    documented_action: str
    who: LowerWho
    why_shown: str = Field(description='Which documented symptom or code it matches, or "".')
    source_index: StrictIndex
    confirmed: TrueOnly
    evidence: str = Field(description='Verbatim quote from the cited source, or "".')


class DraftHappenedBefore(_Draft):
    matches: TrueOnly
    summary: str = Field(description='One line summary, or "".')
    record_id: str = Field(description='record_id of the matching service record, or "".')


class DraftWarranty(_Draft):
    """The model's warranty block. The age statement is code output (decision 29)."""

    cautions: list[DraftCaution]
    verify: list[str]


class DraftNoReliableAnswer(_Draft):
    found: list[str]
    why_insufficient: str


class DraftUpgrade(_Draft):
    successor_manufacturer: str | None
    successor_model: str
    reason: LowerReason
    summary: str
    source_index: StrictIndex
    evidence: str = Field(description='Verbatim quote from the cited source, or "".')


class DraftMaintenance(_Draft):
    task: str
    interval: str = Field(description="The interval exactly as the source states it.")
    source_index: StrictIndex
    evidence: str = Field(description='Verbatim quote from the cited source, or "".')
    last_done_record_id: str = Field(description='record_id of the last time it was done, or "".')


class DraftSourceTier(_Draft):
    source_index: StrictIndex
    tier: LowerTier
    authorship_quote: str = Field(description='Verbatim quote naming the maker as author, or "".')


class BriefDraft(_Draft):
    """What synthesize asks the model for. Code builds sources and the trail."""

    status: LowerStatus
    matched_identity: str | None = Field(description="What you matched, or null.")
    warranty_caution: DraftCaution | None
    happened_before: DraftHappenedBefore | None
    try_first: list[DraftStep]
    candidates: list[DraftCandidate]
    warranty: DraftWarranty | None
    no_reliable_answer: DraftNoReliableAnswer | None
    upgrade_options: list[DraftUpgrade]
    maintenance_due: list[DraftMaintenance]
    source_tiers: list[DraftSourceTier]


def _none_if_empty(value: str | None) -> str | None:
    return value or None


def _caution_dict(c: DraftCaution) -> dict:
    return {"text": c.text, "source_index": c.source_index}


def _attach_sources(draft: BriefDraft, sources: list[Any]) -> list[dict]:
    """Code built sources, each with the tier the model proposed.

    The final tier is decided later by the tier rule (host ceiling). Until then
    `tier` holds the proposal, or forum when the model gave none.
    """
    proposals: dict[int, DraftSourceTier] = {}
    for entry in draft.source_tiers:
        if 0 <= entry.source_index < len(sources):
            proposals.setdefault(entry.source_index, entry)
    out = []
    for i, source in enumerate(sources):
        item = source.model_dump(exclude_none=True) if isinstance(source, BaseModel) else dict(source)
        proposal = proposals.get(i)
        item["proposed_tier"] = proposal.tier if proposal else None
        item["tier"] = proposal.tier if proposal else "forum"
        if proposal and proposal.authorship_quote:
            # Not a Brief field; the tier rule reads it before model_validate.
            item["authorship_quote"] = proposal.authorship_quote
        out.append(item)
    return out


def draft_to_brief_dict(
    draft: BriefDraft,
    sources: list[Any],
    *,
    observed_code: str | None = None,
    searched: list[str] | None = None,
) -> dict:
    """Convert a draft to the parity brief shape, turning "" into None.

    `sources` is the run's code built registry; `searched` is the code built
    search trail for a refusal block; `observed_code` is set by code. The
    warranty age statement, record_id and terms are left for the registry
    dates rule (agent/rules/citations.py) to write.
    """
    brief: dict[str, Any] = {
        "status": draft.status,
        "matched_identity": _none_if_empty(draft.matched_identity),
        "observed_code": observed_code,
        "warranty_caution": _caution_dict(draft.warranty_caution) if draft.warranty_caution else None,
        "happened_before": None,
        "try_first": [
            {
                "step": s.step,
                "detail": _none_if_empty(s.detail),
                "safety_flag": s.safety_flag,
                "source_index": s.source_index,
            }
            for s in draft.try_first
        ],
        "candidates": [
            {
                "code": _none_if_empty(c.code),
                "documented_meaning": c.documented_meaning,
                "documented_action": c.documented_action,
                "who": c.who,
                "why_shown": _none_if_empty(c.why_shown),
                "source_index": c.source_index,
                "confirmed": c.confirmed,
                "evidence": _none_if_empty(c.evidence),
            }
            for c in draft.candidates
        ],
        "warranty": None,
        "no_reliable_answer": None,
        "upgrade_options": [
            {
                "successor_manufacturer": _none_if_empty(u.successor_manufacturer),
                "successor_model": u.successor_model,
                "reason": u.reason,
                "summary": u.summary,
                "source_index": u.source_index,
                "evidence": _none_if_empty(u.evidence),
            }
            for u in draft.upgrade_options
        ],
        "maintenance_due": [
            {
                "task": m.task,
                "interval": m.interval,
                "source_index": m.source_index,
                "evidence": _none_if_empty(m.evidence),
                "last_done_record_id": _none_if_empty(m.last_done_record_id),
                "last_done_on": None,
                "due_date": None,
            }
            for m in draft.maintenance_due
        ],
        "sources": _attach_sources(draft, sources),
    }
    if draft.happened_before:
        hb = draft.happened_before
        brief["happened_before"] = {
            "matches": hb.matches,
            "summary": _none_if_empty(hb.summary),
            "record_id": _none_if_empty(hb.record_id),
        }
    if draft.warranty:
        w = draft.warranty
        brief["warranty"] = {
            # Placeholder text so the parity layer's check 24 sees a string;
            # the registry dates rule writes the real statement (decision 29).
            "age_statement": "",
            "cautions": [_caution_dict(c) for c in w.cautions],
            "verify": list(w.verify),
            "record_id": None,
            "terms": None,
        }
    if draft.no_reliable_answer:
        n = draft.no_reliable_answer
        brief["no_reliable_answer"] = {
            "searched": list(searched or []),
            "found": list(n.found),
            "why_insufficient": n.why_insufficient,
        }
    return brief


# ---------------------------------------------------------------------------
# Structured output limits (decision 22)
# ---------------------------------------------------------------------------


def _is_union(node: dict) -> bool:
    return "anyOf" in node or isinstance(node.get("type"), list)


def _count_node(node: Any, defs: dict, follow_refs: bool, seen: tuple[str, ...], tally: dict) -> None:
    """Walk one schema node, counting optional properties and unions."""
    if isinstance(node, list):
        for item in node:
            _count_node(item, defs, follow_refs, seen, tally)
        return
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if ref is not None:
        name = ref.rsplit("/", 1)[-1]
        if follow_refs and name not in seen:
            _count_node(defs[name], defs, follow_refs, seen + (name,), tally)
        return
    if _is_union(node):
        tally["unions"] += 1
    if node.get("type") == "object":
        required = set(node.get("required", []))
        tally["optional"] += sum(1 for key in node.get("properties", {}) if key not in required)
        for child in node.get("properties", {}).values():
            _count_node(child, defs, follow_refs, seen, tally)
    for key in ("items", "anyOf", "allOf"):
        if key in node:
            _count_node(node[key], defs, follow_refs, seen, tally)


def count_structured_output_params(model_cls: type[BaseModel]) -> dict:
    """Count optional and union parameters the way the API would see them.

    Runs the model through anthropic's `transform_schema` (what
    langchain-anthropic sends as `output_config.format`). "Per defs" counts each
    `$defs` entry once plus the root; "per use site" inlines every `$ref`, so a
    model used in two places counts twice.
    """
    from anthropic import transform_schema

    schema = transform_schema(model_cls)
    defs = schema.get("$defs", {})
    root = {k: v for k, v in schema.items() if k != "$defs"}

    per_defs = {"optional": 0, "unions": 0}
    by_def: dict[str, dict[str, int]] = {}
    for name, body in [("<root>", root), *defs.items()]:
        tally = {"optional": 0, "unions": 0}
        _count_node(body, defs, False, (), tally)
        by_def[name] = tally
        per_defs["optional"] += tally["optional"]
        per_defs["unions"] += tally["unions"]

    per_use = {"optional": 0, "unions": 0}
    _count_node(root, defs, True, (), per_use)

    return {
        "optional_per_defs": per_defs["optional"],
        "unions_per_defs": per_defs["unions"],
        "optional_per_use_site": per_use["optional"],
        "unions_per_use_site": per_use["unions"],
        "by_def": by_def,
    }
