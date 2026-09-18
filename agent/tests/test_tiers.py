"""Tier ceiling by host (PLAN 8.6, decision 5): code lowers a tier, never raises it.

All page text here is synthetic. Each test names, in a comment above it, a code
change that turns it red.
"""

from __future__ import annotations

import pytest

from agent.rules.tiers import apply_tiers

MAKER = "Sundance Spas"
PAGE = ("Owner's Manual.\nThis manual was written and published by  Sundance Spas, Inc.\n"
        "Section 4: error codes and what they mean.")
QUOTE = "This manual was written and published by Sundance Spas, Inc."

# (id, url, proposed tier, authorship quote, page text, manufacturer, final tier)
ROWS = [
    ("forum_host_caps_manufacturer", "https://reddit.com/r/hottubs/1", "manufacturer", None, None, MAKER, "forum"),
    ("forum_subdomain", "https://www.reddit.com/r/hottubs/1", "dealer", None, None, MAKER, "forum"),
    ("stackexchange_host", "https://diy.stackexchange.com/q/1", "manufacturer", None, None, MAKER, "forum"),
    ("justanswer_even_with_quote", "https://www.justanswer.com/spa/1", "manufacturer", QUOTE, PAGE, MAKER,
     "forum"),
    ("forums_prefix", "https://forums.spaboard.example/t/1", "dealer", None, None, MAKER, "forum"),
    ("forum_prefix_after_www", "https://www.forum.spaboard.example/t/1", "manufacturer", None, None, MAKER,
     "forum"),
    ("maker_domain", "https://www.sundancespas.com/manual.pdf", "manufacturer", None, None, MAKER,
     "manufacturer"),
    ("maker_domain_short_name", "https://sundancespas.com/manual.pdf", "manufacturer", None, None, "Sundance",
     "manufacturer"),
    ("maker_domain_never_raises_dealer", "https://www.sundancespas.com/p", "dealer", None, None, MAKER, "dealer"),
    ("maker_domain_never_raises_forum", "https://www.sundancespas.com/p", "forum", None, None, MAKER, "forum"),
    ("lookalike_domain", "https://notsundancespas.com/manual.pdf", "manufacturer", None, None, MAKER, "dealer"),
    ("other_makers_domain", "https://www.trane.com/manual.pdf", "manufacturer", None, None, MAKER, "dealer"),
    ("archive_without_text", "https://www.manualslib.com/manual/1", "manufacturer", QUOTE, None, MAKER,
     "dealer"),
    ("archive_verified_quote", "https://www.thespaworks.com/manual.pdf", "manufacturer", QUOTE, PAGE, MAKER,
     "manufacturer"),
    ("quote_verified_but_proposal_dealer", "https://www.thespaworks.com/m.pdf", "dealer", QUOTE, PAGE, MAKER,
     "dealer"),
    ("quote_one_character_changed", "https://www.thespaworks.com/m.pdf", "manufacturer",
     QUOTE.replace("written", "writen"), PAGE, MAKER, "dealer"),
    ("quote_case_flipped", "https://www.thespaworks.com/m.pdf", "manufacturer", QUOTE.upper(), PAGE, MAKER,
     "dealer"),
    ("quote_not_naming_maker", "https://www.thespaworks.com/m.pdf", "manufacturer",
     "Section 4: error codes and what they mean.", PAGE, MAKER, "dealer"),
    ("quote_with_snippet_joiner", "https://www.thespaworks.com/m.pdf", "manufacturer",
     "published by [...] Sundance Spas", "published by [...] Sundance Spas", MAKER, "dealer"),
    ("quote_with_unknown_maker", "https://www.thespaworks.com/m.pdf", "manufacturer", QUOTE, PAGE, None,
     "dealer"),
    ("no_proposal_is_forum", "https://www.sundancespas.com/p", None, None, None, MAKER, "forum"),
    ("dealer_host_keeps_dealer", "https://www.spastore.com/blog/1", "dealer", None, None, MAKER, "dealer"),
    ("dealer_host_caps_manufacturer", "https://www.spastore.com/blog/1", "manufacturer", None, None, MAKER,
     "dealer"),
    # The maker name as a plate reads it: symbols, punctuation and a corporate suffix.
    ("maker_name_with_symbol", "https://www.sundancespas.com/manual.pdf", "manufacturer", None, None,
     "SUNDANCE\u00ae SPAS", "manufacturer"),
    ("maker_name_with_suffix", "https://www.sundancespas.com/manual.pdf", "manufacturer", None, None,
     "Sundance Spas, Inc.", "manufacturer"),
    # A maker with no MAKER_DOMAINS key: only its own normalized name can match the quote.
    ("unlisted_maker_suffix_quote", "https://www.heaterparts.example.com/m.pdf", "manufacturer",
     "Written and published by Acme Heaters.", "Manual. Written and published by Acme Heaters. Page 2.",
     "Acme Heaters, Inc.", "manufacturer"),
    ("fqdn_forum_url", "https://www.reddit.com./r/hottubs/1", "manufacturer", None, None, MAKER, "forum"),
]


# Mutations: `final_tier` returns the proposal (ceiling ignored); `is_forum_host`
# checked after the maker domain; `_matches_domain` uses endswith(domain)
# without the dot; `quote_in_text` folds case; the maker name check removed;
# tiers_maker_name_symbols_kept and tiers_maker_suffix_kept (the plate's
# "SUNDANCE® SPAS" or "Sundance Spas, Inc." loses the maker domain ceiling).
@pytest.mark.parametrize("url,proposed,quote,page,maker,expected", [r[1:] for r in ROWS],
                         ids=[r[0] for r in ROWS])
def test_host_ceiling_table(url, proposed, quote, page, maker, expected) -> None:
    source = {"url": url, "title": "t", "tier": proposed or "forum", "proposed_tier": proposed}
    if quote is not None:
        source["authorship_quote"] = quote
    [out] = apply_tiers([source], manufacturer=maker, page_texts={url: page} if page else None)
    assert out["tier"] == expected
    assert out["proposed_tier"] == proposed
    assert out["host"] and out["host"] in url
    assert "authorship_quote" not in out


# A host stored by research in fully qualified form ("www.reddit.com.") keeps
# its ceiling (C4). Mutation tiers_forum_host_keeps_trailing_dot: is_forum_host
# no longer strips the dot, so the forum gets the dealer ceiling.
@pytest.mark.parametrize("host,maker,expected", [
    ("www.reddit.com.", MAKER, "forum"),
    ("forums.spaboard.example.", MAKER, "forum"),
    ("www.sundancespas.com.", MAKER, "manufacturer"),
])
def test_stored_fqdn_host_keeps_its_ceiling(host: str, maker: str, expected: str) -> None:
    source = {"url": f"https://{host}/p", "host": host, "title": "t", "tier": "manufacturer",
              "proposed_tier": "manufacturer"}
    [out] = apply_tiers([source], manufacturer=maker, page_texts=None)
    assert out["tier"] == expected
