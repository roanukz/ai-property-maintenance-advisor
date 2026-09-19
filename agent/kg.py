"""The knowledge graph (PLAN 8.11; decisions 37 and 45).

A NetworkX MultiDiGraph of document derived facts, saved as JSON.

Node keys (every component normalized so two spellings of one thing share a key):

- Model and SuccessorModel: `model:{maker}:{model}`
- ModelFamily: `family:{maker}:{family}`
- ErrorCode: `code:{maker}:{family or model}:{code}`
- Part: `part:{maker}:{part_no}`
- Cause: `cause:{maker}:{family or model}:{sha256(normalized label)[:12]}`
- Source: `source:{sha256(url)[:12]}`

Normalization, per component:

- maker (`norm_maker`): NFKC, casefolded; symbols and punctuation (the ® of
  "SUNDANCE® SPAS") become spaces; trailing corporate suffixes ("Inc.",
  "LLC") dropped; words joined with "-". "Sundance Spas" gives "sundance-spas".
- model and family (`norm_model`): NFKC, casefolded, only letters and digits
  kept, so plate spellings "XR16 4TTR6036" and "XR16-4TTR6036" share
  "xr164ttr6036".
- code (`norm_code`): rule 3's code normalization (NFC, uppercase, surrounding
  whitespace and punctuation stripped), then inner whitespace runs to one
  space, so an observed code "flo" finds the edge for "FLO".
- part number (`norm_part`): NFKC, uppercase, whitespace removed; dashes kept.
- cause label (`norm_label`): NFC, casefolded, whitespace runs to one space,
  surrounding whitespace and punctuation stripped; the key holds the first
  12 hex digits of its sha256, since a label is a sentence.

Each node keeps its value as printed ("code", "part_no", "model", "family")
because an edge's evidence must contain its target as printed.

Persisted edges (IN_FAMILY, HAS_CODE, CODE_POINTS_TO_PART, SUPERSEDED_BY,
PART_DISCONTINUED, DOCUMENTED_CAUSE) each carry `source_url`, `retrieved_at`, `evidence`,
`evidence_sha256`, `brief_run_id` and `validated_at`; `add_edge` refuses an
edge missing any of them, whose hash does not match, or whose evidence fails
the section 8.11 conditions it can check (and all five when page text is
given). Property and Appliance nodes and INSTALLED_AT and IS_MODEL edges come
from the registry at load time and are never saved (decision 45).

DOCUMENTED_CAUSE (decision D2, 18 September 2026) joins a model or family to
a Cause node and also carries `label` (the candidate's documented_meaning,
the cause as the brief printed it) and `action` (its documented_action). Its
evidence passes conditions 1 to 4 like any edge; condition 5 is the cause
condition of agent/rules/evidence.py (the label's content words, or the whole
label, in the evidence), checked against the edge's own label, whose
normalized form must be the Cause node's.

An edge may also carry `text_sha256`, the hash of the page text its evidence
was verified against, so a later run that fetches a changed page at the same
URL cannot move an older edge onto the newer text (decision 41).

Loading is lenient by default: a saved edge that fails a check is dropped,
kept in `load_drops` with its reason, logged, and appended to the drop log
beside the graph file (PLAN 8.11: a failing edge is dropped and the drop
logged), so one damaged edge never fails a run. `strict=True` raises instead
(the integrity test uses it).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx

from agent.rules.evidence import (
    TARGET_NOT_TOKEN,
    SpanResult,
    check_cause_shape,
    check_span_shape,
    target_is_token,
    verify_cause_span,
    verify_span,
)
from agent.rules.evidence import evidence_sha256 as hash_evidence
from agent.rules.observed_code import normalize_code

log = logging.getLogger(__name__)

GRAPH_VERSION = 1

IN_FAMILY = "IN_FAMILY"
HAS_CODE = "HAS_CODE"
CODE_POINTS_TO_PART = "CODE_POINTS_TO_PART"
SUPERSEDED_BY = "SUPERSEDED_BY"
PART_DISCONTINUED = "PART_DISCONTINUED"
DOCUMENTED_CAUSE = "DOCUMENTED_CAUSE"
INSTALLED_AT = "INSTALLED_AT"
IS_MODEL = "IS_MODEL"

# Allowed (source node types, target node type) per persisted edge kind.
EDGE_KINDS: dict[str, tuple[tuple[str, ...], str]] = {
    IN_FAMILY: (("model",), "family"),
    HAS_CODE: (("model", "family"), "code"),
    CODE_POINTS_TO_PART: (("code",), "part"),
    SUPERSEDED_BY: (("model",), "model"),
    PART_DISCONTINUED: (("model", "family"), "part"),
    DOCUMENTED_CAUSE: (("model", "family"), "cause"),
}
# Kinds whose target must stand as its own token where the quote sits in the
# page: every persisted kind ("E1" cut from "E10", "Optima 880" cut from
# "Optima 880X", part "FK-1" cut from "FK-10").
TOKEN_TARGET_KINDS = (HAS_CODE, SUPERSEDED_BY, PART_DISCONTINUED, CODE_POINTS_TO_PART, IN_FAMILY)
REGISTRY_KINDS = (INSTALLED_AT, IS_MODEL)
REGISTRY_NODE_TYPES = ("property", "appliance")

# The node attribute holding each target type's value as printed.
PRINTED = {"family": "family", "code": "code", "part": "part_no", "model": "model", "cause": "label"}

EDGE_FIELDS = ("source_url", "retrieved_at", "evidence", "evidence_sha256", "brief_run_id", "validated_at")
# Optional per edge: the sha256 of the page text the evidence was verified against.
EDGE_TEXT_SHA = "text_sha256"
# Required on a DOCUMENTED_CAUSE edge only: the cause as printed and its action.
CAUSE_FIELDS = ("label", "action")
DROPS_SUFFIX = ".drops.jsonl"

CORPORATE_SUFFIXES = ("inc", "incorporated", "llc", "co", "corp", "corporation", "ltd", "limited", "company")


class EdgeRejected(ValueError):
    """An edge `add_edge` refused; `reason` names the failed check."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


