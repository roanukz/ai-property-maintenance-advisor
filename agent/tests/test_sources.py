"""Non web URLs never enter the source registry (PLAN 6.1 row 9, the registry half).

A retrieved result whose URL is not http or https (a javascript: link, an ftp
link, or a fetch of a URL the model typed without its scheme) is skipped by
research and recorded in the trail. As a second line, a registry source the
draft does not cite never reaches v1's source URL check, so one bad uncited
URL cannot reject every draft. Each test names the mutation that turns it red.
"""

from __future__ import annotations

from pathlib import Path

from agent.nodes.research import REGISTRY_TRAIL_TOOL, SKIPPED_NON_WEB, build_sources
from agent.rules.clearing import searched_from_trail
from agent.rules.pipeline import run_rules
from agent.state import RunContext

GOOD = "https://www.spastore.example./manual.pdf"
BAD = ("javascript:alert(1)", "www.sundancespas.com/manual.pdf", "ftp://files.example.com/m.pdf")


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(run_id="run-src", mode="replay", ledger_path=tmp_path / "l.sqlite",
                      registry_path=tmp_path / "r.sqlite", graph_path=tmp_path / "g.json",
                      pages_dir=tmp_path / "pages")


def test_non_http_retrieved_url_is_not_registered(tmp_path: Path) -> None:
    """Mutation research_non_web_registered: build_sources registers every URL."""
    results = [{"url": url, "title": "t", "content": "snippet", "raw_content": "page text"}
               for url in (*BAD, GOOD)]
    artifact = {"tool": "fetch", "results": results}
    trail = [{"tool": "search", "query": "q1", "n_results": 4, "credits": 1, "at": "t0", "status": "ok",
              "artifact": artifact, "pages": {}}]
    sources = build_sources(_ctx(tmp_path), [artifact], trail, set())
    assert [s["url"] for s in sources] == [GOOD]
    assert sources[0]["host"] == "www.spastore.example"  # stored without the trailing dot
    skipped = [e for e in trail if e["status"] == SKIPPED_NON_WEB]
    assert [e["query"] for e in skipped] == list(BAD)
    assert {e["tool"] for e in skipped} == {REGISTRY_TRAIL_TOOL}
    assert searched_from_trail(trail) == ["q1"]  # a skip is not a search


def _draft(index: int) -> dict:
    return {
        "status": "ok", "matched_identity": None, "warranty_caution": None, "happened_before": None,
        "try_first": [{"step": "Check the breaker", "detail": "", "safety_flag": True, "source_index": index}],
        "candidates": [], "warranty": None, "no_reliable_answer": None, "upgrade_options": [],
        "maintenance_due": [],
        "source_tiers": [{"source_index": index, "tier": "dealer", "authorship_quote": ""}],
    }


def _rules(draft: dict, urls: list[str]):
    sources = [{"url": u, "title": f"s{i}", "origin": "search"} for i, u in enumerate(urls)]
    return run_rules(draft, sources=sources, observed_code=None, history_hits=[], registry=None,
                     mode="replay", provenance=None, pass_kind="synthesize")


def test_uncited_non_web_source_does_not_reject_the_brief() -> None:
    """Mutations: pipeline_parity_sees_uncited_sources (the drop before the parity
    layer is cut; one uncited ftp URL rejects the draft); pipeline_drop_without_renumber
    (the step still points at index 1 after index 0 is dropped)."""
    https = "https://a.example.com/x"
    after = _rules(_draft(0), [https, "ftp://b.example.com/y"])
    assert after.errors == [] and [s["url"] for s in after.brief["sources"]] == [https]
    before = _rules(_draft(1), ["ftp://b.example.com/y", https])
    assert before.errors == []
    assert [s["url"] for s in before.brief["sources"]] == [https]
    assert before.brief["try_first"][0]["source_index"] == 0
    # A cited non web URL is still refused, as v1 refused it.
    cited = _rules(_draft(0), ["ftp://b.example.com/y", https])
    assert cited.brief is None and cited.errors[0].startswith("source_url_not_web")
