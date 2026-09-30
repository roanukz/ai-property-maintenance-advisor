"""The live preflight: planned calls and cost from config, then exactly "proceed" (PRD Part A rule 1).

Driven through the live commands on fake clients with the network blocked.
The expected figures are recomputed here from config with the PLAN section 9
arithmetic, and the typical research route is also held to section 9's
published figure, so a changed formula or a changed constant both show.

Every test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent import cli, config
from agent.ledger import Ledger
from agent.tests.test_live_path import (  # noqa: F401  (live_env is a fixture)
    NRA_DRAFT,
    CheckedChat,
    LiveFakes,
    flo_data,
    live_ask,
    live_env,
    live_resume,
    schema_check,
    thread_of,
)

# PLAN section 9, table B: "Total, research route (read_plate, research, synthesize)", typical.
SECTION_9_TYPICAL_RESEARCH_ROUTE = 0.083


def usd(amount: float) -> str:
    return f"{amount:.4f} USD"


def price(model: str, tokens_in: float, tokens_out: float) -> float:
    prices = config.PRICES_PER_MTOK[model]
    return (tokens_in * prices["input"] + tokens_out * prices["output"]) / 1_000_000


def expected_jev_calls(passes: int) -> int:
    """Jev calls one run plans: one per step per draft, config.JEV_PLAN_STEPS_PER_PASS steps a draft."""
    return passes * config.JEV_PLAN_STEPS_PER_PASS if config.JEV_SAFETY_ENABLED else 0


def expected_typical(mode: str, *, read_plate: bool, jev: bool = True) -> float:
    """Section 9 table B, typical column, from config, plus one draft's Jev calls (input only)."""
    models = config.MODEL_FOR[mode]
    searches = config.RESEARCH_LIMITS["main"]["search"]
    research_in = sum(config.RESEARCH_CALL_BASE_TOKENS + k * config.RESEARCH_TOKENS_PER_SEARCH
                      for k in range(searches + 1))
    research_out = (searches + 1) * config.RESEARCH_OUTPUT_TOKENS
    total = price(models["research"], research_in, research_out)
    total += searches * config.TAVILY_CREDITS["search_basic"] * config.tavily_credit_usd()
    total += price(models["synthesize"], config.SYNTH_EXCERPT_TOKENS_TYPICAL + config.SYNTH_PROMPT_TOKENS_ESTIMATE,
                   config.SYNTH_OUTPUT_TOKENS_TYPICAL)
    if read_plate:
        tokens = config.READ_PLATE_TOKENS_TYPICAL
        total += price(models["read_plate"], tokens["input"], tokens["output"])
    if jev:
        total += price(config.JEV_MODEL, expected_jev_calls(1) * config.JEV_EST_INPUT_TOKENS, 0)
    return total


def expected_max_calls(*, read_plate: bool) -> int:
    limits = config.RESEARCH_LIMITS
    return (1 if read_plate else 0) + 1 + limits["main"]["loop_guard"] + 1 + limits["retry_cheap"]["loop_guard"] + 1


def preload_spend(amount: float, credits: int = 0) -> None:
    Ledger(config.LEDGER_PATH, create=True)
    with sqlite3.connect(config.LEDGER_PATH) as conn:
        conn.execute("INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd, tavily_credits) VALUES "
                     "('2026-09-01T00:00:00+00:00', 'earlier', 'cheap', 'synthesize', 'anthropic', 'charge', ?, ?)",
                     (amount, credits))


