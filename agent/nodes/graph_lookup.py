"""graph_lookup: validated knowledge graph edges and their sources (PLAN 8.3, 8.11; SC7a).

Code only. Opens the KnowledgeGraph at the RunContext graph path (a temp file
in tests, config.REPLAY_GRAPH_PATH from the CLI in replay, config.GRAPH_PATH
live; decision 37) and loads, for the confirmed identity:

- with a confirmed code: the edges for that code on the model or its family
  (HAS_CODE, and the code's CODE_POINTS_TO_PART edges);
- with no confirmed code: those edges for every code the model and family have;
- in both cases: the model's SUPERSEDED_BY edges (upgrade_check reads them
  from graph_hits in Phase 4).

The graph's query methods return only edges whose evidence is present,
matches its evidence_sha256 and passes the span shape check, so an edge
without evidence is never coverage and never a source. Each edge's source is
appended to `sources` with origin "graph" and the edge's own retrieved_at and
page hash (decision 41; the Source node's hash only when the edge has none),
and the edge itself to graph_hits. A damaged saved edge is dropped and logged
at load (KnowledgeGraph.load), so it never fails the run.

The coverage helpers here are shared with route, so route and graph_lookup can
never disagree about what the graph knows.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent import config
from agent.kg import KnowledgeGraph
from agent.rules.tiers import host_of
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "graph_lookup"
ORIGIN = "graph"


# ---------------------------------------------------------------------------
# Opening the graph and reading coverage (shared with route)
# ---------------------------------------------------------------------------


def open_knowledge_graph(path: Path | str | None) -> KnowledgeGraph:
    """The KnowledgeGraph at `path`; a missing file is an empty graph."""
    return KnowledgeGraph.load(path)


def _identity_key(identity: dict[str, Any] | None) -> tuple[str | None, str | None]:
    identity = identity or {}
    maker, model = identity.get("manufacturer"), identity.get("model")
    maker = maker.strip() if isinstance(maker, str) and maker.strip() else None
    model = model.strip() if isinstance(model, str) and model.strip() else None
    return maker, model


def _edge_id(edge: dict[str, Any]) -> tuple:
    return (edge.get("kind"), edge.get("src"), edge.get("dst"), edge.get("source_url"),
            edge.get("evidence_sha256"))


def _unique(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for edge in edges:
        if _edge_id(edge) in seen:
            continue
        seen.add(_edge_id(edge))
        out.append(edge)
    return out


@dataclass
class Coverage:
    """What the graph holds, verified, for one identity and confirmed code."""

    maker: str | None = None
    model: str | None = None
    family: str | None = None
    model_known: bool = False  # the graph's has_model: any verified document edge of the model
    code_edges: list[dict[str, Any]] = field(default_factory=list)  # for the confirmed code
    model_edges: list[dict[str, Any]] = field(default_factory=list)  # every code's edges, model or family
    upgrade_edges: list[dict[str, Any]] = field(default_factory=list)  # SUPERSEDED_BY

    @property
    def has_model(self) -> bool:
        return self.model_known or bool(self.model_edges)


def graph_coverage(kg: KnowledgeGraph, identity: dict[str, Any] | None, code: str | None) -> Coverage:
    """Verified edges for the model, its family and the confirmed code."""
    maker, model = _identity_key(identity)
    try:
        model_edges: list[dict[str, Any]] = []
        for each in kg.codes_for(maker, model):
            model_edges += kg.edges_for_code(maker, model, each)
        return Coverage(
            maker=maker,
            model=model,
            family=kg.family_of(maker, model),
            model_known=kg.has_model(maker, model),
            code_edges=_unique(kg.edges_for_code(maker, model, code)) if code else [],
            model_edges=_unique(model_edges),
            upgrade_edges=_unique(kg.superseded_by(maker, model)),
        )
    except ValueError:
        # The graph's key helpers refuse a missing maker or model, or one that
        # normalizes to nothing: no key, so no coverage.
        return Coverage(maker, model)


def edge_code(edge: dict[str, Any]) -> str | None:
    """The code an edge documents, as printed, when it names one."""
    code = edge.get("code")
    return code if isinstance(code, str) and code else None


# ---------------------------------------------------------------------------
# Sources and hits
# ---------------------------------------------------------------------------


def source_id(url: str) -> str:
    """The same id research gives a URL, so a URL has one id whatever found it."""
    return "src-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _source_attrs(kg: KnowledgeGraph, edge: dict[str, Any]) -> dict[str, Any]:
    """The Source node's attributes for the edge's URL, or {} when the graph has none."""
    found = edge.get("source")
    if not isinstance(found, dict):
        found = kg.source_for(edge["source_url"])
    return found if isinstance(found, dict) else {}


def _json_native(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def graph_sources(kg: KnowledgeGraph, edges: list[dict[str, Any]], known_urls: set[str]) -> list[dict[str, Any]]:
    """One source per edge URL not already in state, first appearance first."""
    by_url: dict[str, dict[str, Any]] = {}
    for edge in edges:
        url = edge["source_url"]
        if url in known_urls:
            continue
        if url in by_url:
            if edge["evidence"] not in by_url[url]["excerpts"]:
                by_url[url]["excerpts"].append(edge["evidence"])
            continue
        attrs = _source_attrs(kg, edge)
        by_url[url] = {
            "source_id": source_id(url),
            "url": url,
            "host": attrs.get("host") or host_of(url),
            "title": attrs.get("title") or None,
            # The edge's own retrieval time and page hash, never this run's and
            # never a later fetch of the same URL (decision 41).
            "retrieved_at": edge["retrieved_at"],
            "origin": ORIGIN,
            "text_sha256": edge.get("text_sha256") or attrs.get("text_sha256") or None,
            "snippet": None,
            # The verified evidence sentences are the verbatim text synthesize sees.
            "excerpts": [edge["evidence"]],
        }
    return list(by_url.values())


def lookup_edges(coverage: Coverage, code: str | None) -> list[dict[str, Any]]:
    """The edges graph_lookup loads: the code's edges (or every code edge), then upgrades."""
    edges = coverage.code_edges if code else coverage.model_edges
    return _unique([*edges, *coverage.upgrade_edges])


def graph_lookup(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Append the graph's verified sources and edges for this identity and code."""
    started = time.perf_counter()
    kg = open_knowledge_graph(runtime.context.graph_path)
    code = state.get("observed_code")
    coverage = graph_coverage(kg, state.get("identity"), code)
    edges = lookup_edges(coverage, code)
    known = {s.get("url") for s in state.get("sources") or []}
    sources = graph_sources(kg, edges, known)
    # The Source node goes to `sources`; each hit keeps only its source_id.
    hits = [{**_json_native({k: v for k, v in edge.items() if k != "source"}),
             "source_id": source_id(edge["source_url"])} for edge in edges]
    return {
        "sources": _json_native(sources),
        "graph_hits": hits,
        **latency_entry(state, NODE, time.perf_counter() - started),
    }
