"""SC8 and G3: service history and registry dates reach the brief (PLAN section 5; decision 29).

Each test replays the synthetic cassette optima_history (every field
synthetic, page text only on example.com URLs) through the real graph with a
seeded synthetic registry under tmp_path, and the appliance appl-optima880,
whose one synthetic service record is service:svc-0001. The helpers here are
shared by the other Phase 3 graph tests. Each test names the mutation that
turns it red.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import config
from agent.registry import Registry, open_registry
from agent.replay.cassettes import cassette_from_dict
from agent.rules.citations import age_statement
from agent.tests.helpers import load_case, replay_ctx, resume_command

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_GRAPH = FIXTURES / "graphs" / "optima_flo.json"
FIXTURE_PAGES = FIXTURES / "pages"
OPTIMA = "appl-optima880"
SEEDED_RECORD = "service:svc-0001"
# A second synthetic service record, on another appliance, for the variant
# that cites a record this appliance does not own.
OTHER_RECORD = {"id": "svc-9001", "appliance_id": "appl-xr16", "date": "2024-07-02",
                "symptom": "Synthetic record on another appliance: outdoor fan did not spin.",
                "observed_code": None, "work_done": "Synthetic: replaced the capacitor.",
                "performed_by": "Synthetic technician", "synthetic": True}


# ---------------------------------------------------------------------------
# Shared helpers (also imported by the SC7a, fan out and persist tests)
# ---------------------------------------------------------------------------


def seed_registry(path: Path, *, extra_records: list[dict] = (), drop_records: bool = False) -> Registry:
    """A registry at `path` from the synthetic seed, optionally edited (still all synthetic)."""
    seed = json.loads(config.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    if drop_records:
        seed["service_records"] = []
    seed["service_records"] = [*seed["service_records"], *extra_records]
    seed_path = path.with_name("seed_for_test.json")
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    return open_registry(path, seed=seed_path)


def copy_fixture_pages(pages_dir: Path) -> None:
    """Put the synthetic fixture page texts where the run reads raw text: pages/<sha256>.txt."""
    pages_dir.mkdir(parents=True, exist_ok=True)
    for path in FIXTURE_PAGES.glob("*.txt"):
        raw = path.read_bytes()
        (pages_dir / f"{hashlib.sha256(raw).hexdigest()}.txt").write_bytes(raw)


def run_phase3_case(cassette, tmp_path: Path, *, appliance_id: str | None = None, thread_id: str = "thread-1",
                    registry: dict | None = None, graph: Path | None = None, pages: bool = False,
                    ctx=None):
    """Run a cassette through the real graph with a seeded registry; resume with its answer if it pauses.

    `graph` is copied to the run's graph path first; `pages` puts the fixture
    page texts in the run's pages folder; `ctx` is a RunContext made by the
    caller (helpers.replay_ctx) when it needs the run's objects up front.
    Returns (graph, ctx, outcomes).
    """
    from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end
    from agent.nodes.intake import intake_input

    ctx = ctx or replay_ctx(tmp_path, cassette, run_id=thread_id)
    seed_registry(Path(ctx.registry_path), **(registry or {}))
    if graph is not None:
        Path(ctx.graph_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(graph, ctx.graph_path)
    if pages:
        copy_fixture_pages(Path(ctx.pages_dir))
    inp = cassette.input
    data = intake_input(symptom=inp["symptom"], identity=inp["identity"], appliance_id=appliance_id)
    data["html_path"] = str(tmp_path / "brief.html")
    compiled = build_graph(open_checkpointer(tmp_path / "checkpoints.sqlite"))
    outcomes = [run_until_pause_or_end(compiled, data, ctx, thread_id)]
    if outcomes[0].paused and cassette.resume is not None:
        outcomes.append(run_until_pause_or_end(compiled, resume_command(cassette), ctx, thread_id))
    return compiled, ctx, outcomes


def variant(cassette, label: str, edit) -> Any:
    """A copy of a synthetic cassette with `edit(data)` applied, validated like any cassette."""
    data = copy.deepcopy(cassette.data)
    edit(data)
    return cassette_from_dict(data, f"{cassette.case} ({label})")


def synth_request(ctx) -> str:
    """The user message of the one synthesize request ReplayChatModel recorded."""
    [request] = ctx.replay["models"]["synthesize"].received
    return request[-1].content


def final_state(outcomes: list) -> dict[str, Any]:
    assert not outcomes[-1].paused
    return outcomes[-1].state


# ---------------------------------------------------------------------------
# SC8
# ---------------------------------------------------------------------------


def test_happened_before_cites_prior_record(tmp_path: Path) -> None:
    """Mutations: sc8_history_not_in_prompt (history_hits left out of the
    synthesize prompt); sc8_record_id_unchecked (a record ID that is not loaded
    is kept); sc8_render_record_date_dropped (the brief no longer shows the
    record's date)."""
    cassette = load_case("synthetic/optima_history")
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path / "seeded", appliance_id=OPTIMA)
    state = final_state(outcomes)
    assert state["status"] == "ok"
    assert "history" in state["route"]

    registry = Registry(ctx.registry_path)
    record = registry.get_service_record(SEEDED_RECORD)
    user = synth_request(ctx)
    for text in (record["record_id"], record["date"], record["symptom"]):
        assert text in user, text

    hb = state["brief"]["happened_before"]
    assert hb["matches"] is True
    assert hb["record_id"] == SEEDED_RECORD
    assert record["appliance_id"] == state["appliance_id"] == OPTIMA
    assert record["date"] not in hb["summary"], "the date must come from the record, not the model"

    html = Path(state["html_path"]).read_text(encoding="utf-8")
    assert "This has happened before" in html
    assert f"Service record {SEEDED_RECORD}, dated {record['date']}." in html

    for label, record_id in (("other_appliance", "service:" + OTHER_RECORD["id"]),
                             ("nonexistent", "service:svc-9999")):
        def cite(data: dict, record_id: str = record_id) -> None:
            data["synthesize"][0]["draft"]["happened_before"]["record_id"] = record_id

        _, vctx, vout = run_phase3_case(variant(cassette, label, cite), tmp_path / label, appliance_id=OPTIMA,
                                        registry={"extra_records": [OTHER_RECORD]})
        vstate = final_state(vout)
        assert vstate["status"] == "ok", label
        if label == "other_appliance":
            other = Registry(vctx.registry_path).get_service_record(record_id)
            assert other is not None and other["appliance_id"] != OPTIMA
        assert vstate["brief"]["happened_before"] is None, label
        assert "This has happened before" not in Path(vstate["html_path"]).read_text(encoding="utf-8")


