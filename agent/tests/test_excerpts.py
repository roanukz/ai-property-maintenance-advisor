"""Excerpts cut from raw_content (decision 24): verbatim, anchored, within budget."""

from __future__ import annotations

import random
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from agent import config
from agent.research.excerpts import (
    JOIN,
    Terms,
    cut_excerpts,
    cut_spans,
    excerpt_terms,
    model_family,
    render_excerpts,
    source_text,
)
from agent.research.tools import fetch_text, search_text
from agent.tests.test_research_limits import FakeCassette, answer, call, make_ctx, run_node

WORDS = ["Optima", "880", "FLO", "flow", "switch", "heater", "pump", "the", "manual", "HC-1234A", "reset",
         "breaker", "filter", "error", "code", "Sundance", "spa"]
SPACES = [" ", "  ", "\n", "\n\n", "\t", " \n "]


@st.composite
def pages(draw: st.DrawFn) -> str:
    # A seeded generator is far faster than drawing each word from hypothesis.
    rng = random.Random(draw(st.integers(min_value=0, max_value=2**32)))
    n = draw(st.integers(min_value=0, max_value=600))
    return "".join(rng.choice(WORDS) + rng.choice(SPACES) for _ in range(n))


@settings(max_examples=150, deadline=None)
@given(
    text=pages(),
    ids=st.lists(st.sampled_from(WORDS + ["Optima 880", "absent"]), max_size=3),
    symptom=st.lists(st.sampled_from(WORDS), max_size=3),
    budget=st.integers(min_value=0, max_value=config.FETCH_EXCERPT_MAX_CHARS),
)
@example(text="Optima  880\n\nshows FLO\tafter   the\npump starts. " * 60, ids=["FLO", "Optima 880"],
         symptom=["pump"], budget=300)
def test_every_excerpt_is_verbatim_substring_of_raw_content(text: str, ids: list[str], symptom: list[str],
                                                            budget: int) -> None:
    """Mutation excerpts_whitespace_normalized: excerpts are rejoined with
    single spaces (not substrings any more). Mutation excerpts_budget_ignored:
    the budget check on a new window is skipped (the rendered text passes
    the budget)."""
    excerpts = cut_excerpts(text, Terms(ids=tuple(ids), symptom=tuple(symptom)), budget)
    for excerpt in excerpts:
        assert excerpt and excerpt in text
    assert len(render_excerpts(excerpts)) <= budget


def long_page() -> str:
    filler = "General safety information about electrical equipment and water. " * 80
    return (filler + "Error code FLO on the Optima 880 means the flow switch did not close. "
            + filler + "Replace part HC-1234A if the switch is stuck. " + filler)


def test_excerpts_center_on_terms_deep_in_the_page_within_budgets(tmp_path: Path) -> None:
    """Mutation excerpts_no_identity_anchors: identity terms are not searched
    (only the page opening and the part number come back, so the FLO sentence
    is missing)."""
    raw = long_page()
    terms = excerpt_terms({"model": "Optima 880"}, "FLO", "heater shows FLO and will not heat")
    assert terms.ids == ("FLO", "Optima 880", "Optima")
    assert "heater" in terms.symptom and "will" not in terms.symptom and "shows" not in terms.symptom

    excerpts = cut_excerpts(raw, terms, config.FETCH_EXCERPT_MAX_CHARS)
    rendered = render_excerpts(excerpts)
    assert "Error code FLO on the Optima 880 means the flow switch did not close." in rendered
    assert "HC-1234A" in rendered
    assert len(rendered) <= config.FETCH_EXCERPT_MAX_CHARS
    assert JOIN in rendered and all(e in raw for e in excerpts)

    url = "https://example.com/optima-manual"
    shown = fetch_text(url, [{"url": url, "raw_content": raw}], terms)
    assert len(shown) <= config.FETCH_EXCERPT_MAX_CHARS and shown.startswith(url + "\n")
    assert "FLO on the Optima 880" in shown

    for_synth = source_text(raw, "snippet", terms)
    assert len(for_synth) <= config.SEARCH_RESULT_MAX_CHARS and "FLO on the Optima 880" in for_synth
    assert source_text(None, "the snippet", terms) == "the snippet"

    results = [{"url": f"https://example.com/{i}", "title": "T" * 50, "content": "c" * 5_000} for i in range(3)]
    for block in search_text(results).split("\n\n"):
        assert len(block) <= config.SEARCH_RESULT_MAX_CHARS


