"""SC14, the "Plan a replacement" sweep and its lifecycle research mode (decision 72).

The offline sweep reads verified graph facts and the registry and writes the
property page; `--research` looks up discontinuation and successors for units
with no lifecycle facts, replayed here from hand built synthetic cassettes
(agent/tests/cassettes/synthetic/sweep_*_synthetic.json). The live preflight
is checked on fake clients with the network blocked: no test constructs a
real Anthropic or Tavily client, and none reads the repo's .env.

Every graph edge, URL and page below is synthetic, on example.com style hosts.
Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent import cli, config, lifecycle, models, sweep
from agent.kg import PART_DISCONTINUED, SUPERSEDED_BY, KnowledgeGraph, _edge_id, hash_evidence
from agent.ledger import Ledger
from agent.registry import open_registry
from agent.render.brief_html import esc
from agent.render.property_page import render_property_page
from agent.replay.cassettes import load_cassette
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR, replay_ctx

AS_OF = date(2026, 10, 2)
XR16 = ("Trane", "XR16 4TTR6036")
OPTIMA = ("Sundance Spas", "Optima 880")
SUCCESSOR = "XR99 (synthetic)"
XR_CASSETTE = SYNTHETIC_CASSETTE_DIR / "sweep_xr16_successor_synthetic.json"
RHEEM_CASSETTE = SYNTHETIC_CASSETTE_DIR / "sweep_waterheater_nothing_synthetic.json"
SX_CASSETTE = SYNTHETIC_CASSETTE_DIR / "sweep_sx100_invented_synthetic.json"
NOTICE_URL = "https://maker.example.com/synthetic/xr16-notice"
PARTS_URL = "https://parts.example.org/synthetic/fk-1234"


@pytest.fixture
def sweep_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every path the sweep reads or writes, under a temp data folder; replay mode; a fixed UTC date."""
    data = tmp_path / "data"
    for name, path in {
        "DATA_DIR": data, "REGISTRY_PATH": data / "registry.sqlite", "PROPERTY_OUT_DIR": data / "property",
        "BRIEFS_OUT_DIR": data / "briefs", "GRAPH_PATH": data / "graph.json",
        "REPLAY_GRAPH_PATH": data / "replay_graph.json", "PAGES_DIR": data / "pages",
        "LOOKUPS_DIR": data / "lookups", "LEDGER_PATH": data / "ledger" / "ledger.sqlite",
        "REPLAY_LEDGER_PATH": data / "ledger" / "replay_ledger.sqlite",
        "CHECKPOINT_PATH": data / "checkpoints.sqlite", "EVAL_DIR": data / "eval",
        "STAGING_DIR": data / "staging", "RECORDINGS_DIR": data / "recordings",
    }.items():
        monkeypatch.setattr(config, name, path)
    for name in (config.ENV_MODE, config.ENV_CASSETTE, config.ENV_TRACING):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "_today", lambda: AS_OF)
    monkeypatch.chdir(config.REPO_ROOT)
    return data


def add_superseded(kg: KnowledgeGraph, maker: str, model: str, successor: str, url: str, evidence: str,
                   retrieved_at: str = "2026-07-01", title: str = "Synthetic notice") -> None:
    src, dst = kg.add_model(maker, model), kg.add_model(maker, successor)
    kg.add_source(url, host="maker.example.com", title=title, retrieved_at=retrieved_at, text_sha256=None,
                  tier="dealer")
    kg.add_edge(SUPERSEDED_BY, src, dst, source_url=url, retrieved_at=retrieved_at, evidence=evidence,
                evidence_sha256=hash_evidence(evidence), brief_run_id="run-syn", validated_at="2026-07-01")


def add_part(kg: KnowledgeGraph, maker: str, model: str, part: str, url: str, evidence: str,
             retrieved_at: str = "2026-07-02") -> None:
    src, dst = kg.add_model(maker, model), kg.add_part(maker, part)
    kg.add_source(url, host="parts.example.org", title="Synthetic parts notice", retrieved_at=retrieved_at,
                  text_sha256=None, tier="dealer")
    kg.add_edge(PART_DISCONTINUED, src, dst, source_url=url, retrieved_at=retrieved_at, evidence=evidence,
                evidence_sha256=hash_evidence(evidence), brief_run_id="run-syn", validated_at="2026-07-02")


def page_of(property_id: str) -> str:
    return (config.PROPERTY_OUT_DIR / f"{property_id}.html").read_text(encoding="utf-8")


def section(page: str) -> str:
    """The Plan a replacement section of a property page."""
    start = page.index("<h2>Plan a replacement</h2>")
    return page[start:page.index("</section>", start)]


