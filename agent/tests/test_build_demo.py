"""The recorded v2 demo's builder and checker (agent/replay/DEMO_V2_DESIGN.md).

Offline. The builder runs on the synthetic inputs in fixtures/demo/ (copied to a
temp folder, with the ledger built from ledger_rows.json), and the checker runs
on those builds and on the installed site. Nothing here reads .env, makes a
network call or writes under data/.

Each test names the mutation that turns it red; every one is listed in
mutations.toml.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent import build_info, config
from agent.replay import build_demo, check_demo, privacy_diff

ROOT = config.REPO_ROOT
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "demo"
BUILD = "0123456789abcdef"
OTHER_BUILD = "ffffffffffffffff"
VAGUE = "t-5a00000000000003"
AC_FIRST = "t-5a00000000000004"
HISTORY = "t-5a00000000000008"
DASHES = (chr(0x2013), chr(0x2014))
# Planted by the allowlist test; none may reach the output.
RAW_PAGE_TEXT = "RAW PAGE TEXT SENTINEL, never shown on the page"
NARRATION = "RESEARCH NARRATION SENTINEL"
LOCAL_PATH = "/Users/example-owner/private-folder/advisor"


class Env:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.data = tmp / "data"
        self.out = tmp / "out"
        self.selection = tmp / "selection.json"

    def json(self, rel: str) -> dict:
        return json.loads((self.data / rel).read_text(encoding="utf-8"))

    def write(self, rel: str, value: dict) -> None:
        (self.data / rel).write_text(json.dumps(value, indent=1), encoding="utf-8")

    def cassette_rel(self, run_id: str) -> str:
        return f"recordings/live_t_{run_id[2:]}.json"

    def sel(self) -> dict:
        return json.loads(self.selection.read_text(encoding="utf-8"))

    def write_sel(self, value: dict) -> None:
        self.selection.write_text(json.dumps(value), encoding="utf-8")

    def build(self, *extra: str) -> int:
        return build_demo.main(["--selection", str(self.selection), "--out", str(self.out),
                                "--src-dir", str(ROOT / "src"), "--preview", *extra])

    def denylist(self) -> list[str]:
        return privacy_diff.load_denylist(self.data / "staging" / privacy_diff.DENYLIST_FILE)

    def out_text(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted(self.out.rglob("*"))
                         if p.is_file() and p.suffix not in (".jpg", ".jpeg", ".png"))

    def fixtures(self) -> dict:
        return build_demo.parse_fixtures((self.out / "src" / "fixtures.js").read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path)
    shutil.copytree(FIXTURES / "data", e.data)
    shutil.copyfile(FIXTURES / "selection.json", e.selection)
    rows = json.loads((e.data / "ledger_rows.json").read_text(encoding="utf-8"))
    (e.data / "ledger").mkdir()
    conn = sqlite3.connect(e.data / "ledger" / "ledger.sqlite")
    conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, ts TEXT, run_id TEXT, kind TEXT, mode TEXT, "
                 "node TEXT, provider TEXT, model TEXT, input_tokens INTEGER, cache_read INTEGER, "
                 "cache_write INTEGER, output_tokens INTEGER, tavily_credits INTEGER, usd REAL)")
    cols = ("ts", "run_id", "kind", "mode", "node", "provider", "model", "input_tokens", "cache_read",
            "cache_write", "output_tokens", "tavily_credits", "usd")
    conn.executemany(f"INSERT INTO entries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DATA_DIR", e.data)
    monkeypatch.setattr(config, "GRAPH_PATH", e.data / "graph.json")
    monkeypatch.setattr(config, "LEDGER_PATH", e.data / "ledger" / "ledger.sqlite")
    monkeypatch.setattr(config, "STAGING_DIR", e.data / "staging")
    monkeypatch.setattr(build_demo, "current_build_id", lambda: BUILD)
    return e


# ---------------------------------------------------------------------------
# A clean build of the synthetic selection
# ---------------------------------------------------------------------------


def test_synthetic_build_shows_every_role_and_labels_synthetic_records(env: Env) -> None:
    """Mutation build_demo_records_label_dropped: the history case loses its
    "Live run against synthetic property records" label; the checker and this test see it."""
    assert env.build() == 0
    assert check_demo.check(env.out, env.denylist()) == []
    demo = env.fixtures()
    assert demo["data_status"] == "rerun" and demo["build"]["build_id"] == BUILD
    assert [c["role"] for c in demo["cases"]] == [*build_demo.ROLES, "hot_tub_with_history"]
    history = demo["cases"][-1]
    assert history["run_id"] == HISTORY
    assert history["records_label"] == "Live run against synthetic property records"
    assert history["records"]["happened_before"]["record_id"] == "service:svc-0001"
    assert history["records"]["age"]["install_date"] == "2021-06-10"
    assert "synthetic records" in history["takeaway"]
    assert demo["cases"][5]["also_count"]["no_reliable_answer"] == 3
    assert 'href="#c8"' in (env.out / "tool.html").read_text(encoding="utf-8")
    # Only the page's files and the preview copies; the report never reaches an install list.
    assert (env.out / "build_report.json").is_file()


def test_optional_role_can_be_left_out(env: Env) -> None:
    """Mutation build_demo_optional_block_kept: the template's case 8 block is kept
    when the selection has no hot_tub_with_history, so the page links to a case it lacks."""
    sel = env.sel()
    del sel["roles"]["hot_tub_with_history"]
    env.write_sel(sel)
    assert env.build() == 0
    assert len(env.fixtures()["cases"]) == len(build_demo.ROLES)
    page = (env.out / "tool.html").read_text(encoding="utf-8")
    assert 'href="#c8"' not in page and "<!-- if:" not in page
    assert "Seven cases replayed" in page


# ---------------------------------------------------------------------------
# The build guard
# ---------------------------------------------------------------------------


def test_guard_refuses_a_run_from_another_build(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_guard_ignores_other_build: the reason for a run on
    another build is never added; the refusal names no such run."""
    record = env.json(f"runs/{VAGUE}.json")
    record["build_id"] = OTHER_BUILD
    env.write(f"runs/{VAGUE}.json", record)
    assert env.build() == 2
    err = capsys.readouterr().err
    assert f"{VAGUE} ran on build {OTHER_BUILD}, not the current build {BUILD}" in err
    assert not env.out.exists()