# ---------------------------------------------------------------------------
# Normalization and keys
# ---------------------------------------------------------------------------


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non empty string, got {value!r}")
    return value


def norm_maker(maker: str) -> str:
    """Maker key component: "SUNDANCE® SPAS, Inc." and "Sundance Spas" give "sundance-spas"."""
    text = "".join(
        " " if unicodedata.category(ch)[0] in "SP" else ch
        for ch in unicodedata.normalize("NFKC", _text(maker, "maker")).casefold()
    )
    words = text.split()
    while len(words) > 1 and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    if not words:
        raise ValueError(f"maker {maker!r} normalizes to nothing")
    return "-".join(words)


def norm_model(model: str) -> str:
    """Model or family key component: casefolded letters and digits only."""
    out = "".join(ch for ch in unicodedata.normalize("NFKC", _text(model, "model")).casefold() if ch.isalnum())
    if not out:
        raise ValueError(f"model {model!r} normalizes to nothing")
    return out


def norm_code(code: str) -> str:
    """Code key component: rule 3's normalization with inner whitespace runs to one space."""
    out = normalize_code(_text(code, "code"))
    if out is None:
        raise ValueError(f"code {code!r} normalizes to nothing")
    return " ".join(out.split())


def norm_part(part_no: str) -> str:
    out = "".join(unicodedata.normalize("NFKC", _text(part_no, "part number")).upper().split())
    return out


def norm_label(label: str) -> str:
    """Cause label component before hashing: NFC, casefolded, one space per run, punctuation trimmed."""
    text = " ".join(unicodedata.normalize("NFC", _text(label, "cause label")).casefold().split())
    out = text.strip(" " + "".join(ch for ch in set(text) if unicodedata.category(ch)[0] == "P"))
    if not out:
        raise ValueError(f"cause label {label!r} normalizes to nothing")
    return out


