"""The advisor graph (PLAN sections 8.3 and 8.4) and the runner the CLI and tests share.

Phase 4 wiring:

    START -> intake -> read_plate -> confirm_identity (loops on itself until a
    model is given) -> route -> {history, graph_lookup, research} -> gather
    -> synthesize -> validate -> [upgrade_check -> validate, once, on an ok
    brief] -> render -> persist -> END

with these branches:

- route fans out to the branches it names (route's "graph" is the
  graph_lookup node). They run in one superstep, each a single node with a
  plain edge to gather, so gather and synthesize run once per attempt (PLAN
  8.3). Every key written inside the fan out has a reducer or one writer
  (PLAN 8.2): sources, search_trail, latency, cost_usd and tavily_credits have
  reducers; history_hits, graph_hits, research_attempts, status and
  stop_reason each have one writer in the group.
- read_plate budget stop, and gather on a budget stop: to validate, which
  validates nothing on a budget stop and writes the one budget stop brief
  (refuse.build_budget_stopped_brief), then render.
- after validate (section 8.4): budget stop or a clean refusal to render; a
  clean ok brief to upgrade_check the first time (decision 27), which adds
  graph derived upgrade options to the draft and returns to validate once
  (straight to render when the graph has nothing to add), and to render
  after that upgrade pass; a second failure to refuse; a first failure to retry_gate, which takes the
  retry through research alone when it is affordable (decision 23) and
  otherwise ends the run budget_stopped with stop reason "retry not
  affordable". A budget stop never reaches refuse.
- persist runs after render on every path that reaches END, and writes the
  lookup row, the run record and the graph edges (decision 37).
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent import config
from agent.ledger import Ledger, price_usage
from agent.models import max_tokens_for
from agent.nodes.confirm_identity import confirm_identity, next_after_confirm
from agent.nodes.gather import gather, next_after_gather
from agent.nodes.intake import intake
from agent.nodes.graph_lookup import graph_lookup
from agent.nodes.history import history
from agent.nodes.persist import persist
from agent.nodes.read_plate import read_plate
from agent.nodes.refuse import build_budget_stopped_brief, refuse
from agent.nodes.render import render
from agent.nodes.research import research
from agent.nodes.route import route
from agent.nodes.synthesize import open_ledger, synthesize
from agent.nodes.upgrade_check import upgrade_check, upgrade_pass_done
from agent.nodes.validate import validate
from agent.state import AdvisorState, RunContext, check_json_native, latency_entry

RETRY_NOT_AFFORDABLE = "retry not affordable"
RETRY_GATE = "retry_gate"
UPGRADE_CHECK = "upgrade_check"
# route's branch names and the node that runs each; all three run in one superstep.
BRANCH_NODES = {"history": "history", "graph": "graph_lookup", "research": "research"}


# ---------------------------------------------------------------------------
# Retry affordability (decision 23)
# ---------------------------------------------------------------------------


def synthesize_reservation_usd(mode: str) -> float:
    """The full synthesize reservation: the source budget plus prompt, with margin, plus max_tokens."""
    model = config.MODEL_FOR[mode]["synthesize"]
    prices = config.REPLAY_PRICES if mode == "replay" else config.PRICES_PER_MTOK[model]
    margin = config.ESTIMATE_MARGIN.get(model, max(config.ESTIMATE_MARGIN.values()))
    input_tokens = math.ceil(
        (config.SYNTH_SOURCE_TOKEN_BUDGET + config.SYNTH_PROMPT_TOKENS_ESTIMATE) * margin
    )
    return price_usage(
        prices, {"input_tokens": input_tokens, "output_tokens": max_tokens_for("synthesize", mode)}
    )


def retry_need_usd(mode: str) -> float:
    """What a retry must be able to spend: the retry pass's typical cost plus the synthesize reservation."""
    return config.RETRY_TYPICAL_USD + synthesize_reservation_usd(mode)


def retry_affordable(ctx: RunContext) -> tuple[bool, float, float]:
    """(affordable, remaining run budget, need), read from the ledger, which is the truth."""
    ledger: Ledger = open_ledger(ctx)
    cap = Ledger.run_cap(ctx.mode, (ctx.caps or {}).get("run_cap_usd"))
    remaining = cap - ledger.run_total(ctx.run_id)
    need = retry_need_usd(ctx.mode)
    return remaining >= need, remaining, need


