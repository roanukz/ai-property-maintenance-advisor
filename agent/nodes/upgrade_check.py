"""upgrade_check: graph derived upgrade options for an ok brief (PLAN 8.3, 8.4; decision 27; SC9).

Code only, and it enforces nothing. After validate passes an `ok` brief,
upgrade_check:

1. opens the knowledge graph at the RunContext graph path and reads, for the
   confirmed identity, the verified SUPERSEDED_BY edges and PART_DISCONTINUED
   edges of the model (and of its family, for part edges);
2. turns each into a candidate upgrade option in the model facing draft shape
   (`DraftUpgrade`): a SUPERSEDED_BY edge gives reason "discontinued" and the
   successor as the edge prints it; a PART_DISCONTINUED edge gives reason
   "parts_unavailable" for each successor the model has a SUPERSEDED_BY edge
   to, but only when the part edge's own evidence sentence names that
   successor, since rule 9 needs the successor inside the quote. The quote is
   the edge's verified evidence sentence and the summary is code written;
3. skips a candidate the validated brief already carries (same successor,
   normalized, and same reason), so a successor the model cited is not
   listed twice;
4. registers each candidate's graph source in `sources` (origin "graph", the
   edge's own `retrieved_at` and page hash, decision 41) when the run has not
   registered that URL yet, points the candidate's `source_index` at the
   source's place in the run's registry, and proposes the graph Source node's
   stored tier (the host ceiling still applies) only for a source no model
   text relies on: one it registered itself, or an already registered graph
   source that nothing in the draft cites. A source the model cites keeps
   whatever tier proposal the model made (or none), so this pass cannot
   change the tier, and so the outcome, of a model cited entry: a
   maintenance entry dropped on the first pass for its tier stays dropped;
5. appends the candidates to the draft and sends it back through validate
   once (`Command(goto="validate")`). With nothing to add it goes straight to
   render, since a second pass over an unchanged draft would change nothing.

The node runs at most once per thread. Whether it has run is read from its own
latency entry (`upgrade_pass_done`): validate uses it to label its second pass
"upgrade", and the routing after validate uses it so the pass happens once.
Reading the latency entry keeps AdvisorState's key set unchanged.

Every check (index, verbatim quote with the successor inside it, prices,
clearing, pruning and renumbering) runs in validate on that second pass,
exactly as for options the model proposed. On any status but `ok` the node
adds nothing and goes to render; the routing never sends a refusal or a
budget stop here in the first place (section 8.4).
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.kg import KnowledgeGraph, model_key, norm_model
from agent.nodes.graph_lookup import _identity_key, _json_native, graph_sources, open_knowledge_graph
from agent.rules.evidence import normalize_for_span
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "upgrade_check"
DISCONTINUED = "discontinued"
PARTS_UNAVAILABLE = "parts_unavailable"


def upgrade_pass_done(state: AdvisorState) -> bool:
    """True once upgrade_check has run in this thread (its latency entry is there)."""
    return NODE in (state.get("latency") or {})


def _successor_key(model: Any) -> str | None:
    try:
        return norm_model(model) if isinstance(model, str) else None
    except ValueError:
        return None


def superseded_edges(kg: KnowledgeGraph, maker: str, model: str) -> list[dict[str, Any]]:
    """Verified SUPERSEDED_BY edges of the model, each with the successor's maker and model."""
    return kg.superseded_by(maker, model)


def part_edges(kg: KnowledgeGraph, maker: str, model: str) -> list[dict[str, Any]]:
    """Verified PART_DISCONTINUED edges from the model or its family."""
    return kg.parts_discontinued(maker, model)


def _summary(reason: str, model: str, successor: str, part: str | None = None) -> str:
    if reason == DISCONTINUED:
        return f"The cited source names the {successor} as the successor to the {model}."
    return f"The cited source says part {part} for the {model} is discontinued and names the {successor}."


