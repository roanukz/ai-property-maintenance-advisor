"""persist: verified edges only, idempotent, and never into the runtime graph from replay.

PLAN section 5 rows SC6 (whole graph) and replay graph isolation; section
8.11; decision 37. Runs replay synthetic cassettes (every field synthetic, page
text only on example.com URLs) through the real graph. Each test names the
mutation that turns it red.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

import pytest

from agent import config
from agent.kg import HAS_CODE, IN_FAMILY, KnowledgeGraph, code_key, drops_path, family_key, model_key
from agent.ledger import Ledger
from agent.nodes import persist as persist_mod
from agent.nodes.persist import persist_edges, persist_run, run_record_path
from agent.registry import Registry
from agent.research.tools import save_page_text
from agent.rules.evidence import NOT_IN_PAGE, TARGET_NOT_TOKEN, evidence_sha256
from agent.state import RunContext
from agent.tests.helpers import GUARD_MARKER, SYNTHETIC_CASSETTE_DIR, load_case, spawn_offline_child
from agent.tests.test_sc8_history import FIXTURE_GRAPH, FIXTURE_PAGES, final_state, run_phase3_case, variant

MANUAL_URL = "https://example.com/synthetic/sundance/optima-880-manual"
FLO_EVIDENCE = ("FLO: The heater senses no water flow. Clean or replace the filter cartridge, "
                "then check that the circulation pump runs.")
# Reworded from the synthetic manual ("20 degrees"), so it is not a verbatim span.
COOL_REWORDED = "COOL: Water is twenty degrees below the set point."


def _three_codes(data: dict) -> None:
    """No confirmed code, so all three candidates stay: one verbatim quote, one reworded, one without a quote."""
    data["input"]["symptom"] = "heater is not warming the water"
    data["resume"] = None
    draft = data["synthesize"][0]["draft"]
    draft["happened_before"] = None
    draft["warranty"] = None
    flo = draft["candidates"][0]
    draft["candidates"] = [
        flo,
        {**flo, "code": "COOL", "documented_meaning": "Water is below the set point.", "evidence": COOL_REWORDED},
        {**flo, "code": "ICE", "documented_meaning": "Water is near freezing.", "evidence": ""},
    ]


def test_persist_writes_only_verified_edges(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Mutations: persist_no_span_check (persist checks each quote against
    itself instead of the page text, so the reworded edge is written, or the
    run fails when the graph refuses it); persist_ok_check_off (a brief that
    is not ok still writes edges); persist_not_idempotent (a second persist of
    the same thread logs its drops again)."""
    cassette = variant(load_case("synthetic/optima_history"), "three codes", _three_codes)
    with caplog.at_level(logging.INFO, logger="agent.nodes.persist"):
        _, ctx, outcomes = run_phase3_case(cassette, tmp_path / "run")
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    assert [c["code"] for c in state["brief"]["candidates"]] == ["FLO", "COOL", "ICE"]
    assert state["grounding_status"] == "verified"

    report = state["persist_report"]["graph_edges"]
    assert report["target"] == str(ctx.graph_path) and report["skipped"] is None
    assert report["written"] == 1
    assert sorted((d["code"], d["reason"]) for d in report["dropped"]) == [
        ("COOL", NOT_IN_PAGE), ("ICE", persist_mod.DROP_NO_EVIDENCE)]
    assert "edge dropped: HAS_CODE COOL" in caplog.text

    saved = json.loads(Path(ctx.graph_path).read_text(encoding="utf-8"))
    [edge] = saved["edges"]
    assert edge["kind"] == HAS_CODE and edge["evidence"] == FLO_EVIDENCE
    assert edge["src"] == model_key("Sundance Spas", "Optima 880")
    assert edge["dst"] == code_key("Sundance Spas", "Optima 880", "FLO")
    assert edge["brief_run_id"] == ctx.run_id
    search_source = next(s for s in state["sources"] if s["url"] == MANUAL_URL)
    assert edge["retrieved_at"] == search_source["retrieved_at"]
    assert "verified" not in edge and "COOL" not in json.dumps(saved) and "ICE" not in json.dumps(saved["edges"])
    graph = KnowledgeGraph.load(ctx.graph_path)
    full = graph.reverify(ctx.pages_dir)
    assert full["failures"] == [] and full["full"] == 1

    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["graph_edges"] == report
    drops = drops_path(ctx.graph_path).read_text(encoding="utf-8").splitlines()
    assert len(drops) == 2

    # Idempotent by thread: persisting the same thread again changes nothing.
    graph_bytes = Path(ctx.graph_path).read_bytes()
    again = persist_run(state, ctx, "thread-1")
    assert again["graph_edges"]["written"] == 0 and again["graph_edges"]["already_present"] == 1
    assert Path(ctx.graph_path).read_bytes() == graph_bytes
    assert drops_path(ctx.graph_path).read_text(encoding="utf-8").splitlines() == drops
    assert len([r for r in Registry(ctx.registry_path).lookups(run_id=ctx.run_id)]) == 1

    # Only an ok brief writes edges.
    other = RunContext(**{**ctx.__dict__, "graph_path": tmp_path / "other_graph.json"})
    refused = {**state, "status": "no_reliable_answer", "brief": {**state["brief"], "status": "no_reliable_answer"}}
    out = persist_edges(refused, other, "thread-2")
    assert out["written"] == 0 and out["skipped"] == persist_mod.SKIP_NOT_OK
    assert not (tmp_path / "other_graph.json").exists()


