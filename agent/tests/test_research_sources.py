"""SC6: the source registry is built from successful tool artifacts only (PLAN section 5)."""

from __future__ import annotations

import json
from pathlib import Path

from agent import config
from agent.research.tools import load_page_text, lookup_log_path
from agent.tests.test_research_limits import FakeCassette, answer, call, make_ctx, page, run_node

SOURCE_KEYS = {"source_id", "url", "host", "title", "retrieved_at", "origin", "text_sha256", "snippet",
               "excerpts"}


def result(url: str, raw: str | None = None) -> dict:
    return {"url": url, "title": f"Title of {url}", "content": f"Snippet of {url}", "raw_content": raw,
            "score": 0.5}


def test_registry_is_exactly_successful_tool_artifacts(tmp_path: Path) -> None:
    """Mutation research_sources_from_whole_collector: build sources from
    ctx.collector instead of ctx.collector[start:] (the stale artifact from an
    earlier invocation leaks in). Mutation research_sources_duplicate_urls:
    the first appearance check is dropped (doc2 appears twice). Mutation
    research_sources_repeat_earlier_pass: the known URL check is dropped (a
    URL already in state is added again)."""
    a = "https://example.com/doc1"
    b = "https://example.com/doc2"
    c = "https://example.org/manual.pdf"
    earlier = "https://example.net/from-an-earlier-pass"
    stale = "https://example.com/stale-from-another-invocation"
    invented = "https://example.com/named-only-by-the-model"
    failed_fetch = "https://example.com/fetch-fails"
    searches = [
        {"results": [result(a, "AR-500 manual, raw text."), result(b)], "credits": 1},
        {"results": [result(b, "AR-500 page two."), result(earlier)], "credits": 1},  # b repeats
        {"error": "Error 500: upstream"},  # an ordinary failure: error ToolMessage, no artifact
        {"results": [result(c, "AR-500 service manual.")], "credits": 1},
        {"results": [], "credits": 1},  # empty: a failure
        {"results": [result("https://example.com/sixth")], "credits": 1},  # never reached: blocked
    ]
    fetches = [{"results": [], "credits": 1}]
    script = [call("search", query=f"q{i}") for i in range(1, 7)] + [
        call("fetch", url=failed_fetch),
        answer(f"The answer is at {invented} and {a}."),
    ]
    ctx = make_ctx(tmp_path, FakeCassette(script, search=searches, fetch=fetches))
    ctx.collector.append({"tool": "search", "query": "old", "results": [result(stale)], "credits": 1})
    prior = [{"source_id": "src-prior", "url": earlier, "host": "example.net", "title": None,
              "retrieved_at": "2026-09-18T00:00:00+00:00", "origin": "search", "text_sha256": None,
              "snippet": None, "excerpts": []}]

    out = run_node(ctx, sources=prior)

    urls = [s["url"] for s in out["sources"]]
    assert urls == [a, b, c]
    assert invented not in urls and failed_fetch not in urls and stale not in urls
    for source in out["sources"]:
        assert set(source) == SOURCE_KEYS
        assert source["origin"] == "search" and source["host"] in ("example.com", "example.org")
    # b had no raw text in its first result; the later result for b brought it.
    texts = [load_page_text(ctx.pages_dir, s["text_sha256"]) for s in out["sources"]]
    assert texts == ["AR-500 manual, raw text.", "AR-500 page two.", "AR-500 service manual."]
    assert len({s["source_id"] for s in out["sources"]}) == 3
    assert [s["excerpts"] for s in out["sources"]] == [[t] for t in texts]  # short pages are kept whole

    statuses = [(e["tool"], e["status"]) for e in out["search_trail"]]
    assert statuses == [("search", "ok"), ("search", "ok"), ("search", "error"), ("search", "ok"),
                        ("search", "empty"), ("search", "blocked"), ("fetch", "empty")]
    assert [e["n_results"] for e in out["search_trail"]] == [2, 2, 0, 1, 0, 0, 0]

    log = [json.loads(line) for line in lookup_log_path(ctx).read_text(encoding="utf-8").splitlines()]
    assert [(r["tool"], r["status"]) for r in log] == [s for s in statuses if s[1] != "blocked"]
    assert all("raw_content" not in r for rec in log for r in rec.get("results", []))
    assert lookup_log_path(ctx).parent == tmp_path / config.LOOKUPS_DIR.name


def test_fetch_source_keeps_extract_origin_and_text(tmp_path: Path) -> None:
    """Mutation research_fetch_origin_wrong: fetch results get origin "search"."""
    url = "https://example.com/manual"
    script = [call("fetch", url=url), answer("done")]
    ctx = make_ctx(tmp_path, FakeCassette(script, fetch=[page(url)]))

    out = run_node(ctx)

    (source,) = out["sources"]
    assert source["origin"] == "extract" and source["snippet"] is None and source["title"] is None
    assert load_page_text(ctx.pages_dir, source["text_sha256"]) == page(url)["results"][0]["raw_content"]


def test_sources_follow_the_call_order_not_the_finish_order(tmp_path: Path, monkeypatch) -> None:
    """Mutation research_sources_in_finish_order: sources are built in the order
    parallel calls finished, so a replayed draft's source_index names a
    different page from run to run (decision D4 finding)."""
    import threading
    import time

    from agent.replay.tool_stubs import StubInner

    first, second = "https://example.com/called-first", "https://example.com/called-second"
    finished = threading.Event()
    real_invoke = StubInner.invoke

    def invoke(self, input):  # the first call finishes only after the second has
        if input.get("query") == "slow":
            finished.wait(timeout=5)
            time.sleep(0.2)
            return real_invoke(self, input)
        try:
            return real_invoke(self, input)
        finally:
            finished.set()

    monkeypatch.setattr(StubInner, "invoke", invoke)
    both = {"message": {"content": "", "usage": {"input_tokens": 1_000, "output_tokens": 100},
                        "tool_calls": [{"name": "search", "args": {"query": "slow"}},
                                       {"name": "search", "args": {"query": "fast"}}]}}
    searches = [{"query": "slow", "results": [result(first, "AR-500 page one.")], "credits": 1},
                {"query": "fast", "results": [result(second, "AR-500 page two.")], "credits": 1}]
    ctx = make_ctx(tmp_path, FakeCassette([both, answer("done")], search=searches))

    out = run_node(ctx)

    assert [a["query"] for a in ctx.collector] == ["fast", "slow"]  # they did finish out of order
    assert [s["url"] for s in out["sources"]] == [first, second]