def test_preflight_prints_planned_calls_and_costs_from_config(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: preflight_typical_drops_read_plate; preflight_worst_not_cap (worst case is the
    typical figure); preflight_build_spend_zero (build spend read as 0); preflight_research_calls_off
    (the final answer turn is not counted); preflight_jev_calls_not_listed (no Jev line)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    preload_spend(1.25, credits=7)
    code, out, err = live_ask(monkeypatch, capsys, typed="no\n")
    assert code == cli.EXIT_REFUSED
    typical = expected_typical("cheap", read_plate=True)
    assert abs(expected_typical("cheap", read_plate=True, jev=False) - SECTION_9_TYPICAL_RESEARCH_ROUTE) < 0.0005
    searches = config.RESEARCH_LIMITS["main"]["search"]
    expected = [
        "advisor ask: preflight, mode cheap, LIVE (real money)",
        "planned: one cheap run",
        f"  model calls: typical {searches + 1 + 2}, at most {expected_max_calls(read_plate=True)}",
        f"  Tavily credits: typical {searches}, at most {config.RUN_CREDIT_CAP}",
        f"  Jev calls (typesafe, {config.JEV_MODEL}): planned {expected_jev_calls(1)} for one draft, "
        f"{expected_jev_calls(2)} for 2 drafts at {config.JEV_PLAN_STEPS_PER_PASS} steps each; one call per "
        "try_first step (a longer draft makes more)",
        f"  estimated cost: typical {usd(typical)}, worst case {usd(config.RUN_CAP_USD['cheap'])}",
        f"per run cap (cheap): {usd(config.RUN_CAP_USD['cheap'])}",
        f"build spend so far (live runs): {usd(1.25)} of {usd(config.BUILD_CAP_USD)}, "
        f"remaining {usd(config.BUILD_CAP_USD - 1.25)}",
        f"build Tavily credits so far: 7 of {config.BUILD_CREDIT_CAP}, remaining {config.BUILD_CREDIT_CAP - 7}",
        "Type proceed to spend this",
    ]
    for line in expected:
        assert line in out, line
    assert out.index("planned: one cheap run") < out.index("Type proceed")
    assert fakes.constructed == []  # printed and canceled before any client was built


@pytest.mark.parametrize("typed", ["yes\n", "proceed \n", " proceed\n", "Proceed\n", "PROCEED\n", "\n", "",
                                   "proceed now\n", "proceed.\n", "y\n"])
def test_anything_but_exactly_proceed_cancels(typed: str, live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: preflight_confirm_strips (surrounding spaces ignored); preflight_confirm_casefold;
    preflight_confirm_prefix (any line starting with proceed)."""
    chat = CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": {"input_tokens": 1, "output_tokens": 1}}],
                       model=config.HAIKU, max_tokens=config.SCHEMA_CHECK_MAX_TOKENS,
                       ledger_path=str(config.LEDGER_PATH))
    code, out, err, built = schema_check(monkeypatch, capsys, chat, typed=typed)
    assert code == cli.EXIT_REFUSED, out + err
    assert 'not exactly "proceed"' in err
    assert built == [] and chat.received == []
    assert Ledger(config.LEDGER_PATH).rows() == []


@pytest.mark.parametrize("typed", ["proceed\n", "proceed", "proceed\r\n"])
def test_exactly_proceed_continues(typed: str, live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: preflight_confirm_keeps_newline (the line ending is compared too)."""
    chat = CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": {"input_tokens": 1, "output_tokens": 1}}],
                       model=config.HAIKU, max_tokens=config.SCHEMA_CHECK_MAX_TOKENS,
                       ledger_path=str(config.LEDGER_PATH))
    code, out, err, built = schema_check(monkeypatch, capsys, chat, typed=typed)
    assert code == cli.EXIT_OK, out + err
    assert len(built) == 1 and len(chat.received) == 1


def test_check_schema_preflight_shows_one_call_and_its_reservation(
        live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: schema_check_worst_ignores_max_tokens (worst case priced at the typical reply);
    schema_check_typical_is_worst."""
    chat = CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": {"input_tokens": 1, "output_tokens": 1}}],
                       model=config.HAIKU, max_tokens=config.SCHEMA_CHECK_MAX_TOKENS,
                       ledger_path=str(config.LEDGER_PATH))
    code, out, err, _ = schema_check(monkeypatch, capsys, chat)
    assert code == cli.EXIT_OK, out + err
    reserve = next(r for r in Ledger(config.LEDGER_PATH).rows() if r["kind"] == "reserve")
    model = config.MODEL_FOR["cheap"]["synthesize"]
    worst = price(model, reserve["input_tokens"], config.SCHEMA_CHECK_MAX_TOKENS)
    typical = price(model, reserve["input_tokens"], config.SCHEMA_CHECK_OUTPUT_TOKENS_TYPICAL)
    assert reserve["usd"] == pytest.approx(worst)  # the worst case printed is exactly the reservation
    assert "planned: 1 minimal synthesize call on " + model in out
    assert "  model calls: typical 1, at most 1" in out and "  Tavily credits: typical 0, at most 0" in out
    assert f"  estimated cost: typical {usd(typical)}, worst case {usd(worst)}" in out
    assert typical < worst


@pytest.mark.parametrize("enabled", [True, False], ids=["jev_enabled", "jev_disabled"])
def test_preflight_counts_jev_calls_and_cost(enabled: bool, live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: preflight_jev_cost_dropped (the Jev calls are counted but not priced);
    preflight_jev_counted_when_disabled (Jev calls planned while JEV_SAFETY_ENABLED is False)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    # Inflated so the Jev share shows at the preflight's four decimals.
    monkeypatch.setattr(config, "JEV_EST_INPUT_TOKENS", 100_000)
    monkeypatch.setattr(config, "JEV_SAFETY_ENABLED", enabled)
    code, out, err = live_ask(monkeypatch, capsys, "--new-ledger", typed="no\n")
    assert code == cli.EXIT_REFUSED
    jev_usd = expected_typical("cheap", read_plate=True) - expected_typical("cheap", read_plate=True, jev=False)
    per_call = 100_000 * config.PRICES_PER_MTOK[config.JEV_MODEL]["input"] / 1_000_000
    assert jev_usd == pytest.approx(config.JEV_PLAN_STEPS_PER_PASS * per_call if enabled else 0)
    assert f"estimated cost: typical {usd(expected_typical('cheap', read_plate=True))}," in out
    assert ("Jev calls" in out) is enabled
    assert fakes.constructed == [] and fakes.jev_requests == []


def test_resume_preflight_counts_what_the_run_spent(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: preflight_resume_worst_is_cap (the resume worst case ignores the run's spend so far)."""
    LiveFakes(flo_data()).install(monkeypatch)
    code, out, err = live_ask(monkeypatch, capsys, "--new-ledger")
    assert code == cli.EXIT_OK, out + err
    assert f"typical {usd(expected_typical('cheap', read_plate=True))}" in out
    thread_id = thread_of(out)
    spent = Ledger(config.LEDGER_PATH).run_total(thread_id)
    assert spent > 0  # read_plate was charged
    code, out, err = live_resume(monkeypatch, capsys, thread_id, typed="no\n")
    assert code == cli.EXIT_REFUSED
    assert "planned: the rest of one cheap run" in out
    assert f"note: this run has already spent {usd(spent)}" in out
    assert (f"estimated cost: typical {usd(expected_typical('cheap', read_plate=False))}, "
            f"worst case {usd(config.RUN_CAP_USD['cheap'] - spent)}") in out
    assert f"at most {expected_max_calls(read_plate=False)}" in out


def test_no_flag_skips_the_typed_proceed() -> None:
    """Mutation: cli_confirm_flag_added (a --yes or --confirm flag appears on a live command)."""
    parser = cli.build_parser()
    for argv in (["ask", "--symptom", "s", "--live", "--yes"], ["ask", "--symptom", "s", "--live", "--confirm"],
                 ["resume", "t-1", "--live", "--yes"], ["check-schema", "--live", "--yes"],
                 ["check-schema", "--live", "--confirm"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