def test_fetch_tool_shows_the_model_excerpts_not_the_page(tmp_path: Path) -> None:
    """Mutation research_fetch_terms_dropped: the research node builds the
    tools without terms (the model sees only the page opening). Mutation
    research_source_excerpts_unanchored: sources are cut with empty Terms."""
    url = "https://example.com/optima-manual"
    script = [call("fetch", url=url), answer("done")]
    cassette = FakeCassette(script, fetch=[{"results": [{"url": url, "raw_content": long_page()}], "credits": 1}])
    ctx = make_ctx(tmp_path, cassette)

    out = run_node(ctx, identity={"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "",
                                  "manufacture_date": ""}, observed_code="FLO", symptom="shows FLO")

    tool_message = ctx.replay["models"]["research"].received[-1][-1]
    assert tool_message.type == "tool"
    assert "FLO on the Optima 880" in tool_message.content
    assert len(tool_message.content) <= config.FETCH_EXCERPT_MAX_CHARS
    # The same anchored excerpts are stored on the source for synthesize.
    (source,) = out["sources"]
    assert any("FLO on the Optima 880" in e for e in source["excerpts"])
    assert all(e in long_page() for e in source["excerpts"])
    assert len(render_excerpts(source["excerpts"])) <= config.SEARCH_RESULT_MAX_CHARS


def test_model_family() -> None:
    """Mutation excerpts_family_is_model: model_family returns the model itself."""
    assert model_family("Optima 880") == "Optima"
    assert model_family("XR16") == "XR"
    assert model_family("880") is None and model_family("") is None


GLUED = ["Code:FLO/flow-switch", "filter/circulation", "(FLO)", "FLO,", "deactivated.", "880-series", "1.",
         "intake.When", "fault-code-display:FLO"]


@st.composite
def glued_pages(draw: st.DrawFn) -> str:
    rng = random.Random(draw(st.integers(min_value=0, max_value=2**32)))
    n = draw(st.integers(min_value=0, max_value=400))
    return "".join(rng.choice(WORDS + GLUED) + rng.choice(SPACES) for _ in range(n)).lstrip(" ")


def at_word_edges(text: str, start: int, end: int) -> bool:
    """Neither edge of text[start:end] sits between two non whitespace characters."""
    def edge(i: int) -> bool:
        return i <= 0 or i >= len(text) or text[i - 1].isspace() or text[i].isspace()
    return edge(start) and edge(end)


@settings(max_examples=200, deadline=None)
@given(
    text=glued_pages(),
    ids=st.lists(st.sampled_from(WORDS + ["Optima 880", "absent"]), max_size=3),
    symptom=st.lists(st.sampled_from(WORDS), max_size=3),
    budget=st.integers(min_value=0, max_value=config.FETCH_EXCERPT_MAX_CHARS),
)
@example(text="Opening words of a page with no anchor in it at all. " * 40, ids=[], symptom=[], budget=103)
def test_excerpts_never_start_or_end_inside_a_word(text: str, ids: list[str], symptom: list[str],
                                                  budget: int) -> None:
    """Mutations: excerpts_snap_off (window edges are never moved to a word
    boundary); excerpts_narrow_window_unsnapped (the window cut to fit the
    remaining budget keeps its raw edges); excerpts_opening_mid_word (a page
    with no anchor is cut at the budget, mid word). Phase 6 finding 3."""
    spans = cut_spans(text, Terms(ids=tuple(ids), symptom=tuple(symptom)), budget)
    for start, end in spans:
        assert 0 <= start < end <= len(text)
        assert at_word_edges(text, start, end), (text[max(0, start - 10):start], text[end:end + 10])
    excerpts = cut_excerpts(text, Terms(ids=tuple(ids), symptom=tuple(symptom)), budget)
    assert excerpts == [text[s:e] for s, e in spans]
    assert len(render_excerpts(excerpts)) <= budget


def test_excerpt_keeps_the_whole_word_the_phase6_edge_cut() -> None:
    """Mutations: excerpts_narrow_window_unsnapped (the excerpt ends "and filte",
    the Phase 6 cut); excerpts_snap_cuts_anchor_end and
    excerpts_snap_cuts_anchor_start (moving an edge inward past the anchor
    drops the code the window was cut for)."""
    filler = "General notes on water care and cover storage for the season. " * 30
    line = ("FLO stands for flow switch. The heater is deactivated and filter/circulation "
            "may be deactivated, also.")
    page = filler + line + " " + filler
    terms = excerpt_terms({"model": "Optima 880"}, "FLO", "panel shows FLO")
    for budget in range(60, 200, 7):
        spans = cut_spans(page, terms, budget)
        rendered = render_excerpts([page[s:e] for s, e in spans])
        assert len(rendered) <= budget
        assert all(at_word_edges(page, s, e) for s, e in spans)
        assert "FLO" in rendered
        assert "filte" not in rendered or "filter/circulation" in rendered

    # The code glued to its neighbors on both sides is kept whole, not cut out.
    for token, budgets in (("fault-code-display:FLO/flow-switch-fault-indicator", (50, 52, 55, 58)),
                           ("fault-code-display-panel-reading:FLO", (40, 45, 50))):
        glued = filler + token + " shows on the panel. " + filler
        for budget in budgets:
            [(s, e)] = cut_spans(glued, Terms(ids=("FLO",)), budget)
            assert token in glued[s:e], (budget, glued[s:e])
