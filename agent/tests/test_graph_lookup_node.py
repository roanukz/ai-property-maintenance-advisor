"""The graph_lookup node (PLAN 8.3, 8.11; decision 41).

graph_lookup reads a KnowledgeGraph built in the test from synthetic edges on
example.com URLs (test_router.build_kg), saved at the RunContext graph path.
Each test names the mutation that turns it red.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.runtime import Runtime

from agent.kg import KnowledgeGraph, model_key
from agent.nodes.graph_lookup import graph_lookup
from agent.state import RunContext, check_json_native
from agent.tests.test_router import (
    FLO_EVIDENCE, FLO_TITLE, FLO_URL, IDENTITY, MAKER, MODEL, OH_URL, RETRIEVED_AT, SOURCE_RETRIEVED_AT,
    SYNTHETIC, UPGRADE_URL, _fields, build_kg, install_graph, sha,
)

UPGRADE_RETRIEVED_AT = "2026-04-02T08:00:00+00:00"
SIBLING = "Optima 885 (synthetic)"
HL_EVIDENCE = "Synthetic manual text: HL means the high limit sensor tripped; let the water cool first."
COOL_EVIDENCE = "Synthetic manual text, later edition: COOL means the water is below the set point."


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(run_id="run-graph", mode="replay", ledger_path=tmp_path / "l.sqlite",
                      registry_path=tmp_path / "r.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages")


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parts: list[str], identity: dict | None = None,
         in_memory: bool = False, **state) -> dict:
    ctx = _ctx(tmp_path)
    install_graph(ctx, monkeypatch, parts, in_memory=in_memory, upgrade_retrieved_at=UPGRADE_RETRIEVED_AT)
    out = graph_lookup({"identity": identity or IDENTITY, **state}, Runtime(context=ctx))
    check_json_native(out)
    return out


def test_graph_sources_have_origin_graph_and_original_retrieved_at(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: graph_lookup_origin_search (sources marked origin "search");
    graph_lookup_retrieved_now (retrieved_at set to this run's time, decision 41);
    graph_lookup_no_excerpts (the verified evidence is not given as the source text);
    graph_lookup_source_time_first (the Source node's retrieved_at wins over the edge's)."""
    out = _run(tmp_path, monkeypatch, ["flo", "upgrade"], observed_code="FLO")
    by_url = {s["url"]: s for s in out["sources"]}
    assert list(by_url) == [FLO_URL, UPGRADE_URL]
    assert {s["origin"] for s in out["sources"]} == {"graph"}
    # The edge's own retrieval time, not the Source node's (which a later fetch may set).
    assert SOURCE_RETRIEVED_AT != RETRIEVED_AT
    assert by_url[FLO_URL]["retrieved_at"] == RETRIEVED_AT
    assert by_url[UPGRADE_URL]["retrieved_at"] == UPGRADE_RETRIEVED_AT
    assert by_url[FLO_URL]["title"] == FLO_TITLE
    assert by_url[FLO_URL]["text_sha256"] == sha("synthetic page text")
    assert by_url[FLO_URL]["excerpts"] == [FLO_EVIDENCE]
    assert by_url[UPGRADE_URL]["host"] == "manuals.example.com"
    assert [h["kind"] for h in out["graph_hits"]] == ["HAS_CODE", "SUPERSEDED_BY"]
    assert out["graph_hits"][0]["source_id"] == by_url[FLO_URL]["source_id"]
    assert out["graph_hits"][0]["code"] == "FLO"
    assert all(h["retrieved_at"] in (RETRIEVED_AT, UPGRADE_RETRIEVED_AT) for h in out["graph_hits"])
    assert "graph_lookup" in out["latency"]


