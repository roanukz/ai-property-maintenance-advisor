"""Decision 69: cheap mode on Claude Haiku 5.5, its settings in config, and a run's worst case at them.

Offline and $0. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from agent import cli, config, models
from agent.ledger import estimate_input_tokens, price_usage
from agent.nodes.classify import classifier_messages
from agent.nodes.read_plate import plate_messages
from agent.tests.test_live_path import LiveFakes, flo_data, live_ask, live_env  # noqa: F401  (a fixture)
from agent.tests.test_preflight import expected_max_calls

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def test_every_haiku_step_runs_on_haiku_5_5_and_no_4_5_id_is_left() -> None:
    """Mutation haiku55_id_reverted: config.HAIKU names Claude Haiku 4.5 again."""
    assert config.HAIKU == "claude-haiku-5-5"
    used = {model for steps in config.MODEL_FOR.values() for model in steps.values()}
    assert used == {config.HAIKU, config.SONNET}
    assert not any("haiku-4" in model for model in used)
    for mode in config.MODES:
        for node in ("read_plate", "classifier", "research"):
            assert config.MODEL_FOR[mode][node] == config.HAIKU, (mode, node)
    assert config.MODEL_FOR["cheap"]["synthesize"] == config.MODEL_FOR["replay"]["synthesize"] == config.HAIKU
    assert config.MODEL_FOR["full"]["synthesize"] == config.SONNET  # full mode's Sonnet is unchanged


@pytest.mark.parametrize(("today", "refused"), [
    (date(2026, 10, 15), False),  # Haiku 4.5's date no longer gates
    (date(2027, 10, 6), False),
    (date(2027, 10, 7), True),
    (date(2027, 10, 8), True),
])
def test_the_date_gate_refuses_from_haiku_5_5s_earliest_retirement(
        today: date, refused: bool, live_env: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """A live ask on fakes, refused before its preflight from the gate day on.

    Mutations: haiku55_gate_date_reverted (the gate keeps Haiku 4.5's 15 October 2026);
    haiku55_gate_day_allowed (the gate day itself is let through)."""
    assert config.HAIKU_RETIREMENT_EARLIEST == "2027-10-07"
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    monkeypatch.setattr(cli, "_today", lambda: today)
    code, out, err = live_ask(monkeypatch, capsys, "--new-ledger", typed="no\n")
    assert code == cli.EXIT_REFUSED and fakes.constructed == []
    if refused:
        assert "Recheck the model list" in err and config.HAIKU in err and "2027-10-07" in err
        assert "preflight" not in out
    else:
        assert "Recheck the model list" not in err and "advisor ask: preflight" in out


def test_effort_per_haiku_node() -> None:
    """Mutations haiku55_classifier_effort_raised (the classifier's one word verdict runs at medium) and
    haiku55_research_effort_back_to_medium (research falls back to the level that stopped early, decision 89)."""
    assert config.HAIKU_EFFORT == {"read_plate": "low", "classifier": "low", "research": "high",
                                   "synthesize": "medium"}
    assert set(config.HAIKU_EFFORT) == set(models.NODES)
    for level in config.HAIKU_EFFORT.values():
        # xhigh and max are never used: they are the levels where disabled thinking is a 400 and
        # replies run long; nothing here needs them.
        assert level in EFFORT_LEVELS[:3]
    assert config.FULL_SYNTH_EFFORT == "medium"


def test_never_send_lists() -> None:
    """Mutation haiku55_effort_still_banned: effort stays on the never send list from Haiku 4.5."""
    assert config.NEVER_SEND_PARAMS == ("temperature", "top_p", "top_k")
    assert config.NEVER_SEND_TO_HAIKU == ("thinking", "budget_tokens", "fallbacks")
    assert not {"effort", "reasoning_effort"} & set(config.NEVER_SEND_TO_HAIKU)


def test_budgets_leave_room_for_thinking_and_stay_distinct() -> None:
    """Mutation haiku55_classifier_budget_64: the classifier keeps Haiku 4.5's 64 tokens, which
    thinking alone can use up."""
    assert config.MAX_TOKENS == {"read_plate": 2_048, "classifier": 1_024, "research": 4_096,
                                 "synthesize_cheap": 16_000, "synthesize_full": 16_000}
    assert min(config.MAX_TOKENS.values()) >= 1_024
    assert config.SCHEMA_CHECK_MAX_TOKENS >= 4_096
    # The live recorder names a call's node by its max_tokens, so they differ within each live mode.
    for mode in config.LIVE_MODES:
        budgets = [models.max_tokens_for(node, mode) for node in models.NODES]
        assert len(set(budgets)) == len(budgets), mode


def test_estimates_count_the_newer_tokenizer() -> None:
    """Mutation haiku55_margin_without_tokenizer: Haiku's estimate margin leaves out the 1.3."""
    assert config.NEW_TOKENIZER_FACTOR == 1.3
    assert config.ESTIMATE_MARGIN[config.HAIKU] == pytest.approx(1.25 * 1.3)
    assert config.TOOL_PROMPT_TOKENS[config.HAIKU] == 406  # the pricing page's figure, not a scaled guess
    assert config.TYPICAL_TOKENS_FACTOR == {config.HAIKU: 1.3}
    assert config.typical_tokens(config.HAIKU, 1_000) == 1_300
    assert config.typical_tokens(config.HAIKU, 1) == 2  # rounded up, never down
    assert config.typical_tokens(config.SONNET, 1_000) == 1_000


# ---------------------------------------------------------------------------
# A cheap run's worst case reservation at the Claude Haiku 5.5 budgets and prices
# ---------------------------------------------------------------------------


def research_pass_worst_usd(row: str) -> tuple[float, int]:
    """(dollars, calls) if every call of one research pass were reserved at its largest prompt.

    Call k's prompt is bounded by the base, every search and fetch result the pass allows (each
    search's results at RESEARCH_TOKENS_PER_SEARCH, a fetch's excerpt at its character cap), the
    tool prompt, and k earlier replies at the full max_tokens: Claude Haiku 5.5 keeps earlier
    thinking blocks in the prompt, so they are counted as billed input here.
    """
    limits = config.RESEARCH_LIMITS[row]
    out = config.MAX_TOKENS["research"]
    results = (limits["search"] * config.RESEARCH_TOKENS_PER_SEARCH
               + limits["fetch"] * math.ceil(config.FETCH_EXCERPT_MAX_CHARS / config.CHARS_PER_TOKEN_ESTIMATE))
    usd = 0.0
    for k in range(limits["loop_guard"]):
        prompt = config.RESEARCH_CALL_BASE_TOKENS + results + config.TOOL_PROMPT_TOKENS[config.HAIKU] + k * out
        usd += price_usage(config.HAIKU, {"input_tokens": math.ceil(prompt * config.ESTIMATE_MARGIN[config.HAIKU]),
                                          "output_tokens": out})
    return usd, limits["loop_guard"]


def test_a_cheap_runs_worst_case_reservation_fits_the_run_and_build_caps() -> None:
    """Every call one cheap run with a photo can make, each reserved at its full budget, on the
    cards its prompt selects, adds up to less than the cheap run cap.

    Mutation haiku55_synth_budget_oversized: the cheap synthesize budget is 64,000 tokens."""
    assert config.MODEL_FOR["cheap"] == {node: config.HAIKU for node in models.NODES}
    margin = config.ESTIMATE_MARGIN[config.HAIKU]

    _, plate_estimate = plate_messages(str(config.CAPABILITIES_PHOTO))
    plate = price_usage(config.HAIKU, {"input_tokens": estimate_input_tokens(plate_estimate, model=config.HAIKU,
                                                                             images=1),
                                       "output_tokens": config.MAX_TOKENS["read_plate"]})
    causes = [{"code": None, "evidence": "x" * config.EVIDENCE_MAX_CHARS}] * 10
    classifier_in = estimate_input_tokens(classifier_messages({"model": "x"}, "y" * 400, causes), model=config.HAIKU)
    classifier = price_usage(config.HAIKU, {"input_tokens": classifier_in,
                                            "output_tokens": config.MAX_TOKENS["classifier"]})
    main, main_calls = research_pass_worst_usd("main")
    retry, retry_calls = research_pass_worst_usd("retry_cheap")
    synth_in = math.ceil((config.SYNTH_SOURCE_TOKEN_BUDGET + config.SYNTH_PROMPT_TOKENS_ESTIMATE) * margin)
    synth = price_usage(config.HAIKU, {"input_tokens": synth_in, "output_tokens": config.MAX_TOKENS["synthesize_cheap"]})

    # The same calls the preflight counts as "at most".
    assert 1 + 1 + main_calls + 1 + retry_calls + 1 == expected_max_calls(read_plate=True)
    worst = plate + classifier + main + retry + 2 * synth
    assert worst < config.RUN_CAP_USD["cheap"] < config.BUILD_CAP_USD
    assert worst == pytest.approx(0.114, abs=0.005)  # the arithmetic in this test, at 13 October 2026's settings
    # No single prompt here reaches the long prompt card.
    assert synth_in <= config.LONG_PROMPT_ABOVE_TOKENS
    main_row = config.RESEARCH_LIMITS["main"]
    largest_research_prompt = (config.RESEARCH_CALL_BASE_TOKENS + main_row["search"] * config.RESEARCH_TOKENS_PER_SEARCH
                               + main_row["fetch"] * math.ceil(config.FETCH_EXCERPT_MAX_CHARS
                                                               / config.CHARS_PER_TOKEN_ESTIMATE)
                               + (main_row["loop_guard"] - 1) * config.MAX_TOKENS["research"]
                               + config.TOOL_PROMPT_TOKENS[config.HAIKU])
    assert largest_research_prompt * margin <= config.LONG_PROMPT_ABOVE_TOKENS