def _live_charge(ledger_path: Path, run_id: str) -> None:
    ledger = Ledger(ledger_path, create=True)
    hold = ledger.reserve(run_id, mode="cheap", node="synthesize", model=config.HAIKU,
                          input_tokens_est=100, max_tokens=100)
    ledger.charge(hold, {"input_tokens": 100, "output_tokens": 10})


def test_edges_go_where_decision_37_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: persist_replay_writes_runtime_graph (a replay context pointed
    at config.GRAPH_PATH writes it); persist_live_without_charges (a live mode
    run with no live ledger charge writes the runtime graph);
    persist_replay_writes_seed (a replay context pointed under seed/ writes
    there); persist_live_writes_other_graph (a live run that reads one graph
    writes config.GRAPH_PATH anyway)."""
    cassette = variant(load_case("synthetic/optima_history"), "three codes", _three_codes)
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path / "run")
    state = final_state(outcomes)
    runtime_graph = tmp_path / "data" / "graph.json"
    monkeypatch.setattr(config, "GRAPH_PATH", runtime_graph)

    replay_on_live = RunContext(**{**ctx.__dict__, "graph_path": runtime_graph})
    out = persist_edges(state, replay_on_live, "t")
    assert out["target"] is None and out["skipped"] == persist_mod.SKIP_REPLAY_ON_LIVE_GRAPH
    assert not runtime_graph.exists()

    seed_dir = tmp_path / "seed"
    monkeypatch.setattr(config, "SEED_DIR", seed_dir)
    replay_on_seed = RunContext(**{**ctx.__dict__, "graph_path": seed_dir / "graph.json"})
    out = persist_edges(state, replay_on_seed, "t")
    assert out["target"] is None and out["skipped"] == persist_mod.SKIP_REPLAY_ON_SEED
    assert not seed_dir.exists()

    live = RunContext(**{**ctx.__dict__, "mode": "cheap", "run_id": "live-1",
                         "ledger_path": tmp_path / "live_ledger.sqlite", "graph_path": runtime_graph})
    Ledger(live.ledger_path, create=True)
    out = persist_edges({**state, "run_id": "live-1"}, live, "t-live")
    assert out["target"] is None and out["skipped"] == persist_mod.SKIP_NO_LIVE_CHARGES
    assert not runtime_graph.exists()

    _live_charge(live.ledger_path, "live-1")
    elsewhere = RunContext(**{**live.__dict__, "graph_path": tmp_path / "eval_graph.json"})
    out = persist_edges({**state, "run_id": "live-1"}, elsewhere, "t-live")
    assert out["target"] is None and out["skipped"] == persist_mod.SKIP_LIVE_PATH_MISMATCH
    assert not runtime_graph.exists() and not (tmp_path / "eval_graph.json").exists()

    out = persist_edges({**state, "run_id": "live-1"}, live, "t-live")
    assert out["target"] == str(runtime_graph) and out["written"] == 1
    [edge] = KnowledgeGraph.load(runtime_graph).edges()
    assert edge["brief_run_id"] == "live-1"


def _advisor(args: list[str], data_dir: Path, cassette: Path):
    return spawn_offline_child(
        ["-m", "agent.cli", *args], kind="python",
        env_extra={"ADVISOR_DATA_DIR": str(data_dir), config.ENV_CASSETTE: str(cassette),
                   config.ENV_MODE: None, config.ENV_GATE: None},
    )


def test_cli_replay_leaves_runtime_graph_unchanged(tmp_path: Path) -> None:
    """Mutations: persist_ignores_mode (replay writes edges into
    config.GRAPH_PATH); cli_replay_uses_runtime_graph (the CLI gives a replay
    run config.GRAPH_PATH, so the replay graph never grows)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime_graph = data_dir / "graph.json"
    shutil.copy(FIXTURE_GRAPH, runtime_graph)  # a temp copy standing for data/graph.json
    before = runtime_graph.read_bytes()
    cassette = SYNTHETIC_CASSETTE_DIR / "optima_history.json"

    ask = _advisor(["ask", "--symptom", "panel shows FLO", "--appliance", "appl-optima880", "--code", "FLO"],
                   data_dir, cassette)
    assert ask.returncode == 0, ask.stderr
    assert GUARD_MARKER in ask.stderr
    assert "status: ok" in ask.stdout, ask.stdout
    assert "  ran: persist" in ask.stdout

    assert runtime_graph.read_bytes() == before
    replay_graph = data_dir / "replay_graph.json"
    [edge] = KnowledgeGraph.load(replay_graph).edges()
    assert edge["kind"] == HAS_CODE and edge["source_url"] == MANUAL_URL
    thread = re.search(r"^advisor ask: thread (\S+) ", ask.stdout, re.MULTILINE).group(1)
    record = json.loads((data_dir / "runs" / f"{thread}.json").read_text(encoding="utf-8"))
    assert record["graph_edges"]["target"] == str(replay_graph) and record["graph_edges"]["written"] == 1

    stats = _advisor(["graph", "stats"], data_dir, cassette)
    assert stats.returncode == 0, stats.stderr
    replay_block = stats.stdout.split("replay graph (replay runs):", 1)[1]
    assert "edges: HAS_CODE 1" in replay_block
    assert "edges dropped for failed evidence: 0" in replay_block
    assert "edges: HAS_CODE 1, IN_FAMILY 2" in stats.stdout.split("replay graph (replay runs):", 1)[0]
    assert runtime_graph.read_bytes() == before


