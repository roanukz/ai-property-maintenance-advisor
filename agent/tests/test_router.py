"""The route table and the route classifier (PLAN 8.4; section 5 row SC7a).

route runs as a plain function against a temp registry, a temp ledger, a
knowledge graph built in the test, and a ReplayChatModel classifier scripted
through a small fake cassette. Every graph edge here is synthetic, labeled
synthetic, and sits on an example.com URL (decision 15). Each test names the
mutation that turns it red.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.runtime import Runtime

from agent import config
from agent.ledger import Ledger
from agent.nodes import graph_lookup as gl
from agent.nodes import route as route_mod
from agent.nodes.classify import classify_symptom
from agent.kg import KnowledgeGraph, drops_path
from agent.nodes.route import route
from agent.registry import Registry, open_registry
from agent.state import RunContext, check_json_native

# ---------------------------------------------------------------------------
# Synthetic graph fixtures (shared with test_graph_lookup_node.py)
# ---------------------------------------------------------------------------

FIXTURE_GRAPH = Path(__file__).resolve().parent / "fixtures" / "graphs" / "optima_flo.json"
SYNTHETIC = "synthetic test fixture: invented evidence text on example.com URLs (decision 15)"
MAKER = "Sundance Spas"
MODEL = "Optima 880"
FAMILY = "880 Series"
SUCCESSOR = "Optima 990"
IDENTITY = {"manufacturer": MAKER, "model": MODEL, "serial": "", "manufacture_date": ""}
FLO_URL = "https://manuals.example.com/synthetic/optima-880-error-codes"
OH_URL = "https://dealer.example.com/synthetic/880-series-overheat"
UPGRADE_URL = "https://manuals.example.com/synthetic/optima-880-successor"
FAMILY_URL = "https://manuals.example.com/synthetic/880-series-overview"
RETRIEVED_AT = "2026-06-01T12:00:00+00:00"
SOURCE_RETRIEVED_AT = "2026-05-30T09:00:00+00:00"
FLO_TITLE = "Synthetic Optima 880 error code table"
FLO_EVIDENCE = "Synthetic manual text: FLO means the heater sees low water flow; clean the filter."
OH_EVIDENCE = "Synthetic dealer text: code OH means the water is too hot; remove the cover to cool it."
UPGRADE_EVIDENCE = "Synthetic manual text: the Optima 880 is replaced by the Optima 990 in current models."
FAMILY_EVIDENCE = "Synthetic manual text: the Optima 880 is one of the 880 Series spas."
USAGE = {"input_tokens": 300, "output_tokens": 10}

# Parts a test graph can hold. "flo": HAS_CODE FLO on the model; "oh_family":
# HAS_CODE OH on the family; "upgrade": SUPERSEDED_BY; "family_only": only the
# IN_FAMILY edge. The "_no_evidence" and "_bad_hash" parts are put straight
# into the NetworkX graph, as a hand edited or damaged file would hold them,
# because add_edge refuses them; saved to disk, load drops them and logs the
# drop.
PARTS = ("flo", "oh_family", "upgrade", "family_only", "flo_no_evidence", "oh_family_no_evidence",
         "flo_bad_hash")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fields(url: str, evidence: str, retrieved_at: str = RETRIEVED_AT) -> dict[str, str]:
    return {"source_url": url, "retrieved_at": retrieved_at, "evidence": evidence,
            "evidence_sha256": sha(evidence), "brief_run_id": "run-synthetic-0001",
            "validated_at": "2026-06-01T12:05:00+00:00"}


def build_kg(parts: list[str] | tuple[str, ...], *, upgrade_retrieved_at: str = RETRIEVED_AT) -> tuple[KnowledgeGraph, bool]:
    """(graph, damaged): a synthetic graph for the Optima 880 holding the named parts.

    damaged is True when a part was put in without add_edge, so a load of the
    saved file drops that edge.
    """
    assert set(parts) <= set(PARTS), parts
    kg = KnowledgeGraph(meta={"synthetic": SYNTHETIC})
    m = kg.add_model(MAKER, MODEL)
    kg.add_source(FLO_URL, host="manuals.example.com", title=FLO_TITLE, retrieved_at=SOURCE_RETRIEVED_AT,
                  text_sha256=sha("synthetic page text"), tier="manufacturer")
    damaged = False
    f = kg.add_family(MAKER, FAMILY)
    if set(parts) & {"flo", "oh_family", "upgrade", "family_only"}:
        kg.add_edge("IN_FAMILY", m, f, **_fields(FAMILY_URL, FAMILY_EVIDENCE))
    if "flo" in parts:
        kg.add_edge("HAS_CODE", m, kg.add_code(MAKER, MODEL, "FLO"), **_fields(FLO_URL, FLO_EVIDENCE))
    if "oh_family" in parts:
        kg.add_edge("HAS_CODE", f, kg.add_code(MAKER, FAMILY, "OH"), **_fields(OH_URL, OH_EVIDENCE))
    if "upgrade" in parts:
        kg.add_edge("SUPERSEDED_BY", m, kg.add_model(MAKER, SUCCESSOR),
                    **_fields(UPGRADE_URL, UPGRADE_EVIDENCE, upgrade_retrieved_at))
    for part, src, scope, code, url, evidence in (
        ("flo_no_evidence", m, MODEL, "FLO", FLO_URL, ""),
        ("oh_family_no_evidence", "family", FAMILY, "OH", OH_URL, ""),
        ("flo_bad_hash", m, MODEL, "FLO", FLO_URL, FLO_EVIDENCE),
    ):
        if part not in parts:
            continue
        src_key = kg.add_family(MAKER, FAMILY) if src == "family" else src
        fields = {**_fields(url, evidence), "evidence_sha256": sha("some other text") if evidence else ""}
        kg.g.add_edge(src_key, kg.add_code(MAKER, scope, code), key=f"damaged-{part}", kind="HAS_CODE", **fields)
        damaged = True
    return kg, damaged


def install_graph(ctx: RunContext, monkeypatch: pytest.MonkeyPatch, parts: list[str] | tuple[str, ...],
                  *, in_memory: bool = False, **kw: Any) -> bool:
    """Save the graph at ctx.graph_path, so route and graph_lookup load it for real; returns damaged.

    With `in_memory`, the graph is handed to route and graph_lookup directly,
    damaged edges and all, which tests the query level check that is the
    second line of defense behind the lenient load.
    """
    kg, damaged = build_kg(parts, **kw)
    if in_memory:
        monkeypatch.setattr(gl, "open_knowledge_graph", lambda path: kg)
        monkeypatch.setattr(route_mod, "open_knowledge_graph", lambda path: kg)
    else:
        kg.save(ctx.graph_path)
    return damaged


# ---------------------------------------------------------------------------
# Context, registry and classifier script
# ---------------------------------------------------------------------------


class ClassifierCassette:
    """The one cassette method models.py reads, scripting only the classifier."""

    def __init__(self, verdicts: list[str | dict]) -> None:
        self.script = [v if isinstance(v, dict) else {"structured": {"verdict": v}, "usage": USAGE}
                       for v in verdicts]

    def responses_for(self, node: str) -> list[dict]:
        return list(self.script) if node == "classifier" else []


def make_ctx(tmp_path: Path, verdicts: list[str | dict] = (), caps: dict | None = None) -> RunContext:
    return RunContext(run_id="run-route", mode="replay", ledger_path=tmp_path / "ledger" / "replay.sqlite",
                      registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                      pages_dir=tmp_path / "pages", cassette=ClassifierCassette(list(verdicts)), caps=caps)


def classifier_calls(ctx: RunContext) -> int:
    model = ctx.replay.get("models", {}).get("classifier")
    return 0 if model is None else len(model.received)


EMPTY_APPLIANCE_SEED = {
    "_about": "synthetic test seed: one appliance with no records, dates or warranty terms",
    "properties": [{"id": "prop-t", "label": "Synthetic property T", "synthetic": True}],
    "appliances": [{"id": "appl-bare", "property_id": "prop-t", "category": "dehumidifier",
                    "manufacturer": "Synthetic Bare Co.", "model": "SB-1 (synthetic)",
                    "synthetic": True}],
    "service_records": [],
    "maintenance_log": [],
}


def seed_registry(path: Path) -> Registry:
    """The default synthetic seed plus one appliance that has no records at all."""
    registry = open_registry(path)
    extra = path.with_name("bare_seed.json")
    extra.write_text(json.dumps(EMPTY_APPLIANCE_SEED), encoding="utf-8")
    registry.load_seed(extra)
    return registry


# ---------------------------------------------------------------------------
# The PLAN 8.4 route table
# ---------------------------------------------------------------------------

# (id, graph parts, observed_code, candidate, appliance_id, verdicts,
#  expected route, expected limits, expected row, classifier calls)
TABLE = [
    ("row1_code_on_model", ["flo", "oh_family"], "FLO", None, None, [],
     ["graph"], None, 1, 0),
    ("row1_code_on_family", ["flo", "oh_family"], "OH", None, None, [],
     ["graph"], None, 1, 0),
    ("row1_code_as_typed_lower_case", ["flo"], "flo", None, None, [],
     ["graph"], None, 1, 0),
    ("row1_with_history", ["flo"], "FLO", None, "appl-optima880", [],
     ["history", "graph"], None, 1, 0),
    ("row2_code_not_in_graph", ["flo"], "HL", None, None, [],
     ["graph", "research"], "top_up", 2, 0),
    ("row2_code_edge_without_evidence", ["flo", "oh_family_no_evidence"], "OH", None, None, [],
     ["graph", "research"], "top_up", 2, 0),
    ("row2_model_known_by_upgrade_only", ["upgrade"], "FLO", None, None, [],
     ["graph", "research"], "top_up", 2, 0),
    ("row3_classifier_match", ["flo"], None, None, None, ["match"],
     ["graph"], None, 3, 1),
    ("row3_candidate_code_is_not_confirmed", ["flo"], None, "FLO", None, ["match"],
     ["graph"], None, 3, 1),
    ("row4_classifier_no_match", ["flo"], None, None, None, ["no_match"],
     ["graph", "research"], "top_up", 4, 1),
    ("row4_classifier_unsure_with_history", ["flo"], None, None, "appl-optima880", ["unsure"],
     ["history", "graph", "research"], "top_up", 4, 1),
    ("row4_no_documented_cause_skips_classifier", ["family_only"], None, None, None, [],
     ["graph", "research"], "top_up", 4, 0),
    ("row5_empty_graph", [], "FLO", None, None, [],
     ["research"], "main", 5, 0),
    ("row5_empty_graph_no_code", [], None, None, None, [],
     ["research"], "main", 5, 0),
    ("row5_only_edge_lacks_evidence", ["flo_no_evidence"], "FLO", None, None, [],
     ["research"], "main", 5, 0),
    ("row5_only_edge_lacks_evidence_no_code", ["flo_no_evidence"], None, None, None, [],
     ["research"], "main", 5, 0),
    ("row5_evidence_hash_mismatch", ["flo_bad_hash"], "FLO", None, None, [],
     ["research"], "main", 5, 0),
    ("row5_with_history", [], None, None, "appl-optima880", [],
     ["history", "research"], "main", 5, 0),
    ("row5_appliance_without_records", [], None, None, "appl-bare", [],
     ["research"], "main", 5, 0),
]


@pytest.mark.parametrize(
    "parts, code, candidate, appliance_id, verdicts, want_route, want_limits, row, calls",
    [case[1:] for case in TABLE], ids=[case[0] for case in TABLE],
)
def test_route_decision_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parts, code, candidate,
                              appliance_id, verdicts, want_route, want_limits, row, calls) -> None:
    """Every row of PLAN 8.4, history added when the appliance has records.

    Mutations: route_ignores_graph (every run routed to research),
    route_row1_ignores_code_edges, route_row2_without_top_up,
    route_classifier_always_called, route_classifier_verdict_ignored,
    route_classifier_without_causes, kg_unverified_edge_counts (an edge
    without evidence counted as coverage), kg_hash_unchecked,
    route_candidate_code_counts (the symptom's guessed code picks a row),
    route_history_never_added, route_history_always_added,
    route_reason_without_row, kg_load_lenient_reraises (a damaged saved edge
    fails the run at load instead of being dropped).

    The graph is saved to ctx.graph_path and loaded for real, so a damaged
    edge goes through the lenient load: it is dropped and logged, and the
    route is what the rest of the graph supports.
    """
    seed_registry(tmp_path / "registry.sqlite")
    ctx = make_ctx(tmp_path, verdicts)
    damaged = install_graph(ctx, monkeypatch, parts)
    state = {"identity": IDENTITY, "symptom": "the heater will not run", "observed_code": code,
             "observed_code_candidate": candidate, "appliance_id": appliance_id}
    out = route(state, Runtime(context=ctx))
    check_json_native(out)
    assert out["route"] == want_route
    assert out["research_limits"] == want_limits
    assert f"Route table row {row}:" in out["route_reason"]
    assert ("History is added" in out["route_reason"]) == ("history" in want_route)
    assert classifier_calls(ctx) == calls
    if want_limits is not None:
        assert want_limits in config.RESEARCH_LIMITS
    assert drops_path(ctx.graph_path).is_file() == damaged


DAMAGED = [case for case in TABLE if set(case[1]) & {"flo_no_evidence", "oh_family_no_evidence", "flo_bad_hash"}]


@pytest.mark.parametrize(
    "parts, code, candidate, appliance_id, verdicts, want_route, want_limits, row, calls",
    [case[1:] for case in DAMAGED], ids=[case[0] for case in DAMAGED],
)
def test_query_level_check_on_in_memory_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parts, code,
                                              candidate, appliance_id, verdicts, want_route, want_limits, row,
                                              calls) -> None:
    """The damaged rows again, with the damaged edges held in memory past the load.

    Mutations: router_kg_unverified_edge_counts (an edge without evidence
    counted as coverage), router_kg_hash_unchecked (an edge whose evidence does
    not hash passes).
    """
    seed_registry(tmp_path / "registry.sqlite")
    ctx = make_ctx(tmp_path, verdicts)
    assert install_graph(ctx, monkeypatch, parts, in_memory=True)
    state = {"identity": IDENTITY, "symptom": "the heater will not run", "observed_code": code,
             "observed_code_candidate": candidate, "appliance_id": appliance_id}
    out = route(state, Runtime(context=ctx))
    assert (out["route"], out["research_limits"]) == (want_route, want_limits)
    assert f"Route table row {row}:" in out["route_reason"]


def test_damaged_graph_file_falls_back_to_research(tmp_path: Path) -> None:
    """Mutation kg_load_lenient_reraises: a saved edge that fails a check raises
    at load, so route crashes before research instead of taking row 5.

    The fixture graph's only HAS_CODE edge is damaged on disk twice over: its
    evidence and hash blanked, then its hash replaced. Each time route takes
    row 5 and the drop is logged beside the graph with its reason.
    """
    fixture = json.loads(FIXTURE_GRAPH.read_text(encoding="utf-8"))
    identity = {"manufacturer": MAKER, "model": MODEL}
    for label, edit, reason in (
        ("blanked", {"evidence": "", "evidence_sha256": ""}, "missing_fields"),
        ("bad_hash", {"evidence_sha256": "0" * 64}, "evidence_sha256"),
    ):
        ctx = make_ctx(tmp_path / label)
        data = json.loads(json.dumps(fixture))
        [code_edge] = [e for e in data["edges"] if e["kind"] == "HAS_CODE"]
        code_edge.update(edit)
        Path(ctx.graph_path).parent.mkdir(parents=True, exist_ok=True)
        Path(ctx.graph_path).write_text(json.dumps(data), encoding="utf-8")
        out = route({"identity": identity, "observed_code": "FLO", "symptom": "x"}, Runtime(context=ctx))
        # The IN_FAMILY edge survives, so the model is known: row 2, a top up.
        assert out["route"] == ["graph", "research"] and out["research_limits"] == "top_up", label
        [drop] = [json.loads(line) for line in drops_path(ctx.graph_path).read_text(encoding="utf-8").splitlines()]
        assert (drop["at"], drop["kind"], drop["reason"]) == ("load", "HAS_CODE", reason), label


@pytest.mark.parametrize("code", ["FLO", None], ids=["code", "no_code"])
def test_row5_model_known_only_as_successor(tmp_path: Path, code: str | None) -> None:
    """Mutation kg_has_model_counts_incoming: a model the graph holds only as the
    successor end of another model's SUPERSEDED_BY edge counts as known, so it
    takes row 2 or row 4 with the top up limits although the graph documents
    nothing about it (PLAN 8.4 row 5)."""
    ctx = make_ctx(tmp_path, ["match"])
    build_kg(["upgrade"])[0].save(ctx.graph_path)
    successor = {"manufacturer": MAKER, "model": SUCCESSOR}
    out = route({"identity": successor, "observed_code": code, "symptom": "x"}, Runtime(context=ctx))
    assert out["route"] == ["research"] and out["research_limits"] == "main"
    assert out["route_reason"].startswith("Route table row 5:")
    assert classifier_calls(ctx) == 0


def test_route_without_identity_goes_to_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation route_no_identity_guard: a missing identity is read as if it were a dict.

    Another maker's model, or no identity at all, is never graph coverage.
    """
    ctx = make_ctx(tmp_path)
    install_graph(ctx, monkeypatch, ["flo"])
    other = {"manufacturer": "Synthetic Other Co.", "model": "SO-9 (synthetic)"}
    for identity in (other, None, {"manufacturer": MAKER, "model": "   "}):
        out = route({"identity": identity, "observed_code": "FLO", "symptom": "x"}, Runtime(context=ctx))
        assert out["route"] == ["research"], identity


def test_route_reads_the_graph_at_the_run_context_path(tmp_path: Path) -> None:
    """Mutation route_graph_path_ignored: route opens config.GRAPH_PATH instead of ctx.graph_path
    (a replay run would then read the runtime graph, decision 37)."""
    ctx = make_ctx(tmp_path)
    build_kg(["flo"])[0].save(ctx.graph_path)
    out = route({"identity": IDENTITY, "observed_code": "FLO", "symptom": "x"}, Runtime(context=ctx))
    assert out["route"] == ["graph"]


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------


def test_classifier_is_charged_through_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: classify_skips_ledger (the model is invoked directly, no reserve or charge);
    route_drops_classifier_cost (route does not return the classifier's cost_usd delta)."""
    ctx = make_ctx(tmp_path, ["no_match"])
    install_graph(ctx, monkeypatch, ["flo"])
    out = route({"identity": IDENTITY, "symptom": "jets are weak"}, Runtime(context=ctx))
    rows = [r for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["node"] == "classifier"]
    assert [r["kind"] for r in rows] == ["reserve", "charge", "release"]
    charge = next(r for r in rows if r["kind"] == "charge")
    assert (charge["input_tokens"], charge["output_tokens"]) == (USAGE["input_tokens"], USAGE["output_tokens"])
    assert charge["usd"] > 0
    assert out["cost_usd"] == pytest.approx(Ledger(ctx.ledger_path).run_total(ctx.run_id))


def test_classifier_max_tokens_from_config(tmp_path: Path) -> None:
    """Mutation classify_wrong_node: the classifier call is made under another node's name,
    so it takes that node's model script and max_tokens."""
    ctx = make_ctx(tmp_path, ["match"])
    result = classify_symptom(ctx, identity=IDENTITY, symptom="jets are weak",
                              documented=[{"code": "FLO", "evidence": FLO_EVIDENCE}])
    assert result.verdict == "match"
    model = ctx.replay["models"]["classifier"]
    assert model.max_tokens == config.MAX_TOKENS["classifier"]
    reserve = next(r for r in Ledger(ctx.ledger_path).rows(ctx.run_id) if r["kind"] == "reserve")
    assert (reserve["node"], reserve["output_tokens"]) == ("classifier", config.MAX_TOKENS["classifier"])
    assert reserve["model"] == config.MODEL_FOR["replay"]["classifier"]


def test_classifier_sees_the_documented_causes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation route_classifier_blind: the classifier is sent no documented causes, so its
    verdict cannot be about what the graph holds."""
    ctx = make_ctx(tmp_path, ["match"])
    install_graph(ctx, monkeypatch, ["flo", "oh_family"])
    route({"identity": IDENTITY, "symptom": "jets are weak"}, Runtime(context=ctx))
    sent = ctx.replay["models"]["classifier"].received[0][-1].content
    assert FLO_EVIDENCE in sent and OH_EVIDENCE in sent
    assert "jets are weak" in sent


@pytest.mark.parametrize("reply", [
    {"structured": {"verdict": "unsure"}, "usage": USAGE},
    {"message": {"content": "not json", "tool_calls": [], "usage": USAGE}},
], ids=["unsure", "unparseable"])
def test_classifier_unsure_maps_to_top_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: dict) -> None:
    """Mutations: route_unsure_is_graph (unsure treated as a match); classify_parse_error_is_match."""
    ctx = make_ctx(tmp_path, [reply])
    install_graph(ctx, monkeypatch, ["flo"])
    out = route({"identity": IDENTITY, "symptom": "jets are weak"}, Runtime(context=ctx))
    assert out["route"] == ["graph", "research"]
    assert out["research_limits"] == "top_up"
    assert "Route table row 4:" in out["route_reason"] and "unsure" in out["route_reason"]


def test_unaffordable_classifier_is_unsure_and_spends_nothing(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation classify_budget_exceeded_is_match: a refused reservation maps to match (graph
    only, no research), which would answer from the graph unchecked when the budget is gone."""
    ctx = make_ctx(tmp_path, ["match"], caps={"run_cap_usd": 0.0})
    install_graph(ctx, monkeypatch, ["flo"])
    out = route({"identity": IDENTITY, "symptom": "jets are weak"}, Runtime(context=ctx))
    assert out["route"] == ["graph", "research"]
    assert "not affordable" in out["route_reason"]
    assert "cost_usd" not in out
    assert classifier_calls(ctx) == 0
    assert Ledger(ctx.ledger_path).run_total(ctx.run_id) == 0
