"""gather: the join after the retrieval branches (PLAN 8.3).

It writes nothing but its latency. `next_after_gather` sends a budget stopped
run to validate, which checks nothing on a budget stop and writes the one
budget stop brief before render (a Phase 2 departure: every budget stop gets
the same brief), a run whose research call was declined to render (decision
69: research wrote the decline brief), and every other run to synthesize. The
graph maps each label to the node of the same name.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from agent.declined import is_declined
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "gather"


def gather(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Join point; runs once per attempt."""
    started = time.perf_counter()
    return latency_entry(state, NODE, time.perf_counter() - started)


def next_after_gather(state: AdvisorState) -> str:
    """Conditional edge: render after a declined research call (its brief is written), validate (the
    budget stop brief) on a budget stop, else synthesize."""
    if is_declined(state):
        return "render"
    return "validate" if state.get("status") == "budget_stopped" else "synthesize"
