"""Config facts checked on Anthropic's live pages on 13 October 2026, and the dates code reads.

- Tool use system prompt tokens for Claude Haiku 5.5: 406 (tool_choice any or tool), the higher of
  the pricing page's two figures, replacing the 765 placeholder.
- Claude Haiku 5.5's rate cards and cache multipliers against the pricing page.
- The retirement date and the image input note cite the live pages, not the build brief.
- parse_partial_date: a full date that is not on the calendar is not a date, and no caller
  computes an age, a due date or a coverage end date from one.

Offline and $0. Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import math
from datetime import date

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from agent import config, sweep
from agent.ledger import estimate_input_tokens
from agent.rules import coverage
from agent.rules.citations import age_statement, parse_partial_date
from agent.rules.maintenance import due_date

CONFIG_TEXT = (config.REPO_ROOT / "agent" / "config.py").read_text(encoding="utf-8")
TODAY = date(2026, 10, 13)


@tool
def search(query: str) -> str:
    """A stand in search tool."""
    return query


def test_haiku_tool_prompt_tokens_are_the_pricing_pages_406() -> None:
    """A live Haiku estimate with tools adds 406 tokens before the margin; replay keeps Haiku 4.5's
    588 as recorded. Mutation: config_tool_prompt_tokens_placeholder."""
    assert config.TOOL_PROMPT_TOKENS == {config.HAIKU: 406, config.SONNET: 474}
    messages = [HumanMessage("x" * 400)]
    bare = estimate_input_tokens(messages, model=config.HAIKU)
    with_tools = estimate_input_tokens(messages, model=config.HAIKU, tools=[search])
    from langchain_core.messages.utils import count_tokens_approximately

    counted = count_tokens_approximately(messages, tools=[search])
    assert with_tools == math.ceil((counted + 406) * config.ESTIMATE_MARGIN[config.HAIKU])
    assert with_tools > bare
    assert estimate_input_tokens(messages, model=config.HAIKU, tools=[search], mode="replay") == math.ceil(
        (counted + 588) * config.REPLAY_ESTIMATE_MARGIN)


def test_haiku_rate_cards_and_cache_prices_match_the_pricing_page() -> None:
    """Per million tokens on 13 October 2026: up to 100,000 prompt tokens, input 0.10, 5 minute cache
    writes 0.125, 1 hour writes 0.20, cache hits 0.01, output 0.50; above, 0.50, 0.625, 1, 0.05 and
    2.50. Mutation: config_cache_read_multiplier_wrong."""
    short, long = config.PRICES_PER_MTOK[config.HAIKU], config.LONG_PROMPT_PRICES_PER_MTOK[config.HAIKU]
    assert config.LONG_PROMPT_ABOVE_TOKENS == 100_000
    page = {"short": (0.10, 0.125, 0.20, 0.01, 0.50), "long": (0.50, 0.625, 1.0, 0.05, 2.50)}
    for name, card in (("short", short), ("long", long)):
        derived = (card["input"], card["input"] * config.CACHE_WRITE_5M_MULTIPLIER,
                   card["input"] * config.CACHE_WRITE_1H_MULTIPLIER, card["input"] * config.CACHE_READ_MULTIPLIER,
                   card["output"])
        assert derived == pytest.approx(page[name], abs=1e-12), name


def test_config_cites_the_live_pages_checked_13_october_2026() -> None:
    """The retirement date cites the deprecations page and the tool prompt figure the pricing page,
    both checked 13 October 2026; nothing still rests on the build brief; the image input note
    names the capabilities check. Mutation: config_retirement_cites_build_brief."""
    docstring = CONFIG_TEXT.split('"""', 2)[1]
    block = CONFIG_TEXT.split("# The earliest retirement date for Claude Haiku 5.5.", 1)[1].split(
        "HAIKU_RETIREMENT_EARLIEST =", 1)[0]
    assert "build brief" not in docstring and "build brief" not in block
    assert "deprecations page" in " ".join(docstring.split())
    flat = " ".join(line.lstrip("# ").strip() for line in block.splitlines())
    assert "deprecations page, checked 13 October 2026" in flat and "not sooner than 7 October 2027" in flat
    assert config.HAIKU_RETIREMENT_EARLIEST == "2027-10-07"
    tool_block = CONFIG_TEXT.split("TOOL_PROMPT_TOKENS = {", 1)[0].rsplit("# Tool use system prompt", 1)[1]
    assert "pricing page" in tool_block and "13 October 2026" in " ".join(tool_block.split())
    assert "image input" in CONFIG_TEXT and "capabilities check" in CONFIG_TEXT


@pytest.mark.parametrize("text", ["2019-02-30", "2021-13-01", "2023-02-29", "2019-04-31", "02/30/2019",
                                  "0000-01-01", "2100-02-29"])
def test_a_full_date_not_on_the_calendar_is_not_a_date(text: str) -> None:
    """Mutation: citations_calendar_check_off."""
    assert parse_partial_date(text) is None


@pytest.mark.parametrize(("text", "parsed"), [
    ("2024-02-29", (2024, 2, 29)), ("2000-02-29", (2000, 2, 29)), ("2019-04-30", (2019, 4, 30)),
    ("02/29/2024", (2024, 2, 29)), ("2019", (2019, None, None)), ("2019-06", (2019, 6, None)),
    ("06/2019", (2019, 6, None)),
])
def test_real_and_partial_dates_still_parse(text: str, parsed: tuple) -> None:
    """Mutation: citations_calendar_check_rejects_every_day."""
    assert parse_partial_date(text) == parsed


def test_no_caller_computes_from_an_impossible_date() -> None:
    """The brief's age statement says the recorded date could not be read (or moves on to the next
    date), the maintenance due date and the coverage end date are not computed, and the sweep gives
    no age. Mutation: citations_unreadable_registry_date_said_missing."""
    registry = {"id": "appl-x", "record_id": "appliance:appl-x", "install_date": "2019-02-30"}
    statement, cited = age_statement(registry=registry, manufacture_date=None, today=TODAY)
    assert statement == ('The install date "2019-02-30" in property record appliance:appl-x could not be read as a '
                         "date, so the unit's age is unknown.")
    assert cited == "appliance:appl-x"
    statement, _ = age_statement(registry={**registry, "purchase_date": "2019-03-01"}, manufacture_date=None,
                                 today=TODAY)
    assert statement == ("Purchased 2019-03-01 per property record appliance:appl-x; about 7 years old as of "
                         "2026-10-13.")
    statement, _ = age_statement(registry=registry, manufacture_date="2018-05", today=TODAY)
    assert statement.startswith("Manufactured 2018-05 per the data plate; about 8 years old")
    assert age_statement(registry={}, manufacture_date=None, today=TODAY)[0] == (
        "No install, purchase or manufacture date is recorded, so the unit's age is unknown.")

    assert due_date("2019-02-30", "1 year") is None
    assert coverage.end_date("2019-02-30", 12) is None
    (row,) = coverage.coverage_rows([{"record_id": "warranty:x", "component": "tank", "term_months": 12,
                                      "start_event": "install"}], registry, TODAY)
    assert row["status"] == coverage.END_UNKNOWN
    assert sweep.install_age(registry, TODAY)["years"] is None
