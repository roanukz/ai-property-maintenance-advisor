"""Graph state and the per call run context (PLAN section 8.2).

AdvisorState holds only JSON native values so the SQLite checkpointer never has
to pickle or msgpack a custom type. RunContext is passed per call and is never
checkpointed, which is why it can hold a cassette object and a live collector.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

STATUSES = ("running", "ok", "no_reliable_answer", "budget_stopped")
Status = Literal["running", "ok", "no_reliable_answer", "budget_stopped"]


def merge_dicts(left: dict[str, float] | None, right: dict[str, float] | None) -> dict[str, float]:
    """Reducer for latency: each node adds its own entry without erasing others."""
    return {**(left or {}), **(right or {})}


def add_sources(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Reducer for sources: append only, and a URL already registered is not added again.

    history, graph_lookup and research run in one superstep, so research
    cannot see what graph_lookup found; both may find one URL. The first
    registration wins, so every earlier index keeps pointing at the same URL.
    """
    out = list(left or [])
    seen = {s.get("url") for s in out}
    for source in right or []:
        if source.get("url") in seen:
            continue
        seen.add(source.get("url"))
        out.append(source)
    return out


def latency_entry(state: Any, node: str, seconds: float) -> dict[str, dict[str, float]]:
    """The latency delta for one node run: `node` the first time, then `node_2`, `node_3`.

    A node that runs again (a retry, or a confirm loop) keeps every run's
    duration, so the run record's latency_s covers them all.
    """
    seen = (state or {}).get("latency") or {}
    key, n = node, 1
    while key in seen:
        n += 1
        key = f"{node}_{n}"
    return {"latency": {key: seconds}}


class AdvisorState(TypedDict, total=False):
    """Checkpointed state. Keys written inside the parallel fan out have reducers."""

    run_id: str
    mode: str
    property_id: str | None
    appliance_id: str | None
    symptom: str
    photo: dict[str, str] | None  # {path, sha256}, never base64
    extraction: dict[str, Any] | None
    identity: dict[str, Any] | None
    identity_confirmed: bool
    observed_code: str | None  # confirmed: from --code or the confirm_identity resume only
    observed_code_candidate: str | None  # guessed from the symptom text; never enforced
    confirm_prompt: str | None
    route: list[str]
    route_reason: str
    research_limits: str | None  # the config.RESEARCH_LIMITS row route picked; route is its only writer
    sources: Annotated[list[dict[str, Any]], add_sources]
    search_trail: Annotated[list[dict[str, Any]], operator.add]
    graph_hits: list[dict[str, Any]]
    history_hits: list[dict[str, Any]]
    draft: dict[str, Any] | None
    validation_errors: list[str]
    validation_failures: int
    research_attempts: int
    status: Status
    refusal_origin: str | None
    stop_reason: str | None
    brief: dict[str, Any] | None
    # Rule 4's report for the last validated draft (decision 25): one entry per
    # candidate code, and the run's summary (verified, unverifiable, failed).
    grounding: list[dict[str, Any]]
    grounding_status: str | None
    # Mirrors of the ledger (the ledger is the truth); writers return deltas.
    cost_usd: Annotated[float, operator.add]
    tavily_credits: Annotated[int, operator.add]
    latency: Annotated[dict[str, float], merge_dicts]
    html_path: str | None
    persist_report: dict[str, Any] | None  # what persist wrote: lookup, run record, graph edges


@dataclass
class RunContext:
    """Per run settings and handles. Holds paths, never open connections."""

    run_id: str
    mode: str
    ledger_path: Path
    registry_path: Path
    graph_path: Path
    pages_dir: Path
    cassette: object | None = None
    caps: dict | None = None
    # Tool wrappers append successful artifacts here, so sources survive even
    # if the research agent raises.
    collector: list = field(default_factory=list)
    # Replay only: the per run ReplayChatModel for each node ("models") and the
    # stub inners and wrapped tools ("stubs", "tools"). models.py fills it so
    # every node execution in a run shares one script cursor and one call
    # count, and a retry is served the next scripted response, never the first.
    replay: dict = field(default_factory=dict)
    # Live only: the loaded key values, so the paid call sites redact them from
    # any error text before it reaches a log, a model or the checkpoint
    # (agent/redaction.py). Never written anywhere; replay leaves it empty.
    secrets: list = field(default_factory=list, repr=False)


def check_json_native(value: Any, path: str = "state") -> None:
    """Raise TypeError if value is not strictly JSON native.

    Stricter than json.dumps, which quietly accepts tuples and int keys that
    would not survive a round trip unchanged.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            check_json_native(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} has a non string key {key!r}")
            check_json_native(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} holds {type(value).__name__}, which is not JSON native")
