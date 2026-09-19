"""The recording candidate a finished live run leaves (PLAN 8.10, Phase 6 "new cassettes").

Driven through `advisor ask/resume --live` on fake clients with the network
blocked. The candidate must load with load_cassette, replay to the same
outcome, name its provenance, and never hold a key, a header, the photo
bytes or page text on a real URL (decision 15).

Every test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent import cli, config
from agent.replay import privacy_diff
from agent.replay.cassettes import load_cassette
from agent.tests.test_live_path import (  # noqa: F401  (live_env is a fixture)
    FLO_CASSETTE,
    PLATE,
    RESUME_FLAGS,
    SYMPTOM,
    LiveFakes,
    fake_key,
    flo_data,
    live_env,
    ran,
    run_flo_live,
    run_record,
    thread_of,
)

REAL_URL = "https://www.sundancespas.com/owners/manuals/optima-880?utm_source=newsletter&page=4"
REAL_URL_CLEAN = "https://www.sundancespas.com/owners/manuals/optima-880?page=4"
REAL_SNIPPET = "Optima 880 owner manual, chapter four, heater and flow."
REAL_BODY = "Optima 880 owner manual. Chapter four covers the heater and flow switch in full."


def recording_files(data_dir: Path) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(cassette path, cassette, privacy report, withheld texts) of the one recording written."""
    folder = data_dir / "recordings"
    [path] = [p for p in folder.glob("live_*.json") if p.name.count(".") == 1]
    stem = path.stem
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
    return path, read(path), read(folder / f"{stem}.privacy.json"), read(folder / f"{stem}.texts.json")


