"""The `advisor` command rules of PLAN 8.13, offline.

Mutations that turn these red:
- the `if is_inside_published(path)` check in resolve_html_path deleted: every
  published path is accepted.
- BRIEFS_OUT_DIR or PROPERTY_OUT_DIR pointed into the repo root: the default
  path test fails.
- either --live check in resolve_mode deleted, or the mode check in
  resolve_cassette deleted: the matching refusal test passes the command on.
- the live ledger check in cmd_run deleted, or the ledger opened with
  create=True: a live run starts with no ledger file (decision 52).
- property show: the page written somewhere other than the resolved path, the
  unknown property check removed, or validated maintenance items not passed to
  the page (see the property show tests below for the tracked ids).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent import cli, config
from agent.ledger import Ledger

REPO_ROOT = config.REPO_ROOT


# ---------------------------------------------------------------------------
# Output paths (decision 51)
# ---------------------------------------------------------------------------

REFUSED_HTML = [
    "index.html",
    "tool.html",
    "src/new.html",
    "briefs/new.html",
    "briefs/549892815cb6.html",
    "demo-assets/page.html",
    "demo-assets/nested/deeper/page.html",
    "./briefs/../briefs/sneaky.html",
    "data/../src/sneaky.html",
    "BRIEFS/upper.html",
]


@pytest.fixture
def repl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv(config.ENV_MODE, raising=False)
    monkeypatch.delenv(config.ENV_CASSETTE, raising=False)


def assert_defaults_land_under_data() -> None:
    """The default brief and property page paths sit under data/, outside every published path."""
    brief = cli.resolve_html_path(None, kind="brief", ident="run-1")
    prop = cli.resolve_html_path(None, kind="property", ident="prop-1")
    assert brief == config.BRIEFS_OUT_DIR / "run-1.html"
    assert prop == config.PROPERTY_OUT_DIR / "prop-1.html"
    for path in (brief, prop):
        assert path.resolve().is_relative_to(config.DATA_DIR.resolve())
        assert not cli.is_inside_published(path)


@pytest.mark.parametrize("html", REFUSED_HTML)
def test_html_path_refused_inside_published_paths(
    html: str, repl_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: cli_published_path_check_removed, cli_published_check_case_sensitive
    (a published path is accepted); cli_briefs_default_in_repo_root,
    cli_property_default_in_repo_root (a default output path lands in a
    published folder). PLAN section 5 names this one node for both halves."""
    assert_defaults_land_under_data()
    for argv in (
        ["ask", "--symptom", "no heat", "--html", html],
        ["resume", "thread-1", "--html", html],
    ):
        assert cli.main(argv) == cli.EXIT_REFUSED
        assert "published pages" in capsys.readouterr().err
    assert cli.main(["property", "show", "prop-1", "--html", html]) == cli.EXIT_REFUSED
    assert "published pages" in capsys.readouterr().err


def test_html_path_refused_as_absolute_path(repl_env: None) -> None:
    for name in config.PUBLISHED_PATHS:
        target = REPO_ROOT / name
        path = target if target.is_file() else target / "x.html"
        with pytest.raises(cli.CliRefusal):
            cli.resolve_html_path(str(path), kind="brief", ident="r1")


