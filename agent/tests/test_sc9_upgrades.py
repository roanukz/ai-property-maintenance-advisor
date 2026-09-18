"""SC9: upgrade options only from cited, verified successors (PLAN section 5; decisions 13, 27).

Every test replays a synthetic cassette (agent/tests/cassettes/synthetic/
sx100_*.json) through the real graph for the seeded synthetic discontinued
appliance appl-sx100 (maker "Synthetic Appliance Co.", model "SX-100
(synthetic)"). The page texts, the successors SX-200 and SX-300, the part
FK-10 and the graph edges all come from agent/tests/fixtures/discontinued.json,
which is synthetic and serves text only on example.com and example.org URLs.
The graph fixture is built from that file in each test through
KnowledgeGraph.add_edge with page text, so every edge passes the full span
check before a test starts. The helpers here are shared by test_maintenance.py,
test_prices.py and the Phase 4 half of test_persist.py. Each test names the
mutation that turns it red.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Command

from agent import config
from agent.kg import PART_DISCONTINUED, SUPERSEDED_BY, KnowledgeGraph
from agent.nodes.upgrade_check import plan_upgrades, upgrade_check
from agent.registry import open_registry
from agent.research.tools import save_page_text
from agent.rules.evidence import TARGET_NOT_TOKEN, evidence_sha256
from agent.rules.upgrades import check_upgrade_options
from agent.rules.tiers import host_of
from agent.tests.helpers import load_case, replay_ctx, resume_command
from agent.tests.test_sc8_history import final_state, variant

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "discontinued.json"
FIXTURE: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SX100 = FIXTURE["appliance_id"]
MAKER = FIXTURE["identity"]["manufacturer"]
MODEL = FIXTURE["identity"]["model"]
NOTICE = "https://example.com/synthetic/sx/discontinued-notice"
DEALER = "https://dealer.example.org/synthetic/sx/upgrade-guide"
FORUM = "https://forum.example.org/synthetic/sx/thread-42"
PARTS = "https://example.com/synthetic/sx/parts-notice"
GUIDE = "https://example.com/synthetic/sx/service-guide"
SX200_QUOTE = "The SX-100 (synthetic) is discontinued and is replaced by the SX-200."
DEALER_QUOTE = "Dealer notes: we recommend the SX-300 as a replacement for the SX-100 (synthetic)."


def page(url: str) -> str:
    return FIXTURE["pages"][url]["text"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def seed_sx_registry(path: Path) -> None:
    """The synthetic seed plus the fixture's maintenance_log rows for appl-sx100 (all synthetic)."""
    seed = json.loads(config.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    seed["maintenance_log"] = [*seed["maintenance_log"], *FIXTURE["maintenance_log"]]
    seed_path = path.with_name("seed_sx_for_test.json")
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    open_registry(path, seed=seed_path)


def build_sx_graph(graph_path: Path, pages_dir: Path) -> KnowledgeGraph:
    """The fixture's SUPERSEDED_BY and PART_DISCONTINUED edges, each checked against its page text."""
    kg = KnowledgeGraph(meta={"synthetic": True, "_about": "built from agent/tests/fixtures/discontinued.json"})
    src = kg.add_model(MAKER, MODEL)
    for edge in FIXTURE["graph_edges"]:
        url = edge["source_url"]
        sha = save_page_text(pages_dir, page(url))
        kg.add_source(url, host=host_of(url), title=FIXTURE["pages"][url]["title"],
                      retrieved_at=edge["retrieved_at"], text_sha256=sha, tier=edge["tier"])
        if edge["kind"] == SUPERSEDED_BY:
            dst = kg.add_model(edge["successor_manufacturer"], edge["successor_model"])
        else:
            dst = kg.add_part(MAKER, edge["part_no"])
        kg.add_edge(edge["kind"], src, dst, source_url=url, retrieved_at=edge["retrieved_at"],
                    evidence=edge["evidence"], evidence_sha256=evidence_sha256(edge["evidence"]),
                    brief_run_id=edge["brief_run_id"], validated_at=edge["retrieved_at"],
                    page_text=page(url), text_sha256=sha)
    kg.save(graph_path)
    return kg


def run_sx(cassette, tmp_path: Path, *, graph: bool = False, thread_id: str = "thread-1"):
    """Run a cassette for appl-sx100 through the real graph; returns (ctx, outcomes)."""
    from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end
    from agent.nodes.intake import intake_input

    ctx = replay_ctx(tmp_path, cassette, run_id=thread_id)
    seed_sx_registry(Path(ctx.registry_path))
    if graph:
        build_sx_graph(Path(ctx.graph_path), Path(ctx.pages_dir))
    inp = cassette.input
    data = intake_input(symptom=inp["symptom"], identity=inp["identity"], appliance_id=SX100)
    data["html_path"] = str(tmp_path / "brief.html")
    compiled = build_graph(open_checkpointer(tmp_path / "checkpoints.sqlite"))
    outcomes = [run_until_pause_or_end(compiled, data, ctx, thread_id)]
    if outcomes[0].paused and cassette.resume is not None:
        outcomes.append(run_until_pause_or_end(compiled, resume_command(cassette), ctx, thread_id))
    return ctx, outcomes


def draft_of(data: dict) -> dict:
    return data["synthesize"][0]["draft"]


def ran(outcomes: list) -> list[str]:
    return [n for o in outcomes for n in o.nodes]


def html_of(state: dict) -> str:
    return Path(state["html_path"]).read_text(encoding="utf-8")


def successors(brief: dict) -> list[str]:
    return [o["successor_model"] for o in brief["upgrade_options"]]


def cited(brief: dict, entry: dict) -> str:
    return brief["sources"][entry["source_index"]]["url"]


# ---------------------------------------------------------------------------
# SC9, cited half
# ---------------------------------------------------------------------------


def test_upgrade_options_only_cited_successors(tmp_path: Path) -> None:
    """Mutations: upgrades_quote_unchecked (rule 9 keeps an option whatever its
    quote, so the reworded SX-300 quote survives); upgrades_successor_not_target
    (the span check's target is the quote itself, so a verbatim quote that does
    not name the successor keeps the option); upgrades_hook_off (the pipeline
    hook stops calling rule 9)."""
    cassette = load_case("synthetic/sx100_discontinued")
    proposed = [o["successor_model"] for o in draft_of(cassette.data)["upgrade_options"]]
    assert proposed == ["SX-200", "SX-300"]

    ctx, outcomes = run_sx(cassette, tmp_path / "base")
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    assert state["route"] == ["history", "research"]
    brief = state["brief"]
    [option] = brief["upgrade_options"]
    assert option["successor_model"] == "SX-200" and option["evidence"] == SX200_QUOTE
    assert cited(brief, option) == NOTICE
    # The dealer page was cited only by the dropped option, so it is pruned.
    assert [s["url"] for s in brief["sources"]] == [NOTICE]
    html = html_of(state)
    assert "SX-200" in html and "SX-300" not in html

    def verbatim_but_bad_index(data: dict) -> None:
        draft_of(data)["upgrade_options"][1].update(evidence=DEALER_QUOTE, source_index=7)

    def quote_without_successor(data: dict) -> None:
        draft_of(data)["upgrade_options"][1].update(evidence=SX200_QUOTE, source_index=0)

    def verbatim_dealer_quote(data: dict) -> None:
        draft_of(data)["upgrade_options"][1].update(evidence=DEALER_QUOTE, source_index=1)

    for label, edit, kept in (("bad index", verbatim_but_bad_index, ["SX-200"]),
                              ("quote without successor", quote_without_successor, ["SX-200"]),
                              ("verbatim dealer quote", verbatim_dealer_quote, ["SX-200", "SX-300"])):
        _, vout = run_sx(variant(cassette, label, edit), tmp_path / label.replace(" ", "_"))
        vstate = final_state(vout)
        assert vstate["status"] == "ok", (label, vstate.get("validation_errors"))
        assert successors(vstate["brief"]) == kept, label
        for entry in vstate["brief"]["upgrade_options"]:
            assert entry["evidence"] in page(cited(vstate["brief"], entry)), label


def test_graph_superseded_edge_becomes_cited_option(tmp_path: Path) -> None:
    """Mutations: upgrade_check_skips_validate (upgrade_check sends its options
    straight to render, so they never pass validate: added after pruning);
    upgrade_check_not_wired (validate never routes an ok brief to
    upgrade_check); upgrade_check_graph_source_not_registered (the part
    notice is never added to the run's sources, so its option cites the
    wrong source and its quote fails)."""
    cassette = load_case("synthetic/sx100_graph_top_up")
    assert draft_of(cassette.data)["upgrade_options"] == []
    ctx, outcomes = run_sx(cassette, tmp_path, graph=True)
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    assert state["route"] == ["history", "graph", "research"]
    assert ctx.replay["stubs"]["search"].calls == 1

    nodes = ran(outcomes)
    assert nodes.count("upgrade_check") == 1 and nodes.count("validate") == 2
    assert nodes.index("upgrade_check") < len(nodes) - 1 - nodes[::-1].index("validate") < nodes.index("render")
    # The first validate pass saw no upgrade and pruned the uncited graph source.
    first = next(s for n, s in (st for o in outcomes for st in o.states) if n == "validate")
    assert first["brief"]["upgrade_options"] == []
    assert [s["url"] for s in first["brief"]["sources"]] == [DEALER]
    registry_urls = [s["url"] for s in state["sources"]]
    assert registry_urls == [NOTICE, DEALER, PARTS]

    brief = state["brief"]
    by_reason = {o["reason"]: o for o in brief["upgrade_options"]}
    assert sorted(by_reason) == ["discontinued", "parts_unavailable"]
    assert successors(brief) == ["SX-200", "SX-200"]
    fixture_edges = {e["kind"]: e for e in FIXTURE["graph_edges"]}
    for reason, kind, url in (("discontinued", SUPERSEDED_BY, NOTICE), ("parts_unavailable", PART_DISCONTINUED, PARTS)):
        option = by_reason[reason]
        source = brief["sources"][option["source_index"]]
        assert source["url"] == url and source["origin"] == "graph", reason
        assert source["retrieved_at"] == fixture_edges[kind]["retrieved_at"], reason
        assert option["evidence"] == fixture_edges[kind]["evidence"], reason
        assert option["successor_manufacturer"] == MAKER, reason
    # Pruned and renumbered by validate: every source is cited, in registry order.
    assert [s["url"] for s in brief["sources"]] == [NOTICE, DEALER, PARTS]
    assert sorted(o["source_index"] for o in brief["upgrade_options"]) == [0, 2]
    assert brief["try_first"][0]["source_index"] == 1
    assert "SX-200" in html_of(state)


# ---------------------------------------------------------------------------
# SC9, no citation half
# ---------------------------------------------------------------------------


def test_upgrade_options_empty_without_citation(tmp_path: Path) -> None:
    """Mutation upgrades_uncited_kept: an option with no evidence quote is kept
    (as if it could be shown as "unverified") instead of dropped."""
    cassette = load_case("synthetic/sx100_discontinued")

    def uncited(data: dict) -> None:
        for option in draft_of(data)["upgrade_options"]:
            option["evidence"] = ""

    _, outcomes = run_sx(variant(cassette, "uncited", uncited), tmp_path)
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    assert state["brief"]["upgrade_options"] == []
    assert "upgrade_check" in ran(outcomes)  # it ran and found nothing in an empty graph
    html = html_of(state)
    for text in ("SX-200", "SX-300"):
        assert text not in html, text


def test_upgrade_check_adds_nothing_to_refusal_or_budget_stop(tmp_path: Path) -> None:
    """Mutations: upgrade_check_runs_on_refusal (the routing after validate sends
    a refusal to upgrade_check); upgrade_check_status_guard_off (the node adds
    graph options whatever the status)."""
    cassette = load_case("synthetic/sx100_graph_top_up")

    def refusal(data: dict) -> None:
        draft = draft_of(data)
        draft.update(status="no_reliable_answer", try_first=[], source_tiers=[],
                     no_reliable_answer={"found": [], "why_insufficient": "Synthetic: nothing documented."})

    def budget_stop(data: dict) -> None:
        data["caps"] = {"run_credit_cap": 0}

    for label, edit, status in (("refusal", refusal, "no_reliable_answer"),
                                ("budget stop", budget_stop, "budget_stopped")):
        ctx, outcomes = run_sx(variant(cassette, label, edit), tmp_path / label.replace(" ", "_"), graph=True)
        state = final_state(outcomes)
        assert state["status"] == status, label
        assert "upgrade_check" not in ran(outcomes), label
        assert state["brief"]["upgrade_options"] == [], label
        assert PARTS not in [s["url"] for s in state["sources"]], label
        assert "SX-200" not in html_of(state), label

    # The node itself: on a refusal it adds nothing and goes to render; on an
    # ok brief (the control) the same graph gives it options to add.
    ctx = replay_ctx(tmp_path / "direct", cassette)
    build_sx_graph(Path(ctx.graph_path), Path(ctx.pages_dir))
    base = {"identity": FIXTURE["identity"], "sources": [], "latency": {},
            "draft": copy.deepcopy(draft_of(cassette.data)), "brief": {"upgrade_options": []}}
    refused = upgrade_check({**base, "status": "no_reliable_answer"}, Runtime(context=ctx))
    assert isinstance(refused, Command) and refused.goto == "render"
    assert set(refused.update) == {"latency"}
    ok = upgrade_check({**base, "status": "ok"}, Runtime(context=ctx))
    assert ok.goto == "validate"
    assert [o["successor_model"] for o in ok.update["draft"]["upgrade_options"]] == ["SX-200", "SX-200"]
    assert {s["url"] for s in ok.update["sources"]} == {NOTICE, PARTS}


# ---------------------------------------------------------------------------
# upgrade_check's plan, and rule 9's token boundary
# ---------------------------------------------------------------------------


def _plan_graph() -> KnowledgeGraph:
    """The fixture's two edges, with the notice's Source node stored at dealer tier (synthetic)."""
    kg = KnowledgeGraph(meta={"synthetic": True})
    src = kg.add_model(MAKER, MODEL)
    for edge in FIXTURE["graph_edges"]:
        url = edge["source_url"]
        tier = "dealer" if url == NOTICE else edge["tier"]
        kg.add_source(url, host=host_of(url), title=FIXTURE["pages"][url]["title"],
                      retrieved_at=edge["retrieved_at"], text_sha256=None, tier=tier)
        dst = (kg.add_model(edge["successor_manufacturer"], edge["successor_model"])
               if edge["kind"] == SUPERSEDED_BY else kg.add_part(MAKER, edge["part_no"]))
        kg.add_edge(edge["kind"], src, dst, source_url=url, retrieved_at=edge["retrieved_at"],
                    evidence=edge["evidence"], evidence_sha256=evidence_sha256(edge["evidence"]),
                    brief_run_id=edge["brief_run_id"], validated_at=edge["retrieved_at"], page_text=page(url))
    return kg


def test_upgrade_check_skips_known_options_and_proposes_tiers_only_for_its_own_sources() -> None:
    """Mutations: upgrade_check_dedupe_off (an option the validated brief
    already carries is added again); upgrade_check_tier_not_stored (the
    proposal ignores the graph Source node's stored tier);
    upgrade_check_tier_for_model_sources (a tier is proposed for a source the
    run registered by search, which could raise the tier of a model cited
    maintenance entry on the second pass); upgrade_check_tier_for_cited_graph_source
    (likewise for a graph source the draft already cites)."""
    kg = _plan_graph()
    empty_draft = {"upgrade_options": [], "maintenance_due": [], "try_first": [], "candidates": []}
    base = {"identity": FIXTURE["identity"], "sources": [], "draft": empty_draft, "brief": {"upgrade_options": []}}

    # Nothing registered yet: both options, both sources, each with its stored tier.
    options, new_sources, tiers = plan_upgrades(kg, base)
    assert [(o["successor_model"], o["reason"]) for o in options] == [
        ("SX-200", "discontinued"), ("SX-200", "parts_unavailable")]
    assert [s["url"] for s in new_sources] == [NOTICE, PARTS]
    assert [(t["source_index"], t["tier"]) for t in tiers] == [(0, "dealer"), (1, "manufacturer")]

    # The validated brief already lists SX-200 as discontinued: only the part option is new.
    known = {**base, "brief": {"upgrade_options": [{"successor_model": "sx-200", "reason": "discontinued"}]}}
    options, _, _ = plan_upgrades(kg, known)
    assert [(o["successor_model"], o["reason"]) for o in options] == [("SX-200", "parts_unavailable")]

    # The notice was found by search and the model cites it for a maintenance
    # entry with no tier proposal: upgrade_check proposes no tier for it.
    cites_notice = {**empty_draft, "maintenance_due": [
        {"task": "Clean the bucket filter", "interval": "every 3 months", "source_index": 0,
         "evidence": "Clean the bucket filter every 3 months.", "last_done_record_id": ""}]}
    searched = {**base, "draft": cites_notice, "sources": [{"url": NOTICE, "origin": "search"}]}
    options, new_sources, tiers = plan_upgrades(kg, searched)
    assert [o["source_index"] for o in options] == [0, 1]
    assert [s["url"] for s in new_sources] == [PARTS]
    assert [(t["source_index"], t["tier"]) for t in tiers] == [(1, "manufacturer")]

    # A graph source the draft cites is the model's to grade; one it does not cite gets the stored tier.
    graph_cited = {**searched, "sources": [{"url": NOTICE, "origin": "graph"}]}
    assert [t["source_index"] for t in plan_upgrades(kg, graph_cited)[2]] == [1]
    graph_uncited = {**graph_cited, "draft": empty_draft}
    assert [(t["source_index"], t["tier"]) for t in plan_upgrades(kg, graph_uncited)[2]] == [
        (0, "dealer"), (1, "manufacturer")]


def test_successor_cut_inside_a_longer_model_is_dropped() -> None:
    """Mutation upgrades_successor_token_unchecked: rule 9 keeps "SX-20"
    because the quote naming the SX-200 contains it as a substring."""
    brief = {"status": "ok", "sources": [{"url": NOTICE, "tier": "manufacturer"}], "upgrade_options": [
        {"successor_model": successor, "reason": "discontinued", "summary": "s", "source_index": 0,
         "evidence": SX200_QUOTE} for successor in ("SX-20", "SX-200")]}
    report: list[dict] = []
    check_upgrade_options(brief, page_texts={NOTICE: page(NOTICE)}, report=report)
    assert successors(brief) == ["SX-200"]
    assert [(d["successor"], d["reason"]) for d in report] == [("SX-20", TARGET_NOT_TOKEN)]