# ---------------------------------------------------------------------------
# Edge candidates from a hand built ok brief (every page text synthetic, on
# example.com URLs, decision 15)
# ---------------------------------------------------------------------------

MANUAL_TEXT = (FIXTURE_PAGES / "optima_880_synthetic_manual.txt").read_bytes().decode("utf-8")
DEALER_URL = "https://dealer.example.com/synthetic/optima-880-panel-codes"
DEALER_TEXT = ("SYNTHETIC PAGE TEXT for tests (not a real document).\n"
               "Dealer notes: HL: the high limit sensor tripped; let the water cool before a reset.\n")
BULLETIN_URL = "https://example.com/synthetic/acme/service-bulletin"
BULLETIN_TEXT = ("Service manual (synthetic). When the panel shows E10 the heater has tripped. "
                 "Reset the breaker.")
COOL_VERBATIM = "COOL: Water is 20 degrees below the set point."
SIBLING = "Optima 885 (synthetic)"


def unit_ctx(tmp_path: Path, graph: Path | None = None) -> RunContext:
    ctx = RunContext(run_id="run-unit", mode="replay", ledger_path=tmp_path / "ledger.sqlite",
                     registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "graph.json",
                     pages_dir=tmp_path / "pages")
    if graph is not None:
        shutil.copy(graph, ctx.graph_path)
    return ctx