def test_only_verified_edges_are_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: router_kg_unverified_edge_counts (the graph's evidence check is
    skipped); router_kg_hash_unchecked (an edge whose evidence does not match
    its hash passes); both caught by the in memory graph. kg_load_lenient_reraises
    (the saved graph's damaged edges fail the node at load) is caught on disk."""
    for in_memory in (False, True):  # disk first: the in memory graph stays patched in
        where = tmp_path / ("memory" if in_memory else "disk")
        out = _run(where, monkeypatch, ["flo_no_evidence", "flo_bad_hash"], in_memory=in_memory,
                   observed_code="FLO")
        assert (out["sources"], out["graph_hits"]) == ([], []), in_memory
        out = _run(where / "b", monkeypatch, ["upgrade", "oh_family_no_evidence"], in_memory=in_memory,
                   observed_code=None)
        assert [s["url"] for s in out["sources"]] == [UPGRADE_URL], in_memory


def test_confirmed_code_loads_only_that_codes_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation graph_lookup_ignores_code: every code edge is loaded when a code was confirmed,
    so synthesize sees another code's documentation."""
    assert [s["url"] for s in _run(tmp_path, monkeypatch, ["flo", "oh_family"], observed_code="OH")["sources"]] \
        == [OH_URL]
    assert [s["url"] for s in _run(tmp_path, monkeypatch, ["flo", "oh_family"], observed_code=None)["sources"]] \
        == [FLO_URL, OH_URL]


def test_known_urls_are_not_registered_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation graph_lookup_duplicates_known: a URL already in sources is appended again
    (append only sources would then hold it at two indexes)."""
    known = [{"url": FLO_URL, "origin": "graph", "retrieved_at": RETRIEVED_AT}]
    out = _run(tmp_path, monkeypatch, ["flo", "oh_family"], observed_code=None, sources=known)
    assert [s["url"] for s in out["sources"]] == [OH_URL]


def test_missing_model_adds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation graph_lookup_no_key_guard: the graph's key helpers refuse a missing model, and
    the node fails instead of adding nothing."""
    out = _run(tmp_path, monkeypatch, ["flo"], identity={"manufacturer": MAKER, "model": None},
               observed_code="FLO")
    assert (out["sources"], out["graph_hits"]) == ([], [])
    out = graph_lookup({"identity": IDENTITY, "observed_code": "FLO"}, Runtime(context=_ctx(tmp_path / "none")))
    assert (out["sources"], out["graph_hits"]) == ([], [])  # no graph file yet: an empty graph


def test_reads_the_graph_at_the_run_context_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation graph_lookup_graph_path_ignored: config.GRAPH_PATH read instead of ctx.graph_path."""
    out = _run(tmp_path, monkeypatch, ["flo"], observed_code="FLO")
    assert [s["url"] for s in out["sources"]] == [FLO_URL]


def test_two_edges_on_one_url_give_one_source_with_both_excerpts(tmp_path: Path) -> None:
    """Mutation graph_lookup_one_excerpt_per_url: a second verified edge on a URL
    already added does not add its evidence, so synthesize never sees it."""
    kg, _ = build_kg(["flo"])
    hl = kg.add_code(MAKER, MODEL, "HL")
    kg.add_edge("HAS_CODE", model_key(MAKER, MODEL), hl, **_fields(FLO_URL, HL_EVIDENCE))
    ctx = _ctx(tmp_path)
    kg.save(ctx.graph_path)
    out = graph_lookup({"identity": IDENTITY, "observed_code": None}, Runtime(context=ctx))
    [source] = out["sources"]
    assert source["url"] == FLO_URL
    assert source["excerpts"] == [FLO_EVIDENCE, HL_EVIDENCE]
    assert [h["code"] for h in out["graph_hits"]] == ["FLO", "HL"]


def test_a_later_fetch_of_the_same_url_keeps_the_older_edges_time_and_page(tmp_path: Path) -> None:
    """Mutations: kg_add_source_overwrites (a later add_source re-dates the Source
    node); graph_lookup_text_sha_from_source (the source's page hash comes from
    the Source node, not the edge that was verified against it).

    Two runs share one synthetic manual URL: the first writes FLO for the
    Optima 880 against page text "aaa..."; a later run fetches the changed page
    ("bbb...") and writes COOL for another model. graph_lookup for the Optima
    880's FLO must still report the first run's retrieval time and page hash.
    """
    first_at, later_at = "2026-01-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"
    first_sha, later_sha = "a" * 64, "b" * 64
    kg = KnowledgeGraph(meta={"synthetic": SYNTHETIC})
    kg.add_source(FLO_URL, host="manuals.example.com", title=FLO_TITLE, retrieved_at=first_at,
                  text_sha256=first_sha, tier="manufacturer")
    kg.add_edge("HAS_CODE", kg.add_model(MAKER, MODEL), kg.add_code(MAKER, MODEL, "FLO"),
                text_sha256=first_sha, **_fields(FLO_URL, FLO_EVIDENCE, first_at))
    kg.add_source(FLO_URL, host="manuals.example.com", title=FLO_TITLE, retrieved_at=later_at,
                  text_sha256=later_sha, tier="manufacturer")
    kg.add_edge("HAS_CODE", kg.add_model(MAKER, SIBLING), kg.add_code(MAKER, SIBLING, "COOL"),
                text_sha256=later_sha, **_fields(FLO_URL, COOL_EVIDENCE, later_at))
    assert kg.source_for(FLO_URL)["retrieved_at"] == first_at
    assert kg.source_for(FLO_URL)["text_sha256"] == first_sha
    ctx = _ctx(tmp_path)
    kg.save(ctx.graph_path)
    out = graph_lookup({"identity": IDENTITY, "observed_code": "FLO"}, Runtime(context=ctx))
    [source] = out["sources"]
    assert (source["retrieved_at"], source["text_sha256"]) == (first_at, first_sha)
    sibling = graph_lookup({"identity": {"manufacturer": MAKER, "model": SIBLING}, "observed_code": "COOL"},
                           Runtime(context=ctx))
    [source] = sibling["sources"]
    assert (source["retrieved_at"], source["text_sha256"]) == (later_at, later_sha)
