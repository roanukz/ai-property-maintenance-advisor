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
    assert recorded == {"url": REAL_URL_CLEAN, "title": recorded["title"], "content": "", "raw_content": None,
                        "score": recorded["score"]}
    assert REAL_SNIPPET not in text and REAL_BODY not in text
    [withheld] = texts["withheld"]
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
