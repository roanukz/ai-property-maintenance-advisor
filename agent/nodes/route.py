"""route: pick the retrieval branches (PLAN 8.4; SC7a, decision 46).

The route table, first matching row wins:

| # | Condition | Route | Research limits |
|---|---|---|---|
| 1 | confirmed code, and the graph has a verified HAS_CODE edge for it on the model or its family | graph | none |
| 2 | confirmed code, the graph has the model verified but not that code | graph + research | top_up |
| 3 | no confirmed code, the graph holds at least config.GRAPH_ONLY_MIN_CAUSES distinct verified DOCUMENTED_CAUSE edges for the model or its family, and the classifier says the symptom matches one of them | graph | none |
| 4 | no confirmed code, the graph has the model verified (any edge), and row 3 does not apply | graph + research | top_up |
| 5 | the graph has nothing verified for the model or family | research | main |

history is added whenever the registry holds records for the appliance
(service records, or its purchase date, install date or warranty terms).

Only the confirmed code counts (decision 7): observed_code_candidate, a guess
from the symptom text, never picks a row. "Verified" means what the
KnowledgeGraph's queries return: edges whose evidence is present, hashes to
its evidence_sha256 and passes the span shape check, so an edge without
evidence is never coverage.

Decision D1 (18 September 2026, after Phase 6, where a vague "not heating"
repeat was answered from a single FLO edge): a question with no confirmed
code is never answered from HAS_CODE edges alone. Row 3 needs enough
documented causes (Coverage.cause_count: distinct normalized labels, so one
cause found on two pages or under both model and family counts once, capped
by the distinct quotes behind them, so one quote under several labels counts
once) and a classifier match; everything else the graph covers takes
row 4. The Haiku classifier runs, through the ledger, only when row 3 is
possible, and it is shown only the cause edges' verbatim evidence, never a
code edge's, so a code's documentation cannot stand in for a symptom.

route writes `route` (branch names in the fixed order history, graph,
research), `route_reason` (a plain sentence that names the row), and
`research_limits`, the config.RESEARCH_LIMITS row research uses ("main",
"top_up", or None when research is not routed).
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from agent import config
from agent.nodes.classify import Classification, classify_symptom
from agent.nodes.graph_lookup import Coverage, graph_coverage, open_knowledge_graph
from agent.nodes.history import load_history
from agent.state import AdvisorState, RunContext

NODE = "route"
HISTORY, GRAPH, RESEARCH = "history", "graph", "research"
BRANCH_ORDER = (HISTORY, GRAPH, RESEARCH)
MAIN, TOP_UP = "main", "top_up"


def _model_name(cov: Coverage) -> str:
    return f"{cov.maker or '(no maker)'} {cov.model or '(no model)'}".strip()


def decide(cov: Coverage, code: str | None, classify: Any) -> tuple[int, list[str], str | None, str, Classification | None]:
    """(row, branches without history, research limits key, reason, classification) for the table above.

    `classify` is called with no arguments, and only when row 3 is possible.
    """
    name = _model_name(cov)
    if code and cov.code_edges:
        return 1, [GRAPH], None, (
            f"Route table row 1: the graph has a verified HAS_CODE edge for the confirmed code {code} "
            f"on {name}, so the graph answers with no search."), None
    if code and cov.has_model:
        return 2, [GRAPH, RESEARCH], TOP_UP, (
            f"Route table row 2: the graph has verified edges for {name} but none for the confirmed "
            f"code {code}, so the graph is topped up with a short research pass."), None
    if not code and cov.has_model:
        causes, needed = cov.cause_count, config.GRAPH_ONLY_MIN_CAUSES
        if causes < needed:
            return 4, [GRAPH, RESEARCH], TOP_UP, (
                f"Route table row 4: no code was confirmed, and the graph has verified edges for {name} "
                f"but {causes} verified documented cause(s), fewer than the {needed} needed to answer "
                "from the graph alone (a code edge never answers a question with no confirmed code), "
                "so the graph is topped up with a short research pass and the classifier is not "
                "called."), None
        verdict = classify()
        if verdict.verdict == "match":
            return 3, [GRAPH], None, (
                f"Route table row 3: no code was confirmed, the graph holds {causes} verified documented "
                f"causes for {name} (at least {needed}), and the classifier says the symptom matches one "
                "of them, so the graph answers with no search."), verdict
        why = f" ({verdict.note})" if verdict.note else ""
        return 4, [GRAPH, RESEARCH], TOP_UP, (
            f"Route table row 4: no code was confirmed, the graph holds {causes} verified documented "
            f"causes for {name}, and the classifier says {verdict.verdict}{why}, so the graph is "
            "topped up with a short research pass."), verdict
    return 5, [RESEARCH], MAIN, (
        f"Route table row 5: the graph has nothing verified for {name} or its family, so research "
        "runs with the main limits."), None


def _documented(cov: Coverage) -> list[dict[str, Any]]:
    """The documented causes the classifier sees: each verified cause edge's evidence, no code edge."""
    return [{"code": None, "evidence": e["evidence"]} for e in cov.cause_edges]


def route(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Apply the route table and add history when the appliance has records."""
    started = time.perf_counter()
    ctx = runtime.context
    code = state.get("observed_code") or None
    cov = graph_coverage(open_knowledge_graph(ctx.graph_path), state.get("identity"), code)

    def classify() -> Classification:
        return classify_symptom(ctx, identity=state.get("identity"), symptom=state.get("symptom") or "",
                                documented=_documented(cov))

    row, branches, limits, reason, verdict = decide(cov, code, classify)
    records = load_history(ctx.registry_path, state.get("appliance_id"))
    if records:
        branches = [HISTORY, *branches]
        reason += f" History is added: the registry holds {len(records)} record(s) for this appliance."
    out: dict[str, Any] = {
        "route": [b for b in BRANCH_ORDER if b in branches],
        "route_reason": reason,
        "research_limits": limits,
        "latency": {NODE: time.perf_counter() - started},
    }
    if verdict is not None and verdict.cost_usd:
        out["cost_usd"] = verdict.cost_usd
    return out