def retry_gate(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """After a first validation failure: allow the retry, or stop the run as budget_stopped.

    A node rather than an edge so the decision is checkpointed once and a
    resumed or replayed thread never re-reads a ledger that has moved on.
    """
    started = time.perf_counter()
    ok, _, _ = retry_affordable(runtime.context)
    out: dict[str, Any] = latency_entry(state, RETRY_GATE, time.perf_counter() - started)
    if not ok:
        stopped = {**state, "status": "budget_stopped", "stop_reason": RETRY_NOT_AFFORDABLE}
        out.update(
            status="budget_stopped",
            stop_reason=RETRY_NOT_AFFORDABLE,
            brief=build_budget_stopped_brief(stopped),
        )
    return out


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------


def after_read_plate(state: AdvisorState) -> str:
    return "validate" if state.get("status") == "budget_stopped" else "confirm_identity"


def after_route(state: AdvisorState) -> list[str]:
    """The branch nodes route chose; returning several runs them in parallel."""
    route_list = list(state.get("route") or [])
    unknown = [b for b in route_list if b not in BRANCH_NODES]
    if unknown or not route_list:
        raise ValueError(f"route names no branch, or one this graph cannot run: {route_list!r}")
    return [BRANCH_NODES[b] for b in route_list]


def after_validate(state: AdvisorState) -> str:
    """The section 8.4 table: upgrade_check runs once, and only after a clean ok brief."""
    if state.get("status") == "budget_stopped":
        return "render"
    if state.get("brief") is not None and not state.get("validation_errors"):
        if state["brief"].get("status") == "ok" and not upgrade_pass_done(state):
            return UPGRADE_CHECK
        return "render"
    if int(state.get("validation_failures") or 0) >= 2:
        return "refuse"
    return RETRY_GATE


def after_retry_gate(state: AdvisorState) -> str:
    return "render" if state.get("status") == "budget_stopped" else "research"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_graph(checkpointer: Any) -> Any:
    """Compile the Phase 4 graph with the given checkpointer."""
    builder = StateGraph(AdvisorState, context_schema=RunContext)
    builder.add_node("intake", intake)
    builder.add_node("read_plate", read_plate)
    builder.add_node("confirm_identity", confirm_identity)
    builder.add_node("route", route)
    builder.add_node("history", history)
    builder.add_node("graph_lookup", graph_lookup)
    builder.add_node("research", research)
    builder.add_node("gather", gather)
    builder.add_node("synthesize", synthesize)
    builder.add_node("validate", validate)
    builder.add_node(RETRY_GATE, retry_gate)
    # upgrade_check returns Command(goto=...): validate when it added options,
    # render when there was nothing to add (the second pass would be a no-op).
    builder.add_node(UPGRADE_CHECK, upgrade_check, destinations=("validate", "render"))
    builder.add_node("refuse", refuse)
    builder.add_node("render", render)
    builder.add_node("persist", persist)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "read_plate")
    builder.add_conditional_edges("read_plate", after_read_plate, ["validate", "confirm_identity"])
    builder.add_conditional_edges(
        "confirm_identity", next_after_confirm, {"confirm_identity": "confirm_identity", "route": "route"}
    )
    builder.add_conditional_edges("route", after_route, list(BRANCH_NODES.values()))
    # One plain edge per branch, never add_edge([...], "gather"): that form
    # waits for every listed node, and a route runs only some of them.
    for node in BRANCH_NODES.values():
        builder.add_edge(node, "gather")
    # A budget stop goes through validate, which writes the budget stop brief
    # and checks nothing, so every budget stop carries the same brief.
    builder.add_conditional_edges("gather", next_after_gather, ["validate", "synthesize"])
    builder.add_edge("synthesize", "validate")
    builder.add_conditional_edges("validate", after_validate, ["render", "refuse", RETRY_GATE, UPGRADE_CHECK])
    builder.add_conditional_edges(RETRY_GATE, after_retry_gate, ["render", "research"])
    builder.add_edge("refuse", "render")
    builder.add_edge("render", "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=checkpointer)


def open_checkpointer(path: Path | str | None = None) -> Any:
    """A SqliteSaver on its own connection (check_same_thread=False: nodes run on a thread pool)."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    target = Path(path) if path is not None else config.CHECKPOINT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(target), check_same_thread=False))


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": config.RECURSION_LIMIT}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class RunOutcome:
    """What one ask or resume call reached."""

    thread_id: str
    paused: bool
    interrupt: dict[str, Any] | None
    state: dict[str, Any]
    nodes: list[str] = field(default_factory=list)  # nodes that ran in this call, in order
    states: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (node, state after it)

    @property
    def status(self) -> str | None:
        return self.state.get("status")


def run_until_pause_or_end(
    graph: Any, input_or_command: dict[str, Any] | Command, ctx: RunContext, thread_id: str,
) -> RunOutcome:
    """Run a thread until it pauses at the interrupt or reaches END.

    Streams updates and values so callers see every node and the state after
    it. The persist node, not this runner, records a finished run.
    """
    cfg = thread_config(thread_id)
    nodes: list[str] = []
    states: list[tuple[str, dict[str, Any]]] = []
    last_nodes: list[str] = []
    for part in graph.stream(
        input_or_command, cfg, context=ctx, durability="sync", version="v2",
        stream_mode=["updates", "values"],
    ):
        if part["type"] == "updates":
            # Parallel branches report one update each before their superstep's values.
            ran = [name for name in part["data"] if not name.startswith("__")]
            nodes.extend(ran)
            last_nodes.extend(ran)
        elif part["type"] == "values" and last_nodes:
            for name in last_nodes:
                states.append((name, part["data"]))
            last_nodes = []
    snapshot = graph.get_state(cfg)
    values = dict(snapshot.values)
    check_json_native(values)
    interrupts = list(snapshot.interrupts or ())
    paused = bool(interrupts)
    payload = interrupts[0].value if interrupts else None
    return RunOutcome(thread_id=thread_id, paused=paused, interrupt=payload, state=values,
                      nodes=nodes, states=states)
