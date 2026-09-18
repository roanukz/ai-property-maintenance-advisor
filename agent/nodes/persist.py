"""persist: record a finished run (PLAN 8.3, 8.11; decisions 37 and 45).

A graph node (render -> persist -> END). Everything it writes is keyed by the
thread ID, so writing a thread twice changes nothing:

- the lookup row in the registry (the registry's record of the lookup);
- the run record (the RunResult wrapper of PLAN 7.3) as JSON in `<data>/runs/`;
- knowledge graph edges from a validated ok brief.

Edges. Each candidate with a code, an evidence quote and a source cited from
this run's search or fetch results is a HAS_CODE edge candidate to the code,
from the model's family when the graph holds a verified IN_FAMILY edge for the
model (the ErrorCode key is `code:{maker}:{family or model}:{code}`, PLAN
8.11), else from the confirmed model. It is added only after `verify_span`
passes against the cited source's raw page text (section 8.11 conditions 1 to
5, with the code as printed in the quote as the target) and the code stands as
its own token where the quote sits in the page; a candidate that fails is
dropped, logged, counted in the run record and in the graph's drop log, and
never stored with a flag. A source loaded from the graph is not added again,
so an edge keeps the run that first validated it as its `brief_run_id`, and an
edge already in the graph is left as it is.

Where edges go (decision 37): in replay, the RunContext graph path (a temp file
in tests, config.REPLAY_GRAPH_PATH from the CLI), and never config.GRAPH_PATH
or anything under config.SEED_DIR; in cheap or full, config.GRAPH_PATH, only
when the run has live ledger charges and only when the RunContext graph path
is config.GRAPH_PATH, so a run never reads one graph and writes another.
Registry derived edges (INSTALLED_AT, IS_MODEL) are built at load time and
never written here (decision 45).

Phase 4 adds the other four document edge kinds, each under the same rules as
HAS_CODE: the entry must cite a source this run retrieved (search or fetch,
never a graph source), carry an evidence quote, and that quote must pass the
full span check against the cited page with the edge's target as printed.
The brief has no structured family or part number field, so those targets are
read by code from the verified quote itself, with narrow patterns, and a
quote that matches no pattern proposes no edge (it is not a drop):

- SUPERSEDED_BY: each kept upgrade option with reason "discontinued", from
  the model to the successor (the successor's maker is the option's, else
  the unit's); the target is the successor model as the option prints it.
- PART_DISCONTINUED: each kept upgrade option with reason "parts_unavailable"
  whose quote says "<part> is discontinued" (or "has been discontinued", "is
  no longer available", "is obsolete"), from the model to that part; a token
  naming the model or the successor is never taken as a part.
- CODE_POINTS_TO_PART: each candidate whose code has a HAS_CODE edge in the
  graph (written now or before) and whose quote names a part after "part",
  "part number", "part no." or "P/N", from the code to that part.
- IN_FAMILY: any kept candidate, upgrade or maintenance quote that says
  "<model> is part of the <Family>" (or "belongs to", "is a member of", "is
  one of", "is in"), from the model to that family. The family is added after
  the HAS_CODE edges of the same run, so this run's codes keep the key they
  were checked under.

A node whose printed form differs from the target in the quote is never
reprinted (an older edge's evidence would stop matching it); the edge is
dropped with reason "printed_form_conflict".
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent import config
from agent.kg import (
    CODE_POINTS_TO_PART,
    HAS_CODE,
    IN_FAMILY,
    PART_DISCONTINUED,
    PRINTED,
    SUPERSEDED_BY,
    EdgeRejected,
    KnowledgeGraph,
    _edge_id,
    append_drops,
    code_key,
    family_key,
    model_key,
    node_type,
    part_key,
)
from agent.ledger import Ledger
from agent.nodes.validate import page_texts_for
from agent.registry import Registry
from agent.rules.evidence import (
    TARGET_MISSING,
    TARGET_NOT_TOKEN,
    anchor_to_target,
    evidence_sha256,
    normalize_for_span,
    target_is_token,
    verify_span,
)
from agent.rules.grounding import code_forms
from agent.state import AdvisorState, RunContext

log = logging.getLogger(__name__)

NODE = "persist"
# The span check passed once code widened the quote to start at the code.
OK_WIDENED = "verified_widened_to_code"
RUNS_DIRNAME = "runs"
# Trail statuses that never reached Tavily: a blocked call, or one the ledger
# refused before the request. The eval records count with the same tuple.
NOT_SENT = config.TRAIL_NOT_REACHED_STATUSES
# Sources whose text this run retrieved; a "graph" source is already in the graph.
RETRIEVED_ORIGINS = ("search", "extract")

# Why no edge was written at all (the run record's graph_edges.skipped).
SKIP_REPLAY_ON_LIVE_GRAPH = "replay never writes the runtime graph (decision 37)"
SKIP_REPLAY_ON_SEED = "replay never writes under seed/ (decision 37)"
SKIP_LIVE_PATH_MISMATCH = "a live run writes only config.GRAPH_PATH, and this run reads another graph"
SKIP_NO_LIVE_CHARGES = "no live ledger charges for this run (decision 37)"
SKIP_NOT_OK = "the brief is not an ok brief"
SKIP_NO_IDENTITY = "no confirmed manufacturer and model"
# Why one edge was dropped (besides the evidence.py span reasons).
DROP_NO_EVIDENCE = "no_evidence_quote"
DROP_NO_SOURCE = "cited_source_not_retrieved_this_run"
DROP_NO_RETRIEVED_AT = "source_without_retrieved_at"
DROP_PRINTED_CONFLICT = "printed_form_conflict"
DROP_NO_CODE_NODE = "code_not_in_graph"

# Phase 4 target patterns, read only from a quote that then passes the span check.
_PART_TOKEN = r"[A-Z0-9][A-Z0-9-]*\d[A-Z0-9-]*"
PART_DISCONTINUED_RE = re.compile(
    rf"(?P<part>{_PART_TOKEN})\s+(?:is|are|has been|have been)\s+(?:now\s+)?"
    r"(?:discontinued|no longer available|obsolete)\b")
PART_NUMBER_RE = re.compile(rf"(?:\b(?i:part number|part no\.|part|p/n))\s*[:#]?\s*(?P<part>{_PART_TOKEN})\b")
FAMILY_PHRASE = r"\s+(?:is part of|belongs to|is a member of|is one of|is in)\s+the\s+"
FAMILY_NAME = r"(?P<family>[A-Z0-9][\w-]*(?:\s[A-Z0-9][\w-]*){0,3})"


def runs_dir(ctx: RunContext) -> Path:
    """Run records sit beside pages/ and lookups/ in the run's data folder."""
    return Path(ctx.pages_dir).parent / RUNS_DIRNAME


