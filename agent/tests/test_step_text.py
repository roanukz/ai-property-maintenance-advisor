"""Step text helpers (agent/rules/step_text.py): normalizing, keying and the word rule.

Each test names the mutation in agent/tests/mutations.toml that turns it red.
"""

from __future__ import annotations

import hashlib

import pytest

from agent import config
from agent.rules.step_text import normalize_text, step_key, word_rule

BREAKER_STEP = "Turn off power at the breaker, wait 20 minutes, then restore power"
WATER_STEP = "Check the water level is at the correct fill line"


def test_normalize_text_folds_case_space_prefix_and_period() -> None:
    """Mutations: step_text_case_kept (no lowercasing); step_text_prefix_kept
    (v1's "Safety: " title prefix survives, so a v1 step and its v2 twin get
    two keys)."""
    assert normalize_text("  Safety:  Turn OFF\tpower\n at the  breaker. ") == "turn off power at the breaker"
    assert normalize_text(None) == ""
    assert normalize_text("Clean the filter") == normalize_text("clean   the FILTER.")


def test_step_key_is_sha256_of_normalized_step_and_detail() -> None:
    """Mutation: step_key_ignores_detail (two steps with one title and different
    details share a key, so one Jev answer would be served for both)."""
    expected = hashlib.sha256("check the filter\nit may be clogged".encode("utf-8")).hexdigest()
    assert step_key("Check the filter.", "It may be  clogged") == expected
    assert step_key("Safety: Check the filter", "it may be clogged.") == expected
    assert step_key("Check the filter", "a") != step_key("Check the filter", "b")
    assert step_key("a", "b c") != step_key("a b", "c")


def test_word_rule_flags_the_published_miss_and_not_the_water_step() -> None:
    """Mutation: safety_words_published_list_no_breaker_power (the published list
    loses "breaker" and "power")."""
    assert word_rule(BREAKER_STEP, "This resets the flow switch sensor", config.SAFETY_WORDS_PUBLISHED)
    assert not word_rule(WATER_STEP, "", config.SAFETY_WORDS_PUBLISHED)


@pytest.mark.parametrize("step,detail,flagged", [
    ("Clean or replace the air filter", "With power off at the BREAKER, clean the filters", True),  # from the detail
    ("Let the heater cool", None, True),
    ("Check the Heating element", "", True),
    ("Check the preheat setting", "", False),       # inside a longer word
    ("Inspect the powerpack cover", "", False),
    ("Open the breakerbox door", "", False),
    ("Wipe the overheated panel", "", False),
    ("Check the heatsink", "", False),
    ("Run the pump", "Listen for the motor", False),
])
def test_word_rule_matches_whole_words_in_any_case(step: str, detail: str | None, flagged: bool) -> None:
    """Mutation: word_rule_substring (the pattern loses its word boundaries, so
    "preheat" and "powerpack" match)."""
    assert word_rule(step, detail, config.SAFETY_WORDS_PUBLISHED) is flagged


def test_word_rule_with_no_words_flags_nothing() -> None:
    assert not word_rule(BREAKER_STEP, "", ())
    assert not word_rule(BREAKER_STEP, "", ("", "  "))


PUBLISHED_WORDS = ("breaker", "breakers", "power", "powered", "powering",
                   "heat", "heater", "heaters", "heating", "heated")


def test_published_word_list_is_exactly_the_ten_words() -> None:
    """Mutations: word_list_published_drops_heated (a word leaves the list);
    word_list_published_adds_panel (a word joins it). Either would change the
    shipped flags and SC12a's "word rule, as published" arm at once."""
    assert config.SAFETY_WORDS_PUBLISHED == PUBLISHED_WORDS
    for word in PUBLISHED_WORDS:
        assert word_rule(f"Check the {word}", "", config.SAFETY_WORDS_PUBLISHED), word
