"""route: pick the retrieval branches (PLAN 8.4; SC7a, decision 46).

The route table, first matching row wins:

| # | Condition | Route | Research limits |
|---|---|---|---|
| 1 | confirmed code, and the graph has a verified HAS_CODE edge for it on the model or its family | graph | none |
| 2 | confirmed code, the graph has the model verified but not that code | graph + research | top_up |
| 3 | no confirmed code, the graph has the model verified, and the classifier says match | graph | none |
| 4 | no confirmed code, the graph has the model verified, and the classifier says no_match or unsure | graph + research | top_up |
| 5 | the graph has nothing verified for the model or family | research | main |

history is added whenever the registry holds records for the appliance
(service records, or its purchase date, install date or warranty terms).

Only the confirmed code counts (decision 7): observed_code_candidate, a guess
from the symptom text, never picks a row. "Verified" means what the
KnowledgeGraph's queries return: edges whose evidence is present, hashes to
its evidence_sha256 and passes the span shape check, so an edge without
evidence is never coverage. The Haiku classifier runs only when rows 3 and 4
need it, through the ledger; when the graph knows the model but documents no
cause for it, there is nothing to match and row 4 applies without a call.

route writes `route` (branch names in the fixed order history, graph,
research), `route_reason` (a plain sentence that names the row), and
`research_limits`, the config.RESEARCH_LIMITS row research uses ("main",
"top_up", or None when research is not routed).
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from agent.nodes.classify import Classification, classify_symptom
from agent.nodes.graph_lookup import Coverage, edge_code, graph_coverage, open_knowledge_graph
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

    `classify` is called with no arguments, and only for rows 3 and 4.
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
        verdict = classify() if cov.model_edges else Classification(
            "unsure", 0.0, "the graph documents no cause for this model to match")
        if verdict.verdict == "match":
            return 3, [GRAPH], None, (
                f"Route table row 3: no code was confirmed, the graph has verified edges for {name}, "
                "and the classifier says the symptom matches a documented cause, so the graph answers "
                "with no search."), verdict
        why = f" ({verdict.note})" if verdict.note else ""
        return 4, [GRAPH, RESEARCH], TOP_UP, (
            f"Route table row 4: no code was confirmed, the graph has verified edges for {name}, "
            f"and the classifier says {verdict.verdict}{why}, so the graph is topped up with a short "
            "research pass."), verdict
    return 5, [RESEARCH], MAIN, (
        f"Route table row 5: the graph has nothing verified for {name} or its family, so research "
        "runs with the main limits."), None


def _documented(cov: Coverage) -> list[dict[str, Any]]:
    """The documented causes the classifier sees: each verified code edge's code and evidence."""
    return [{"code": edge_code(e), "evidence": e["evidence"]} for e in cov.model_edges]


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
