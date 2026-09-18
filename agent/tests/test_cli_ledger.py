"""`advisor ledger` (decision 48): what it prints, and that it never creates a ledger.

Offline, $0. Each test names the mutation that turns it red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import cli, config
from agent.ledger import Ledger


@pytest.fixture
def ledger_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ledger" / "ledger.sqlite"
    monkeypatch.setattr(config, "LEDGER_PATH", path)
    return path


def test_missing_ledger_is_reported_not_created(
    ledger_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: open the ledger with create=True, or drop the exists() check (the
    Ledger constructor then raises LedgerMissing)."""
    assert cli.main(["ledger"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "no ledger file" in out
    assert str(ledger_path) in out
    assert not ledger_path.exists()
    assert not ledger_path.parent.exists()


def _usage(input_tokens: int, output_tokens: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def test_report_shows_live_spend_remaining_budget_and_estimated_rows(
    ledger_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutations: sum the build line over every row instead of build_total()
    (replay spend then shows); drop the estimated filter; print the cap instead
    of the remaining budget; write stop rows with an empty note (the stop
    reason vanishes from the run line)."""
    ledger = Ledger(ledger_path, create=True)
    live = ledger.reserve(
        "live-1", mode="cheap", node="synthesize", model=config.HAIKU,
        input_tokens_est=10_000, max_tokens=1_000,
    )
    live_usd = ledger.charge(live, _usage(10_000, 1_000))
    timed_out = ledger.reserve(
        "live-1", mode="cheap", node="research", model=config.HAIKU,
        input_tokens_est=4_000, max_tokens=500,
    )
    timeout_usd = ledger.charge_timeout(timed_out)
    ledger.charge_credits("live-1", mode="cheap", node="research", credits=3)
    ledger.stop("live-1", mode="cheap", reason="tavily_plan_limit", node="research")
    replay = ledger.reserve(
        "replay-1", mode="replay", node="synthesize", model=config.HAIKU,
        input_tokens_est=50_000, max_tokens=6_000,
    )
    replay_usd = ledger.charge(replay, _usage(50_000, 6_000))
    assert replay_usd > live_usd + timeout_usd  # so a leak into the build line would show

    assert cli.main(["ledger"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    build = live_usd + timeout_usd
    build_line = next(line for line in out.splitlines() if line.startswith("build spend"))
    assert cli._usd(build) in build_line
    assert f"remaining {cli._usd(config.BUILD_CAP_USD - build)}" in build_line
    credit_line = next(line for line in out.splitlines() if line.startswith("build Tavily"))
    assert f"3 of {config.BUILD_CREDIT_CAP}" in credit_line
    assert f"remaining {config.BUILD_CREDIT_CAP - 3}" in credit_line
    assert "runs: 2" in out
    assert f"live-1 [cheap]: {cli._usd(build)}, 3 credits, stopped: tavily_plan_limit" in out
    assert f"replay-1 [replay]: {cli._usd(replay_usd)}" in out
    assert "estimated rows (reconcile against the console): 1" in out
    assert "timed out, charged at reservation" in out


def test_run_filter_shows_one_run(ledger_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: ignore --run (pass None to rows())."""
    ledger = Ledger(ledger_path, create=True)
    for rid in ("run-a", "run-b"):
        ledger.charge_credits(rid, mode="cheap", node="research", credits=1)
    assert cli.main(["ledger", "--run", "run-b"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "runs: 1" in out
    assert "run-b [cheap]" in out
    assert "run-a" not in out


def test_run_report_shows_tokens_estimate_and_r4(ledger_path: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: report_r4_not_normalized (snippet chunks looked for in the raw page text, so a
    whitespace difference counts as a miss); report_tokens_output_as_input (the output column repeats
    the input); report_estimate_without_photo (the estimate leaves out read_plate). Finding P7: the
    Phase 5 gate reports tokens, actual cost against the estimate and the R4 match rate."""
    import hashlib
    import json

    from agent.research.tools import save_page_text

    data = tmp_path / "data"
    monkeypatch.setattr(config, "PAGES_DIR", data / "pages")
    monkeypatch.setattr(config, "LOOKUPS_DIR", data / "lookups")
    ledger = Ledger(ledger_path, create=True)
    for node, (tin, tout) in {"read_plate": (2_800, 95), "research": (40_000, 1_500), "synthesize": (13_000, 3_000)}.items():
        hold = ledger.reserve("run-r4", mode="cheap", node=node, model=config.HAIKU, input_tokens_est=10, max_tokens=10)
        ledger.charge(hold, _usage(tin, tout))

    page = "Intro text. The FLO code means low\nflow   through the heater. Later: clean the filter."
    sha = save_page_text(config.PAGES_DIR, page)
    snippet = "The FLO code means low flow through the heater [...] clean the filter [...] a chunk not on the page"
    lookups = data / "lookups"
    lookups.mkdir(parents=True)
    (lookups / "run-r4.jsonl").write_text(json.dumps({
        "tool": "search", "query": "q", "status": "ok",
        "results": [{"url": "https://example.com/a", "content": snippet, "text_sha256": sha},
                    {"url": "https://example.com/b", "content": "no page for this one",
                     "text_sha256": hashlib.sha256(b"never saved").hexdigest()}],
    }) + "\n", encoding="utf-8")

    assert cli.main(["ledger", "--run", "run-r4"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "  read_plate: 2800, 0, 0, 95" in out
    assert "  research: 40000, 0, 0, 1500" in out and "  synthesize: 13000, 0, 0, 3000" in out
    # The estimate: section 9's typical cheap run with a photo, from config values.
    haiku = config.PRICES_PER_MTOK[config.HAIKU]
    price = lambda i, o: (i * haiku["input"] + o * haiku["output"]) / 1_000_000  # noqa: E731
    searches = config.RESEARCH_LIMITS["main"]["search"]
    research = price(sum(config.RESEARCH_CALL_BASE_TOKENS + k * config.RESEARCH_TOKENS_PER_SEARCH
                         for k in range(searches + 1)), (searches + 1) * config.RESEARCH_OUTPUT_TOKENS)
    typical = (price(config.READ_PLATE_TOKENS_TYPICAL["input"], config.READ_PLATE_TOKENS_TYPICAL["output"])
               + research + searches * config.tavily_credit_usd()
               + price(config.SYNTH_EXCERPT_TOKENS_TYPICAL + config.SYNTH_PROMPT_TOKENS_ESTIMATE,
                       config.SYNTH_OUTPUT_TOKENS_TYPICAL))
    total = ledger.run_total("run-r4")
    assert (f"  actual {total:.4f} USD against the estimate: typical {typical:.4f} USD "
            f"({total - typical:+.4f}), worst case {config.RUN_CAP_USD['cheap']:.4f} USD") in out
    # R4: two of the three chunks are on the page once whitespace is normalized; one result has no text.
    assert "R4 snippet versus raw_content: 2 of 3 snippet chunks found in the page text (66.7%)" in out
    assert "R4: 1 search result(s) had a snippet but no saved page text" in out
