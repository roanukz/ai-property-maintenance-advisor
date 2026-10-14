"""Decision 69: Claude Haiku 5.5's two rate cards, picked by the prompt's length, and replay's frozen card.

Offline and $0. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import config
from agent.ledger import Ledger, call_prices, price_usage, prompt_tokens, rate_card
from agent.research.middleware import SpendCap
from agent.state import RunContext

HAIKU = config.HAIKU
LOW = {"input": 0.10, "output": 0.50}
LONG = {"input": 0.50, "output": 2.50}


def cost(card: dict[str, float], input_tokens: float, output_tokens: float) -> float:
    return (input_tokens * card["input"] + output_tokens * card["output"]) / 1_000_000


def test_both_rate_cards_and_the_line_between_them() -> None:
    """Mutation haiku55_long_card_input_wrong: the long prompt card's input price is the short one's."""
    assert config.PRICES_PER_MTOK[HAIKU] == LOW
    assert config.LONG_PROMPT_PRICES_PER_MTOK == {HAIKU: LONG}
    assert config.LONG_PROMPT_ABOVE_TOKENS == 100_000
    assert (config.CACHE_READ_MULTIPLIER, config.CACHE_WRITE_5M_MULTIPLIER, config.CACHE_WRITE_1H_MULTIPLIER) == (
        0.10, 1.25, 2.00)


def test_the_card_changes_between_100000_and_100001_prompt_tokens() -> None:
    """Mutation haiku55_boundary_inclusive: a prompt of exactly 100,000 tokens goes on the long card."""
    assert rate_card(HAIKU, 100_000) == LOW
    assert rate_card(HAIKU, 100_001) == LONG
    assert price_usage(HAIKU, {"input_tokens": 100_000, "output_tokens": 1_000}) == pytest.approx(
        cost(LOW, 100_000, 1_000))
    # Above the line the whole call, output included, is billed on the long card.
    assert price_usage(HAIKU, {"input_tokens": 100_001, "output_tokens": 1_000}) == pytest.approx(
        cost(LONG, 100_001, 1_000))


def test_cache_tokens_count_toward_the_prompt_and_take_the_selected_cards_rate() -> None:
    """Mutation haiku55_prompt_ignores_cache: the card is picked from the uncached input alone."""
    # langchain-anthropic's input_tokens includes cache reads and writes: 60,001 uncached here.
    usage = {"input_tokens": 100_001, "output_tokens": 0,
             "input_token_details": {"cache_read": 30_000, "ephemeral_5m_input_tokens": 6_000,
                                     "ephemeral_1h_input_tokens": 4_000}}
    assert prompt_tokens(usage) == 100_001
    expected = (60_001 + 30_000 * 0.10 + 6_000 * 1.25 + 4_000 * 2.00) * LONG["input"] / 1_000_000
    assert price_usage(HAIKU, usage) == pytest.approx(expected)
    # One token fewer in the cache read and the same call is on the short card.
    usage["input_tokens"] = 100_000
    usage["input_token_details"]["cache_read"] = 29_999
    expected = (60_001 + 29_999 * 0.10 + 6_000 * 1.25 + 4_000 * 2.00) * LOW["input"] / 1_000_000
    assert price_usage(HAIKU, usage) == pytest.approx(expected)


def _ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite", create=True)


def test_a_reservation_whose_estimate_could_pass_the_line_holds_at_the_long_card(tmp_path: Path) -> None:
    """Mutation haiku55_reserve_short_card_only: reserve prices every estimate on the short card."""
    ledger = _ledger(tmp_path)
    under = ledger.reserve("r", mode="cheap", node="research", model=HAIKU, input_tokens_est=100_000,
                           max_tokens=4_096)
    over = ledger.reserve("r", mode="cheap", node="research", model=HAIKU, input_tokens_est=100_001,
                          max_tokens=4_096)
    assert under.usd == pytest.approx(cost(LOW, 100_000, 4_096))
    assert over.usd == pytest.approx(cost(LONG, 100_001, 4_096))


def test_a_charge_picks_the_card_from_the_reported_usage(tmp_path: Path) -> None:
    """Mutation haiku55_charge_short_card_only: charge prices every reply on the short card."""
    ledger = _ledger(tmp_path)
    hold = ledger.reserve("c", mode="cheap", node="synthesize", model=HAIKU, input_tokens_est=20_000,
                          max_tokens=16_000)
    charged = ledger.charge(hold, {"input_tokens": 150_000, "output_tokens": 2_000})
    assert charged == pytest.approx(cost(LONG, 150_000, 2_000))
    # Replay stays on the recorded model's one card at any length (decision 69).
    replay = ledger.reserve("p", mode="replay", node="synthesize", model=HAIKU, input_tokens_est=10,
                            max_tokens=10, run_cap_usd=10.0)
    assert ledger.charge(replay, {"input_tokens": 150_000, "output_tokens": 2_000}) == pytest.approx(
        cost(config.REPLAY_PRICES, 150_000, 2_000))


def test_sonnet_jev_and_tavily_pricing_are_unchanged() -> None:
    """Mutation haiku55_long_card_for_every_model: the long prompt card is applied whatever the model."""
    assert config.PRICES_PER_MTOK[config.SONNET] == {"input": 2.00, "output": 10.00}
    assert config.PRICES_PER_MTOK[config.JEV_MODEL] == {"input": 0.042, "output": 0.0}
    assert rate_card(config.SONNET, 500_000) == config.PRICES_PER_MTOK[config.SONNET]
    assert price_usage(config.SONNET, {"input_tokens": 500_000, "output_tokens": 1_000}) == pytest.approx(
        cost(config.PRICES_PER_MTOK[config.SONNET], 500_000, 1_000))
    assert call_prices("cheap", config.JEV_PROVIDER, config.JEV_MODEL, 500_000) == {"input": 0.042, "output": 0}
    assert (config.TAVILY_PAYG_ENABLED, config.TAVILY_CREDIT_USD_PAYG, config.tavily_credit_usd()) == (
        False, 0.008, 0.0)


def test_replay_keeps_the_recorded_models_card() -> None:
    """Mutation haiku55_replay_priced_at_haiku_5_5: replay is priced on Claude Haiku 5.5's card,
    which would change every replay cost the cassettes and the demo were checked against."""
    assert config.REPLAY_PRICES == {"input": 1.00, "output": 5.00}
    for prompt in (0, 100_001, 900_000):
        assert call_prices("replay", "anthropic", HAIKU, prompt) == config.REPLAY_PRICES


def test_research_spend_cap_holds_live_calls_on_the_card_their_estimate_selects(tmp_path: Path) -> None:
    """Mutation haiku55_research_cap_short_card: the research sub budget check prices every live
    research call on the short card."""
    ctx = RunContext(run_id="r", mode="cheap", ledger_path=tmp_path / "l", registry_path=tmp_path / "g",
                     graph_path=tmp_path / "g.json", pages_dir=tmp_path / "p")
    cap = SpendCap(ctx, config.RESEARCH_BUDGET_USD)
    assert cap._reservation_usd(HAIKU, 100_001, 4_096) == pytest.approx(cost(LONG, 100_001, 4_096))
    assert cap._reservation_usd(HAIKU, 100_000, 4_096) == pytest.approx(cost(LOW, 100_000, 4_096))
    replay = SpendCap(RunContext(run_id="r", mode="replay", ledger_path=tmp_path / "l", registry_path=tmp_path / "g",
                                 graph_path=tmp_path / "g.json", pages_dir=tmp_path / "p"), 1.0)
    assert replay._reservation_usd(HAIKU, 100_001, 4_096) == pytest.approx(cost(config.REPLAY_PRICES, 100_001, 4_096))
