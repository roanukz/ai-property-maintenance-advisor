"""Citations resolve into this run's source registry; prune and renumber (SC6
citation half, v1 guardrail tests 6 and 7). Each test names, in a comment above
it, a code change that turns it red.
"""

from __future__ import annotations

import copy
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.rules.citations import prune_and_renumber
from agent.rules.pipeline import run_rules


def registry(n: int) -> list[dict[str, Any]]:
    return [
        {"source_id": f"src-{i}", "url": f"https://site{i}.example.com/page", "title": f"Page {i}",
         "retrieved_at": "2026-09-18", "origin": "search", "text_sha256": None,
         # Names the code the candidates below carry, so rule 4 (code grounding,
         # Phase 3) passes and only the citation rules decide these cases.
         "snippet": "Synthetic snippet: panel code FLO means low flow."}
        for i in range(n)
    ]


def draft(**overrides: Any) -> dict[str, Any]:
    d = {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [], "candidates": [], "warranty": None, "no_reliable_answer": None,
        "upgrade_options": [], "maintenance_due": [], "source_tiers": [],
    }
    d.update(overrides)
    return d


def step(index: Any) -> dict[str, Any]:
    return {"step": "Check the filter", "detail": "", "safety_flag": False, "source_index": index}


def cand(index: Any, code: str | None = None) -> dict[str, Any]:
    return {"code": code, "documented_meaning": "Low flow", "documented_action": "Clean the filter",
            "who": "anyone", "why_shown": "", "source_index": index, "confirmed": False, "evidence": ""}


def rules(d: dict[str, Any], sources: list[dict[str, Any]]):
    return run_rules(d, sources=sources, observed_code=None, history_hits=[], registry=None, mode="replay",
                     provenance=None, pass_kind="synthesize")


def codes(errors: list[str]) -> list[str]:
    return [e.split(":")[0] for e in errors]


# Mutations: `draft_to_brief_dict` takes sources from the draft instead of the
# registry; `resolve_citations` stops dropping an upgrade whose index does not
# resolve; a caution's bad index is kept.
def test_every_index_resolves_to_a_source_registered_this_run() -> None:
    sources = registry(3)
    urls = {s["url"] for s in sources}
    raw = draft(
        try_first=[step(0)],
        candidates=[cand(2)],
        warranty_caution={"text": "Rental use may void coverage", "source_index": 3},
        upgrade_options=[{"successor_manufacturer": None, "successor_model": "M2", "reason": "discontinued",
                          "summary": "s", "source_index": 9, "evidence": ""}],
        sources=[{"url": "https://invented.example.net/x", "title": "Invented", "tier": "manufacturer"}],
    )
    brief = rules(raw, sources).brief
    assert {s["url"] for s in brief["sources"]} <= urls
    assert [s["url"] for s in brief["sources"]] == [sources[0]["url"], sources[2]["url"]]
    assert brief["warranty_caution"]["source_index"] is None
    assert brief["upgrade_options"] == []
    for item in brief["try_first"] + brief["candidates"]:
        assert 0 <= item["source_index"] < len(brief["sources"])
    # An index one past this run's registry is not a source, even if the model says so.
    assert codes(rules(draft(candidates=[cand(3)]), sources).errors) == ["candidate_index_out_of_range"]


# v1 guardrail test 6. Mutation: the candidate range check in the parity layer
# uses `<=` against the source count, or is removed.
def test_candidate_index_out_of_range_is_rejected() -> None:
    result = rules(draft(candidates=[cand(7, "FLO")]), registry(1))
    assert result.brief is None
    assert codes(result.errors) == ["candidate_index_out_of_range"]
    assert codes(rules(draft(candidates=[cand(1, "FLO")]), registry(1)).errors) == ["candidate_index_out_of_range"]
    assert rules(draft(candidates=[cand(0, "FLO")]), registry(1)).errors == []


# v1 guardrail test 7. Mutation: the step range check in the parity layer
# uses `<=`, or is removed.
def test_step_index_out_of_range_is_rejected() -> None:
    result = rules(draft(try_first=[step(5)]), registry(2))
    assert result.brief is None
    assert codes(result.errors) == ["step_index_out_of_range"]
    assert codes(rules(draft(try_first=[step(2)]), registry(2)).errors) == ["step_index_out_of_range"]
    assert rules(draft(try_first=[step(1)]), registry(2)).errors == []


def _cited_urls(brief: dict[str, Any]) -> list[tuple[str, Any]]:
    """Every citing field as (path, URL or None), in a fixed order."""
    urls = [s["url"] for s in brief["sources"]]

    def url(i: Any) -> Any:
        return None if i is None else urls[i]

    out = []
    for key in ("try_first", "candidates", "upgrade_options", "maintenance_due"):
        out += [(f"{key}[{n}]", url(e["source_index"])) for n, e in enumerate(brief[key])]
    if brief.get("warranty_caution"):
        out.append(("warranty_caution", url(brief["warranty_caution"]["source_index"])))
    if brief.get("warranty"):
        out += [(f"warranty.cautions[{n}]", url(c["source_index"]))
                for n, c in enumerate(brief["warranty"]["cautions"])]
    return out


@st.composite
def _briefs(draw) -> dict[str, Any]:
    n = draw(st.integers(min_value=1, max_value=8))
    index = st.integers(min_value=0, max_value=n - 1)
    maybe = st.one_of(st.none(), index)
    entries = lambda: st.lists(index.map(lambda i: {"source_index": i}), max_size=4)  # noqa: E731
    brief = {
        "sources": [{"url": f"https://s{i}.example.com/p"} for i in range(n)],
        "try_first": draw(entries()),
        "candidates": draw(entries()),
        "upgrade_options": draw(entries()),
        "maintenance_due": draw(entries()),
        "warranty_caution": draw(st.one_of(st.none(), maybe.map(lambda i: {"text": "t", "source_index": i}))),
        "warranty": draw(st.one_of(st.none(), st.lists(maybe, max_size=3).map(
            lambda idx: {"age_statement": "", "cautions": [{"text": "c", "source_index": i} for i in idx],
                         "verify": []}))),
    }
    return brief


# SC6 property. Mutations: renumbering off by one (`enumerate(keep, 1)`),
# pruning by position instead of by the kept list, or keeping uncited sources.
@settings(max_examples=200, deadline=None)
@given(brief=_briefs())
def test_prune_and_renumber_property(brief: dict[str, Any]) -> None:
    before = _cited_urls(brief)
    original_order = [s["url"] for s in brief["sources"]]
    pruned = copy.deepcopy(brief)
    prune_and_renumber(pruned)
    assert _cited_urls(pruned) == before
    kept = [s["url"] for s in pruned["sources"]]
    assert set(kept) == {u for _, u in before if u is not None}
    assert kept == [u for u in original_order if u in set(kept)]
