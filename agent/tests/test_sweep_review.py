"""SC14 review findings B1 to B6: the sweep's lifecycle lookups and offline facts, tightened.

- B1: a lookup saves an edge only when its quote names the unit's full registry model, as its own
  token, not only the successor (a quote about a sibling model is dropped as unit_not_in_span);
  persist applies the same check to a brief's upgrade options.
- B2: the preflight states the run cap's scope; since 13 October 2026 one cap covers the whole sweep,
  so the sweep's worst case is the cap (agent/tests/test_round2_sweep_cap.py proves it is shared).
- B3: after --research the sweep no longer says it spent nothing.
- B4: a successor's maker is shown only when the quote names it, and a lookup clears a successor
  maker that is neither the unit's nor in the quote.
- B5: a live --research sweep checks the build's Tavily credits before its preflight.
- B6: an install date that is not a calendar date gives no age.

Every cassette here is a copy of the synthetic XR16 cassette with one sentence changed, written to
a temp folder; every graph edge and URL is synthetic. Each test names the mutations in
mutations.toml that turn it red.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from agent import cli, config, lifecycle, sweep
from agent.kg import SUPERSEDED_BY, KnowledgeGraph, hash_evidence
from agent.ledger import Ledger
from agent.registry import open_registry
from agent.render.brief_html import esc
from agent.render.property_page import render_property_page
from agent.tests.test_sweep import (  # noqa: F401  (sweep_data and live_sweep are fixtures)
    AS_OF,
    NOTICE_URL,
    SUCCESSOR,
    XR16,
    XR_CASSETTE,
    entry,
    live_sweep,
    page_of,
    sweep_cmd,
    sweep_data,
)

ORIGINAL = "The XR16 4TTR6036 is discontinued and is replaced by the XR99 (synthetic) in this synthetic notice."


def variant(folder: Path, name: str, sentence: str | None = None, maker: str | None = None) -> Path:
    """The XR16 cassette with the notice's sentence and the draft's quote both set to `sentence`
    (the page still names the XR16 4TTR6036 elsewhere), or with the first option's successor maker set."""
    data = json.loads(XR_CASSETTE.read_text(encoding="utf-8"))
    if sentence is not None:
        for result in data["research"]["tool_results"][0]["results"]:
            for key in ("content", "raw_content"):
                result[key] = result[key].replace(ORIGINAL, sentence)
        data["synthesize"][0]["draft"]["upgrade_options"][0]["evidence"] = sentence
    if maker is not None:
        data["synthesize"][0]["draft"]["upgrade_options"][0]["successor_manufacturer"] = maker
    path = folder / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_ids(out: str) -> list[str]:
    return [ln.split("lookup ", 1)[1].split(":", 1)[0] for ln in out.splitlines() if ": lookup lifecycle-" in ln]


