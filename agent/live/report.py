"""The Phase 5 gate numbers for one run, offline (PLAN section 10, Phase 5 row; decision 24).

`advisor ledger --run RUN_ID` prints these after the ledger lines. Nothing
here calls a model or Tavily: it reads the ledger rows, the run's lookup log
and the page files a live run saved.

- tokens by node: input, cache read, cache write and output, summed over the
  run's charge rows;
- actual cost against the estimate: the ledger's run total against
  preflight.run_plan's typical and worst case for one run of that mode (with
  a photo when a read_plate charge exists);
- R4, the snippet versus raw_content match rate: every search result snippet
  in the lookup log whose page text was saved is cut into its chunks at
  config.SNIPPET_JOINER, and each chunk is looked for in the page text under
  the evidence normalization (rules/evidence.normalize_for_span).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent import config

TOKEN_COLUMNS = ("input_tokens", "cache_read", "cache_write", "output_tokens")


def tokens_by_node(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Summed token columns of the charge rows, per node, in first charge order."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.get("kind") != "charge" or row.get("provider") != "anthropic":
            continue
        node = str(row.get("node") or "unknown")
        sums = out.setdefault(node, {c: 0 for c in TOKEN_COLUMNS})
        for column in TOKEN_COLUMNS:
            sums[column] += int(row.get(column) or 0)
    return out


def snippet_chunks(snippet: str) -> list[str]:
    """A snippet cut at Tavily's joiner, each chunk normalized; empty chunks dropped."""
    from agent.rules.evidence import normalize_for_span

    chunks = (normalize_for_span(part) for part in snippet.split(config.SNIPPET_JOINER))
    return [c for c in chunks if c]


def r4_match(lookups: list[dict[str, Any]], pages_dir: Path) -> dict[str, int]:
    """Snippet chunks found in their page's saved text: {"chunks", "found", "results", "no_text"}."""
    from agent.research.tools import load_page_text
    from agent.rules.evidence import normalize_for_span

    counts = {"chunks": 0, "found": 0, "results": 0, "no_text": 0}
    for entry in lookups:
        if entry.get("tool") != "search":
            continue
        for result in entry.get("results") or []:
            snippet = result.get("content") or ""
            if not snippet:
                continue
            text = load_page_text(pages_dir, result.get("text_sha256"))
            if text is None:
                counts["no_text"] += 1
                continue
            page = normalize_for_span(text)
            counts["results"] += 1
            for chunk in snippet_chunks(snippet):
                counts["chunks"] += 1
                counts["found"] += chunk in page
    return counts


def _lookups(run_id: str) -> list[dict[str, Any]]:
    path = config.PAGES_DIR.parent / config.LOOKUPS_DIR.name / f"{run_id}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_report_lines(ledger: Any, run_id: str) -> list[str]:
    """Tokens by node, the run total against its estimate, and R4, for one run."""
    from agent.live.preflight import STAGE_ASK, run_plan

    rows = ledger.rows(run_id)
    if not rows:
        return [f"run {run_id}: no ledger rows"]
    lines = [f"run {run_id}: tokens by node (input, cache read, cache write, output)"]
    for node, sums in tokens_by_node(rows).items():
        lines.append(f"  {node}: " + ", ".join(str(sums[c]) for c in TOKEN_COLUMNS))
    mode = str(rows[0].get("mode"))
    total = ledger.run_total(run_id)
    if mode in config.LIVE_MODES:
        photo = any(r.get("kind") == "charge" and r.get("node") == "read_plate" for r in rows)
        plan = run_plan(mode, photo=photo, stage=STAGE_ASK)
        lines.append(f"  actual {total:.4f} USD against the estimate: typical {plan.typical_usd:.4f} USD "
                     f"({total - plan.typical_usd:+.4f}), worst case {plan.worst_usd:.4f} USD")
    else:
        lines.append(f"  actual {total:.4f} USD ({mode} rows; no live estimate applies)")
    counts = r4_match(_lookups(run_id), config.PAGES_DIR)
    if counts["chunks"]:
        rate = 100.0 * counts["found"] / counts["chunks"]
        lines.append(f"  R4 snippet versus raw_content: {counts['found']} of {counts['chunks']} snippet chunks "
                     f"found in the page text ({rate:.1f}%), over {counts['results']} results with saved text")
    else:
        lines.append(f"  R4 snippet versus raw_content: no snippet with saved page text "
                     f"({counts['results']} results)")
    if counts["no_text"]:
        lines.append(f"  R4: {counts['no_text']} search result(s) had a snippet but no saved page text")
    return lines
