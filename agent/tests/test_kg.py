"""The knowledge graph (PLAN 8.11; decisions 37 and 45): keys, normalization,
deterministic save, refusal of incomplete edges, and registry edges that are
never saved. Every page text here is synthetic (fixtures/pages, decision 15).
Each test names, in its docstring, the mutation that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import kg as kgmod
from agent.kg import (
    HAS_CODE, IN_FAMILY, IS_MODEL, INSTALLED_AT, EdgeRejected, KnowledgeGraph,
    code_key, family_key, model_key, part_key, source_key,
)
from agent.registry import open_registry
from agent.rules.evidence import evidence_sha256

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGES = FIXTURES / "pages"
GRAPHS = FIXTURES / "graphs"
OPTIMA_FLO = GRAPHS / "optima_flo.json"

OPTIMA_URL = "https://example.com/synthetic/sundance/optima-880-manual"
XR16_URL = "https://example.com/synthetic/trane/xr16-overview"
RETRIEVED = "2026-09-18T00:00:00+00:00"
RUN_ID = "synthetic-fixture-run-1"

FAMILY_EVIDENCE = "The Optima 880 is part of the 880 Series of hot tubs in this synthetic guide."
# Verbatim after normalization: the page has two spaces after "FLO:".
FLO_EVIDENCE = ("FLO: The heater senses no water flow. Clean or replace the filter cartridge, "
                "then check that the circulation pump runs.")
XR16_EVIDENCE = "The XR16 4TTR6036 belongs to the XR16 family of split system outdoor units."

META = {
    "synthetic": True,
    "_about": ("Synthetic graph fixture for Phase 3 tests (SC7a, route rows 1 to 5). Every edge's evidence "
               "is a verbatim span of a synthetic page text in agent/tests/fixtures/pages, served only at "
               "an example.com URL; no fact here comes from a real document. The Optima 880 and its 880 "
               "Series family carry a HAS_CODE edge for FLO; the Trane XR16 4TTR6036 has a verified "
               "IN_FAMILY edge and no code edge, for the top up rows."),
}


def page(name: str) -> str:
    return (PAGES / name).read_bytes().decode("utf-8")


def fields(evidence: str, url: str) -> dict[str, str]:
    return {"source_url": url, "retrieved_at": RETRIEVED, "evidence": evidence,
            "evidence_sha256": evidence_sha256(evidence), "brief_run_id": RUN_ID,
            "validated_at": RETRIEVED}


def build_optima_flo() -> KnowledgeGraph:
    """The optima_flo.json fixture, rebuilt through the full span check."""
    g = KnowledgeGraph(meta=META)
    optima_text, xr16_text = page("optima_880_synthetic_manual.txt"), page("xr16_synthetic_overview.txt")
    g.add_source(OPTIMA_URL, host="example.com", title="Synthetic Optima 880 owner guide",
                 retrieved_at=RETRIEVED, text_sha256=evidence_sha256(optima_text), tier="dealer")
    g.add_source(XR16_URL, host="example.com", title="Synthetic XR16 overview",
                 retrieved_at=RETRIEVED, text_sha256=evidence_sha256(xr16_text), tier="dealer")
    optima = g.add_model("Sundance Spas", "Optima 880")
    series = g.add_family("Sundance Spas", "880 Series")
    flo = g.add_code("Sundance Spas", "880 Series", "FLO")
    g.add_edge(IN_FAMILY, optima, series, page_text=optima_text, **fields(FAMILY_EVIDENCE, OPTIMA_URL))
    g.add_edge(HAS_CODE, series, flo, page_text=optima_text, **fields(FLO_EVIDENCE, OPTIMA_URL))
    xr16 = g.add_model("Trane", "XR16 4TTR6036")
    xr16_family = g.add_family("Trane", "XR16")
    g.add_edge(IN_FAMILY, xr16, xr16_family, page_text=xr16_text, **fields(XR16_EVIDENCE, XR16_URL))
    return g


def test_node_keys_and_normalization() -> None:
    """Mutation kg_model_key_keeps_case: norm_model stops casefolding, so two
    spellings of one model get two keys."""
    assert model_key("Sundance Spas", "Optima 880") == "model:sundance-spas:optima880"
    assert model_key("SUNDANCE\u00ae SPAS, Inc.", "OPTIMA-880") == model_key("Sundance Spas", "optima 880")
    assert model_key("Trane", "XR16 4TTR6036") == model_key("trane", "XR16-4TTR6036") == "model:trane:xr164ttr6036"
    assert family_key("Sundance Spas", "880 Series") == "family:sundance-spas:880series"
    assert code_key("Sundance Spas", "880 Series", "flo.") == code_key("Sundance Spas", "880 series", " FLO ")
    assert code_key("Sundance Spas", "880 Series", "FLO") == "code:sundance-spas:880series:FLO"
    assert code_key("Sundance Spas", "Optima 880", "COOL /  ICE") == "code:sundance-spas:optima880:COOL / ICE"
    assert part_key("Sundance Spas", "6600 - 194") == "part:sundance-spas:6600-194"
    assert part_key("Sundance Spas", "6600-194") != part_key("Sundance Spas", "6600194")
    assert source_key(OPTIMA_URL).startswith("source:") and len(source_key(OPTIMA_URL)) == len("source:") + 12
    with pytest.raises(ValueError):
        model_key("Sundance Spas", "  ")
    with pytest.raises(ValueError):
        code_key("Sundance Spas", "Optima 880", "...")


def test_save_is_deterministic(tmp_path: Path) -> None:
    """Mutation kg_save_unsorted_nodes: to_json keeps graph insertion order for nodes."""
    built = build_optima_flo()
    assert OPTIMA_FLO.read_text(encoding="utf-8") == built.dumps(), "fixture drifted from build_optima_flo"
    # The same facts added in the reverse order give the same bytes.
    reverse = KnowledgeGraph(meta=META)
    for key, attrs in reversed(list(built.g.nodes(data=True))):
        reverse.g.add_node(key, **attrs)
    for edge in reversed(built.edges()):
        reverse.add_edge(edge["kind"], edge["src"], edge["dst"], **{f: edge[f] for f in kgmod.EDGE_FIELDS})
    assert reverse.dumps() == built.dumps()
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    built.save(first)
    KnowledgeGraph.load(first).save(second)
    assert first.read_bytes() == second.read_bytes()
    # Adding the same edge again is an upsert, not a duplicate.
    again = build_optima_flo()
    edge = again.edges()[0]
    again.add_edge(edge["kind"], edge["src"], edge["dst"], **{f: edge[f] for f in kgmod.EDGE_FIELDS})
    assert again.dumps() == built.dumps()


def test_incomplete_edges_are_refused() -> None:
    """Mutation kg_add_edge_skips_field_check: add_edge stops checking for
    missing fields, so an edge with a blank field is stored."""
    g = build_optima_flo()
    optima = model_key("Sundance Spas", "Optima 880")
    series = family_key("Sundance Spas", "880 Series")
    before = len(g.edges())
    good = fields(FAMILY_EVIDENCE, OPTIMA_URL)
    for name in kgmod.EDGE_FIELDS:
        for blank in ("", "   ", None):
            bad = {**good, name: blank}
            with pytest.raises(EdgeRejected) as info:
                g.add_edge(IN_FAMILY, optima, series, **bad)
            assert info.value.reason == "missing_fields", name
    with pytest.raises(EdgeRejected, match="evidence_sha256"):
        g.add_edge(IN_FAMILY, optima, series, **{**good, "evidence_sha256": evidence_sha256("other text")})
    no_target = "Every model shares the same heater and control panel."
    with pytest.raises(EdgeRejected, match="target_not_in_span"):
        g.add_edge(IN_FAMILY, optima, series, **fields(no_target, OPTIMA_URL))
    with pytest.raises(EdgeRejected, match="not_in_page_text"):
        g.add_edge(IN_FAMILY, optima, series, page_text=page("xr16_synthetic_overview.txt"), **good)
    with pytest.raises(EdgeRejected, match="wrong_endpoints"):
        g.add_edge(HAS_CODE, series, optima, **good)
    with pytest.raises(EdgeRejected, match="unknown_node"):
        g.add_edge(IN_FAMILY, model_key("Sundance Spas", "Optima 990"), series, **good)
    with pytest.raises(EdgeRejected, match="source_url"):
        g.add_edge(IN_FAMILY, optima, series, **{**good, "source_url": "file:///tmp/page.txt"})
    assert len(g.edges()) == before
    assert g.try_add_edge(IN_FAMILY, optima, series, page_text=None, **{**good, "brief_run_id": ""}).ok is False
    assert len(g.edges()) == before


def test_registry_edges_never_persisted(tmp_path: Path) -> None:
    """Mutation kg_save_keeps_registry_edges: to_json saves registry derived
    edges (edges(include_derived=True))."""
    registry = open_registry(tmp_path / "registry.sqlite")
    graph_path = tmp_path / "graph.json"
    build_optima_flo().save(graph_path)
    g = KnowledgeGraph.load(graph_path, registry=registry)
    stats = g.stats()
    assert stats["registry_edges"][IS_MODEL] == stats["registry_edges"][INSTALLED_AT] == len(registry.list_appliances())
    assert "appl-optima880" in g.appliances_for_model("Sundance Spas", "Optima 880")
    # A model known only from the registry is not in the graph for routing.
    assert g.has_model("Rheem", "PRO+E50 M2 RH92 CL") is False
    assert model_key("Rheem", "PRO+E50 M2 RH92 CL") in g.g
    out = tmp_path / "saved.json"
    g.save(out)
    assert out.read_bytes() == graph_path.read_bytes()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert {e["kind"] for e in saved["edges"]} == {IN_FAMILY, HAS_CODE}
    assert not [n for n in saved["nodes"] if n["type"] in ("property", "appliance")]
    assert model_key("Rheem", "PRO+E50 M2 RH92 CL") not in {n["key"] for n in saved["nodes"]}
    reloaded = KnowledgeGraph.load(out)
    assert reloaded.stats()["registry_edges"] == {}


def test_queries_return_only_verified_edges(tmp_path: Path) -> None:
    """Mutation kg_edges_for_code_ignores_code: edges_for_code returns every
    HAS_CODE edge of the model and family, whatever the code."""
    g = KnowledgeGraph.load(OPTIMA_FLO)
    assert g.meta["synthetic"] is True
    assert g.has_model("Sundance Spas", "Optima 880") and g.has_model("SUNDANCE SPAS", "optima-880")
    assert g.family_of("Sundance Spas", "Optima 880") == "880 Series"
    assert g.codes_for("Sundance Spas", "Optima 880") == ["FLO"]
    assert g.codes_for("Sundance Spas", "880 Series") == ["FLO"]
    [edge] = g.edges_for_code("Sundance Spas", "Optima 880", "flo")
    assert edge["kind"] == HAS_CODE and edge["code"] == "FLO" and edge["evidence"] == FLO_EVIDENCE
    assert edge["source"]["url"] == OPTIMA_URL and edge["source"]["retrieved_at"] == RETRIEVED
    assert g.edges_for_code("Sundance Spas", "Optima 880", "COOL") == []
    # The top up model: in the graph, verified, with no code edge.
    assert g.has_model("Trane", "XR16 4TTR6036")
    assert g.codes_for("Trane", "XR16 4TTR6036") == []
    assert g.edges_for_code("Trane", "XR16 4TTR6036", "FLO") == []
    assert not g.has_model("Sundance Spas", "Optima 990")
    # A successor and a part pointed at by a code.
    text = page("optima_880_synthetic_manual.txt")
    flo = code_key("Sundance Spas", "880 Series", "FLO")
    part = g.add_part("Sundance Spas", "filter cartridge")
    g.add_edge(kgmod.CODE_POINTS_TO_PART, flo, part, page_text=text,
               **fields("Clean or replace the filter cartridge, then check that the circulation pump runs.",
                        OPTIMA_URL))
    kinds = [e["kind"] for e in g.edges_for_code("Sundance Spas", "Optima 880", "FLO")]
    assert kinds == [HAS_CODE, kgmod.CODE_POINTS_TO_PART]
    successor = g.add_model("Sundance Spas", "Optima 990")
    g.add_edge(kgmod.SUPERSEDED_BY, model_key("Sundance Spas", "Optima 880"), successor,
               **fields("Synthetic note: the Optima 880 was replaced by the Optima 990.", OPTIMA_URL))
    [up] = g.superseded_by("Sundance Spas", "Optima 880")
    assert up["successor_model"] == "Optima 990"
    assert g.stats()["edges"] == {"CODE_POINTS_TO_PART": 1, HAS_CODE: 1, IN_FAMILY: 2, "SUPERSEDED_BY": 1}
    # A model known only as another model's successor has no edge of its own.
    assert not g.has_model("Sundance Spas", "Optima 990")


def test_damaged_saved_edge_is_dropped_and_logged_at_load(tmp_path: Path) -> None:
    """Mutations: kg_load_lenient_reraises (a lenient load raises on a damaged
    edge); kg_load_drops_unlogged (the dropped edge is not written to the drop
    log beside the graph).

    PLAN 8.11: an edge failing any condition is dropped and the drop logged.
    strict=True (the integrity test) still refuses the file.
    """
    tampered = json.loads(OPTIMA_FLO.read_text(encoding="utf-8"))
    tampered["edges"][0]["evidence"] += " Edited."
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(EdgeRejected, match="evidence_sha256"):
        KnowledgeGraph.load(bad, strict=True)
    assert not kgmod.drops_path(bad).exists()
    g = KnowledgeGraph.load(bad)
    assert [d["reason"] for d in g.load_drops] == ["evidence_sha256"]
    assert g.stats()["load_drops"] == 1
    assert len(g.edges()) == len(tampered["edges"]) - 1
    assert g.codes_for("Sundance Spas", "Optima 880") == []  # the dropped edge was the HAS_CODE edge
    assert g.family_of("Sundance Spas", "Optima 880") == "880 Series"
    logged = [json.loads(line) for line in kgmod.drops_path(bad).read_text(encoding="utf-8").splitlines()]
    assert [(d["at"], d["kind"], d["reason"]) for d in logged] == [("load", HAS_CODE, "evidence_sha256")]
    KnowledgeGraph.load(bad)  # loading again logs nothing new
    assert len(kgmod.drops_path(bad).read_text(encoding="utf-8").splitlines()) == 1


def test_has_model_ignores_incoming_edges() -> None:
    """Mutation kg_has_model_counts_incoming: the successor end of a SUPERSEDED_BY
    edge counts as a known model (route would then top up instead of row 5)."""
    g = KnowledgeGraph()
    old, new = g.add_model("Acme", "X1"), g.add_model("Acme", "X2")
    g.add_edge(kgmod.SUPERSEDED_BY, old, new,
               **fields("Synthetic note: the Acme X1 is replaced by the Acme X2.", OPTIMA_URL))
    assert g.has_model("Acme", "X1") is True
    assert g.has_model("Acme", "X2") is False


def test_has_code_target_must_be_a_token_in_the_page() -> None:
    """Mutation kg_has_code_token_unchecked: add_edge stores a HAS_CODE edge
    whose quote was cut off inside a longer code ("shows E1" from "shows E10")."""
    page_text = "Service manual (synthetic). When the panel shows E10 the heater has tripped. Reset it."
    g = KnowledgeGraph()
    m = g.add_model("Acme", "X1")
    cut = "Service manual (synthetic). When the panel shows E1"
    with pytest.raises(EdgeRejected, match="target_not_a_token"):
        g.add_edge(HAS_CODE, m, g.add_code("Acme", "X1", "E1"), page_text=page_text, **fields(cut, OPTIMA_URL))
    whole = "When the panel shows E10 the heater has tripped."
    g.add_edge(HAS_CODE, m, g.add_code("Acme", "X1", "E10"), page_text=page_text, **fields(whole, OPTIMA_URL))
    assert g.codes_for("Acme", "X1") == ["E10"]


def test_edge_page_hash_round_trips_and_source_keeps_first_fetch(tmp_path: Path) -> None:
    """Mutations: kg_load_drops_edge_text_sha (load forgets an edge's own page
    hash); kg_add_source_overwrites (a later fetch re-dates the Source node)."""
    g = build_optima_flo()
    g.add_source(OPTIMA_URL, host="example.com", title="Synthetic Optima 880 owner guide",
                 retrieved_at="2027-01-01T00:00:00+00:00", text_sha256="c" * 64, tier="dealer")
    assert g.source_for(OPTIMA_URL)["retrieved_at"] == RETRIEVED
    assert g.source_for(OPTIMA_URL)["text_sha256"] == evidence_sha256(page("optima_880_synthetic_manual.txt"))
    successor = g.add_model("Sundance Spas", "Optima 990")
    g.add_edge(kgmod.SUPERSEDED_BY, model_key("Sundance Spas", "Optima 880"), successor, text_sha256="d" * 64,
               **fields("Synthetic note: the Optima 880 was replaced by the Optima 990.", OPTIMA_URL))
    path = tmp_path / "g.json"
    g.save(path)
    [up] = KnowledgeGraph.load(path).superseded_by("Sundance Spas", "Optima 880")
    assert up["text_sha256"] == "d" * 64


def test_graph_stats_reports_load_drops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation cli_graph_stats_hides_load_drops: `advisor graph stats` leaves out
    the saved edges a load dropped (PLAN 8.13: stats report dropped edges)."""
    from agent import cli, config

    tampered = json.loads(OPTIMA_FLO.read_text(encoding="utf-8"))
    tampered["edges"][0]["evidence_sha256"] = "0" * 64
    replay_graph = tmp_path / "replay_graph.json"
    replay_graph.write_text(json.dumps(tampered), encoding="utf-8")
    monkeypatch.setattr(config, "GRAPH_PATH", tmp_path / "graph.json")
    monkeypatch.setattr(config, "REPLAY_GRAPH_PATH", replay_graph)
    monkeypatch.setattr(config, "REGISTRY_PATH", tmp_path / "no_registry.sqlite")
    lines = cli.graph_stats_lines()
    replay_block = "\n".join(lines[lines.index(f"replay graph (replay runs): {replay_graph}"):])
    assert "saved edges dropped at load (failed a check; see the drop log): 1" in replay_block
    assert "edges dropped for failed evidence: 1" in replay_block
    assert "by reason: evidence_sha256 1" in replay_block