@pytest.mark.parametrize("sentence", [
    "The XR13 is discontinued and is replaced by the XR99 (synthetic) in this synthetic notice.",
    "The XR16 4TTR6024 is discontinued and is replaced by the XR99 (synthetic) in this synthetic notice.",
    "The XR16 4TTR6036A is discontinued and is replaced by the XR99 (synthetic) in this synthetic notice.",
], ids=["sibling_family", "sibling_model_number", "longer_model_number"])
def test_a_quote_that_never_names_the_unit_saves_nothing(
        sentence: str, sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A verbatim quote naming the successor but a sibling model (or the model with a suffix, so not
    as its own token) is dropped as unit_not_in_span: no edge, no graph file, and the page keeps the
    no discontinuation sentence. Mutations: lifecycle_unit_check_skipped, lifecycle_unit_token_skipped."""
    path = variant(sweep_data / "cassettes", "sibling", sentence)
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(path))
    assert code == cli.EXIT_OK, out + err
    assert f"    dropped: successor {SUCCESSOR}: {lifecycle.UNIT_NOT_IN_SPAN}" in out
    assert f"verified: discontinued, successor {SUCCESSOR}" not in out
    (run_id,) = run_ids(out)
    record = json.loads((sweep_data / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
    assert record["status"] == lifecycle.NOTHING_SAVED and record["kept"] == []
    assert not config.REPLAY_GRAPH_PATH.exists()
    xr_entry = entry(page_of("prop-a"), "appl-xr16")
    assert sweep.NONE_DOCUMENTED in xr_entry and "XR99" not in xr_entry


def test_the_unchanged_quote_still_saves_its_successor(sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The unit check keeps a quote that names the XR16 4TTR6036 as its own token. Mutation:
    lifecycle_unit_check_always_drops."""
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(XR_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    assert f"    verified: discontinued, successor {SUCCESSOR}" in out
    assert [e["evidence"] for e in KnowledgeGraph.load(config.REPLAY_GRAPH_PATH).superseded_by(*XR16)] == [ORIGINAL]


def test_a_successor_maker_the_quote_never_names_is_not_shown(tmp_path: Path) -> None:
    """A SUPERSEDED_BY edge to a successor held under another maker shows only the model, on the page
    and in the sweep's line; a quote that names the maker shows it. Mutation: sweep_fact_maker_unverified."""
    registry = open_registry(tmp_path / "registry.sqlite")
    kg = KnowledgeGraph()
    bare = "The XR16 4TTR6036 is discontinued and is replaced by the XR99 (synthetic) per this notice."
    src, dst = kg.add_model(*XR16), kg.add_model("Carrier", SUCCESSOR)
    kg.add_source(NOTICE_URL, host="maker.example.com", title="Synthetic notice", retrieved_at="2026-07-01",
                  text_sha256=None, tier="dealer")
    kg.add_edge(SUPERSEDED_BY, src, dst, source_url=NOTICE_URL, retrieved_at="2026-07-01", evidence=bare,
                evidence_sha256=hash_evidence(bare), brief_run_id="run-syn", validated_at="2026-07-01")
    xr = registry.get_appliance("appl-xr16")
    (fact,) = sweep.replacement_facts(kg, xr)
    assert fact["successor_manufacturer"] is None and fact["successor_model"] == SUCCESSOR
    assert "Carrier" not in sweep.fact_line(fact) and f"successor {SUCCESSOR};" in sweep.fact_line(fact)
    page = render_property_page("prop-a", registry, graph=kg, generated_at=AS_OF.isoformat())
    assert f"Successor named in the evidence: <strong>{esc(SUCCESSOR)}</strong>" in entry(page, "appl-xr16")
    assert "Carrier" not in page

    named = "The XR16 4TTR6036 is replaced by the Carrier XR99 (synthetic) per this notice."
    kg2 = KnowledgeGraph()
    src, dst = kg2.add_model(*XR16), kg2.add_model("Carrier", SUCCESSOR)
    kg2.add_source(NOTICE_URL, host="maker.example.com", title="Synthetic notice", retrieved_at="2026-07-01",
                   text_sha256=None, tier="dealer")
    kg2.add_edge(SUPERSEDED_BY, src, dst, source_url=NOTICE_URL, retrieved_at="2026-07-01", evidence=named,
                 evidence_sha256=hash_evidence(named), brief_run_id="run-syn", validated_at="2026-07-01")
    (fact,) = sweep.replacement_facts(kg2, xr)
    assert fact["successor_manufacturer"] == "Carrier" and f"successor Carrier {SUCCESSOR};" in sweep.fact_line(fact)


def test_a_lookup_clears_a_successor_maker_neither_the_units_nor_quoted(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A draft that names "Carrier" as the successor's maker, with a quote that never does, saves the
    successor under the unit's maker. Mutation: lifecycle_successor_maker_kept."""
    path = variant(sweep_data / "cassettes", "carrier", maker="Carrier")
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(path))
    assert code == cli.EXIT_OK, out + err
    (edge,) = KnowledgeGraph.load(config.REPLAY_GRAPH_PATH).superseded_by(*XR16)
    assert (edge["successor_manufacturer"], edge["successor_model"]) == ("Trane", SUCCESSOR)
    assert "Carrier" not in out and "Carrier" not in page_of("prop-a")


def test_after_research_the_sweep_does_not_say_it_spent_nothing(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A replay --research sweep ends on the pages note, not the offline note, and its plan states the
    run cap's scope. Mutations: cli_sweep_offline_note_after_research, lifecycle_plan_cap_scope_dropped."""
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(XR_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    assert run_ids(out) and cli.SWEEP_OFFLINE_NOTE not in out
    assert out.rstrip("\n").endswith(cli.SWEEP_PAGES_NOTE)
    cap = config.RUN_CAP_USD["cheap"]
    assert f"run cap {cap:.2f} USD for the whole sweep: 2 lookup(s), typical total " in out
    assert f"worst case {cap:.4f} USD; a lookup starts only while what is left of the cap covers its worst case" in out
    assert "per lookup" not in out

    code, out, err = sweep_cmd(capsys, "prop-a")
    assert code == cli.EXIT_OK and out.rstrip("\n").endswith(cli.SWEEP_OFFLINE_NOTE)


def test_live_preflight_says_the_cap_is_for_the_whole_sweep(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The live preflight, before the typed prompt, gives the one cap for the whole sweep, the number
    of lookups, their typical total and a worst case equal to the cap. Mutation:
    cli_sweep_cap_scope_not_printed."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_REFUSED, out + err
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    eligible = [registry.get_appliance(a) for a in ("appl-optima880", "appl-xr16")]
    plan = lifecycle.sweep_plan("cheap", eligible)
    line = lifecycle.cap_scope_line(plan, 2, "cheap")
    assert line.startswith(f"run cap {config.RUN_CAP_USD['cheap']:.2f} USD for the whole sweep: 2 lookup(s)")
    lines = out.splitlines()
    assert line in lines and lines.index(line) == len(lines) - 2 and lines[-1].startswith("Type proceed")


def test_live_research_is_refused_when_the_build_credits_cannot_cover_its_lookups(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Two lookups may use 3 credits each; with 4 build credits left the sweep is refused before its
    preflight, and nothing is reserved. Mutation: cli_sweep_credits_not_checked."""
    plans = [lifecycle.lookup_plan("cheap", {"id": "x", "manufacturer": "Trane", "model": "XR16 4TTR6036"})]
    assert plans[0].max_credits == 3
    Ledger(config.LEDGER_PATH, create=True)
    with sqlite3.connect(config.LEDGER_PATH) as conn:
        conn.execute("INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd, tavily_credits)"
                     " VALUES ('2026-09-01T00:00:00+00:00', 'earlier', 'cheap', 'research', 'tavily', 'charge', 0, ?)",
                     (config.BUILD_CREDIT_CAP - 4,))
    before = Ledger(config.LEDGER_PATH).rows()
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live")
    assert code == cli.EXIT_REFUSED, out + err
    assert "build credits" in err and "preflight" not in out
    assert Ledger(config.LEDGER_PATH).rows() == before
    assert not config.GRAPH_PATH.exists()


@pytest.mark.parametrize("install", ["2019-02-30", "2023-02-29", "2019-04-31", "02/30/2019"])
def test_an_install_date_that_is_not_a_calendar_date_gives_no_age(install: str) -> None:
    """parse_partial_date gives None for a full date not on the calendar, so the age reads only a
    real date. Mutation: sweep_age_impossible_date_accepted."""
    age = sweep.install_age({"install_date": install, "record_id": "appliance:x"}, date(2026, 10, 1))
    assert age == {"years": None, "statement": f"The install date on record, {install}, could not be read as a "
                                               "date, so the age is not computed."}
    assert sweep.install_age({"install_date": "2024-02-29", "record_id": "appliance:x"},
                             date(2026, 10, 1))["years"] == 2


def test_a_brief_saves_no_lifecycle_edge_from_a_quote_about_another_model(tmp_path: Path) -> None:
    """persist applies the same unit check to a brief's upgrade options: with the unit confirmed as a
    sibling model, the verified SX-100 quotes propose no SUPERSEDED_BY or PART_DISCONTINUED edge and
    are dropped as unit_not_in_span; for the SX-100 itself both pass. Mutation:
    persist_unit_check_skipped."""
    from agent.kg import PART_DISCONTINUED
    from agent.nodes.persist import DROP_UNIT_NOT_IN_SPAN, document_edge_candidates
    from agent.tests.test_persist import sx_state, unit_ctx
    from agent.tests.test_sc9_upgrades import MAKER, MODEL, NOTICE, PARTS, SX200_QUOTE

    upgrades = [
        {"successor_manufacturer": MAKER, "successor_model": "SX-200", "reason": "discontinued", "source_index": 0,
         "summary": "s", "evidence": SX200_QUOTE},
        {"successor_manufacturer": None, "successor_model": "SX-200", "reason": "parts_unavailable",
         "source_index": 1, "summary": "s",
         "evidence": "Filter kit FK-10 is discontinued; SX-100 (synthetic) owners are directed to the SX-200."},
    ]
    ctx = unit_ctx(tmp_path)
    state = sx_state(ctx, [NOTICE, PARTS], upgrades=upgrades, candidates=[])
    passed, dropped = document_edge_candidates(state, ctx)
    assert MODEL == "SX-100 (synthetic)"
    assert sorted(p["kind"] for p in passed) == [PART_DISCONTINUED, SUPERSEDED_BY] and dropped == []

    state["identity"] = {**state["identity"], "model": "SX-110 (synthetic)"}
    passed, dropped = document_edge_candidates(state, ctx)
    assert passed == []
    assert [(d["kind"], d["target"], d["reason"]) for d in dropped] == [
        (SUPERSEDED_BY, "SX-200", DROP_UNIT_NOT_IN_SPAN), (PART_DISCONTINUED, "FK-10", DROP_UNIT_NOT_IN_SPAN)]