def test_recording_loads_as_a_cassette_and_replays(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: recorder_drops_tool_calls (research replies lose their tool calls); recorder_provenance_wrong;
    recorder_structured_as_text (a structured reply is stored unparsed); recorder_no_resume (the owner's
    confirmation is not recorded); recorder_capture_off (no reply is captured)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    thread_id, live_out = run_flo_live(monkeypatch, capsys, fakes)
    path, cassette, report, _ = recording_files(live_env)
    load_cassette(path)
    assert "loads as a cassette: yes" in live_out
    today = cli._today().isoformat()
    assert cassette["provenance"]["derived_from"] == f"recorded live {today} run {thread_id}"
    assert cassette["provenance"]["built_by"] == "agent/live/recorder.py" and cassette["provenance"]["reviewed"] is None
    source = json.loads(FLO_CASSETTE.read_text(encoding="utf-8"))
    assert cassette["read_plate"]["extraction"] == source["read_plate"]["extraction"]
    assert [s["draft"] for s in cassette["synthesize"]] == [s["draft"] for s in source["synthesize"]]
    assert [s["message"]["tool_calls"] for s in cassette["research"]["script"]] == \
        [[{k: c[k] for k in ("name", "args", "id")} for c in s["message"]["tool_calls"]]
         for s in source["research"]["script"]]
    assert cassette["resume"]["observed_code"] == "FLO"
    assert report["loadable"] is True and report["verdict"].startswith("INCOMPLETE")

    # Replaying the recording gives the same nodes and the same outcome as the live run.
    monkeypatch.setenv(config.ENV_MODE, "replay")
    assert cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--cassette", str(path)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert cli.main(["resume", thread_of(out), *RESUME_FLAGS, "--cassette", str(path)]) == cli.EXIT_OK
    out += capsys.readouterr().out
    assert ran(out) == ran(live_out)
    assert "status: ok" in out


def test_recording_holds_no_key_photo_bytes_or_real_page_text(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: recorder_keeps_real_page_text (decision 15 ignored); recorder_keeps_tracking_params;
    recorder_records_photo_path."""
    data = flo_data()
    decoy = data["research"]["tool_results"][0]["results"][0]  # never cited by the draft
    decoy.update({"url": REAL_URL, "content": REAL_SNIPPET, "raw_content": REAL_BODY})
    fakes = LiveFakes(data).install(monkeypatch)
    run_flo_live(monkeypatch, capsys, fakes)
    path, cassette, report, texts = recording_files(live_env)
    load_cassette(path)
    text = path.read_text(encoding="utf-8")

    recorded = cassette["research"]["tool_results"][0]["results"][0]
    [withheld] = texts["withheld"]
    # The page text stays out; only its hash is kept, so a replay can read it locally (D4).
    assert recorded == {"url": REAL_URL_CLEAN, "title": recorded["title"], "content": "", "raw_content": None,
                        "score": recorded["score"], "text_sha256": withheld["raw_content_sha256"]}
    assert REAL_SNIPPET not in text and REAL_BODY not in text
    assert withheld["url"] == REAL_URL_CLEAN
    pages = Path(texts["pages_dir"])
    assert (pages / f"{withheld['content_sha256']}.txt").read_text(encoding="utf-8") == REAL_SNIPPET
    assert (pages / f"{withheld['raw_content_sha256']}.txt").read_text(encoding="utf-8") == REAL_BODY
    assert report["withheld_texts"] == 1

    photo_b64 = base64.b64encode(PLATE.read_bytes()).decode("ascii")
    assert photo_b64[:200] not in text and str(PLATE) not in text and PLATE.name not in text
    assert cassette["input"]["plate_sha256"] and cassette["input"]["identity"] is None
    for pattern in (*privacy_diff.KEY_RES.values(), privacy_diff.HEADER_RE):
        assert not pattern.search(text)
    capture = next((live_env / "recordings").glob("*.capture.jsonl")).read_text(encoding="utf-8")
    assert photo_b64[:200] not in capture and "base64" not in capture


def test_key_shaped_text_withholds_the_recording(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: recorder_key_hits_written (a key pattern hit still writes the cassette);
    recorder_report_shows_key_prefix (the report keeps the start of the matched key)."""
    stray = fake_key("sk" + "-ant-" + "api03-", "a key nobody loaded")
    data = flo_data()
    data["research"]["script"][0]["message"]["content"] = f"found {stray} on a page"
    fakes = LiveFakes(data).install(monkeypatch)
    _, out = run_flo_live(monkeypatch, capsys, fakes)
    folder = live_env / "recordings"
    assert [p for p in folder.glob("live_*.json") if p.name.count(".") == 1] == []
    [report_path] = folder.glob("*.privacy.json")
    report = report_path.read_text(encoding="utf-8")
    assert json.loads(report)["verdict"].startswith("WITHHELD")
    assert stray[:10] not in report and "recording candidate: withheld" in out


def test_privacy_report_applies_the_local_denylist(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: recorder_denylist_ignored."""
    staging = config.STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)
    (staging / privacy_diff.DENYLIST_FILE).write_text("# local names\nOptima\n", encoding="utf-8")
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    run_flo_live(monkeypatch, capsys, fakes)
    _, _, report, _ = recording_files(live_env)
    assert report["verdict"] == "BLOCKED" and report["denylist_entries"] == 1
    assert any(hit["kind"] == "denylist" for hit in report["hits"])


def test_staged_recording_passes_the_privacy_diff_with_its_copy_log(
        live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: recorder_copy_log_not_written (the candidate has no copy log, so the PLAN 8.10 diff
    blocks it); recorder_copy_log_skips_lookups (strings from the lookup log are not listed as copies).
    Finding P1: a live recording staged the way next_step says must reach the diff unblocked."""
    import shutil

    fakes = LiveFakes(flo_data()).install(monkeypatch)
    _, out = run_flo_live(monkeypatch, capsys, fakes)
    path, cassette, report, _ = recording_files(live_env)
    case = cassette["case"]
    log = live_env / "recordings" / privacy_diff.COPY_LOG_SUBDIR / f"{case}.copies.json"
    assert report["copy_log"] == str(log) and f"copy log: {log}" in out
    assert "copy_logs" in report["next_step"] and privacy_diff.CASSETTES_SUBDIR in report["next_step"]

    staging = tmp_path / "staging_for_diff"
    (staging / privacy_diff.CASSETTES_SUBDIR).mkdir(parents=True)
    (staging / privacy_diff.COPY_LOG_SUBDIR).mkdir(parents=True)
    shutil.copy(path, staging / privacy_diff.CASSETTES_SUBDIR / path.name)
    shutil.copy(log, staging / privacy_diff.COPY_LOG_SUBDIR / log.name)
    (staging / privacy_diff.DENYLIST_FILE).write_text("# local names\nnobody-by-this-name\n", encoding="utf-8")
    results, report_text = privacy_diff.run(staging, status=lambda: "(stub)")
    [result] = [r for r in results if r.name.endswith(path.name)]
    assert [h for h in result.hits if h["kind"] in ("missing copy log", "copy log mismatch")] == []
    assert result.hits == [], result.hits
    assert "Result: CLEAN" in report_text
    sources = {c["dest"].split("[", 1)[0].split(".", 1)[0]: c["source"] for c in result.copies}
    assert {"read_plate", "research", "synthesize", "input"} <= set(sources)
    tool_copies = [c for c in result.copies if c["dest"].startswith("research.tool_results")]
    assert tool_copies and all("lookup log line" in c["source"] for c in tool_copies)
    assert any("capture.jsonl line" in c["source"] for c in result.copies if c["dest"].startswith("synthesize"))
    assert [h["matches"] for h in result.hashed] == ["demo-assets/plate-clear.jpg"]
    assert not any(c["dest"].startswith("provenance") for c in result.copies)  # the recorder wrote those


def test_phase5_run_record_carries_the_v1_checks(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: cli_live_v1_checks_dropped (the live run record leaves out PLAN 6.5's E1 and B2 checks;
    finding P4)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    thread_id, out = run_flo_live(monkeypatch, capsys, fakes)
    record = json.loads((live_env / "runs" / f"{thread_id}.json").read_text(encoding="utf-8"))
    checks = {c["case"]: c for c in record["v1_checks"]}
    assert set(checks) == {"E1", "B2"}
    # The synthetic plate reply leaves the serial unreadable, so E1's serial check is a reported miss.
    e1 = {c["check"]: c["passed"] for c in checks["E1"]["asserted"]}
    assert e1["model matches Optima 880"] and e1["manufacturer matches Sundance"]
    assert e1[f"serial equals {config.E1_PLATE_SERIAL}"] is False and checks["E1"]["passed"] is False
    assert checks["E1"]["reported"][0]["passed"] is True  # the raw reply came from the capture
    assert checks["B2"]["passed"] is True
    assert "v1 case B2: pass" in out and "v1 case E1: MISS" in out


def test_blocked_calls_counted_only_at_the_cap() -> None:
    """Mutation recorder_blocked_calls_any_gap: any shortfall of results is recorded
    as blocked, so a recording that lost a tool result would load and replay a
    run that never happened."""
    def script(*tools: str) -> list[dict]:
        return [{"message": {"content": "", "tool_calls": [{"name": t, "args": {}}], "usage": {}}} for t in tools]

    cap = config.RESEARCH_LIMITS["main"]["search"]
    six = script(*["search"] * (cap + 1), "fetch")
    sent = [{"tool": "search"}] * cap + [{"tool": "fetch"}]
    assert cli.recorder_blocked_calls(six, sent, "main") == {"search": 1}
    assert cli.recorder_blocked_calls(six, sent[1:], "main") == {}  # a gap below the cap is not a block
    assert cli.recorder_blocked_calls(six, sent, None) == {}
    assert cli.recorder_blocked_calls(script("search"), [{"tool": "search"}], "main") == {}



def test_parallel_calls_are_recorded_in_the_order_the_model_issued_them() -> None:
    """Mutation recorder_log_order_kept: tool results keep the lookup log's finishing
    order, so a recording of calls issued together fails to load (Phase 5)."""
    script = [{"message": {"content": "", "tool_calls": [
        {"name": "search", "args": {"query": q}} for q in ("a", "b", "c")], "usage": {}}},
        {"message": {"content": "", "tool_calls": [{"name": "fetch", "args": {"url": "https://example.com/x"}}],
                     "usage": {}}}]
    log = [{"tool": "search", "query": "c"}, {"tool": "fetch", "query": "https://example.com/x"},
           {"tool": "search", "query": "a"}, {"tool": "search", "query": "b"},
           {"tool": "search", "query": "unclaimed"}]
    ordered = cli.recorder_in_call_order(log, script)
    assert [(line, e["query"]) for line, e in ordered] == [
        (3, "a"), (4, "b"), (1, "c"), (2, "https://example.com/x"), (5, "unclaimed")]


REAL_FETCH_URL = "https://www.sundancespas.com/owners/manuals/optima-880-heater"
REAL_FETCH_BODY = "Optima 880 heater page. The flow switch opens when the filter is clogged."


def test_recorded_fetch_keeps_its_page_text_hash(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: recorder_fetch_text_hash_dropped (a fetch result loses its
    text_sha256, so a page only fetched, never searched, cannot be read back
    by replay). D4 asks the recorder to keep the hash on every result whose
    page text was saved; review finding T1."""
    data = flo_data()
    data["research"]["script"][1]["message"]["tool_calls"].append(
        {"name": "fetch", "args": {"url": REAL_FETCH_URL}})
    data["research"]["tool_results"].append(
        {"tool": "fetch", "url": REAL_FETCH_URL, "results": [{"url": REAL_FETCH_URL, "raw_content": REAL_FETCH_BODY}]})
    fakes = LiveFakes(data).install(monkeypatch)
    run_flo_live(monkeypatch, capsys, fakes)
    path, cassette, _, texts = recording_files(live_env)
    load_cassette(path)
    [fetched] = [e for e in cassette["research"]["tool_results"] if e["tool"] == "fetch"]
    [result] = fetched["results"]
    [withheld] = [w for w in texts["withheld"] if w["url"] == REAL_FETCH_URL]
    assert result["raw_content"] is None and REAL_FETCH_BODY not in path.read_text(encoding="utf-8")
    assert withheld["raw_content_sha256"] and result["text_sha256"] == withheld["raw_content_sha256"]
    page = Path(texts["pages_dir"]) / f"{result['text_sha256']}.txt"
    assert page.read_text(encoding="utf-8") == REAL_FETCH_BODY


def test_live_recording_replays_grounding_from_local_page_text(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: recorder_text_hash_dropped (results lose their text_sha256, so a
    replay of the recording cannot find the cited page and reports unverifiable
    even with the page text on disk); persist_edge_page_hash_dropped (the
    replayed edge stores no hash of the page it was checked against). D4: the cited page is on a real URL, so its
    text stays out of the cassette; replay reads it from the pages folder by
    hash (grounding verified), and without it reports unverifiable and still
    reaches ok."""
    data = flo_data()
    cited_url = "https://www.sundancespas.com/owners/manuals/optima-880-flo"
    for entry in data["research"]["tool_results"]:
        for result in entry.get("results") or []:
            if result["url"] == "https://example.com/synthetic/spa/flo-code":
                result["url"] = cited_url
    # A verbatim quote of the cited page, so replay has an edge to write.
    data["synthesize"][0]["draft"]["candidates"][1]["evidence"] = "Error FLO means the heater senses no water flow."
    fakes = LiveFakes(data).install(monkeypatch)
    thread_id, live_out = run_flo_live(monkeypatch, capsys, fakes)
    assert run_record(thread_id)["grounding_status"] == "verified"
    path, cassette, _, texts = recording_files(live_env)
    recorded = next(r for e in cassette["research"]["tool_results"] for r in e.get("results") or []
                    if r["url"] == cited_url)
    assert recorded["raw_content"] is None and recorded["content"] == ""
    assert recorded["text_sha256"] in {w["raw_content_sha256"] for w in texts["withheld"]}
    assert (live_env / "pages" / f"{recorded['text_sha256']}.txt").is_file()

    def replay() -> dict[str, Any]:
        assert cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--cassette", str(path)]) == cli.EXIT_OK
        out = capsys.readouterr().out
        thread = thread_of(out)
        assert cli.main(["resume", thread, *RESUME_FLAGS, "--cassette", str(path)]) == cli.EXIT_OK
        out += capsys.readouterr().out
        assert "status: ok" in out and ran(out) == ran(live_out)
        return run_record(thread)

    monkeypatch.setenv(config.ENV_MODE, "replay")
    # Without the page text (a fresh clone): ok, and the record says why grounding was not checked.
    monkeypatch.setattr(config, "PAGES_DIR", live_env / "no_pages")
    without = replay()
    assert without["status"] == "ok" and without["grounding_status"] == "unverifiable"
    assert [(g["source_url"], g["reason"]) for g in without["grounding"]] == [
        (cited_url, "replay of a live recording: the cited page's text is not in the local pages folder")]
    # With the page text in the local pages folder: grounding is checked and verified.
    monkeypatch.setattr(config, "PAGES_DIR", live_env / "pages")
    with_pages = replay()
    assert with_pages["status"] == "ok" and with_pages["grounding_status"] == "verified"
    assert [(g["source_url"], g["status"]) for g in with_pages["grounding"]] == [(cited_url, "verified")]
    # The edge names the page its evidence was checked against, though the
    # replayed source had no text hash of its own (persist_edge_page_hash_dropped).
    edges = json.loads(Path(with_pages["graph_edges"]["target"]).read_text(encoding="utf-8"))["edges"]
    assert [e["text_sha256"] for e in edges if e["source_url"] == cited_url] == [recorded["text_sha256"]]


# The fixed build's hot tub first lookup (run t-267045726dd04118), as its
# live recording sits, untracked, in the local data folder (or the one
# ADVISOR_PHASE5_DATA_DIR names, which the mutation gate sets, since its repo
# copies leave data out). It is read only here and not copied into the tests;
# these tests skip on a checkout without it. The Phase 5 recording these tests
# first used belonged to a build that was superseded, so it is no longer read.
PHASE5_DATA = Path(os.environ.get("ADVISOR_PHASE5_DATA_DIR") or config.REPO_ROOT / "data")
PHASE5 = PHASE5_DATA / "recordings" / "live_t_267045726dd04118.json"
PHASE5_TEXTS = PHASE5.with_name(f"{PHASE5.stem}.texts.json")
PHASE5_CITED = "https://thecoverguy.com/blogs/backyard-blast-blog/sundance-r-spas-error-codes-and-information"
PHASE5_PAGE_SHA = "b926708a0f7ed0534707b1ed63e7730b1663e47f0252ef7713888758f15c4e9d"
PHASE5_PAGES = PHASE5_DATA / "pages"


def _phase5_without_inline_hashes(dest: Path) -> Path:
    """A copy of the recording under dest with every inline text_sha256 removed
    and its texts file beside it, as a recording made before the recorder wrote
    hashes inline would be: the texts file is then the only way to its pages."""
    data = json.loads(PHASE5.read_text(encoding="utf-8"))
    for entry in (data.get("research") or {}).get("tool_results") or []:
        for result in entry.get("results") or []:
            result.pop("text_sha256", None)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / PHASE5.name
    out.write_text(json.dumps(data), encoding="utf-8")
    (dest / PHASE5_TEXTS.name).write_text(PHASE5_TEXTS.read_text(encoding="utf-8"), encoding="utf-8")
    return out


def _replay_phase5(capsys: pytest.CaptureFixture[str], cassette: Path) -> dict[str, Any]:
    assert cli.main(["ask", "--symptom", SYMPTOM, "--photo", str(PLATE), "--cassette", str(cassette)]) == cli.EXIT_OK
    thread = thread_of(capsys.readouterr().out)
    assert cli.main(["resume", thread, *RESUME_FLAGS, "--cassette", str(cassette)]) == cli.EXIT_OK
    assert "status: ok" in capsys.readouterr().out
    return run_record(thread)


@pytest.mark.skipif(not (PHASE5.is_file() and PHASE5_TEXTS.is_file()),
                    reason="the fixed build's hot tub recording is not in this checkout's data folder")
@pytest.mark.parametrize("with_pages", [False, True], ids=["without_page_text", "with_page_text"])
def test_phase5_recording_replays_to_ok(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str], with_pages: bool) -> None:
    """D4 acceptance on a real live recording (review findings T5, P1), the
    fixed build's hot tub first lookup with its inline page hashes removed:
    status ok both ways; without its page text, grounding unverifiable with
    the live recording reason; with data/pages linked in (read only), grounding
    verified from the cited page, found through the recording's texts file,
    and the stored FLO evidence ends on a whole word. Everything the replay
    writes goes under tmp_path. Mutation: validate_sidecar_ignored (with page
    text the grounding stays unverifiable)."""
    cassette = _phase5_without_inline_hashes(live_env / "phase5_recording")
    assert "text_sha256" not in json.dumps(json.loads(cassette.read_text(encoding="utf-8"))["research"])
    pages = live_env / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    if with_pages:
        if not (PHASE5_PAGES / f"{PHASE5_PAGE_SHA}.txt").is_file():
            pytest.skip("the cited page is not in this checkout's data/pages")
        for page in PHASE5_PAGES.glob("*.txt"):
            (pages / page.name).symlink_to(page)
    monkeypatch.setenv(config.ENV_MODE, "replay")
    record = _replay_phase5(capsys, cassette)
    assert record["status"] == "ok"
    [entry] = record["grounding"]
    assert entry["source_url"] == PHASE5_CITED
    if not with_pages:
        assert record["grounding_status"] == "unverifiable"
        assert entry["reason"] == "replay of a live recording: the cited page's text is not in the local pages folder"
        return
    assert record["grounding_status"] == "verified" and entry["status"] == "verified"
    edges = json.loads(Path(record["graph_edges"]["target"]).read_text(encoding="utf-8"))["edges"]
    [flo] = [e for e in edges if e["kind"] == "HAS_CODE"]
    assert flo["source_url"] == PHASE5_CITED and flo["text_sha256"] == PHASE5_PAGE_SHA
    assert flo["evidence"].endswith("The heater is deactivated and")