def run_record_path(ctx: RunContext, run_id: str) -> Path:
    return runs_dir(ctx) / f"{run_id}.json"


def _ledger(ctx: RunContext) -> Ledger | None:
    path = Path(ctx.ledger_path)
    return Ledger(path) if path.exists() else None


def _usage(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    usage: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["kind"] != "charge" or row["provider"] != "anthropic":
            continue
        entry = usage.setdefault(row["node"] or "", {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        entry["calls"] += 1
        entry["input_tokens"] += int(row["input_tokens"] or 0)
        entry["output_tokens"] += int(row["output_tokens"] or 0)
    return usage


def _count(trail: list[dict[str, Any]], tool: str) -> int:
    return sum(1 for e in trail if e.get("tool") == tool and e.get("status") not in NOT_SENT)


# ---------------------------------------------------------------------------
# Graph edges
# ---------------------------------------------------------------------------


def has_live_charges(ctx: RunContext) -> bool:
    """True when the ledger holds a live mode charge for this run."""
    ledger = _ledger(ctx)
    if ledger is None:
        return False
    return any(r["kind"] == "charge" and r["mode"] in config.LIVE_MODES for r in ledger.rows(ctx.run_id))


def graph_write_path(ctx: RunContext) -> tuple[Path | None, str | None]:
    """(where this run's edges go, or None; why none) under decision 37."""
    path = Path(ctx.graph_path)
    if ctx.mode == "replay":
        if path.resolve() == config.GRAPH_PATH.resolve():
            return None, SKIP_REPLAY_ON_LIVE_GRAPH
        if path.resolve().is_relative_to(config.SEED_DIR.resolve()):
            return None, SKIP_REPLAY_ON_SEED
        return path, None
    if ctx.mode in config.LIVE_MODES and has_live_charges(ctx):
        if path.resolve() != config.GRAPH_PATH.resolve():
            return None, SKIP_LIVE_PATH_MISMATCH
        return config.GRAPH_PATH, None
    return None, SKIP_NO_LIVE_CHARGES


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _printed_target(code: str, evidence: str, page: str | None,
                    observed: str | None) -> tuple[str | None, str, str]:
    """(the code as printed, the reason, the evidence to store) for a quote that passes
    the full span check with the code standing as its own token in the page, or
    (None, why not, evidence).

    A quote that is verbatim in the page but leaves out the code is widened by
    code to start at the code when the page prints it just before the quote on
    the same line (evidence.anchor_to_target; Phase 5 finding). The stored
    evidence is then that widened span, which is still verbatim page text.
    """
    forms = code_forms(code, observed)
    reason = "no_target"
    for form in forms:
        result = verify_span(evidence, page, target=form)
        if result.ok:
            if target_is_token(evidence, page, target=form):
                return form, result.reason, evidence
            reason = TARGET_NOT_TOKEN
            continue
        if result.reason == TARGET_MISSING:
            widened = anchor_to_target(evidence, page, target=form)
            if widened is not None:
                return form, OK_WIDENED, widened
        if reason == "no_target" or result.reason != "target_not_in_span":
            reason = result.reason
    return None, reason, evidence


def edge_candidates(state: AdvisorState, ctx: RunContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(edges that passed the span check, dropped candidates with their reason) for an ok brief."""
    brief = state.get("brief") or {}
    identity = state.get("identity") or {}
    maker, model = identity.get("manufacturer"), identity.get("model")
    registry = {s.get("url"): s for s in state.get("sources") or []}
    texts = page_texts_for(list(registry.values()), ctx)
    run_id = state.get("run_id") or ctx.run_id
    passed: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    brief_sources = brief.get("sources") or []
    for cand in brief.get("candidates") or []:
        code = cand.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        index = cand.get("source_index")
        cited = brief_sources[index] if isinstance(index, int) and 0 <= index < len(brief_sources) else {}
        url = cited.get("url")
        found = registry.get(url) or {}
        if found.get("origin") == "graph":
            continue  # already an edge; its first validated run stays its brief_run_id
        drop = {"kind": HAS_CODE, "code": code, "source_url": url, "run_id": run_id}
        if found.get("origin") not in RETRIEVED_ORIGINS:
            dropped.append({**drop, "reason": DROP_NO_SOURCE})
            continue
        evidence = cand.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            dropped.append({**drop, "reason": DROP_NO_EVIDENCE})
            continue
        if not found.get("retrieved_at"):
            dropped.append({**drop, "reason": DROP_NO_RETRIEVED_AT})
            continue
        target, reason, evidence = _printed_target(code, evidence, texts.get(url), brief.get("observed_code"))
        if target is None:
            dropped.append({**drop, "reason": reason})
            continue
        passed.append({
            "maker": maker, "model": model, "code": target, "page_text": texts[url],
            "source": {"url": url, "host": cited.get("host") or found.get("host") or "",
                       "title": cited.get("title"), "retrieved_at": found.get("retrieved_at"),
                       "text_sha256": found.get("text_sha256"), "tier": cited.get("tier") or "forum"},
            "fields": {"source_url": url, "retrieved_at": found["retrieved_at"],
                       "evidence": evidence, "evidence_sha256": evidence_sha256(evidence),
                       "brief_run_id": run_id, "validated_at": _now(),
                       "text_sha256": found.get("text_sha256") or None},
        })
    return passed, dropped


# ---------------------------------------------------------------------------
# Phase 4 edge kinds
# ---------------------------------------------------------------------------


def _retrieved(state: AdvisorState, brief: dict[str, Any], entry: dict[str, Any],
               registry: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any], dict[str, Any], str | None]:
    """(url, brief source, registry source, drop reason or None) for an entry's cited source.

    The reason is "graph" for a graph source (already an edge, skipped, not a drop).
    """
    sources = brief.get("sources") or []
    index = entry.get("source_index")
    cited = sources[index] if isinstance(index, int) and 0 <= index < len(sources) else {}
    url = cited.get("url")
    found = registry.get(url) or {}
    if found.get("origin") == "graph":
        return url, cited, found, "graph"
    if found.get("origin") not in RETRIEVED_ORIGINS:
        return url, cited, found, DROP_NO_SOURCE
    evidence = entry.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        return url, cited, found, DROP_NO_EVIDENCE
    if not found.get("retrieved_at"):
        return url, cited, found, DROP_NO_RETRIEVED_AT
    return url, cited, found, None


def _proposal(kind: str, src: tuple, dst: tuple, target: str, url: str, cited: dict[str, Any],
              found: dict[str, Any], evidence: str, page: str, run_id: str) -> dict[str, Any]:
    return {
        "kind": kind, "src": src, "dst": dst, "target": target, "page_text": page,
        "source": {"url": url, "host": cited.get("host") or found.get("host") or "",
                   "title": cited.get("title"), "retrieved_at": found.get("retrieved_at"),
                   "text_sha256": found.get("text_sha256"), "tier": cited.get("tier") or "forum"},
        "fields": {"source_url": url, "retrieved_at": found["retrieved_at"], "evidence": evidence,
                   "evidence_sha256": evidence_sha256(evidence), "brief_run_id": run_id,
                   "validated_at": _now(), "text_sha256": found.get("text_sha256") or None},
    }


def _not_part(token: str, names: list[str]) -> bool:
    return any(token.upper() in n.upper().split() or token.upper() == n.upper() for n in names if n)


def document_edge_candidates(state: AdvisorState, ctx: RunContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(SUPERSEDED_BY, PART_DISCONTINUED, CODE_POINTS_TO_PART and IN_FAMILY proposals
    whose quote passed the span check, dropped proposals with their reason)."""
    brief = state.get("brief") or {}
    identity = state.get("identity") or {}
    maker, model = identity.get("manufacturer"), identity.get("model")
    registry = {s.get("url"): s for s in state.get("sources") or []}
    texts = page_texts_for(list(registry.values()), ctx)
    run_id = state.get("run_id") or ctx.run_id
    passed: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    def propose(kind: str, src: tuple, dst: tuple, target: str, entry: dict[str, Any]) -> None:
        url, cited, found, why = _retrieved(state, brief, entry, registry)
        if why == "graph":
            return
        drop = {"kind": kind, "target": target, "source_url": url, "run_id": run_id}
        if why is None:
            check = verify_span(entry["evidence"], texts.get(url), target=target)
            why = None if check.ok else check.reason
            # As for HAS_CODE: the target must stand as its own token in the page.
            if why is None and not target_is_token(entry["evidence"], texts[url], target=target):
                why = TARGET_NOT_TOKEN
        if why is not None:
            dropped.append({**drop, "reason": why})
            return
        passed.append(_proposal(kind, src, dst, target, url, cited, found, entry["evidence"],
                                texts[url], run_id))

    model_node = ("model", maker, model)
    for option in brief.get("upgrade_options") or []:
        successor = option.get("successor_model")
        if not isinstance(successor, str) or not successor.strip():
            continue
        if option.get("reason") == "discontinued":
            propose(SUPERSEDED_BY, model_node, ("model", option.get("successor_manufacturer") or maker, successor),
                    successor, option)
        elif option.get("reason") == "parts_unavailable":
            quote = normalize_for_span(option.get("evidence") or "")
            for m in PART_DISCONTINUED_RE.finditer(quote):
                part = m.group("part")
                if not _not_part(part, [model, successor]):
                    propose(PART_DISCONTINUED, model_node, ("part", maker, part), part, option)
    for cand in brief.get("candidates") or []:
        code = cand.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        for m in PART_NUMBER_RE.finditer(normalize_for_span(cand.get("evidence") or "")):
            part = m.group("part")
            if not _not_part(part, [model, code]):
                propose(CODE_POINTS_TO_PART, ("code", maker, code), ("part", maker, part), part, cand)
    if isinstance(model, str) and model.strip():
        family_re = re.compile(re.escape(normalize_for_span(model)) + FAMILY_PHRASE + FAMILY_NAME)
        seen: set[tuple[str, str | None]] = set()
        for key in ("candidates", "upgrade_options", "maintenance_due"):
            for entry in brief.get(key) or []:
                m = family_re.search(normalize_for_span(entry.get("evidence") or ""))
                if m is None or (m.group("family"), entry.get("evidence")) in seen:
                    continue
                seen.add((m.group("family"), entry.get("evidence")))
                propose(IN_FAMILY, model_node, ("family", maker, m.group("family")), m.group("family"), entry)
    return passed, dropped


def _has_code_under_model(kg: KnowledgeGraph, maker: str, model: str, code: str, fields: dict[str, Any]) -> bool:
    """True when the same HAS_CODE edge (URL and evidence) already sits on the model's own code key."""
    src, dst = model_key(maker, model), code_key(maker, model, code)
    return src in kg.g and dst in kg.g and kg.g.has_edge(
        src, dst, key=_edge_id(HAS_CODE, fields["source_url"], fields["evidence_sha256"]))


def _node_key(spec: tuple) -> str:
    """The key of a model, family or part endpoint (code endpoints go through _code_node)."""
    kind, maker, name = spec
    keys = {"model": model_key, "family": family_key, "part": part_key}
    return keys[kind](maker, name)


def _code_node(kg: KnowledgeGraph, maker: str, model: str, code: str) -> str | None:
    """The HAS_CODE target node for this code on the model or its family, when the graph has one."""
    for scope in (kg.family_of(maker, model), model):
        if scope:
            key = code_key(maker, scope, code)
            if key in kg.g and not kg.g.nodes[key].get("derived"):
                return key
    return None


def _ensure_node(kg: KnowledgeGraph, spec: tuple, target_key: str) -> None:
    kind, maker, name = spec
    if target_key in kg.g and not kg.g.nodes[target_key].get("derived"):
        return
    if kind == "model":
        kg.add_model(maker, name)
    elif kind == "family":
        kg.add_family(maker, name)
    elif kind == "part":
        kg.add_part(maker, name)


def write_document_edges(kg: KnowledgeGraph, proposals: list[dict[str, Any]], identity: dict[str, Any],
                         report: dict[str, Any], dropped: list[dict[str, Any]]) -> None:
    """Add each proposal's nodes and edge to `kg`; count them in `report`, drops in `dropped`."""
    for prop in proposals:
        fields = prop["fields"]
        drop = {"kind": prop["kind"], "target": prop["target"], "source_url": fields["source_url"],
                "run_id": fields["brief_run_id"]}
        if prop["src"][0] == "code":
            src = _code_node(kg, prop["src"][1], identity.get("model"), prop["src"][2])
            if src is None:
                dropped.append({**drop, "reason": DROP_NO_CODE_NODE})
                continue
        else:
            src = _node_key(prop["src"])
        dst = _node_key(prop["dst"])
        node = kg.g.nodes[dst] if dst in kg.g else {}
        printed = None if node.get("derived") else node.get(PRINTED[node_type(dst)])
        if printed is not None and printed != prop["target"]:
            dropped.append({**drop, "reason": DROP_PRINTED_CONFLICT})
            continue
        if src in kg.g and dst in kg.g and kg.g.has_edge(
                src, dst, key=_edge_id(prop["kind"], fields["source_url"], fields["evidence_sha256"])):
            report["already_present"] += 1
            continue
        if prop["src"][0] != "code":
            _ensure_node(kg, prop["src"], src)
        _ensure_node(kg, prop["dst"], dst)
        kg.add_source(**prop["source"])
        try:
            # add_edge runs the full span check again, against the same page text.
            kg.add_edge(prop["kind"], src, dst, page_text=prop["page_text"], **fields)
        except EdgeRejected as exc:
            dropped.append({**drop, "reason": exc.reason})
            continue
        report["written"] += 1
        report.setdefault("written_by_kind", {})
        report["written_by_kind"][prop["kind"]] = report["written_by_kind"].get(prop["kind"], 0) + 1


def _log_drops(target: Path, thread_id: str, dropped: list[dict[str, Any]]) -> None:
    """Append each drop to the graph's drop log once per thread (idempotent)."""
    append_drops(target, [{**drop, "thread_id": thread_id} for drop in dropped])


def persist_edges(state: AdvisorState, ctx: RunContext, thread_id: str) -> dict[str, Any]:
    """Write the validated brief's verified edges where decision 37 allows; returns the report."""
    target, skipped = graph_write_path(ctx)
    report: dict[str, Any] = {"target": str(target) if target else None, "skipped": skipped,
                              "written": 0, "already_present": 0, "dropped": []}
    if target is None:
        return report
    brief = state.get("brief") or {}
    if state.get("status") != "ok" or brief.get("status") != "ok":
        report["skipped"] = SKIP_NOT_OK
        return report
    identity = state.get("identity") or {}
    if not identity.get("manufacturer") or not identity.get("model"):
        report["skipped"] = SKIP_NO_IDENTITY
        return report
    passed, dropped = edge_candidates(state, ctx)
    kg = KnowledgeGraph.load(target)
    for edge in passed:
        maker = edge["maker"]
        family = kg.family_of(maker, edge["model"])
        scope = family or edge["model"]
        src_key = family_key(maker, family) if family else model_key(maker, edge["model"])
        c_key = code_key(maker, scope, edge["code"])
        fields = edge["fields"]
        if src_key in kg.g and c_key in kg.g and kg.g.has_edge(
                src_key, c_key, key=_edge_id(HAS_CODE, fields["source_url"], fields["evidence_sha256"])):
            report["already_present"] += 1
            continue
        if _has_code_under_model(kg, maker, edge["model"], edge["code"], fields):
            # Written under the model before this run's IN_FAMILY edge named the family.
            report["already_present"] += 1
            continue
        if not family:
            kg.add_model(maker, edge["model"])
        kg.add_code(maker, scope, edge["code"])
        kg.add_source(**edge["source"])
        # add_edge runs the full span check again, against the same page text.
        kg.add_edge(HAS_CODE, src_key, c_key, page_text=edge["page_text"], **fields)
        report["written"] += 1
    more, more_dropped = document_edge_candidates(state, ctx)
    dropped.extend(more_dropped)
    write_document_edges(kg, more, identity, report, dropped)
    for drop in dropped:
        log.info("edge dropped: %s %s from %s: %s", drop["kind"], drop.get("code") or drop.get("target"),
                 drop["source_url"], drop["reason"])
    if report["written"]:
        kg.save(target)
    _log_drops(target, thread_id, dropped)
    report["dropped"] = dropped
    return report


# ---------------------------------------------------------------------------
# Run record and lookup row
# ---------------------------------------------------------------------------


def run_record(state: AdvisorState, ctx: RunContext, thread_id: str) -> dict[str, Any]:
    """The RunResult wrapper (PLAN 7.3), JSON native."""
    run_id = state.get("run_id") or ctx.run_id
    ledger = _ledger(ctx)
    rows = ledger.rows(run_id) if ledger is not None else []
    brief = state.get("brief")
    sources = (brief or {}).get("sources") or []
    trail = list(state.get("search_trail") or [])
    latency = dict(state.get("latency") or {})
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "mode": state.get("mode") or ctx.mode,
        "status": state.get("status"),
        "refusal_origin": state.get("refusal_origin"),
        "stop_reason": state.get("stop_reason"),
        "route": list(state.get("route") or []),
        "route_reason": state.get("route_reason"),
        "research_limits": state.get("research_limits"),
        "grounding_status": state.get("grounding_status"),
        "grounding": list(state.get("grounding") or []),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "all_forum": bool(sources) and all(s.get("tier") == "forum" for s in sources),
        "cost_usd": ledger.run_total(run_id) if ledger is not None else float(state.get("cost_usd") or 0.0),
        "tavily_credits": ledger.run_credits(run_id) if ledger is not None else int(state.get("tavily_credits") or 0),
        "searches": _count(trail, "search"),
        "fetches": _count(trail, "fetch"),
        "latency_s": sum(float(v) for v in latency.values()),
        "latency": latency,
        "usage": _usage(rows),
        "html_path": state.get("html_path"),
        "brief": brief,
    }


def _upsert_lookup(ctx: RunContext, state: AdvisorState, record: dict[str, Any]) -> None:
    registry = Registry(ctx.registry_path)
    route = ",".join(record["route"]) or None
    existing = [r for r in registry.lookups(run_id=record["run_id"]) if r["thread_id"] == record["thread_id"]]
    if not existing:
        registry.record_lookup(
            record["run_id"], status=record["status"] or "running", thread_id=record["thread_id"],
            appliance_id=state.get("appliance_id"), route=route, cost_usd=record["cost_usd"],
        )
        return
    # Registry has no update method yet; its own connection keeps the busy
    # timeout and foreign keys it sets.
    with registry._connect() as conn:
        conn.execute(
            "UPDATE lookups SET status = ?, route = ?, cost_usd = ? WHERE id = ?",
            (record["status"] or "running", route, float(record["cost_usd"]), existing[0]["id"]),
        )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def persist_run(state: AdvisorState, ctx: RunContext, thread_id: str, *,
                node_started: float | None = None) -> dict[str, Any]:
    """Write edges, upsert the lookup row and write the run record; returns the record.

    With `node_started` (the persist node's start time), the node's own
    latency entry, measured up to the record write, is in the record, so the
    record and the final state hold the same latency.
    """
    edges = persist_edges(state, ctx, thread_id)
    latency = dict(state.get("latency") or {})
    if node_started is not None:
        latency[NODE] = time.perf_counter() - node_started
    record = run_record({**state, "latency": latency}, ctx, thread_id)
    record["graph_edges"] = edges
    _upsert_lookup(ctx, state, record)
    _write_json(run_record_path(ctx, record["run_id"]), record)
    return record


def _thread_id(ctx: RunContext) -> str:
    from langgraph.config import get_config

    return (get_config().get("configurable") or {}).get("thread_id") or ctx.run_id


def persist(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """The persist node: the last node before END (render -> persist -> END)."""
    started = time.perf_counter()
    ctx = runtime.context
    record = persist_run(state, ctx, _thread_id(ctx), node_started=started)
    report = {"run_record": str(run_record_path(ctx, record["run_id"])), "graph_edges": record["graph_edges"]}
    return {"latency": {NODE: record["latency"][NODE]}, "persist_report": report}
