"""Every persisted edge re verifies (SC6 whole graph; PLAN 8.11).

Checked graphs: the synthetic fixtures (their page texts are tracked), a graph
persisted by a replay run in this test, the CLI's replay graph
(config.REPLAY_GRAPH_PATH) and seed/graph.json. Where an edge's page text is
on disk the full span check runs; otherwise `evidence_sha256`, span length,
the joiner and target in span are checked and the edge is counted and reported
as a limitation, never as a full pass. At a gate of phase 6 or later, which
promotes edges into seed/, a seed edge without its page text fails, and so
does a seed edge whose brief_run_id has no live mode charge in the live ledger
(PLAN 8.11 and decision 37: only edges from live runs are eligible).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest

from agent import config
from agent.kg import KnowledgeGraph, page_index
from agent.ledger import Ledger
from agent.rules.evidence import evidence_sha256
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR, run_case

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PAGES = FIXTURES / "pages"
SEED_GRAPH = config.SEED_DIR / "graph.json"
SEED_GATE_PHASE = 6
FLO_SENTENCE = "Error FLO means the heater senses no water flow."


def _replay_run_graph(tmp_path: Path) -> tuple[Path, Path]:
    """A synthetic replay run whose brief carries a verbatim evidence quote; persist writes where it will."""
    from agent.replay.cassettes import cassette_from_dict

    data = json.loads((SYNTHETIC_CASSETTE_DIR / "flo_three_candidates.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    for cand in data["synthesize"][0]["draft"]["candidates"]:
        if cand["code"] == "flo.":
            cand["code"], cand["evidence"] = "FLO", FLO_SENTENCE
    _, ctx, _ = run_case(cassette_from_dict(data, "flo_three_candidates with evidence"), tmp_path)
    return Path(ctx.graph_path), Path(ctx.pages_dir)


def _targets() -> list:
    params = [pytest.param(path, FIXTURE_PAGES, id=f"fixture-{path.stem}")
              for path in sorted((FIXTURES / "graphs").glob("*.json"))]
    params.append(pytest.param("replay_run", None, id="replay_run"))
    params.append(pytest.param(config.REPLAY_GRAPH_PATH, config.PAGES_DIR, id="cli_replay_graph"))
    params.append(pytest.param(SEED_GRAPH, config.PAGES_DIR, id="seed"))
    return params


def edges_without_live_run(graph: KnowledgeGraph, ledger_path: Path) -> list[str]:
    """Edges whose brief_run_id has no charge row in a live mode in the ledger at `ledger_path`."""
    ledger = Ledger(ledger_path) if Path(ledger_path).is_file() else None
    out = []
    for edge in graph.edges():
        rows = ledger.rows(edge["brief_run_id"]) if ledger is not None else []
        if not any(r["kind"] == "charge" and r["mode"] in config.LIVE_MODES for r in rows):
            out.append(f"{edge['kind']} {edge['src']} -> {edge['dst']} (run {edge['brief_run_id']})")
    return out


def _gate_phase() -> int:
    raw = os.environ.get(config.ENV_GATE, "").strip()
    return int(raw) if raw.isdigit() else 0


@pytest.mark.parametrize(("graph_path", "pages_dir"), _targets())
def test_every_edge_reverifies(graph_path, pages_dir, tmp_path: Path, record_property) -> None:
    """Mutations kg_reverify_shape_only (the full span check is skipped where
    page text exists) and kg_reverify_missing_text_is_a_pass (an edge with no
    page text is counted as fully verified)."""
    replay_run = graph_path == "replay_run"
    if replay_run:
        graph_path, pages_dir = _replay_run_graph(tmp_path)
    graph_path = Path(graph_path)
    if replay_run:
        # Mutation persist_replay_run_writes_nothing: this case must check a
        # real persisted edge, never pass on an empty graph.
        assert graph_path.is_file(), "the replay run persisted no graph"
    if not graph_path.is_file():
        record_property("edges", 0)
        record_property("limitation", f"{graph_path.name} does not exist yet: 0 edges to check")
        return
    g = KnowledgeGraph.load(graph_path, strict=True)  # an edge whose evidence no longer hashes is refused
    report = g.reverify(pages_dir)
    for key in ("edges", "full", "limited"):
        record_property(key, report[key])
    if report["limited"]:
        record_property("limitation", f"{report['limited']} edges checked without page text")
        print(f"{graph_path}: {report['limited']} of {report['edges']} edges lack page text "
              f"(checked hash, length, joiner and target only): {report['limited_edges']}")
    assert report["failures"] == []
    assert report["full"] + report["limited"] == report["edges"] == len(g.edges())
    if graph_path.parent == FIXTURES / "graphs":
        assert report["edges"] and report["limited"] == 0, "fixture page texts are tracked"
    if replay_run:
        assert report["edges"] >= 1 and report["limited"] == 0, "the run's page text is on disk"
    if graph_path == SEED_GRAPH and _gate_phase() >= SEED_GATE_PHASE:
        assert report["limited"] == 0, "promotion into seed/ needs full re verification with page text"
        assert edges_without_live_run(g, config.LEDGER_PATH) == [], "seed edges must come from live runs"
    if not report["edges"]:
        return

    # The check can fail: without page text every edge is a limitation, and a
    # verbatim edge edited into the page's wording no longer re verifies.
    assert g.reverify(tmp_path / "no_pages")["limited"] == report["edges"]
    if report["full"]:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        pages = page_index(pages_dir)
        for edge in data["edges"]:
            source = g.source_for(edge["source_url"]) or {}
            if (edge.get("text_sha256") or source.get("text_sha256")) in pages:
                edge["evidence"] = edge["evidence"].rstrip(".") + " (edited)."
                edge["evidence_sha256"] = evidence_sha256(edge["evidence"])
                break
        edited = tmp_path / "edited.json"
        edited.write_text(json.dumps(data), encoding="utf-8")
        edited_report = KnowledgeGraph.load(edited, strict=True).reverify(pages_dir)
        assert len(edited_report["failures"]) == 1 and "not_in_page_text" in edited_report["failures"][0]


def test_reverify_uses_only_matching_page_text(tmp_path: Path) -> None:
    """Mutation kg_page_index_trusts_file_names: page_index keys a page by its
    file name instead of the sha256 of its bytes, so no Source finds its page."""
    pages = tmp_path / "pages"
    pages.mkdir()
    for path in FIXTURE_PAGES.glob("*.txt"):
        shutil.copy(path, pages / path.name)
    g = KnowledgeGraph.load(FIXTURES / "graphs" / "optima_flo.json")
    assert g.reverify(pages)["full"] == len(g.edges())
    # Swap the contents of two pages: they are still found, by hash.
    a, b = pages / "optima_880_synthetic_manual.txt", pages / "xr16_synthetic_overview.txt"
    a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
    a.write_bytes(b_bytes)
    b.write_bytes(a_bytes)
    assert g.reverify(pages)["full"] == len(g.edges()), "hashes, not names, find the pages"
    # Change every page by one byte: nothing matches by hash any more.
    for path in (a, b):
        path.write_bytes(path.read_bytes() + b"\nSynthetic trailing line.\n")
    report = g.reverify(pages)
    assert report["full"] == 0 and report["limited"] == len(g.edges()) and report["failures"] == []


def test_seed_promotion_needs_a_live_run(tmp_path: Path) -> None:
    """Mutation seed_promotion_ledger_unchecked: the promotion check stops
    looking the edge's brief_run_id up in the live ledger, so an edge from a
    replay run copied into seed/ would pass the phase 6 gate.

    Runs at every gate: it checks the helper the phase 6 seed branch uses.
    """
    from agent.kg import HAS_CODE

    ledger_path = tmp_path / "ledger.sqlite"
    ledger = Ledger(ledger_path, create=True)
    for run_id, mode in (("replay-run-1", "replay"), ("live-run-1", "cheap")):
        hold = ledger.reserve(run_id, mode=mode, node="synthesize", model=config.HAIKU,
                              input_tokens_est=100, max_tokens=100)
        ledger.charge(hold, {"input_tokens": 100, "output_tokens": 10})
    g = KnowledgeGraph(meta={"synthetic": "synthetic promotion check, example.com URL (decision 15)"})
    m = g.add_model("Acme", "X1")
    evidence = {"replay-run-1": "Synthetic manual text: code E4 means the drain is blocked.",
                "live-run-1": "Synthetic manual text: code E5 means the door is open."}
    for run_id, text in evidence.items():
        code = text.split("code ")[1].split(" ")[0]
        g.add_edge(HAS_CODE, m, g.add_code("Acme", "X1", code), source_url="https://example.com/synthetic/x1",
                   retrieved_at="2026-09-18T00:00:00+00:00", evidence=text, evidence_sha256=evidence_sha256(text),
                   brief_run_id=run_id, validated_at="2026-09-18T00:00:00+00:00")
    flagged = edges_without_live_run(g, ledger_path)
    assert len(flagged) == 1 and "replay-run-1" in flagged[0]
    assert len(edges_without_live_run(g, tmp_path / "no_ledger.sqlite")) == 2
