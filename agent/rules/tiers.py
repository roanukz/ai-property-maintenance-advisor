"""Tier ceiling by host (PLAN 8.5 rule 2, section 8.6, decision 5).

The final tier is the lower of the model's proposal and the host's ceiling, so
code can lower a tier and never raise one. Ceilings, first match wins:

1. a known forum or Q&A host: forum, always (even for a maker's PDF);
2. the maker's own domain: manufacturer;
3. any other host with an authorship quote that is verbatim in the fetched
   page text and names the maker: manufacturer;
4. any other host: dealer.

With no page text, an authorship quote cannot be verified, so there is no
manufacturer ceiling on a third party host.
"""

from __future__ import annotations

import unicodedata
from typing import Any
from urllib.parse import urlsplit

from agent import config

TIER_RANK = {"forum": 0, "dealer": 1, "manufacturer": 2}
DEFAULT_TIER = "forum"  # a source the model gave no tier


def host_of(url: str) -> str:
    """Lowercase host of a URL, without port or credentials."""
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _norm_host(host: str) -> str:
    """Lowercase, without the trailing dot of a fully qualified name ("www.reddit.com.")."""
    return host.lower().rstrip(".")


def is_forum_host(host: str) -> bool:
    host = _norm_host(host)
    if any(_matches_domain(host, forum) for forum in config.FORUM_HOSTS):
        return True
    bare = host[4:] if host.startswith("www.") else host
    return bare.startswith(config.FORUM_HOST_PREFIXES)


# Trailing corporate suffixes dropped from a maker name before matching.
CORPORATE_SUFFIXES = ("inc", "incorporated", "llc", "co", "corp", "corporation", "ltd", "limited", "company")


def _norm_name(name: str) -> str:
    """Casefolded words, with symbols (the ® of "SUNDANCE® SPAS") and punctuation
    turned into spaces and trailing corporate suffixes ("Inc.", "LLC") dropped."""
    text = "".join(
        " " if unicodedata.category(ch)[0] in "SP" else ch
        for ch in unicodedata.normalize("NFKC", name).casefold()
    )
    words = text.split()
    while len(words) > 1 and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def maker_keys(manufacturer: str | None) -> list[str]:
    """The `MAKER_DOMAINS` keys naming this maker ("Sundance" matches "sundance spas")."""
    if not manufacturer or not manufacturer.strip():
        return []
    name = _norm_name(manufacturer)
    return [
        key for key in config.MAKER_DOMAINS
        if name == key or key.startswith(name + " ") or name.startswith(key + " ")
    ]


def is_maker_host(host: str, manufacturer: str | None) -> bool:
    host = _norm_host(host)
    return any(
        _matches_domain(host, domain)
        for key in maker_keys(manufacturer)
        for domain in config.MAKER_DOMAINS[key]
    )


def normalize_text(text: str) -> str:
    """Section 8.11 normalization: NFC, whitespace runs to one space, trimmed. Nothing else."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def quote_in_text(quote: str, page_text: str) -> bool:
    """True when `quote` is a contiguous substring of the page text after normalization."""
    needle = normalize_text(quote)
    return bool(needle) and config.SNIPPET_JOINER not in needle and needle in normalize_text(page_text)


def authorship_verified(quote: str | None, page_text: str | None, manufacturer: str | None) -> bool:
    """An authorship quote counts only if it is verbatim in the page and names the maker."""
    if not quote or not page_text or not manufacturer or not manufacturer.strip():
        return False
    if not quote_in_text(quote, page_text):
        return False
    folded = _norm_name(quote)
    names = [_norm_name(manufacturer), *maker_keys(manufacturer)]
    return any(name and name in folded for name in names)


def ceiling_for(host: str, *, manufacturer: str | None, authorship_quote: str | None,
                page_text: str | None) -> str:
    """The highest tier this host can hold (section 8.6)."""
    if is_forum_host(host):
        return "forum"
    if is_maker_host(host, manufacturer):
        return "manufacturer"
    if authorship_verified(authorship_quote, page_text, manufacturer):
        return "manufacturer"
    return "dealer"


def lower_of(a: str, b: str) -> str:
    return a if TIER_RANK[a] <= TIER_RANK[b] else b


def final_tier(proposed: str | None, ceiling: str) -> str:
    return lower_of(proposed if proposed in TIER_RANK else DEFAULT_TIER, ceiling)


def apply_tiers(sources: list[dict[str, Any]], *, manufacturer: str | None,
                page_texts: dict[str, str] | None) -> list[dict[str, Any]]:
    """Set each source's host, proposed_tier and final tier; drop the authorship quote.

    `page_texts` maps a source URL to its fetched raw text, when any exists.
    """
    texts = page_texts or {}
    out = []
    for source in sources:
        item = dict(source)
        host = item.get("host") or host_of(item["url"])
        proposed = item.get("proposed_tier")
        quote = item.pop("authorship_quote", None)
        ceiling = ceiling_for(host, manufacturer=manufacturer, authorship_quote=quote,
                              page_text=texts.get(item["url"]))
        item["host"] = host
        item["proposed_tier"] = proposed if proposed in TIER_RANK else None
        item["tier"] = final_tier(proposed, ceiling)
        out.append(item)
    return out