def test_html_defaults_land_under_data(repl_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert_defaults_land_under_data()
    # A name that only starts like a published folder is not inside it.
    assert not cli.is_inside_published(REPO_ROOT / "data" / "briefs" / "x.html")
    assert not cli.is_inside_published(REPO_ROOT / "srcs" / "x.html")
    # With no --html and no cassette, ask passes the rules and stops at the
    # replay notice. (property show now writes its page; see
    # test_property_show_writes_self_contained_page_under_data.)
    assert cli.main(["ask", "--symptom", "no heat"]) == cli.EXIT_NOT_YET
    assert "cassette" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Mode and cassette rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", config.LIVE_MODES)
def test_live_mode_without_live_flag_refused(
    mode: str, repl_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config.ENV_MODE, mode)
    assert cli.main(["ask", "--symptom", "no heat"]) == cli.EXIT_REFUSED
    assert "--live" in capsys.readouterr().err
    assert cli.resolve_mode({config.ENV_MODE: mode}, live=True) == mode


def test_live_flag_in_replay_refused(repl_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ask", "--symptom", "no heat", "--live"]) == cli.EXIT_REFUSED
    assert "--live" in capsys.readouterr().err
    assert cli.resolve_mode({}, live=False) == "replay"


def test_unknown_mode_refused(
    repl_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config.ENV_MODE, "turbo")
    assert cli.main(["ask", "--symptom", "no heat", "--live"]) == cli.EXIT_REFUSED
    assert "turbo" in capsys.readouterr().err


def test_cassette_with_live_refused(
    repl_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    argv = ["ask", "--symptom", "no heat", "--live", "--cassette", "c.json"]
    assert cli.main(argv) == cli.EXIT_REFUSED
    assert "--cassette" in capsys.readouterr().err

    monkeypatch.setenv(config.ENV_CASSETTE, "c.json")
    assert cli.main(["resume", "t1", "--live"]) == cli.EXIT_REFUSED
    assert config.ENV_CASSETTE in capsys.readouterr().err


def test_cassette_accepted_in_replay() -> None:
    assert cli.resolve_cassette("c.json", {}, mode="replay") == Path("c.json")
    assert cli.resolve_cassette(None, {config.ENV_CASSETTE: "e.json"}, mode="replay") == Path("e.json")
    assert cli.resolve_cassette(None, {}, mode="cheap") is None


def test_eval_needs_cheap_and_live(
    repl_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["eval", "sc3b"]) == cli.EXIT_REFUSED
    capsys.readouterr()  # each refusal is checked on its own output
    monkeypatch.setenv(config.ENV_MODE, "full")
    monkeypatch.setattr(config, "FULL_LIVE_APPROVED", True)  # check_mode alone must refuse full
    assert cli.main(["eval", "sc7b", "--live"]) == cli.EXIT_REFUSED
    assert f"advisor eval runs only with {config.ENV_MODE}=cheap and --live" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The ledger a live run spends against (section 8.9, decision 52)
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ledger" / "ledger.sqlite"
    monkeypatch.setattr(config, "LEDGER_PATH", path)
    return path


@pytest.mark.parametrize("command", [["ask", "--symptom", "no heat"], ["resume", "thread-1"]])
def test_live_run_refused_without_ledger_unless_new_ledger(
    command: list[str], ledger_path: Path, repl_env: None,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutations: cli_live_ledger_check_removed_phase5 (a missing ledger is created
    silently); cli_keys_checked_after_ledger (a command refused for a missing key
    has already created the ledger file, so the next run no longer needs
    --new-ledger, decision 52)."""
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    # ENV_FILE points at a temp path, so a real .env is never read here.
    monkeypatch.setattr(config, "ENV_FILE", ledger_path.parent.parent / ".env")
    if command[0] == "resume":
        # A live resume never takes --new-ledger: its thread already spent money (finding M3).
        assert cli.main([*command, "--live", "--new-ledger"]) == cli.EXIT_REFUSED
        assert "--new-ledger is refused with a live resume" in capsys.readouterr().err
        assert not ledger_path.exists()

    # No key: refused before anything is created, with --new-ledger too.
    for extra in ([], ["--new-ledger"]) if command[0] == "ask" else ([],):
        assert cli.main([*command, "--live", *extra]) == cli.EXIT_REFUSED
        assert "missing ANTHROPIC_API_KEY" in capsys.readouterr().err
        assert not ledger_path.exists() and not ledger_path.parent.exists()

    # Keys present (fake values; sockets stay blocked): now the ledger rule applies.
    for name in config.LIVE_KEY_NAMES:
        monkeypatch.setenv(name, "fake-test-value-not-a-key")
    assert cli.main([*command, "--live"]) == cli.EXIT_REFUSED
    assert "no ledger file" in capsys.readouterr().err
    assert not ledger_path.exists() and not ledger_path.parent.exists()


def test_live_run_refused_when_build_budget_cannot_cover_it(
    ledger_path: Path, repl_env: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Mutation: skip assert_can_start_live_run in open_live_ledger.
    Ledger(ledger_path, create=True)
    spent = config.BUILD_CAP_USD - config.RUN_CAP_USD["cheap"] / 2  # less than one run left
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            "INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd)"
            " VALUES ('2026-09-01T00:00:00+00:00', 'earlier', 'cheap', 'synthesize', 'anthropic', 'charge', ?)",
            (spent,),
        )
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    # Keys are checked first; fake values reach the budget check (sockets stay blocked).
    monkeypatch.setattr(config, "ENV_FILE", ledger_path.parent.parent / ".env")
    for name in config.LIVE_KEY_NAMES:
        monkeypatch.setenv(name, "fake-test-value-not-a-key")
    assert cli.main(["ask", "--symptom", "no heat", "--live"]) == cli.EXIT_REFUSED
    assert "build budget" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# advisor property show (PLAN 8.13, decision 51). The seed registry is
# synthetic (PLAN 8.12); the run record below is a hand built synthetic one.
# ---------------------------------------------------------------------------

SEED_PROPERTY = "prop-a"


@pytest.fixture
def data_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every data path the property page reads or writes, under a temp data folder."""
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "REGISTRY_PATH", data / "registry.sqlite")
    monkeypatch.setattr(config, "PROPERTY_OUT_DIR", data / "property")
    monkeypatch.setattr(config, "BRIEFS_OUT_DIR", data / "briefs")
    monkeypatch.setattr(config, "GRAPH_PATH", data / "graph.json")
    monkeypatch.setattr(config, "REPLAY_GRAPH_PATH", data / "replay_graph.json")
    monkeypatch.setattr(config, "PAGES_DIR", data / "pages")
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv(config.ENV_MODE, raising=False)
    return data


def test_property_show_writes_self_contained_page_under_data(
    data_paths: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: cli_property_show_not_written (the page is never written),
    cli_property_show_ignores_html_path (the page goes to a fixed name, not the
    resolved default), cli_property_unknown_accepted (an unknown property is
    rendered instead of refused), cli_property_replay_reads_runtime_graph (a
    replay page reads the live runs' graph, against decision 37)."""
    from agent.tests.test_render import assert_self_contained

    assert cli.property_graph_path({}) == config.REPLAY_GRAPH_PATH
    assert cli.property_graph_path({config.ENV_MODE: "cheap"}) == config.GRAPH_PATH
    with pytest.raises(cli.CliRefusal):
        cli.property_graph_path({config.ENV_MODE: "turbo"})

    assert cli.main(["property", "show", SEED_PROPERTY]) == cli.EXIT_OK
    out = capsys.readouterr().out
    page_path = config.PROPERTY_OUT_DIR / f"{SEED_PROPERTY}.html"
    assert f"html: {page_path}" in out
    assert page_path.resolve().is_relative_to(data_paths.resolve())
    assert not cli.is_inside_published(page_path)
    page = page_path.read_text(encoding="utf-8")
    tree = assert_self_contained(page)
    assert "Synthetic data" in tree.headings
    assert "Every record on this page is synthetic test data" in page
    assert "appl-optima880" in page
    # The published pages are never touched, and the runtime graph never created.
    assert not config.GRAPH_PATH.exists()

    # An explicit --html under data/ is honored.
    other = data_paths / "elsewhere" / "p.html"
    assert cli.main(["property", "show", SEED_PROPERTY, "--html", str(other)]) == cli.EXIT_OK
    assert other.read_text(encoding="utf-8") == page_path.read_text(encoding="utf-8")

    # An unknown property is refused and writes nothing.
    assert cli.main(["property", "show", "prop-missing"]) == cli.EXIT_REFUSED
    assert "unknown property" in capsys.readouterr().err
    assert not (config.PROPERTY_OUT_DIR / "prop-missing.html").exists()


def test_property_show_lists_validated_maintenance_due(
    data_paths: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: cli_property_maintenance_not_passed (the validated items are
    not handed to the renderer), cli_property_maintenance_mode_mixed (a live
    run record feeds a replay page)."""
    import json

    from agent.registry import Registry

    cli.ensure_registry()
    registry = Registry(config.REGISTRY_PATH)
    item = {  # a validated maintenance item as validate leaves it; all synthetic
        "task": "Replace the synthetic filter cartridge", "interval": "every 12 months",
        "source_index": 1, "evidence": "Replace the filter cartridge every 12 months (synthetic).",
        "last_done_record_id": None, "due_date": "2027-03-01",
    }
    runs = config.PAGES_DIR.parent / "runs"
    runs.mkdir(parents=True)
    for run_id, mode, task in (("run-replay", "replay", item["task"]),
                               ("run-live", "cheap", "Live only synthetic task")):
        registry.record_lookup(run_id, status="ok", thread_id=run_id, appliance_id="appl-dishwasher")
        brief = {"status": "ok", "maintenance_due": [{**item, "task": task}]}
        (runs / f"{run_id}.json").write_text(
            json.dumps({"run_id": run_id, "status": "ok", "mode": mode, "brief": brief}), encoding="utf-8")

    assert cli.main(["property", "show", SEED_PROPERTY]) == cli.EXIT_OK
    page = (config.PROPERTY_OUT_DIR / f"{SEED_PROPERTY}.html").read_text(encoding="utf-8")
    assert "Replace the synthetic filter cartridge: interval every 12 months; due 2027-03-01" in page
    assert "Live only synthetic task" not in page