def test_no_history_means_null(tmp_path: Path) -> None:
    """Mutations: sc8_model_happened_before_kept (the model's value passes
    through when no record backs it); sc8_appliance_entry_counts_as_record (the
    appliance's own registry entry is accepted as a prior event)."""
    cassette = load_case("synthetic/optima_history")
    for label, record_id in (("seeded_id", SEEDED_RECORD), ("appliance_entry", f"appliance:{OPTIMA}")):
        def cite(data: dict, record_id: str = record_id) -> None:
            data["synthesize"][0]["draft"]["happened_before"]["record_id"] = record_id

        _, ctx, outcomes = run_phase3_case(variant(cassette, label, cite), tmp_path / label, appliance_id=OPTIMA,
                                           registry={"drop_records": True})
        state = final_state(outcomes)
        assert Registry(ctx.registry_path).service_history(OPTIMA) == []
        assert [h["kind"] for h in state["history_hits"]] == ["appliance"]
        from agent.prompts import NO_RECORDS

        assert NO_RECORDS in synth_request(ctx)
        assert cassette.data["synthesize"][0]["draft"]["happened_before"]["matches"] is True
        assert state["status"] == "ok"
        assert state["brief"]["happened_before"] is None, label


def test_age_from_registry_date(tmp_path: Path) -> None:
    """Mutations: sc8_registry_not_in_prompt (the registry dates and terms left
    out of the synthesize prompt); sc8_install_date_ignored (the age is taken
    from the purchase date instead of the install date)."""
    cassette = load_case("synthetic/optima_history")
    scripted = cassette.data["synthesize"][0]["draft"]["warranty"]["age_statement"]
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path, appliance_id=OPTIMA)
    state = final_state(outcomes)
    appliance = Registry(ctx.registry_path).get_appliance(OPTIMA)

    user = synth_request(ctx)
    for text in (appliance["record_id"], appliance["install_date"], appliance["purchase_date"],
                 appliance["warranty_terms"]):
        assert text in user, text

    warranty = state["brief"]["warranty"]
    today = datetime.now(timezone.utc).date()
    expected, record_id = age_statement(registry=appliance, manufacture_date=None, today=today)
    assert warranty["age_statement"] == expected
    assert expected.startswith(f"Installed {appliance['install_date']} per property record {appliance['record_id']}")
    assert warranty["record_id"] == record_id == f"appliance:{OPTIMA}"
    assert warranty["terms"] == appliance["warranty_terms"]
    html = Path(state["html_path"]).read_text(encoding="utf-8")
    assert expected in html
    assert scripted not in json.dumps(state["brief"]) and scripted not in html