def collect_candidates(kg: KnowledgeGraph, identity: dict[str, Any] | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(edge, option without source_index) pairs from the graph, in a stable order."""
    maker, model = _identity_key(identity)
    if not maker or not model:
        return []
    try:
        model_key(maker, model)
        supers = superseded_edges(kg, maker, model)
        parts = part_edges(kg, maker, model)
    except ValueError:
        return []
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for edge in supers:
        successor = edge.get("successor_model")
        if not isinstance(successor, str) or not successor.strip():
            continue
        out.append((edge, {"successor_manufacturer": edge.get("successor_manufacturer") or "",
                           "successor_model": successor, "reason": DISCONTINUED,
                           "summary": _summary(DISCONTINUED, model, successor), "evidence": edge["evidence"]}))
    successors = [e["successor_model"] for e in supers if isinstance(e.get("successor_model"), str)]
    for edge in parts:
        quote = normalize_for_span(edge.get("evidence") or "")
        for successor, succ_edge in zip(successors, supers, strict=True):
            if normalize_for_span(successor) not in quote:
                continue
            out.append((edge, {"successor_manufacturer": succ_edge.get("successor_manufacturer") or "",
                               "successor_model": successor, "reason": PARTS_UNAVAILABLE,
                               "summary": _summary(PARTS_UNAVAILABLE, model, successor, edge.get("target")),
                               "evidence": edge["evidence"]}))
    return out


def _present(options: list[dict[str, Any]]) -> set[tuple[str | None, str]]:
    return {(_successor_key(o.get("successor_model")), o.get("reason")) for o in options}


def _draft_cited(draft: Any) -> set[int]:
    """Every source_index the draft cites anywhere (lists, blocks and nested cautions)."""
    found: set[int] = set()

    def walk(value: Any, key: str | None) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k != "source_tiers":
                    walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif key == "source_index" and isinstance(value, int) and not isinstance(value, bool):
            found.add(value)

    walk(draft, None)
    return found


def plan_upgrades(kg: KnowledgeGraph, state: AdvisorState) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(draft options to add, new sources to register, draft source tier proposals to add)."""
    state_sources = list(state.get("sources") or [])
    registered = [s.get("url") for s in state_sources]
    cited = _draft_cited(state.get("draft"))
    brief = state.get("brief") or {}
    have = _present(list(brief.get("upgrade_options") or []))
    options: list[dict[str, Any]] = []
    new_sources: list[dict[str, Any]] = []
    tiers: list[dict[str, Any]] = []
    for edge, option in collect_candidates(kg, state.get("identity")):
        key = (_successor_key(option["successor_model"]), option["reason"])
        if key in have:
            continue
        have.add(key)
        url = edge["source_url"]
        known = registered + [s["url"] for s in new_sources]
        if url not in known:
            new_sources.extend(graph_sources(kg, [edge], set(known)))
            index = len(registered) + len(new_sources) - 1
        else:
            index = known.index(url)
        # Registered here, or a graph source no draft text cites: see step 4.
        own = index >= len(registered) or (
            state_sources[index].get("origin") == "graph" and index not in cited)
        stored = (edge.get("source") or kg.source_for(url) or {}).get("tier")
        if own and stored in ("manufacturer", "dealer", "forum"):
            tiers.append({"source_index": index, "tier": stored, "authorship_quote": ""})
        options.append({**option, "source_index": index})
    return options, new_sources, tiers


def upgrade_check(state: AdvisorState, runtime: Runtime[RunContext]) -> Command:
    """Add graph derived upgrade candidates to the draft and hand it back to validate."""
    started = time.perf_counter()
    out: dict[str, Any] = {}
    draft = state.get("draft")
    if state.get("status") == "ok" and isinstance(draft, dict):
        kg = open_knowledge_graph(runtime.context.graph_path)
        options, new_sources, tiers = plan_upgrades(kg, state)
        if options:
            proposed = {t.get("source_index") for t in draft.get("source_tiers") or []}
            out["draft"] = {
                **draft,
                "upgrade_options": [*(draft.get("upgrade_options") or []), *options],
                # A proposal the model made for a source stands; graph sources
                # the model never cited get the graph's stored tier, once each.
                "source_tiers": [*(draft.get("source_tiers") or []),
                                 *[t for i, t in enumerate(tiers) if t["source_index"] not in proposed
                                   and t["source_index"] not in {u["source_index"] for u in tiers[:i]}]],
            }
            out["sources"] = _json_native(new_sources)
    out.update(latency_entry(state, NODE, time.perf_counter() - started))
    return Command(update=out, goto="validate" if "draft" in out else "render")