def unit_state(ctx: RunContext, pages: dict[str, str], candidates: list[tuple[str, int, str]]) -> dict:
    """An ok state for the Optima 880 whose sources are `pages` (url -> text, found by search this run)."""
    sources, brief_sources = [], []
    for url, text in pages.items():
        sha = save_page_text(Path(ctx.pages_dir), text)
        sources.append({"url": url, "origin": "search", "host": url.split("/")[2],
                        "retrieved_at": "2026-09-18T10:00:00+00:00", "text_sha256": sha})
        brief_sources.append({"url": url, "host": url.split("/")[2], "title": "Synthetic page", "tier": "dealer"})
    brief = {"status": "ok", "observed_code": None, "sources": brief_sources,
             "candidates": [{"code": code, "source_index": index, "evidence": evidence}
                            for code, index, evidence in candidates]}
    return {"status": "ok", "brief": brief, "run_id": ctx.run_id, "sources": sources,
            "identity": {"manufacturer": "Sundance Spas", "model": "Optima 880"}}


def test_new_code_is_keyed_on_the_known_family(tmp_path: Path) -> None:
    """Mutation persist_code_keyed_on_model: a code found for a model whose family
    the graph knows is stored under the model, so sibling models never see it
    and FLO would be stored twice (PLAN 8.11: code:{maker}:{family or model})."""
    ctx = unit_ctx(tmp_path, graph=FIXTURE_GRAPH)
    state = unit_state(ctx, {MANUAL_URL: MANUAL_TEXT}, [("COOL", 0, COOL_VERBATIM), ("FLO", 0, FLO_EVIDENCE)])
    report = persist_edges(state, ctx, "thread-unit")
    assert (report["written"], report["already_present"], report["dropped"]) == (1, 1, [])
    g = KnowledgeGraph.load(ctx.graph_path, strict=True)
    series = family_key("Sundance Spas", "880 Series")
    [cool] = [e for e in g.edges() if e["dst"].endswith(":COOL")]
    assert (cool["src"], cool["dst"]) == (series, code_key("Sundance Spas", "880 Series", "COOL"))
    assert cool["text_sha256"] == evidence_sha256(MANUAL_TEXT)
    assert [e["kind"] for e in g.edges()].count(HAS_CODE) == 2
    # A sibling model in the same family finds the new code.
    sibling = g.add_model("Sundance Spas", SIBLING)
    note = "Synthetic dealer note: the Optima 885 (synthetic) is one of the 880 Series spas."
    g.add_edge(IN_FAMILY, sibling, series, source_url=DEALER_URL, retrieved_at="2026-09-18T00:00:00+00:00",
               evidence=note, evidence_sha256=evidence_sha256(note), brief_run_id="synthetic-run",
               validated_at="2026-09-18T00:00:00+00:00")
    assert g.codes_for("Sundance Spas", SIBLING) == ["COOL", "FLO"]


def test_evidence_must_come_from_the_cited_page(tmp_path: Path) -> None:
    """Mutation persist_evidence_any_source_text: the quote is checked against
    every page of the run joined together, so a quote from the dealer page is
    stored as an edge whose source_url is the manual."""
    ctx = unit_ctx(tmp_path)
    quote = "HL: the high limit sensor tripped; let the water cool before a reset."
    state = unit_state(ctx, {MANUAL_URL: MANUAL_TEXT, DEALER_URL: DEALER_TEXT}, [("HL", 0, quote)])
    report = persist_edges(state, ctx, "thread-unit")
    assert report["written"] == 0
    assert [(d["code"], d["source_url"], d["reason"]) for d in report["dropped"]] == [
        ("HL", MANUAL_URL, NOT_IN_PAGE)]
    assert not Path(ctx.graph_path).exists()
    # Cited against its own page, the same quote is written.
    state = unit_state(ctx, {MANUAL_URL: MANUAL_TEXT, DEALER_URL: DEALER_TEXT}, [("HL", 1, quote)])
    assert persist_edges(state, ctx, "thread-unit-2")["written"] == 1


def test_quote_cut_inside_a_longer_code_is_dropped(tmp_path: Path) -> None:
    """Mutation persist_target_token_unchecked: a quote cut off after "E1" in a
    page that prints "E10" passes the span check (the target is only a
    substring) and is written as HAS_CODE E1."""
    ctx = unit_ctx(tmp_path)
    cut = "Service manual (synthetic). When the panel shows E1"
    state = unit_state(ctx, {BULLETIN_URL: BULLETIN_TEXT}, [("E1", 0, cut)])
    report = persist_edges(state, ctx, "thread-unit")
    assert report["written"] == 0
    assert [(d["code"], d["reason"]) for d in report["dropped"]] == [("E1", TARGET_NOT_TOKEN)]