def test_guard_reads_the_build_from_the_run_record_first(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_build_from_eval_only: the guard stops reading the run
    record's build_id, so a run record and an eval record that disagree pass as the eval says."""
    rf_eval = {"kind": "run", "suite": "sc7b", "run_id": AC_FIRST, "build_id": BUILD, "mode": "cheap"}
    (env.data / "eval" / f"sc7b-{AC_FIRST}.json").write_text(json.dumps(rf_eval), encoding="utf-8")
    record = env.json(f"runs/{AC_FIRST}.json")
    record["build_id"] = OTHER_BUILD
    env.write(f"runs/{AC_FIRST}.json", record)
    assert env.build() == 2
    assert f"{AC_FIRST}'s records disagree on its build" in capsys.readouterr().err
    # The blurry plate run has no run record: its eval record names its build, and that is enough.
    assert build_demo.build_of_run(build_demo.RunFiles("t-5a00000000000007", env.data)) == BUILD


def test_guard_refuses_a_missing_role(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_missing_role_allowed: the required role check is dropped."""
    sel = env.sel()
    del sel["roles"]["ac_first"]
    env.write_sel(sel)
    assert env.build() == 1
    assert "selection names no run for: ac_first" in capsys.readouterr().err
    assert not env.out.exists()


def test_stand_in_build_stamps_the_label(env: Env) -> None:
    """Mutation build_demo_stand_in_fixtures_unlabeled: the fixtures file loses its
    stand-in first line; the checker and this test see it."""
    record = env.json(f"runs/{VAGUE}.json")
    record["build_id"] = OTHER_BUILD
    env.write(f"runs/{VAGUE}.json", record)
    assert env.build("--stand-in") == 0
    label = build_demo.STAND_IN_LABEL
    page = (env.out / "tool.html").read_text(encoding="utf-8")
    fixtures = (env.out / "src" / "fixtures.js").read_text(encoding="utf-8")
    assert fixtures.startswith(f"// {label}\n")
    assert label in page.split("</title>")[0] and 'class="standin-bar"' in page
    assert 'name="robots" content="noindex"' in page and 'property="og:' not in page
    assert label in (env.out / "briefs" / "v2" / "STAND-IN.txt").read_text(encoding="utf-8")
    assert env.fixtures()["data_status"] == "stand_in"
    assert check_demo.check(env.out, env.denylist()) == []


def test_rerun_build_refuses_to_name_a_superseded_run(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_superseded_scan_off: the scan for superseded build and
    run IDs is skipped, so a page naming a superseded run is written."""
    old_run = "t-5a0000000000000b"
    folder = env.data / "superseded" / "2026-09-01-build-fedcba9876543210" / "runs"
    folder.mkdir(parents=True)
    (folder / f"{old_run}.json").write_text("{}", encoding="utf-8")
    cas = env.json(env.cassette_rel(AC_FIRST))
    cas["input"]["symptom"] = f"not cooling upstairs, as in {old_run}"
    env.write(env.cassette_rel(AC_FIRST), cas)
    assert env.build() == 3
    assert f"names superseded build or run {old_run}" in capsys.readouterr().err
    assert not env.out.exists()


def test_synthetic_records_are_checked_against_the_seed(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_age_statement_unchecked: an age statement that is not the
    one code writes from the install date is shown anyway."""
    record = env.json(f"runs/{HISTORY}.json")
    record["brief"]["warranty"]["age_statement"] = record["brief"]["warranty"]["age_statement"].replace(
        "about 5 years", "about 9 years")
    env.write(f"runs/{HISTORY}.json", record)
    assert env.build() == 1
    assert "age statement is not the one code writes from the install date" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# What the builder copies, and its scans
# ---------------------------------------------------------------------------


def test_allowlist_never_copies_a_key_raw_page_text_or_a_local_path(env: Env) -> None:
    """Mutation build_demo_trail_copies_results: the trail keeps each search's raw
    results, so page text planted there reaches the page."""
    key = privacy_diff.KEY_PREFIXES["Anthropic key prefix"] + "api03" + "Q7zX" * 8
    for run_id in (AC_FIRST, VAGUE):
        cas = env.json(env.cassette_rel(run_id))
        cas["research"]["script"][0]["message"]["content"] = f"{NARRATION} {key}"
        for res in cas["research"]["tool_results"]:
            for r in res.get("results") or []:
                r["content"] = RAW_PAGE_TEXT
                r["raw_content"] = RAW_PAGE_TEXT
        cas["page_texts"] = {"https://spa.example.com/error-codes": RAW_PAGE_TEXT}
        env.write(env.cassette_rel(run_id), cas)
        (env.data / "recordings" / f"live_t_{run_id[2:]}.texts.json").write_text(
            json.dumps({"texts": [RAW_PAGE_TEXT]}), encoding="utf-8")
        lookups = env.data / "lookups" / f"{run_id}.jsonl"
        rows = [dict(json.loads(line), content=RAW_PAGE_TEXT, api_key=key)
                for line in lookups.read_text(encoding="utf-8").splitlines()]
        lookups.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        record = env.json(f"runs/{run_id}.json")
        record["html_path"] = f"{LOCAL_PATH}/data/briefs/{run_id}.html"
        record["graph_edges"]["target"] = f"{LOCAL_PATH}/data/graph.json"
        record["usage"] = {"note": key}
        env.write(f"runs/{run_id}.json", record)
    blurry = env.json("eval/plates-t-5a00000000000007.json")
    blurry["input"]["photo"] = f"{LOCAL_PATH}/demo-assets/plate-blurry.jpg"
    env.write("eval/plates-t-5a00000000000007.json", blurry)
    (env.data / "pages").mkdir()
    (env.data / "pages" / "page.txt").write_text(RAW_PAGE_TEXT, encoding="utf-8")

    assert env.build() == 0
    text = env.out_text()
    for planted in (RAW_PAGE_TEXT, NARRATION, key, privacy_diff.KEY_PREFIXES["Anthropic key prefix"],
                    LOCAL_PATH, "/Users/"):
        assert planted not in text, planted


def test_privacy_scan_fails_closed(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_missing_denylist_allowed: a missing denylist no longer
    stops the build (and privacy_diff's loader would write a template under data/)."""
    denylist = env.data / "staging" / privacy_diff.DENYLIST_FILE
    entry = denylist.read_text(encoding="utf-8").splitlines()[-1]
    denylist.unlink()
    assert env.build() == 1
    assert "no denylist" in capsys.readouterr().err
    assert not denylist.exists() and not env.out.exists()
    assert any("no denylist" in p for p in check_demo.check(env.tmp, None))
    assert not denylist.exists()

    denylist.write_text(entry + "\n", encoding="utf-8")
    cas = env.json(env.cassette_rel(AC_FIRST))
    cas["input"]["symptom"] = f"not cooling at {entry.title()}"
    env.write(env.cassette_rel(AC_FIRST), cas)
    assert env.build() == 3
    assert "denylist entry" in capsys.readouterr().err
    assert not env.out.exists()


def test_network_scan_fails_closed(env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation build_demo_network_regex_misses_sendbeacon: the builder's network
    pattern drops sendBeacon, so a script that beacons out is written."""
    src = tmp_path / "src-with-beacon"
    src.mkdir()
    shutil.copyfile(ROOT / "src" / "demo.css", src / "demo.css")
    js = (ROOT / "src" / "demo.js").read_text(encoding="utf-8")
    (src / "demo.js").write_text(js + '\nnavigator.sendBeacon("/collect", "x");\n', encoding="utf-8")
    code = build_demo.main(["--selection", str(env.selection), "--out", str(env.out),
                            "--src-dir", str(src), "--preview"])
    assert code == 3
    assert "network call sendBeacon" in capsys.readouterr().err
    assert not env.out.exists()

    site = tmp_path / "site"
    for rel in check_demo.SITE_FILES:
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, site / rel)
    shutil.copytree(ROOT / "briefs" / "v2", site / "briefs" / "v2")
    shutil.copyfile(ROOT / "src" / "tokens.css", site / "src" / "tokens.css")
    (site / "src" / "demo.js").write_text(js + '\nfetch("/track");\n', encoding="utf-8")
    problems = check_demo.check(site, [])
    assert any("network call fetch(" in p for p in problems), problems


# ---------------------------------------------------------------------------
# The installed site
# ---------------------------------------------------------------------------


def _installed_denylist() -> list[str]:
    """The local denylist when this machine has one (data/ is never tracked)."""
    path = config.STAGING_DIR / privacy_diff.DENYLIST_FILE
    return privacy_diff.load_denylist(path) if path.is_file() else []


def test_check_demo_passes_on_the_installed_site() -> None:
    """Mutation installed_demo_off_origin_image: tool.html loads an image from another origin."""
    assert check_demo.check(ROOT, _installed_denylist()) == []
    demo = build_demo.parse_fixtures((ROOT / "src" / "fixtures.js").read_text(encoding="utf-8"))
    assert demo["data_status"] == "rerun"
    selection = json.loads((ROOT / "agent" / "replay" / "demo_selection.json").read_text(encoding="utf-8"))
    assert demo["build"]["builds"] == [selection["build_id"]]
    assert {c["run_id"] for c in demo["cases"]} == set(selection["roles"].values())
    briefs = {p.stem for p in (ROOT / "briefs" / "v2").glob("*.html")}
    assert briefs == {c["run_id"] for c in demo["cases"] if c.get("brief")}


def test_installed_page_text_has_no_em_or_en_dash() -> None:
    """Mutation installed_demo_dash_in_eyebrow: an em dash in tool.html's eyebrow."""
    files = [ROOT / "tool.html", ROOT / "src" / "demo.js", ROOT / "src" / "demo.css", ROOT / "src" / "fixtures.js",
             *sorted((ROOT / "briefs" / "v2").glob("*.html"))]
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for dash in DASHES:
            if dash in text or f"\\u{ord(dash):04x}" in text.lower():
                hits.append(f"{path.relative_to(ROOT)}: U+{ord(dash):04X}")
    assert hits == []


def test_advisor_build_id_matches_build_info() -> None:
    """Mutation build_demo_fingerprint_drifts: the builder's copy of the build
    fingerprint stops hashing the way agent/build_info.py does."""
    assert build_demo.advisor_build_id(frozenset()) == build_info.build_id()
    for rel in build_demo.DEMO_TOOLING:
        assert (ROOT / "agent" / rel).is_file(), rel
    assert build_demo.advisor_build_id() != build_info.build_id()


# ---------------------------------------------------------------------------
# What the takeaways and the trail claim (review findings N1, N9, H2, H3, R1 to R5, R10)
# ---------------------------------------------------------------------------


def test_blocked_calls_are_put_down_to_the_search_limit_not_the_spending_cap(env: Env) -> None:
    """A blocked call was stopped by the run's search or fetch count limit, never by
    the spending cap. Mutations build_demo_blocked_note_says_cap (the trail note)
    and build_demo_nra_takeaway_says_cap (the refusal case's takeaway)."""
    assert env.build() == 0
    cases = env.fixtures()["cases"]
    blocked = [t for c in cases for t in c["trail"] if t["status"] == "blocked"]
    assert blocked, "the synthetic selection must have a blocked call for this test to mean anything"
    assert {t["note"] for t in blocked} == {"Blocked by the run's search or fetch limit before it was sent. Never sent."}
    nra = next(c for c in cases if c["role"] == "no_such_model")
    assert "more blocked by the search limit)" in nra["takeaway"]
    assert "cap)" not in nra["takeaway"]


def test_top_up_takeaways_say_what_memory_did_not_do(env: Env) -> None:
    """The vague question's top up added no cause, so the brief lists only the
    stored code, unconfirmed; the new symptom's brief used none of the stored
    facts. Mutations build_demo_topup_takeaway_did_the_rest and
    build_demo_ac_topup_unused_facts_unsaid."""
    assert env.build() == 0
    by_role = {c["role"]: c for c in env.fixtures()["cases"]}
    vague = by_role["hot_tub_vague"]["takeaway"]
    assert "added a source but no other cause" in vague
    assert "the brief lists only the stored code FLO, unconfirmed" in vague
    assert "did the rest" not in vague
    new_symptom = by_role["ac_new_symptom"]["takeaway"]
    assert "The brief used none of the stored facts" in new_symptom
    assert "came from the new searches" in new_symptom


def test_repeat_takeaway_says_when_the_brief_was_thinner(env: Env) -> None:
    """A repeat answered from memory that lists fewer steps to try first than
    the first brief says so, and the side by side carries both counts.
    Mutations build_demo_thinner_repeat_unsaid and build_demo_try_first_not_compared."""
    sel = env.sel()
    step = {"step": "Check water level", "detail": "Synthetic step", "safety_flag": False, "source_index": 0}
    first_rel = f"runs/{sel['roles']['hot_tub_code_first']}.json"
    first = env.json(first_rel)
    first["brief"]["try_first"] = [step, step]
    env.write(first_rel, first)
    repeat_rel = f"runs/{sel['roles']['hot_tub_code_repeat']}.json"
    repeat = env.json(repeat_rel)
    repeat["brief"]["try_first"] = []
    env.write(repeat_rel, repeat)
    assert env.build() == 0
    by_role = {c["role"]: c for c in env.fixtures()["cases"]}
    case = by_role["hot_tub_code_repeat"]
    assert "The brief was thinner, though: it listed none of the 2 steps to try first" in case["takeaway"]
    assert [col["try_first_steps"] for col in case["compare_with"]["columns"]] == [2, 0]
    # With the same number of steps, nothing is said.
    repeat["brief"]["try_first"] = [step, step]
    env.write(repeat_rel, repeat)
    shutil.rmtree(env.out)
    assert env.build() == 0
    same = {c["role"]: c for c in env.fixtures()["cases"]}["hot_tub_code_repeat"]
    assert "thinner" not in same["takeaway"]


def test_one_stored_fact_is_not_called_each(env: Env) -> None:
    """"1 fact ... each" reads wrong; more than one keeps "each".
    Mutation build_demo_single_fact_says_each."""
    assert env.build() == 0
    by_role = {c["role"]: c for c in env.fixtures()["cases"]}
    assert "It saved 1 fact to memory, checked word for word" in by_role["hot_tub_code_first"]["takeaway"]
    assert "facts to memory, each checked word for word" in by_role["ac_first"]["takeaway"]


def test_installed_demo_names_the_search_limit_and_compares_steps() -> None:
    """The installed page and script put blocked calls down to the search or
    fetch limit, never to the spending cap, show the steps to try first side by
    side, and do not say a top up started from memory. Mutations
    demo_js_blocked_says_cap, demo_js_try_first_row_dropped,
    demo_js_compare_note_started_from and demo_page_bullet_says_cap_blocked."""
    js = (ROOT / "src" / "demo.js").read_text(encoding="utf-8")
    for old in ("blocked by the run's cap", "Blocked by the run's cap", "blocked by the cap"):
        assert old not in js, old
    assert js.count("search or fetch limit") >= 2 and "blocked by the run's search limit" in js
    assert '["Steps to try first in the brief", "try_first_steps"' in js
    assert "started from what memory held" not in js
    for page in (ROOT / "tool.html", ROOT / "agent" / "replay" / "demo_page.html"):
        text = " ".join(page.read_text(encoding="utf-8").split())
        assert "the cap blocked" not in text, page.name
        assert "a fixed limit on searches and page fetches, both enforced in code" in text, page.name