def entry(page: str, appliance_id: str) -> str:
    """One appliance's entry in the Plan a replacement section."""
    text = section(page)
    start = text.index(f'<p class="meta">{appliance_id} &middot;')
    end = text.find("<li>", start)
    return text[start:end if end != -1 else len(text)]


def sweep_cmd(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(["property", "sweep", *args])
    out, err = capsys.readouterr()
    return code, out, err


# ---------------------------------------------------------------------------
# The offline sweep
# ---------------------------------------------------------------------------


def test_offline_sweep_spends_nothing_and_leaves_the_ledger_untouched(
        sweep_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """With sockets blocked (pytest-socket), the sweep builds no client, opens no ledger, and
    leaves an existing live ledger byte for byte. Mutation: sweep_offline_opens_ledger."""
    ledger = Ledger(config.LEDGER_PATH, create=True)
    ledger.stop("run-before", mode="cheap", reason="run_cap")  # a ledger with one row in it
    before = config.LEDGER_PATH.read_bytes()

    def no_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the offline sweep built a paid client")

    for name in (*LIVE_CHAT_NAMES, *LIVE_TOOL_NAMES):
        if hasattr(models, name):
            monkeypatch.setattr(models, name, no_client)
    block_real_clients(monkeypatch)
    kg = KnowledgeGraph()
    add_superseded(kg, *XR16, SUCCESSOR, NOTICE_URL,
                   "The XR16 4TTR6036 is discontinued and is replaced by the XR99 (synthetic) per this notice.")
    kg.save(config.REPLAY_GRAPH_PATH)
    graph_before = config.REPLAY_GRAPH_PATH.read_bytes()

    code, out, err = sweep_cmd(capsys, "prop-a")
    assert code == cli.EXIT_OK, out + err
    assert cli.SWEEP_OFFLINE_NOTE in out
    assert config.LEDGER_PATH.read_bytes() == before
    assert not config.REPLAY_LEDGER_PATH.exists()
    assert Ledger(config.LEDGER_PATH).rows() == ledger.rows()
    assert config.REPLAY_GRAPH_PATH.read_bytes() == graph_before, "the sweep never writes the graph"
    assert not (sweep_data / "runs").exists() and not (sweep_data / "lookups").exists()
    assert SUCCESSOR in page_of("prop-a")


def test_every_shown_successor_traces_to_a_graph_fact(sweep_data: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """Each successor on the page and in the output is one verified edge, shown with its source URL,
    retrieved date and evidence hash. Mutations: sweep_fact_hash_from_text,
    property_page_fact_hash_dropped, property_page_fact_link_dropped."""
    kg = KnowledgeGraph()
    add_superseded(kg, *XR16, SUCCESSOR, NOTICE_URL,
                   "The XR16 4TTR6036 is discontinued and is replaced by the XR99 (synthetic) per this notice.",
                   retrieved_at="2026-07-01T10:00:00+00:00")
    add_part(kg, *XR16, "FK-1234", PARTS_URL,
             "Fan kit FK-1234 is discontinued; owners are pointed to the XR99 (synthetic) instead.")
    kg.save(config.REPLAY_GRAPH_PATH)

    code, out, err = sweep_cmd(capsys, "prop-a")
    assert code == cli.EXIT_OK, out + err
    page = page_of("prop-a")
    loaded = KnowledgeGraph.load(config.REPLAY_GRAPH_PATH)
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    rows = sweep.sweep_rows(loaded, registry.list_appliances("prop-a"), AS_OF)
    facts = [f for row in rows for f in row["facts"]]
    assert [(f["reason"], f["successor_model"]) for f in facts] == [
        (sweep.DISCONTINUED, SUCCESSOR), (sweep.PARTS_UNAVAILABLE, SUCCESSOR)]
    xr_entry = entry(page, "appl-xr16")
    for fact in facts:
        data = loaded.g.get_edge_data(fact["src"], fact["dst"],
                                      key=_edge_id(fact["kind"], fact["source_url"], fact["evidence_sha256"]))
        assert data is not None, fact
        assert data["kind"] in (SUPERSEDED_BY, PART_DISCONTINUED)
        assert data["source_url"] == fact["source_url"] and data["retrieved_at"] == fact["retrieved_at"]
        assert fact["evidence_sha256"] == data["evidence_sha256"] == hash_evidence(data["evidence"])
        assert sweep.names_successor(data["evidence"], fact["successor_model"])
        for shown in (f'href="{esc(fact["source_url"])}"', f"retrieved {fact['retrieved_at'][:10]}",
                      f"evidence sha256 {fact['evidence_sha256']}", esc(data["evidence"])):
            assert shown in xr_entry, shown
        assert f"source {fact['source_url']}; retrieved {fact['retrieved_at'][:10]}; " \
               f"evidence sha256 {fact['evidence_sha256']}" in out
    # The section is the only place a successor is shown: the old successor list is gone.
    assert page.count(esc(SUCCESSOR) + "</strong>") == 2
    assert "Successor models on record" not in page


def test_no_successor_is_shown_without_a_quote_naming_it(tmp_path: Path) -> None:
    """A part edge whose quote names no successor shows the part and says so; a SUPERSEDED_BY edge
    whose quote does not name its successor shows nothing. Mutation: sweep_names_successor_always."""
    registry = open_registry(tmp_path / "registry.sqlite")
    kg = KnowledgeGraph()
    add_superseded(kg, *XR16, SUCCESSOR, NOTICE_URL,
                   "The XR16 4TTR6036 is discontinued and is replaced by the XR99 (synthetic) per this notice.")
    add_part(kg, *XR16, "FK-1234", PARTS_URL, "Fan kit FK-1234 is discontinued and no longer stocked (synthetic).")
    # Stored behind add_edge's back: a superseded edge whose quote never names its successor.
    src, dst = kg.add_model(*OPTIMA), kg.add_model(OPTIMA[0], "Optima 990 (synthetic)")
    bad = "The Optima 880 is discontinued according to this synthetic notice."
    kg.g.add_edge(src, dst, key=_edge_id(SUPERSEDED_BY, NOTICE_URL, hash_evidence(bad)), kind=SUPERSEDED_BY,
                  source_url=NOTICE_URL, retrieved_at="2026-07-01", evidence=bad, evidence_sha256=hash_evidence(bad),
                  brief_run_id="run-syn", validated_at="2026-07-01")

    xr = sweep.sweep_row(kg, registry.get_appliance("appl-xr16"), AS_OF)
    assert [(f["reason"], f["part"], f["successor_model"]) for f in xr["facts"]] == [
        (sweep.DISCONTINUED, None, SUCCESSOR), (sweep.PARTS_UNAVAILABLE, "FK-1234", None)]
    optima = sweep.sweep_row(kg, registry.get_appliance("appl-optima880"), AS_OF)
    assert optima["facts"] == [] and optima["statement"] == sweep.NONE_DOCUMENTED
    assert not sweep.names_successor("Fan kit FK-1234 is discontinued.", SUCCESSOR)
    assert sweep.names_successor("Order the XR99  (synthetic) now.", SUCCESSOR)

    page = render_property_page("prop-a", registry, graph=kg, generated_at=AS_OF.isoformat())
    xr_entry = entry(page, "appl-xr16")
    assert xr_entry.count("Successor named in the evidence") == 1
    assert "Reason: parts unavailable (part FK-1234). " + sweep.NO_SUCCESSOR_NAMED in xr_entry
    assert "Optima 990" not in page
    assert sweep.NONE_DOCUMENTED in entry(page, "appl-optima880")


def test_none_documented_sentence_is_exact_and_never_not_discontinued(
        tmp_path: Path, sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With no graph fact the entry says exactly the sentence, on the page and in the output, and
    nothing ever says a unit is not discontinued. Mutations: sweep_none_documented_reworded,
    property_page_none_documented_dropped."""
    assert sweep.NONE_DOCUMENTED == "No discontinuation documented in what this tool has verified."
    registry = open_registry(tmp_path / "registry.sqlite")
    for graph in (None, KnowledgeGraph()):
        for prop in registry.list_properties():
            page = render_property_page(prop["id"], registry, graph=graph, generated_at=AS_OF.isoformat())
            appliances = registry.list_appliances(prop["id"])
            assert section(page).count(f"<p>{sweep.NONE_DOCUMENTED}</p>") == len(appliances)
            assert "not discontinued" not in page.casefold()
    code, out, err = sweep_cmd(capsys, "--all")
    assert code == cli.EXIT_OK, out + err
    assert out.count(f"    {sweep.NONE_DOCUMENTED}\n") == len(registry.list_appliances())
    assert "not discontinued" not in out.casefold()


@pytest.mark.parametrize(("install", "as_of", "years", "statement"), [
    ("2019-04-15", AS_OF, 7, "Installed 2019-04-15 per property record appliance:x; 7 years old as of "
                             "2026-10-02 (UTC)."),
    ("2019-10-03", AS_OF, 6, None),
    ("2019-10-02", AS_OF, 7, None),
    ("2025-10-03", AS_OF, 0, "Installed 2025-10-03 per property record appliance:x; less than 1 year old as "
                             "of 2026-10-02 (UTC)."),
    ("2025-10-02", AS_OF, 1, "Installed 2025-10-02 per property record appliance:x; 1 year old as of "
                             "2026-10-02 (UTC)."),
    ("2024-02-29", date(2025, 2, 28), 0, None),
    ("2024-02-29", date(2025, 3, 1), 1, None),
    ("06/03/2019", AS_OF, 7, None),
    ("2019", AS_OF, None, "The install date on record, 2019, gives only the year, so the age is not computed."),
    ("2019-06", AS_OF, None, "The install date on record, 2019-06, gives only the year and month, so the age "
                             "is not computed."),
    ("06/2019", AS_OF, None, "The install date on record, 06/2019, gives only the year and month, so the age "
                             "is not computed."),
    (None, AS_OF, None, "No install date is recorded, so the age is not computed."),
    ("", AS_OF, None, "No install date is recorded, so the age is not computed."),
    ("soon", AS_OF, None, "The install date on record, soon, could not be read as a date, so the age is not "
                          "computed."),
    ("2019-13-01", AS_OF, None, None),
    ("2027-01-01", AS_OF, None, "The install date on record, 2027-01-01, is after 2026-10-02 (UTC), so the age "
                                "is not computed."),
])
def test_age_from_the_install_date_including_partial_dates(install: str | None, as_of: date, years: int | None,
                                                           statement: str | None) -> None:
    """Whole years as of the page's date; a partial, unreadable, future or missing date says so.
    Mutations: sweep_age_partial_month_computed, sweep_age_wall_clock, sweep_age_one_year_wording."""
    age = sweep.install_age({"install_date": install, "record_id": "appliance:x"}, as_of)
    assert age["years"] == years
    if statement is not None:
        assert age["statement"] == statement
    if years is None:
        assert "years old" not in age["statement"] and "year old" not in age["statement"]


# ---------------------------------------------------------------------------
# Lifecycle research mode, in replay
# ---------------------------------------------------------------------------


def test_replay_lookup_saves_a_verified_successor_that_the_sweep_then_shows(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The XR16 cassette's verbatim quote becomes a SUPERSEDED_BY edge in the replay graph, reserved
    and charged on the replay ledger, and the page shows it; the option whose quote does not name its
    successor is dropped and saved nowhere. Mutations: lifecycle_persist_skipped,
    lifecycle_rule9_skipped, lifecycle_wrong_system_prompt."""
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--cassette", str(XR_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    assert "appl-xr16 Trane XR16 4TTR6036: one lifecycle lookup planned" in out
    assert "appl-optima880 Sundance Spas Optima 880: no cassette for this unit in replay; nothing looked up" in out
    assert f"    verified: discontinued, successor {SUCCESSOR}" in out
    assert "    dropped: successor XR77 (synthetic): target_not_in_span" in out
    assert "verified: discontinued, successor XR77" not in out

    kg = KnowledgeGraph.load(config.REPLAY_GRAPH_PATH, strict=True)
    supers = kg.superseded_by(*XR16)
    assert [e["successor_model"] for e in supers] == [SUCCESSOR]
    edge = supers[0]
    assert edge["source_url"] == "https://example.com/synthetic/xr16/lifecycle-notice"
    assert edge["evidence"] == ("The XR16 4TTR6036 is discontinued and is replaced by the XR99 (synthetic) "
                                "in this synthetic notice.")
    run_id = edge["brief_run_id"]
    assert run_id.startswith("lifecycle-") and f"lookup {run_id}: 1 graph edge(s) saved" in out
    assert not config.GRAPH_PATH.exists() and not config.LEDGER_PATH.exists()

    rows = Ledger(config.REPLAY_LEDGER_PATH).rows(run_id)
    reserved = [r for r in rows if r["kind"] == "reserve" and r["provider"] == "anthropic"]
    assert [r["node"] for r in reserved] == ["research", "research", "synthesize"]
    assert {r["mode"] for r in rows} == {"replay"}
    record = json.loads((sweep_data / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
    assert record["kind"] == lifecycle.RECORD_KIND and record["status"] == lifecycle.SAVED
    assert record["appliance_id"] == "appl-xr16" and record["graph_edges"]["written"] == 1

    page = page_of("prop-a")
    xr_entry = entry(page, "appl-xr16")
    # The quote never names Trane, so only the successor model is shown (finding B4).
    assert f"Successor named in the evidence: <strong>{esc(SUCCESSOR)}</strong>" in xr_entry
    assert esc(edge["evidence"]) in xr_entry and f"evidence sha256 {edge['evidence_sha256']}" in xr_entry
    assert "XR77" not in page

    cassette = load_cassette(XR_CASSETTE)
    ctx = replay_ctx(sweep_data / "again", cassette, run_id="lifecycle-check")
    lifecycle.lookup(open_registry(config.REGISTRY_PATH, seed=False).get_appliance("appl-xr16"), ctx)
    system = ctx.replay["models"]["research"].received[0][0].content
    assert system == lifecycle.prompts.LIFECYCLE_RESEARCH_PROMPT


def test_replay_lookup_with_nothing_documented_saves_nothing(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The water heater's sources name no successor: the structured call is made and charged, no edge
    is saved, and the page keeps the no discontinuation sentence. Mutation:
    lifecycle_extract_skipped_with_sources."""
    code, out, err = sweep_cmd(capsys, "prop-b", "--research", "--cassette", str(RHEEM_CASSETTE),
                               "--cassette", str(SX_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    line = next(ln for ln in out.splitlines() if "appl-waterheater Rheem PRO+E50 M2 RH92 CL: lookup" in ln)
    assert "no successor verified in the sources found; nothing saved" in line
    run_id = line.split("lookup ", 1)[1].split(":", 1)[0]
    charges = [r for r in Ledger(config.REPLAY_LEDGER_PATH).rows(run_id) if r["kind"] == "charge"]
    assert [r["node"] for r in charges if r["provider"] == "anthropic"] == ["research", "research", "synthesize"]
    assert not config.REPLAY_GRAPH_PATH.exists(), "nothing verified, so no graph file is written"
    record = json.loads((sweep_data / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
    assert record["status"] == lifecycle.NOTHING_SAVED and record["kept"] == []
    assert sweep.NONE_DOCUMENTED in entry(page_of("prop-b"), "appl-waterheater")


def test_invented_model_is_refused_before_any_call_and_nothing_is_saved(
        sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Invented and synthetic units are refused before any call, from the CLI and in lookup itself:
    no model or tool is built, no ledger row is written, no edge is saved. Mutations:
    sweep_refusal_synthetic_off, sweep_refusal_unknown_maker_off, lifecycle_eligibility_ignores_refusal,
    lifecycle_lookup_refusal_off."""
    code, out, err = sweep_cmd(capsys, "prop-b", "--research", "--cassette", str(SX_CASSETTE))
    assert code == cli.EXIT_OK, out + err
    assert ("  appl-sx100 Synthetic Appliance Co. SX-100 (synthetic): refused before any call: "
            f"{sweep.REFUSED_SYNTHETIC}; nothing is saved") in out
    assert "SX-900" not in out and "SX-900" not in page_of("prop-b")
    assert not config.REPLAY_GRAPH_PATH.exists()
    runs = sorted((sweep_data / "runs").glob("*.json")) if (sweep_data / "runs").exists() else []
    assert all(json.loads(p.read_text())["appliance_id"] != "appl-sx100" for p in runs)
    ledger_rows = Ledger(config.REPLAY_LEDGER_PATH).rows() if config.REPLAY_LEDGER_PATH.exists() else []
    assert ledger_rows == [], "the only eligible unit (the water heater) has no cassette here"

    # lookup refuses again on its own, before it builds a model, a tool or a ledger.
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    ctx = replay_ctx(sweep_data / "direct", load_cassette(SX_CASSETTE), run_id="lifecycle-sx")
    result = lifecycle.lookup(registry.get_appliance("appl-sx100"), ctx)
    assert result["status"] == lifecycle.REFUSED and result["stop_reason"] == sweep.REFUSED_SYNTHETIC
    assert ctx.replay == {} and not Path(ctx.ledger_path).exists() and not Path(ctx.graph_path).exists()

    assert sweep.lookup_refusal({"manufacturer": "Trane", "model": "XR99 (synthetic)"}) == sweep.REFUSED_SYNTHETIC
    assert sweep.lookup_refusal({"manufacturer": "Aquarest", "model": "ZX-9000 Pro"}) == sweep.REFUSED_UNKNOWN_MAKER
    assert sweep.lookup_refusal({"manufacturer": "Trane", "model": ""}) == sweep.REFUSED_NO_IDENTITY
    assert sweep.lookup_refusal(registry.get_appliance("appl-xr16")) is None


def test_lookup_run_cap_is_what_is_left_of_the_sweep_cap_and_stops_the_lookup(sweep_data: Path) -> None:
    """Each lookup runs under what is left of the sweep's one cap (config.RUN_CAP_USD for the mode,
    the cheap cap in replay); a cassette may lower it, never raise it; a lookup that reaches it is
    stopped by the ledger and saves nothing. The build cap is not changed. Mutations:
    lifecycle_cap_not_clamped, lifecycle_plan_worst_from_mode."""
    cap = config.RUN_CAP_USD["cheap"]
    assert lifecycle.lookup_caps(None, cap) == {"run_cap_usd": cap}
    assert lifecycle.lookup_caps({"run_cap_usd": cap * 10}, cap / 2)["run_cap_usd"] == cap / 2
    assert lifecycle.lookup_caps({"run_cap_usd": cap / 3, "run_credit_cap": 2}, cap) == {"run_cap_usd": cap / 3,
                                                                                          "run_credit_cap": 2}
    assert {mode: lifecycle.sweep_cap_usd(mode) for mode in config.MODES} == {
        "replay": cap, "cheap": cap, "full": config.RUN_CAP_USD["full"]}
    registry = open_registry(config.REGISTRY_PATH)
    xr16 = registry.get_appliance("appl-xr16")
    for mode in ("cheap", "full"):
        assert lifecycle.sweep_plan(mode, [xr16]).worst_usd == config.RUN_CAP_USD[mode]

    data = json.loads(XR_CASSETTE.read_text(encoding="utf-8"))
    heavy = copy.deepcopy(data)
    # One research call charged about twice the cap at replay prices; the cassette asks for a cap ten times
    # higher, which would leave room for the rest of the lookup.
    tokens = int(2 * cap / config.REPLAY_PRICES["input"] * 1_000_000)
    heavy["research"]["script"][0]["message"]["usage"] = {"input_tokens": tokens, "output_tokens": 250}
    heavy["caps"] = {"run_cap_usd": cap * 10}
    path = sweep_data / "heavy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(heavy), encoding="utf-8")
    cassette = load_cassette(path)
    ctx = replay_ctx(sweep_data / "capped", cassette, run_id="lifecycle-capped")
    ctx.caps = lifecycle.lookup_caps(ctx.caps, lifecycle.sweep_cap_usd("replay"))
    result = lifecycle.lookup(xr16, ctx)
    assert result["status"] == lifecycle.STOPPED and result["stop_reason"] == "run_cap"
    assert not Path(ctx.graph_path).exists()
    rows = Ledger(ctx.ledger_path).rows("lifecycle-capped")
    assert any(r["kind"] == "stop" and r["note"].startswith("run_cap") for r in rows)


# ---------------------------------------------------------------------------
# The live preflight, on fake clients
# ---------------------------------------------------------------------------


# Every live client constructor agent.models may use; each is replaced before a live command runs,
# and the Anthropic base class refuses to build at all, so no real client can exist in these tests.
LIVE_CHAT_NAMES = ("ChatAnthropic", "DeclineAwareChatAnthropic")
LIVE_TOOL_NAMES = ("TavilySearch", "TavilyExtract")


def open_reservations(provider: str) -> int:
    """Reservations for this provider in the live ledger that nothing has settled yet."""
    rows = Ledger(config.LEDGER_PATH).rows()
    reserved = {r["id"] for r in rows if r["kind"] == "reserve" and r["provider"] == provider}
    settled = {int(r["note"].split()[1]) for r in rows if r["kind"] == "release"}
    return len(reserved - settled)


def block_real_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a real Anthropic client was constructed")

    monkeypatch.setattr(ChatAnthropic, "__init__", refuse)


def fake_key(prefix: str, seed: str) -> str:
    """A key shaped value built at run time, so no source file holds one."""
    return prefix + hashlib.sha256(seed.encode()).hexdigest()[:40]


@pytest.fixture
def live_sweep(sweep_data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cheap mode with fake key values in a temp .env (never the repo's); real clients refused."""
    for name in (*config.LIVE_KEY_NAMES, config.TYPESAFE_KEY_NAME):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / "sweep.env"
    env.write_text(f"ANTHROPIC_API_KEY={fake_key('sk' + '-ant-' + 'api03-', 'sweep-anthropic')}\n"
                   f"TAVILY_API_KEY={fake_key('tvly' + '-' + 'dev-', 'sweep-tavily')}\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 1))
    block_real_clients(monkeypatch)
    return sweep_data


def install_fakes(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> dict[str, Any]:
    """Fake live clients serving a cassette's script; each records whether its call was reserved first."""
    from agent.replay.replay_model import ReplayChatModel
    from agent.replay.tool_stubs import StubInner

    seen: dict[str, Any] = {"chats": {}, "stubs": {}, "checks": []}

    class ReservedChat(ReplayChatModel):
        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
            seen["checks"].append(open_reservations("anthropic") > 0)
            return super()._generate(messages, stop, run_manager, **kwargs)

    class ReservedStub(StubInner):
        def invoke(self, input: dict[str, Any]) -> Any:
            seen["checks"].append(open_reservations("tavily") > 0)
            return super().invoke(input)

    def chat(**kwargs: Any) -> Any:
        node = "research" if kwargs["max_tokens"] == models.max_tokens_for("research", "cheap") else "synthesize"
        if node not in seen["chats"]:
            responses = (data["research"]["script"] if node == "research"
                         else [{"structured": a["draft"], "usage": a["usage"]} for a in data["synthesize"]])
            seen["chats"][node] = ReservedChat(responses=copy.deepcopy(responses), model=kwargs["model"],
                                               max_tokens=kwargs["max_tokens"])
        return seen["chats"][node]

    def tool(name: str) -> Any:
        if name not in seen["stubs"]:
            results = [{k: v for k, v in e.items() if k != "tool"}
                       for e in data["research"]["tool_results"] if e["tool"] == name]
            seen["stubs"][name] = ReservedStub(name, results)
        return seen["stubs"][name]

    for name in LIVE_CHAT_NAMES:
        if hasattr(models, name):
            monkeypatch.setattr(models, name, chat)
    monkeypatch.setattr(models, "TavilySearch", lambda **kw: tool("search"))
    monkeypatch.setattr(models, "TavilyExtract", lambda **kw: tool("fetch"))
    return seen


def test_research_preflight_lists_planned_calls_and_cost_and_needs_proceed(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A live --research prints one plan for the sweep, naming each eligible unit only with its
    planned cost from config prices, and anything but "proceed" builds no client, writes no ledger
    row, saves no edge and writes no page. Mutations: cli_sweep_research_skips_confirm,
    lifecycle_plan_drops_extract_call."""
    built: list[str] = []
    for name in (*LIVE_CHAT_NAMES, *LIVE_TOOL_NAMES):
        if hasattr(models, name):
            monkeypatch.setattr(models, name, lambda *a, _n=name, **k: built.append(_n))
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_REFUSED, out + err
    assert 'the typed confirmation was not exactly "proceed"; nothing was spent.' in err
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    assert "advisor property sweep: preflight, mode cheap, LIVE (real money)" in out
    units = [registry.get_appliance(a) for a in ("appl-optima880", "appl-xr16")]
    plan = lifecycle.sweep_plan("cheap", units)
    block = out.split(f"planned: {plan.label}\n", 1)[1]
    assert block.startswith("  model calls: typical 8, at most 12\n")
    assert f"  estimated cost: typical {plan.typical_usd:.4f} USD, worst case {plan.worst_usd:.4f} USD" in block
    for i, unit in enumerate(units, start=1):
        one = lifecycle.lookup_plan("cheap", unit)
        assert one.typical_calls == 4 and one.max_calls == 6
        assert (f"  note: lookup {i}: {lifecycle.unit_label(unit)}, typical {one.typical_usd:.4f} USD, "
                f"at most {one.worst_usd:.4f} USD") in block
    assert "appl-dishwasher Synthetic" not in block
    assert "Type proceed to spend this" in out
    assert built == []
    assert Ledger(config.LEDGER_PATH).rows() == []
    assert not config.GRAPH_PATH.exists() and not config.PROPERTY_OUT_DIR.exists()


def test_live_lookup_on_fakes_reserves_before_every_call_and_saves_to_the_runtime_graph(
        live_sweep: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """After "proceed", the one eligible unit is looked up on fake clients: every call is reserved
    first, the lookup stays under its run cap, and the verified edge lands in the runtime graph,
    which the page then shows. Mutation: lifecycle_live_cap_not_passed."""
    kg = KnowledgeGraph()  # the Optima already has a lifecycle fact, so only the XR16 is looked up
    add_part(kg, *OPTIMA, "OP-5501", PARTS_URL, "Heater OP-5501 is discontinued for the Optima 880 (synthetic).")
    kg.save(config.GRAPH_PATH)
    fakes = install_fakes(monkeypatch, json.loads(XR_CASSETTE.read_text(encoding="utf-8")))
    seen_caps: list[Any] = []
    real_reserve = Ledger.reserve

    def reserve(self: Ledger, run_id: str, **kwargs: Any) -> Any:
        seen_caps.append(kwargs.get("run_cap_usd"))
        return real_reserve(self, run_id, **kwargs)

    monkeypatch.setattr(Ledger, "reserve", reserve)
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    code, out, err = sweep_cmd(capsys, "prop-a", "--research", "--live", "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    assert "appl-optima880 Sundance Spas Optima 880: the graph already holds lifecycle facts for it" in out
    assert set(fakes["chats"]) == {"research", "synthesize"} and fakes["stubs"]["search"].calls == 1
    checks = fakes["checks"]
    assert len(checks) == 4 and all(checks), checks
    assert seen_caps and set(seen_caps) == {config.RUN_CAP_USD["cheap"]}
    run_id = next(e["brief_run_id"] for e in KnowledgeGraph.load(config.GRAPH_PATH).superseded_by(*XR16))
    ledger = Ledger(config.LEDGER_PATH)
    assert {r["mode"] for r in ledger.rows(run_id)} == {"cheap"}
    assert 0 < ledger.run_total(run_id) <= config.RUN_CAP_USD["cheap"]
    assert f"<strong>{esc(SUCCESSOR)}</strong>" in entry(page_of("prop-a"), "appl-xr16")
    assert not config.REPLAY_GRAPH_PATH.exists()


# ---------------------------------------------------------------------------
# Escaping and coverage
# ---------------------------------------------------------------------------


def test_replacement_section_escapes_every_value(tmp_path: Path) -> None:
    """Hostile registry values and graph values are escaped in the new section, in text and in the
    href. Mutations: property_page_fact_successor_raw, property_page_fact_quote_raw,
    property_page_age_raw, property_page_fact_part_raw, property_page_fact_title_raw."""
    from agent.tests.test_render import PAGE_HOSTILE, _hostile_seed, assert_self_contained, parse

    registry = open_registry(tmp_path / "hostile.sqlite", seed=_hostile_seed(tmp_path))
    xr = registry.get_appliance("appl-xr16")
    successor = "XR99 (synthetic)" + PAGE_HOSTILE
    part = "FK-1234" + PAGE_HOSTILE
    url = "https://maker.example.com/notice" + PAGE_HOSTILE
    kg = KnowledgeGraph()
    add_superseded(kg, xr["manufacturer"], xr["model"], successor, url,
                   f"Replaced by the {successor} per the synthetic notice.", retrieved_at="R" + PAGE_HOSTILE,
                   title="Notice (synthetic)" + PAGE_HOSTILE)
    add_part(kg, xr["manufacturer"], xr["model"], part, PARTS_URL,
             f"Part {part} is discontinued; the {successor} replaces the unit.")
    page = render_property_page("prop-a", registry, graph=kg, generated_at=AS_OF.isoformat())
    tree = assert_self_contained(page)
    text = section(page)
    for value in (successor, part, url, "R" + PAGE_HOSTILE, "Notice (synthetic)" + PAGE_HOSTILE,
                  xr["install_date"], xr["manufacturer"], xr["model"]):
        assert esc(value) in text, value
        assert value not in page, value
    assert PAGE_HOSTILE not in page and "<script>" not in page
    assert url in parse(page).hrefs and tree.problems == []


def test_all_covers_both_properties(sweep_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--all sweeps and writes every property; a property must be named or --all given; an unknown
    property and the research flags without --research are refused. Mutation: cli_sweep_all_first_only."""
    code, out, err = sweep_cmd(capsys, "--all")
    assert code == cli.EXIT_OK, out + err
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    properties = [p["id"] for p in registry.list_properties()]
    assert properties == ["prop-a", "prop-b"]
    for property_id in properties:
        assert f"advisor property sweep: {property_id}, " in out
        page = page_of(property_id)
        for appliance in registry.list_appliances(property_id):
            assert f"  {appliance['id']} " in out
            assert f'<p class="meta">{appliance["id"]} &middot;' in section(page)
    with pytest.raises(SystemExit):
        cli.main(["property", "sweep"])
    with pytest.raises(SystemExit):
        cli.main(["property", "sweep", "prop-a", "--all"])
    capsys.readouterr()
    code, out, err = sweep_cmd(capsys, "prop-missing")
    assert code == cli.EXIT_REFUSED and "unknown property" in err
    for flag in ("--live", "--new-ledger"):
        code, out, err = sweep_cmd(capsys, "prop-a", flag)
        assert code == cli.EXIT_REFUSED and cli.SWEEP_FLAGS_REFUSAL in err