# ---------------------------------------------------------------------------
# Phase 4 edge kinds: SUPERSEDED_BY, PART_DISCONTINUED, CODE_POINTS_TO_PART and
# IN_FAMILY from a hand built ok brief for the synthetic appliance appl-sx100
# (agent/tests/fixtures/discontinued.json; example.com and example.org only)
# ---------------------------------------------------------------------------


def sx_state(ctx: RunContext, brief_sources: list[str], *, upgrades: list[dict], candidates: list[dict]) -> dict:
    """An ok state for the synthetic SX-100 whose sources are fixture pages found by search this run."""
    from agent.tests.test_sc9_upgrades import FIXTURE, page

    sources, cited = [], []
    for url in brief_sources:
        sha = save_page_text(Path(ctx.pages_dir), page(url))
        sources.append({"url": url, "origin": "search", "host": url.split("/")[2],
                        "retrieved_at": "2026-09-18T10:00:00+00:00", "text_sha256": sha})
        cited.append({"url": url, "host": url.split("/")[2], "title": "Synthetic page", "tier": "manufacturer"})
    brief = {"status": "ok", "observed_code": None, "sources": cited, "candidates": candidates,
             "upgrade_options": upgrades, "maintenance_due": []}
    return {"status": "ok", "brief": brief, "run_id": ctx.run_id, "sources": sources,
            "identity": dict(FIXTURE["identity"])}


def test_persist_writes_phase4_edge_kinds_only_when_verified(tmp_path: Path) -> None:
    """Mutations: persist_phase4_kinds_off (persist stops proposing the Phase 4
    edge kinds); persist_phase4_no_span_check (a Phase 4 quote is checked
    against itself instead of the cited page, so the reworded SX-300 quote is
    written); persist_part_token_is_model (the model's own number is taken as a
    discontinued part)."""
    from agent.kg import CODE_POINTS_TO_PART, PART_DISCONTINUED, SUPERSEDED_BY, part_key
    from agent.tests.test_sc9_upgrades import DEALER, GUIDE, MAKER, MODEL, NOTICE, PARTS

    ctx = unit_ctx(tmp_path)
    upgrades = [
        {"successor_manufacturer": MAKER, "successor_model": "SX-200", "reason": "discontinued", "source_index": 0,
         "summary": "s", "evidence": "The SX-100 (synthetic) is discontinued and is replaced by the SX-200."},
        {"successor_manufacturer": None, "successor_model": "SX-200", "reason": "parts_unavailable",
         "source_index": 1, "summary": "s",
         "evidence": "Filter kit FK-10 is discontinued; SX-100 (synthetic) owners are directed to the SX-200."},
        # Reworded: not verbatim in the dealer page, so no edge.
        {"successor_manufacturer": None, "successor_model": "SX-300", "reason": "discontinued", "source_index": 3,
         "summary": "s", "evidence": "The dealer says the SX-300 replaces the SX-100 (synthetic)."},
    ]
    candidates = [
        {"code": "E4", "source_index": 2, "evidence": "E4: The pump stalled. Replace part PM-44, then restart the unit."},
        {"code": None, "source_index": 2,
         "evidence": "The SX-100 (synthetic) belongs to the SX Series of synthetic dehumidifiers."},
    ]
    state = sx_state(ctx, [NOTICE, PARTS, GUIDE, DEALER], upgrades=upgrades, candidates=candidates)
    report = persist_edges(state, ctx, "thread-sx")
    assert report["written_by_kind"] == {SUPERSEDED_BY: 1, PART_DISCONTINUED: 1, CODE_POINTS_TO_PART: 1,
                                         IN_FAMILY: 1}
    assert report["written"] == 5  # plus the HAS_CODE edge for E4
    assert [(d["kind"], d["target"], d["reason"]) for d in report["dropped"]] == [
        (SUPERSEDED_BY, "SX-300", NOT_IN_PAGE)]

    g = KnowledgeGraph.load(ctx.graph_path, strict=True)
    assert g.reverify(ctx.pages_dir)["failures"] == []
    by_kind = {e["kind"]: e for e in g.edges()}
    sx100 = model_key(MAKER, MODEL)
    assert (by_kind[SUPERSEDED_BY]["src"], by_kind[SUPERSEDED_BY]["dst"]) == (sx100, model_key(MAKER, "SX-200"))
    assert (by_kind[PART_DISCONTINUED]["src"], by_kind[PART_DISCONTINUED]["dst"]) == (sx100, part_key(MAKER, "FK-10"))
    assert by_kind[CODE_POINTS_TO_PART]["src"] == code_key(MAKER, MODEL, "E4")
    assert by_kind[CODE_POINTS_TO_PART]["dst"] == part_key(MAKER, "PM-44")
    assert (by_kind[IN_FAMILY]["src"], by_kind[IN_FAMILY]["dst"]) == (sx100, family_key(MAKER, "SX Series"))
    assert g.family_of(MAKER, MODEL) == "SX Series"
    assert [e["successor_model"] for e in g.superseded_by(MAKER, MODEL)] == ["SX-200"]
    assert "SX-300" not in json.dumps(g.to_json())
    for edge in g.edges():
        assert edge["brief_run_id"] == ctx.run_id and edge["evidence"] in json.dumps(upgrades + candidates)

    # Idempotent: the same thread again writes nothing, even now that the family is known.
    graph_bytes = Path(ctx.graph_path).read_bytes()
    again = persist_edges(state, ctx, "thread-sx")
    assert again["written"] == 0 and again["already_present"] == 5
    assert Path(ctx.graph_path).read_bytes() == graph_bytes

    # Replay into a graph under decision 37 only: the same brief refused is written nowhere.
    other = RunContext(**{**ctx.__dict__, "graph_path": tmp_path / "other_graph.json"})
    refused = {**state, "status": "no_reliable_answer"}
    assert persist_edges(refused, other, "thread-sx-2")["written"] == 0
    assert not (tmp_path / "other_graph.json").exists()


