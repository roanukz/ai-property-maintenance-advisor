"""The teardown's front matter holds its shape (the portfolio teardown format).

The front of index.html is layered: the lede says why to care, the TL;DR says
what was found, the Summary argues it in scientific method order, and the
numbered parts show it. Each layer has a length and a shape, held here rather
than remembered. The same file also keeps em and en dashes out of the text a
reader sees on index.html and tool.html, holds README.md's section on
connecting the MCP server to what a stranger needs, and keeps README.md and
docs/mcp.md from telling anyone to put a key in an app's settings (decision
107).

These tests only read index.html, tool.html, README.md and docs/mcp.md. Each
test names the mutation in mutations.toml that turns it red.
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
# The MCP section (index.html#mcp): the live runs and builds it reports, and the limits it must state.
# The first live brief, run by a test program through the official client.
MCP_RUN = "t-bc31dd881bff44c4"
MCP_BUILD = "17427277685ed433"
# The live brief in Claude Desktop, on the build with decision 105's fixes.
MCP_DESKTOP_RUN = "t-7f23f74a338140bd"
MCP_DESKTOP_BUILD = "841df66ad9669ef8"
MCP_EXPLAINED = "MCP, the Model Context Protocol, is an open standard that lets an AI assistant call outside tools"
MCP_LIMITS = (
    "Claude Desktop has run one live, paid brief, but no live brief through an assistant has yet carried steps"
    " to try first or safety flags",
    "unverified which apps show MCP forms to the person, and without them both the owner's confirmation of the"
    " unit and the \"proceed\" pass through the assistant",
    "the pause to confirm the unit has not been put to an assistant since",
    "never set these tools to always allow",
    "that retelling is outside the tool's control",
    "shown on the recorded cases, not on a live run",
)
# README.md's MCP section: other text links to its heading's anchor, and it must tell anyone how to connect
# a copy of their own, and who pays for it (decision 107).
README_MCP_HEADING = "### Use it from Claude Desktop or Claude Code (MCP)"
README_MCP_STEPS = (
    'claude mcp add property-advisor -- "$PWD/.venv/bin/advisor-mcp" --cassette '
    '"$PWD/agent/tests/cassettes/flo.json"',
    "`claude mcp list` should show `property-advisor` as connected",
)
README_MCP_WHO_PAYS = (
    "**Nothing is hosted.**",
    "**Replay needs no keys and spends nothing.**",
    "a live brief's cost lands on that person's own Anthropic, Tavily and TypeSafe accounts",
    "No copy can use my keys",
    "**Keep keys in your copy's `.env`, never in an app's settings file.**",
    "**A live entry answers whatever reaches the assistant.**",
    "bypass permissions",
)
# No page may tell a reader to put a key in an app's settings: a key set with -e or --env on `claude mcp add`, or a
# key name as a field of a JSON env block (decision 107). docs/mcp.md's live settings must still show the safe form.
KEY_IN_APP_SETTINGS = re.compile(r"(?:-e|--env)\s+[\"']?(?:ANTHROPIC|TAVILY|TYPESAFE|LANGSMITH)_[A-Z_]*="
                                 r"|\"(?:ANTHROPIC|TAVILY|TYPESAFE|LANGSMITH)_[A-Z_]*\"\s*:")
DOCS_MCP_LIVE = (
    'claude mcp add property-advisor -e ADVISOR_MODE=cheap -- "$PWD/.venv/bin/advisor-mcp" --live',
    "Put only `ADVISOR_MODE` in",
)
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


def test_kappa_is_explained_where_it_first_appears() -> None:
    """Every technical term is explained on first use. Cohen's kappa first appears in
    Part 8's results table, so its explanation must sit beside that first mention, not
    later in Part 9. Mutation teardown_kappa_first_use_unexplained."""
    text = re.sub(r"\s+", " ", visible_text(_page()))
    first = text.lower().find("kappa")
    assert first != -1, "index.html no longer mentions kappa"
    assert "agreement corrected for" in text[first:first + 200], text[first:first + 200]


def test_paired_test_and_p_value_are_explained_where_they_first_appear() -> None:
    """Every technical term is explained on first use. The paired test is first named
    in Part 9's opening, so its explanation sits there; the p value is first named in
    the paired test's own section, so its explanation sits there; and Appendix B
    defines both. Mutations teardown_paired_test_first_use_unexplained,
    teardown_p_value_first_use_unexplained and teardown_terms_drop_p_value."""
    text = re.sub(r"\s+", " ", visible_text(_page()))
    for term, explanation in (("paired test", "compares two checks step by step"),
                              ("p value", "chance of a split")):
        found = re.search(rf"\b{term}\b", text, flags=re.IGNORECASE)
        assert found is not None, f"index.html no longer mentions {term}"
        nearby = text[found.start():found.start() + 200]
        assert explanation in nearby, nearby
    terms = _text(_section("terms"))
    assert "Paired test: a test that compares two checks step by step" in terms
    assert "p value: the chance of a split" in terms


def _paired_section_text() -> str:
    match = re.search(r"<h3>A paired test on the same steps</h3>([\s\S]*?)<p class=\"callout callout-warning\">",
                      _page())
    assert match is not None, "Part 9 has no paired test section ahead of its warning box"
    return _text(match.group(1))


def test_paired_section_gives_both_sides_of_every_jev_split_by_roanuks_labels() -> None:
    """By Roanuk's labels the paired test section reports each of Jev's pairs on both
    catches and false flags, with the p values from the scores file, so a reader sees
    the side that favors the other check too; and it states the 0.05 bar it sorts the
    splits by, with what a correction for the 24 comparisons does to it. Mutations
    teardown_paired_drops_word_list_false_flag_p and teardown_paired_drops_correction."""
    import json

    scores = json.loads((ROOT / "agent/safety_eval/claude_judge_scores.json").read_text(encoding="utf-8"))
    text = _paired_section_text()
    pairs = [pair for pair in scores["paired"]["against_roanuk"]["pairs"] if pair["first"].startswith("Jev")]
    assert len(pairs) == 3, pairs
    for pair in pairs:
        for kind in ("safety_steps_caught", "false_flags_raised"):
            cited = f"p value {pair[kind]['p_value']:.3f}"
            assert cited in text, f"{pair['first']} against {pair['second']}, {kind}: {cited} missing"
    assert text.count("A common bar is a p value under 0.05.") == 1, text
    assert "dividing the 0.05 bar by the 24 comparisons, leaves none of them under it" in text, text


class _ElementText(HTMLParser):
    """The text inside the one element with a given id, however its tags nest."""

    def __init__(self, element_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.element_id = element_id
        self.tag: str | None = None
        self.depth = 0
        self.closed = False
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.tag is None and dict(attrs).get("id") == self.element_id:
            self.tag, self.depth = tag, 1
        elif tag == self.tag and not self.closed:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == self.tag and not self.closed:
            self.depth -= 1
            self.closed = self.depth == 0

    def handle_data(self, data: str) -> None:
        if self.tag is not None and not self.closed:
            self.chunks.append(data)


def _element_text(page: str, element_id: str) -> str:
    parser = _ElementText(element_id)
    parser.feed(page)
    assert parser.tag is not None, f"index.html has no element with id={element_id!r}"
    assert parser.closed, f"the element with id={element_id!r} is never closed"
    return re.sub(r"\s+", " ", "".join(parser.chunks)).strip()


def test_mcp_section_names_its_run_build_and_limits() -> None:
    """The MCP section is the address a resume links to (index.html#mcp), so it must stand on
    its own: one element carries the anchor, MCP is explained where the page first names it and
    again inside the section, the section names each live run (the test program's and Claude
    Desktop's) and the build it ran on, and it states every limit the evidence leaves open;
    Appendix B defines MCP. Mutations
    teardown_mcp_anchor_renamed, teardown_mcp_first_use_unexplained,
    teardown_mcp_section_unexplained, teardown_mcp_run_dropped, teardown_mcp_build_dropped,
    teardown_mcp_desktop_run_dropped, teardown_mcp_desktop_build_dropped,
    teardown_mcp_limit_apps_dropped, teardown_mcp_limit_forms_dropped,
    teardown_mcp_limit_pause_dropped, teardown_mcp_limit_always_allow_dropped,
    teardown_mcp_limit_retelling_dropped, teardown_mcp_limit_live_flags_dropped and
    teardown_terms_drop_mcp."""
    page = _page()
    assert page.count('id="mcp"') == 1, "index.html#mcp must name exactly one element"
    text = re.sub(r"\s+", " ", visible_text(page))
    first = re.search(r"\bMCP\b", text)
    assert first is not None, "index.html no longer mentions MCP"
    assert "the Model Context Protocol, is an open standard" in text[first.start():first.start() + 120], \
        text[first.start():first.start() + 120]
    section = _element_text(page, "mcp")
    assert MCP_EXPLAINED in section
    assert f"run {MCP_RUN}" in section and f"build {MCP_BUILD}" in section
    assert f"run {MCP_DESKTOP_RUN}" in section and f"build {MCP_DESKTOP_BUILD}" in section
    missing = [limit for limit in MCP_LIMITS if limit not in section]
    assert not missing, f"the MCP section no longer states: {missing}"
    assert "MCP: the Model Context Protocol, an open standard" in _text(_section("terms"))


def _readme_mcp_section() -> str:
    """README.md's MCP section, from its heading to the next heading of its level or higher, whitespace collapsed."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.count(README_MCP_HEADING + "\n") == 1, f"README.md must have one {README_MCP_HEADING!r} heading"
    body = readme.split(README_MCP_HEADING + "\n", 1)[1]
    end = re.search(r"^#{1,3} ", body, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", body[:end.start()] if end else body)


def test_readme_tells_anyone_how_to_connect_a_copy_and_who_pays() -> None:
    """README.md's MCP section keeps its heading, which other text links to, and tells a stranger how to connect a
    copy of their own (the `claude mcp add` line with the `advisor-mcp` entry point and the recorded case, then
    `claude mcp list`) and who pays: nothing is hosted, replay needs no keys, a live brief's cost lands on the
    person running that copy, no copy can use my keys, keys belong in the copy's .env, never in an app's settings
    file, and a live entry answers whatever reaches the assistant, with nothing to stop it once a mode such as
    bypass permissions approves tool calls without asking (decision 107). Mutations
    readme_mcp_heading_renamed, readme_mcp_add_line_dropped, readme_mcp_list_dropped,
    readme_mcp_not_hosted_dropped, readme_mcp_replay_no_keys_dropped, readme_mcp_own_accounts_dropped,
    readme_mcp_not_my_keys_dropped, readme_mcp_keys_in_settings_allowed and readme_mcp_injection_warning_dropped."""
    section = _readme_mcp_section()
    missing = [fact for fact in (*README_MCP_STEPS, *README_MCP_WHO_PAYS) if fact not in section]
    assert not missing, f"README.md's MCP section no longer states: {missing}"
    assert "(#use-it-from-claude-desktop-or-claude-code-mcp)" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_no_page_tells_anyone_to_put_a_key_in_an_app_settings_file() -> None:
    """README.md and docs/mcp.md never set a key with -e or --env on `claude mcp add`, nor name one as a field of a
    JSON env block such as Claude Desktop's, since both are saved as plain text in the app's own settings; and
    docs/mcp.md's live settings still pass only ADVISOR_MODE (decision 107). Mutations
    docs_mcp_live_line_passes_a_key (the live `claude mcp add` line also passes a key with -e) and
    readme_desktop_env_block_holds_a_key (the README's Desktop entry gains an env block holding a key)."""
    for name in ("README.md", "docs/mcp.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        found = [match.group(0) for match in KEY_IN_APP_SETTINGS.finditer(text)]
        assert not found, f"{name} puts a key in an app's settings: {found}"
    docs = re.sub(r"\s+", " ", (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8"))
    missing = [line for line in DOCS_MCP_LIVE if line not in docs]
    assert not missing, f"docs/mcp.md no longer states: {missing}"