def model_key(maker: str, model: str) -> str:
    return f"model:{norm_maker(maker)}:{norm_model(model)}"


def family_key(maker: str, family: str) -> str:
    return f"family:{norm_maker(maker)}:{norm_model(family)}"


def code_key(maker: str, scope: str, code: str) -> str:
    """`scope` is the family or model name the code is documented for."""
    return f"code:{norm_maker(maker)}:{norm_model(scope)}:{norm_code(code)}"


def part_key(maker: str, part_no: str) -> str:
    return f"part:{norm_maker(maker)}:{norm_part(part_no)}"


def cause_key(maker: str, scope: str, label: str) -> str:
    """`scope` is the family or model name the cause is documented for."""
    digest = hashlib.sha256(norm_label(label).encode("utf-8")).hexdigest()[:12]
    return f"cause:{norm_maker(maker)}:{norm_model(scope)}:{digest}"


def source_key(url: str) -> str:
    return "source:" + hashlib.sha256(_text(url, "url").encode("utf-8")).hexdigest()[:12]


def property_key(property_id: str) -> str:
    return f"property:{property_id}"


def appliance_key(appliance_id: str) -> str:
    return f"appliance:{appliance_id}"


def node_type(key: str) -> str:
    return key.split(":", 1)[0]


def drops_path(graph_path: Path | str) -> Path:
    """The drop log beside a graph file: one JSON line per dropped edge."""
    p = Path(graph_path)
    return p.with_name(p.stem + DROPS_SUFFIX)


def append_drops(graph_path: Path | str, drops: list[dict[str, Any]]) -> None:
    """Append each drop to the graph's drop log unless the same line is already there."""
    path = drops_path(graph_path)
    seen: set[str] = set()
    if path.is_file():
        seen = {line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    new = []
    for drop in drops:
        line = json.dumps(drop, sort_keys=True)
        if line not in seen:
            seen.add(line)
            new.append(line)
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in new))


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


def _edge_id(kind: str, source_url: str, evidence_sha: str) -> str:
    return f"{kind}|{source_url}|{evidence_sha}"