def test_phase4_target_cut_inside_a_longer_name_is_dropped(tmp_path: Path) -> None:
    """Mutations: persist_phase4_token_unchecked (persist proposes SUPERSEDED_BY
    "SX-20" from a quote that names the SX-200); kg_phase4_token_unchecked (the
    graph accepts a SUPERSEDED_BY or PART_DISCONTINUED edge whose target is
    cut from a longer model or part number)."""
    from agent.kg import PART_DISCONTINUED, SUPERSEDED_BY, EdgeRejected, hash_evidence
    from agent.nodes.persist import document_edge_candidates
    from agent.tests.test_sc9_upgrades import MAKER, MODEL, NOTICE, PARTS, SX200_QUOTE, page

    ctx = unit_ctx(tmp_path)
    upgrades = [{"successor_manufacturer": MAKER, "successor_model": "SX-20", "reason": "discontinued",
                 "source_index": 0, "summary": "s", "evidence": SX200_QUOTE}]
    state = sx_state(ctx, [NOTICE], upgrades=upgrades, candidates=[])
    passed, dropped = document_edge_candidates(state, ctx)
    assert passed == []
    assert [(d["kind"], d["target"], d["reason"]) for d in dropped] == [(SUPERSEDED_BY, "SX-20", TARGET_NOT_TOKEN)]

    kg = KnowledgeGraph()
    src = kg.add_model(MAKER, MODEL)
    part_quote = "Filter kit FK-10 is discontinued; SX-100 (synthetic) owners are directed to the SX-200."
    for kind, dst, url, quote in ((SUPERSEDED_BY, kg.add_model(MAKER, "SX-20"), NOTICE, SX200_QUOTE),
                                  (PART_DISCONTINUED, kg.add_part(MAKER, "FK-1"), PARTS, part_quote)):
        with pytest.raises(EdgeRejected) as exc:
            kg.add_edge(kind, src, dst, source_url=url, retrieved_at="2026-09-18", evidence=quote,
                        evidence_sha256=hash_evidence(quote), brief_run_id="run-unit", validated_at="2026-09-18",
                        page_text=page(url))
        assert exc.value.reason == TARGET_NOT_TOKEN, kind
    assert kg.edges() == []
