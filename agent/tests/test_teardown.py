"""The teardown's front matter holds its shape (the portfolio teardown format).

The front of index.html is layered: the lede says why to care, the TL;DR says
what was found, the Summary argues it in scientific method order, and the
numbered parts show it. Each layer has a length and a shape, held here rather
than remembered. The same file also keeps em and en dashes out of the text a
reader sees on index.html and tool.html.

These tests only read index.html and tool.html. Each test names the mutation
in mutations.toml that turns it red.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

import pytest

from agent import config

ROOT = config.REPO_ROOT
DASHES = (chr(0x2014), chr(0x2013))
SUMMARY_LEADS = [
    "The problem.",
    "The thesis.",
    "The method.",
    "The results.",
    "What it means.",
    "Cautions, and how to check.",
]
BYLINE = "Roanuk Zaman \u00b7 Product teardown of a tool I designed, directed and shipped"
# Attributes whose text a reader sees or hears (tooltips, alt text, labels).
VISIBLE_ATTRS = ("alt", "title", "aria-label", "placeholder", "value")


def _page() -> str:
    return (ROOT / "index.html").read_text(encoding="utf-8")


def _text(fragment: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", fragment)
    plain = html.unescape(plain)
    return re.sub(r"\s+", " ", plain).strip()


def _sentences(plain: str) -> list[str]:
    return [s for s in re.split(r'(?<=[.!?])\s+(?=[A-Z"])', plain) if s]


def _section(section_id: str) -> str:
    match = re.search(rf'<section id="{section_id}"[\s\S]*?</section>', _page())
    assert match is not None, f"section #{section_id} is missing"
    return match.group(0)


def _brief_body() -> str:
    body = re.search(r'<p class="brief-body">([\s\S]*?)</p>', _section("tldr"))
    assert body is not None, "the TL;DR has no .brief-body paragraph"
    return body.group(1)


def _rail() -> list[tuple[str, str]]:
    nav = re.search(r'<nav class="toc"[\s\S]*?</nav>', _page())
    assert nav is not None, "the contents rail is missing"
    return [(href, _text(label)) for href, label in re.findall(r'<a href="#([^"]+)">([\s\S]*?)</a>', nav.group(0))]


def test_title_and_hero_order() -> None:
    """Mutation teardown_hero_eyebrow_changed: the eyebrow reads "Teardown"."""
    page = _page()
    assert "<title>AI Property Maintenance Advisor: A Product Teardown</title>" in page
    hero = re.search(r'<header class="hero">([\s\S]*?)</header>', page)
    assert hero is not None
    order = [
        '<p class="eyebrow">Product teardown</p>',
        "<h1>AI Property Maintenance Advisor</h1>",
        '<p class="lede">',
        '<p class="byline">',
        '<div class="cta-row">',
        '<div class="facts">',
    ]
    positions = [hero.group(1).find(marker) for marker in order]
    assert -1 not in positions, dict(zip(order, positions, strict=True))
    assert positions == sorted(positions)
    assert len(re.findall(r'class="btn ', hero.group(1))) == 2
    assert len(re.findall(r'<div class="fact">', hero.group(1))) == 4


def test_lede_is_two_or_three_sentences() -> None:
    """Mutation teardown_lede_four_sentences: a third and fourth sentence added."""
    lede = re.search(r'<p class="lede">([\s\S]*?)</p>', _page())
    assert lede is not None
    assert 2 <= len(_sentences(_text(lede.group(1)))) <= 3


def test_byline_text() -> None:
    """Mutation teardown_byline_changed: "designed, directed and shipped" edited."""
    byline = re.search(r'<p class="byline">([\s\S]*?)</p>', _page())
    assert byline is not None
    assert _text(byline.group(1)) == BYLINE


def test_tldr_kicker_heading_and_definition_line() -> None:
    """Mutation teardown_tldr_kicker_changed: the kicker reads "The short version"."""
    brief = _section("tldr")
    assert '<p class="section-kicker">Thirty seconds</p>' in brief
    assert "<h2>TL;DR</h2>" in brief
    assert (
        '<p class="brief-definition">Too long; didn\'t read. The whole page in five sentences.</p>'
        in brief
    )


def test_tldr_fits_five_sentences_and_100_words() -> None:
    """Mutation teardown_tldr_sixth_sentence: a sixth sentence added to the TL;DR."""
    plain = _text(_brief_body())
    assert len(_sentences(plain)) <= 5
    assert len(plain.split(" ")) <= 100


def test_tldr_has_at_most_one_number_and_no_links() -> None:
    """Mutations teardown_tldr_two_numbers (two numbers added) and
    teardown_tldr_link (a link added to the TL;DR)."""
    body = _brief_body()
    assert "<a " not in body
    assert len(re.findall(r"\d+", _text(body))) <= 1


def test_summary_has_the_six_leads_in_order() -> None:
    """Mutation teardown_summary_leads_swapped: two leads trade places."""
    summary = _section("summary")
    assert '<p class="section-kicker">Three minutes</p>' in summary
    leads = [m.strip() for m in re.findall(r"<strong>([^<]*)</strong>", summary)]
    assert leads == SUMMARY_LEADS
    body = re.search(r'<main id="main"[^>]*>\s*<section id="([^"]+)"', _page())
    assert body is not None and body.group(1) == "summary", "the Summary is not first in the body"


def test_contents_rail_lists_tldr_and_summary_first() -> None:
    """Mutation teardown_rail_drops_tldr: the TL;DR row removed from the rail."""
    assert _rail()[:2] == [("tldr", "TL;DR"), ("summary", "Summary")]


def test_contents_rail_labels_match_section_kickers() -> None:
    """Mutation teardown_rail_label_scheme: a rail row reads "5." for "Part 5".

    Front matter is listed by its heading. Every numbered or lettered section is
    listed as its kicker followed by its heading, so the rail and the body use
    one numbering scheme (ISO 2145: Arabic numerals for parts, letters for
    appendices).
    """
    page = _page()
    rail = _rail()
    sections = re.findall(
        r'<section id="([^"]+)"[^>]*>\s*<p class="section-kicker">([^<]*)</p>\s*<h2>([^<]*)</h2>', page
    )
    assert len(sections) == len(re.findall(r"<section id=", page)), "a section lacks a kicker and heading"
    expected = []
    for section_id, kicker, heading in sections:
        kicker, heading = html.unescape(kicker), html.unescape(heading)
        if section_id in ("tldr", "summary"):
            expected.append((section_id, heading))
        else:
            assert re.fullmatch(r"Part [1-9]\d*|Appendix [A-Z]", kicker), kicker
            expected.append((section_id, f"{kicker}. {heading}"))
    assert rail == expected
    parts = [int(k[5:]) for _, k, _ in sections if k.startswith("Part ")]
    assert parts == list(range(1, len(parts) + 1))


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        values = dict(attrs)
        self.chunks += [values[a] or "" for a in VISIBLE_ATTRS if a in values]
        if tag == "meta" and (values.get("name") == "description" or (values.get("property") or "").startswith("og:")):
            self.chunks.append(values.get("content") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.chunks.append(data)


def visible_text(page: str) -> str:
    parser = _VisibleText()
    parser.feed(page)
    return "\n".join(parser.chunks)


def test_visible_text_reader_sees_entities_and_attributes() -> None:
    """Mutation teardown_dash_reader_skips_attributes: alt and title text ignored."""
    em, en = DASHES
    sample = (
        f'<html><head><title>A &mdash; B</title><style>p::after{{content:"{en}"}}</style></head>'
        f'<body><img alt="x {en} y"><p>plain</p><script>var s = "{em}";</script></body></html>'
    )
    text = visible_text(sample)
    assert em in text and en in text
    assert visible_text(f'<p>a</p><script>"{em}"</script><style>"{en}"</style>').strip() == "a"


@pytest.mark.parametrize("name", ["index.html", "tool.html"])
def test_no_em_or_en_dash_in_visible_text(name: str) -> None:
    """Mutations teardown_index_em_dash (index.html) and teardown_tool_en_dash
    (tool.html): a dash added to the text a reader sees."""
    text = visible_text((ROOT / name).read_text(encoding="utf-8"))
    found = [line.strip() for line in text.splitlines() if any(d in line for d in DASHES)]
    assert not found, f"{name}: em or en dash in visible text: {found}"