class KnowledgeGraph:
    """Document derived nodes and edges, plus registry derived ones that are never saved."""

    def __init__(self, meta: dict[str, Any] | None = None) -> None:
        self.g = nx.MultiDiGraph()
        # Top level labels kept across load and save (for example "synthetic").
        self.meta: dict[str, Any] = dict(meta or {})
        # Saved edges a lenient load refused, each with its reason.
        self.load_drops: list[dict[str, Any]] = []

    # ------------------------------------------------------------ nodes

    def _upsert(self, key: str, attrs: dict[str, Any], *, derived: bool = False) -> str:
        if key in self.g:
            node = self.g.nodes[key]
            node.update({k: v for k, v in attrs.items() if v is not None})
            if not derived:
                node.pop("derived", None)
        else:
            self.g.add_node(key, type=node_type(key), **{k: v for k, v in attrs.items() if v is not None},
                            **({"derived": True} if derived else {}))
        return key

    def add_model(self, maker: str, model: str, *, display_name: str | None = None) -> str:
        return self._upsert(model_key(maker, model),
                            {"maker": maker, "model": model, "display_name": display_name or f"{maker} {model}"})

    def add_family(self, maker: str, family: str) -> str:
        return self._upsert(family_key(maker, family), {"maker": maker, "family": family})

    def add_code(self, maker: str, scope: str, code: str) -> str:
        return self._upsert(code_key(maker, scope, code),
                            {"maker": maker, "scope": scope, "code": code, "code_norm": norm_code(code)})

    def add_part(self, maker: str, part_no: str) -> str:
        return self._upsert(part_key(maker, part_no), {"maker": maker, "part_no": part_no})

    def add_cause(self, maker: str, scope: str, label: str) -> str:
        """A Cause node; an existing node keeps the label it was first printed with."""
        key = cause_key(maker, scope, label)
        if key in self.g and not self.g.nodes[key].get("derived"):
            return key
        return self._upsert(key, {"maker": maker, "scope": scope, "label": label, "label_norm": norm_label(label)})

    def add_source(self, url: str, *, host: str, title: str | None, retrieved_at: str,
                   text_sha256: str | None, tier: str) -> str:
        """The source node an edge's `source_url` points at (PLAN 8.11 Source row).

        An existing node keeps its first retrieved_at and text_sha256: a later
        fetch of the same URL must not re-date the edges already on it
        (decision 41).
        """
        key = source_key(url)
        existing = self.g.nodes[key] if key in self.g else {}
        return self._upsert(key, {"url": url, "host": host, "title": title,
                                  "retrieved_at": existing.get("retrieved_at") or retrieved_at,
                                  "text_sha256": existing.get("text_sha256") or text_sha256,
                                  "tier": tier})

    def node(self, key: str) -> dict[str, Any] | None:
        return dict(self.g.nodes[key]) if key in self.g else None

    def source_for(self, url: str) -> dict[str, Any] | None:
        return self.node(source_key(url))

    # ------------------------------------------------------------ edges

    def target_of(self, dst_key: str) -> str:
        """The destination's value as printed, which the evidence must contain."""
        attrs = self.g.nodes[dst_key]
        return attrs[PRINTED[attrs["type"]]]

    def add_edge(self, kind: str, src_key: str, dst_key: str, *, source_url: str, retrieved_at: str,
                 evidence: str, evidence_sha256: str, brief_run_id: str, validated_at: str,
                 page_text: str | None = None, text_sha256: str | None = None,
                 label: str | None = None, action: str | None = None) -> dict[str, Any]:
        """Add one document derived edge, or raise EdgeRejected.

        Both endpoints must already be nodes. Every field must be a non empty
        string, `evidence_sha256` must hash `evidence`, and the evidence must
        pass section 8.11 conditions 3 to 5 against the target as printed;
        with `page_text`, the full check (conditions 1 to 5) runs, and the
        target of every persisted kind (TOKEN_TARGET_KINDS) must also stand
        as its own token where the quote sits in the page ("E1" cut from
        "E10" and "Optima 880" cut from "Optima 880X" are refused). `text_sha256`, when
        given, is stored on the edge. Adding the same edge again (kind,
        endpoints, URL and evidence) replaces it.

        A DOCUMENTED_CAUSE edge also needs `label` and `action` (non empty),
        and its evidence is checked by the cause condition against `label`,
        whose normalized form must match the Cause node's; no other kind
        takes them.
        """
        fields = {"source_url": source_url, "retrieved_at": retrieved_at, "evidence": evidence,
                  "evidence_sha256": evidence_sha256, "brief_run_id": brief_run_id,
                  "validated_at": validated_at}
        missing = [name for name, value in fields.items() if not isinstance(value, str) or not value.strip()]
        if missing:
            raise EdgeRejected("missing_fields", f"{kind} {src_key} -> {dst_key} lacks {missing}")
        if kind not in EDGE_KINDS:
            raise EdgeRejected("unknown_kind", f"{kind!r} is not a persisted edge kind")
        for key in (src_key, dst_key):
            if key not in self.g or self.g.nodes[key].get("derived"):
                raise EdgeRejected("unknown_node", f"{key} is not a document node in the graph")
        src_types, dst_type = EDGE_KINDS[kind]
        if node_type(src_key) not in src_types or node_type(dst_key) != dst_type:
            raise EdgeRejected("wrong_endpoints", f"{kind} cannot join {src_key} to {dst_key}")
        if not re.match(r"^https?://", source_url):
            raise EdgeRejected("source_url", f"{source_url!r} is not an http or https URL")
        if evidence_sha256 != hash_evidence(evidence):
            raise EdgeRejected("evidence_sha256", "evidence_sha256 does not hash the evidence")
        cause = self._cause_fields(kind, src_key, dst_key, label, action)
        target = self.target_of(dst_key)
        if kind == DOCUMENTED_CAUSE:
            result = (verify_cause_span(evidence, page_text, label=label) if page_text is not None
                      else check_cause_shape(evidence, label=label))
        else:
            result = (verify_span(evidence, page_text, target=target) if page_text is not None
                      else check_span_shape(evidence, target=target))
        if not result.ok:
            raise EdgeRejected(result.reason, f"{kind} {src_key} -> {dst_key}: evidence rejected")
        if kind in TOKEN_TARGET_KINDS and page_text is not None and not target_is_token(evidence, page_text, target=target):
            raise EdgeRejected(TARGET_NOT_TOKEN, f"{kind} {src_key} -> {dst_key}: {target!r} is part of a longer token")
        if text_sha256 is not None and (not isinstance(text_sha256, str) or not text_sha256.strip()):
            raise EdgeRejected("missing_fields", f"{kind} {src_key} -> {dst_key}: blank text_sha256")
        attrs = {"kind": kind, **fields, **cause, **({EDGE_TEXT_SHA: text_sha256} if text_sha256 else {})}
        self.g.add_edge(src_key, dst_key, key=_edge_id(kind, source_url, evidence_sha256), **attrs)
        return {"src": src_key, "dst": dst_key, **attrs}

    def _cause_fields(self, kind: str, src_key: str, dst_key: str, label: str | None,
                      action: str | None) -> dict[str, str]:
        """The label and action a cause edge stores, or {} for any other kind; raises EdgeRejected."""
        if kind != DOCUMENTED_CAUSE:
            if label is not None or action is not None:
                raise EdgeRejected("unexpected_fields", f"{kind} {src_key} -> {dst_key} takes no label or action")
            return {}
        values = {"label": label, "action": action}
        blank = [name for name, value in values.items() if not isinstance(value, str) or not value.strip()]
        if blank:
            raise EdgeRejected("missing_fields", f"{kind} {src_key} -> {dst_key} lacks {blank}")
        if norm_label(label) != self.g.nodes[dst_key].get("label_norm"):  # type: ignore[arg-type]
            raise EdgeRejected("label_mismatch", f"{kind} {src_key} -> {dst_key}: label is not the cause's")
        return values  # type: ignore[return-value]

    def try_add_edge(self, kind: str, src_key: str, dst_key: str, *, page_text: str | None,
                     **fields: str) -> SpanResult:
        """Add an edge after the full span check; a refused edge is logged and left out."""
        try:
            self.add_edge(kind, src_key, dst_key, page_text=page_text, **fields)  # type: ignore[arg-type]
        except EdgeRejected as exc:
            log.info("edge dropped: %s", exc)
            return SpanResult(False, exc.reason)
        return SpanResult(True, "verified")

    def edges(self, *, include_derived: bool = False) -> list[dict[str, Any]]:
        """Every edge as a dict, sorted for stable output."""
        out = [{"src": u, "dst": v, **data} for u, v, data in self.g.edges(data=True)
               if include_derived or not data.get("derived")]
        return sorted(out, key=lambda e: (e["kind"], e["src"], e["dst"], e.get("source_url", ""),
                                          e.get("evidence_sha256", "")))

    # ------------------------------------------------------------ registry

    def add_registry(self, registry: Any) -> None:
        """Property and Appliance nodes, INSTALLED_AT and IS_MODEL edges from the registry."""
        from agent.registry import Registry

        reg = registry if isinstance(registry, Registry) else Registry(registry)
        for prop in reg.list_properties():
            self._upsert(property_key(prop["id"]), {"label": prop["label"]}, derived=True)
        for appl in reg.list_appliances():
            a_key = self._upsert(appliance_key(appl["id"]),
                                 {"appliance_id": appl["id"], "record_id": appl.get("record_id")}, derived=True)
            self.g.add_edge(a_key, property_key(appl["property_id"]), key=INSTALLED_AT,
                            kind=INSTALLED_AT, derived=True)
            m_key = model_key(appl["manufacturer"], appl["model"])
            if m_key not in self.g:
                self._upsert(m_key, {"maker": appl["manufacturer"], "model": appl["model"],
                                     "display_name": f"{appl['manufacturer']} {appl['model']}"}, derived=True)
            self.g.add_edge(a_key, m_key, key=IS_MODEL, kind=IS_MODEL, derived=True)

    # ------------------------------------------------------------ load and save

    @classmethod
    def load(cls, path: Path | str | None, *, registry: Any = None, strict: bool = False) -> KnowledgeGraph:
        """Read a saved graph (a missing file is an empty graph), then add registry edges.

        Each saved edge goes through add_edge again. A refused edge raises
        EdgeRejected when `strict`; otherwise it is left out, recorded in
        `load_drops`, logged, and appended to the graph's drop log.
        """
        kg = cls()
        p = Path(path) if path is not None else None
        if p is not None and p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("graph_version") != GRAPH_VERSION:
                raise ValueError(f"{p}: unsupported graph_version {data.get('graph_version')!r}")
            kg.meta = dict(data.get("meta") or {})
            for node in data.get("nodes") or []:
                attrs = {k: v for k, v in node.items() if k not in ("key", "derived")}
                kg.g.add_node(node["key"], **attrs)
            for edge in data.get("edges") or []:
                fields = {name: edge.get(name) for name in EDGE_FIELDS}
                fields.update({name: edge[name] for name in CAUSE_FIELDS if name in edge})
                try:
                    kg.add_edge(edge.get("kind"), edge.get("src"), edge.get("dst"),
                                text_sha256=edge.get(EDGE_TEXT_SHA), **fields)
                except EdgeRejected as exc:
                    if strict:
                        raise
                    log.warning("edge dropped at load from %s: %s", p, exc)
                    kg.load_drops.append({"at": "load", "kind": edge.get("kind"), "src": edge.get("src"),
                                          "dst": edge.get("dst"), "source_url": edge.get("source_url"),
                                          "reason": exc.reason})
            if kg.load_drops:
                append_drops(p, kg.load_drops)
        if registry is not None:
            kg.add_registry(registry)
        return kg

    def to_json(self) -> dict[str, Any]:
        """The saved form: document nodes and edges only, sorted."""
        nodes = [{"key": key, **{k: v for k, v in attrs.items() if k != "derived"}}
                 for key, attrs in self.g.nodes(data=True)
                 if not attrs.get("derived") and attrs.get("type") not in REGISTRY_NODE_TYPES]
        edges = [{k: v for k, v in e.items() if k != "derived"} for e in self.edges()]
        return {"graph_version": GRAPH_VERSION, "meta": self.meta,
                "nodes": sorted(nodes, key=lambda n: n["key"]), "edges": edges}

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def save(self, path: Path | str) -> None:
        """Write the graph atomically; the same graph always gives the same bytes."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(self.dumps(), encoding="utf-8")
        tmp.replace(p)

    # ------------------------------------------------------------ queries

    def _document_edges(self, key: str, *, kinds: tuple[str, ...] | None = None,
                        direction: str = "out") -> list[dict[str, Any]]:
        if key not in self.g:
            return []
        items = self.g.out_edges(key, data=True) if direction == "out" else self.g.in_edges(key, data=True)
        return [{"src": u, "dst": v, **d} for u, v, d in items
                if not d.get("derived") and (kinds is None or d["kind"] in kinds)]

    def _with_source(self, edge: dict[str, Any]) -> dict[str, Any]:
        out = dict(edge)
        out["target"] = self.target_of(edge["dst"])
        if node_type(edge["dst"]) == "code":
            out["code"] = out["target"]
        out["source"] = self.source_for(edge["source_url"])
        return out

    @staticmethod
    def _verified(edge: dict[str, Any], target: str) -> bool:
        """The checks a loaded edge can pass without page text: hash and span shape."""
        if edge.get("evidence_sha256") != hash_evidence(edge.get("evidence") or ""):
            return False
        return check_span_shape(edge.get("evidence"), target=target).ok

    def _verified_cause(self, edge: dict[str, Any]) -> bool:
        """The same checks for a cause edge: hash, span shape with the cause
        condition, a non blank action, and its label naming its Cause node."""
        if edge.get("evidence_sha256") != hash_evidence(edge.get("evidence") or ""):
            return False
        label, action = edge.get("label"), edge.get("action")
        if not isinstance(action, str) or not action.strip() or not isinstance(label, str):
            return False
        try:
            if norm_label(label) != self.g.nodes[edge["dst"]].get("label_norm"):
                return False
        except ValueError:
            return False
        return check_cause_shape(edge.get("evidence"), label=label).ok

    def _edge_ok(self, edge: dict[str, Any]) -> bool:
        if edge.get("kind") == DOCUMENTED_CAUSE:
            return self._verified_cause(edge)
        return self._verified(edge, self.target_of(edge["dst"]))

    def _verified_edges(self, key: str, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        return [self._with_source(e) for e in self._document_edges(key, kinds=kinds) if self._edge_ok(e)]

    def has_model(self, maker: str, model: str) -> bool:
        """True when the model has at least one verified document edge of its own (outgoing).

        A model node that exists only because a registry appliance names it
        does not count, and neither does one known only as another model's
        successor: an incoming SUPERSEDED_BY edge documents nothing about the
        model itself, so route row 5 (main research) applies to it.
        """
        key = model_key(maker, model)
        if key not in self.g:
            return False
        return bool(self._verified_edges(key, tuple(EDGE_KINDS)))

    def family_of(self, maker: str, model: str) -> str | None:
        """The family name as printed, from a verified IN_FAMILY edge, or None."""
        edges = self._verified_edges(model_key(maker, model), (IN_FAMILY,))
        return sorted(e["target"] for e in edges)[0] if edges else None

    def _scopes(self, maker: str, model_or_family: str) -> list[str]:
        """The model key and its family key, or the family key alone."""
        keys = []
        m_key = model_key(maker, model_or_family)
        if m_key in self.g:
            keys.append(m_key)
            family = self.family_of(maker, model_or_family)
            if family:
                keys.append(family_key(maker, family))
        f_key = family_key(maker, model_or_family)
        if f_key in self.g and f_key not in keys:
            keys.append(f_key)
        return keys

    def codes_for(self, maker: str, model_or_family: str) -> list[str]:
        """Codes as printed with a verified HAS_CODE edge from the model, its family, or the family named."""
        codes = {e["target"] for key in self._scopes(maker, model_or_family)
                 for e in self._verified_edges(key, (HAS_CODE,))}
        return sorted(codes)

    def edges_for_code(self, maker: str, model: str, code: str) -> list[dict[str, Any]]:
        """Verified HAS_CODE edges for this code (model or family) and the code's part edges.

        Each dict carries the edge fields, `target` as printed, and `source`,
        the Source node for its URL (None when the graph has no such node).
        """
        wanted = norm_code(code)
        out: list[dict[str, Any]] = []
        for key in self._scopes(maker, model):
            for edge in self._verified_edges(key, (HAS_CODE,)):
                if self.g.nodes[edge["dst"]].get("code_norm") != wanted:
                    continue
                out.append(edge)
                out.extend(self._verified_edges(edge["dst"], (CODE_POINTS_TO_PART,)))
        return out

    def superseded_by(self, maker: str, model: str) -> list[dict[str, Any]]:
        """Verified SUPERSEDED_BY edges from this model, each with the successor's maker and model."""
        out = []
        for edge in self._verified_edges(model_key(maker, model), (SUPERSEDED_BY,)):
            successor = self.g.nodes[edge["dst"]]
            out.append({**edge, "successor_manufacturer": successor.get("maker"),
                        "successor_model": successor.get("model")})
        return out

    def parts_discontinued(self, maker: str, model: str) -> list[dict[str, Any]]:
        """Verified PART_DISCONTINUED edges from this model or its family (Phase 4, upgrade_check)."""
        found: list[dict[str, Any]] = []
        for scope in self._scopes(maker, model):
            found.extend(self._verified_edges(scope, (PART_DISCONTINUED,)))
        return found

    def causes_for(self, maker: str, model: str) -> list[dict[str, Any]]:
        """Verified DOCUMENTED_CAUSE edges from this model or its family.

        Each dict carries the edge fields (with `label` and `action`),
        `target` (the Cause node's label as first printed) and `source`.
        """
        found: list[dict[str, Any]] = []
        for scope in self._scopes(maker, model):
            found.extend(self._verified_edges(scope, (DOCUMENTED_CAUSE,)))
        return found

    def appliances_for_model(self, maker: str, model: str) -> list[str]:
        """Registry appliance IDs whose IS_MODEL edge points at this model."""
        key = model_key(maker, model)
        if key not in self.g:
            return []
        return sorted(self.g.nodes[u].get("appliance_id") for u, _, d in self.g.in_edges(key, data=True)
                      if d.get("kind") == IS_MODEL)

    def stats(self) -> dict[str, Any]:
        """Counts of saved nodes by type and edges by kind, and of registry derived ones."""
        saved = self.to_json()
        derived_nodes = Counter(a.get("type") for _, a in self.g.nodes(data=True)
                                if a.get("derived") or a.get("type") in REGISTRY_NODE_TYPES)
        derived_edges = Counter(d["kind"] for _, _, d in self.g.edges(data=True) if d.get("derived"))
        return {
            "nodes": dict(sorted(Counter(n["type"] for n in saved["nodes"]).items())),
            "edges": dict(sorted(Counter(e["kind"] for e in saved["edges"]).items())),
            "registry_nodes": dict(sorted(derived_nodes.items())),
            "registry_edges": dict(sorted(derived_edges.items())),
            "load_drops": len(self.load_drops),
        }

    # ------------------------------------------------------------ integrity

    def reverify(self, pages_dir: Path | str | None) -> dict[str, Any]:
        """Recheck every saved edge (test_every_edge_reverifies).

        Where the page text of the edge's source is on disk (any `*.txt` in
        `pages_dir` whose sha256 matches the edge's own `text_sha256`, else
        the Source node's) the
        full span check runs. Otherwise only `evidence_sha256`, span length,
        the joiner and target in span are checked, and the edge is counted as
        a limitation, not a full pass.
        """
        pages = page_index(pages_dir)
        report: dict[str, Any] = {"edges": 0, "full": 0, "limited": 0, "failures": [], "limited_edges": []}
        for edge in self.edges():
            report["edges"] += 1
            target = self.target_of(edge["dst"])
            label = f"{edge['kind']} {edge['src']} -> {edge['dst']} ({edge['source_url']})"
            if edge["evidence_sha256"] != hash_evidence(edge["evidence"]):
                report["failures"].append(f"{label}: evidence_sha256")
                continue
            source = self.source_for(edge["source_url"])
            text = pages.get(edge.get(EDGE_TEXT_SHA) or (source or {}).get("text_sha256") or "")
            if text is None:
                result = check_span_shape(edge["evidence"], target=target)
                report["limited"] += 1
                report["limited_edges"].append(label)
            else:
                result = verify_span(edge["evidence"], text, target=target)
                report["full"] += 1
            if edge["kind"] == DOCUMENTED_CAUSE:
                # A cause's condition 5 is its label's words, not its label printed verbatim.
                result = (verify_cause_span(edge["evidence"], text, label=edge.get("label")) if text is not None
                          else check_cause_shape(edge["evidence"], label=edge.get("label")))
            if not result.ok:
                report["failures"].append(f"{label}: {result.reason}")
        return report


def page_index(pages_dir: Path | str | None) -> dict[str, str]:
    """Page texts by the sha256 of their UTF-8 bytes, from every `*.txt` in the folder."""
    if pages_dir is None or not Path(pages_dir).is_dir():
        return {}
    out = {}
    for path in sorted(Path(pages_dir).glob("*.txt")):
        raw = path.read_bytes()  # bytes, so newline translation cannot change the hash
        out[hashlib.sha256(raw).hexdigest()] = raw.decode("utf-8")
    return out
