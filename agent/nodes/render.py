"""render: write the brief as a self contained HTML page (Phase 4 renderer).

The page is the v2 format of `agent.render.brief_html.render_brief` (PLAN 6.3,
SC10). Besides the brief, the page is given the run's history_hits (so
happened_before shows the service record's date), and for a budget stop the
search trail and stop reason.

Default output is config.BRIEFS_OUT_DIR / f"{run_id}.html" (decision 51), or
the path already in state as html_path. A path inside the published pages is
refused here as well as in the CLI. A budget stop that never reached validate
has no brief, so one is built from the sources retrieved so far and written
to state with the page.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent import config
from agent.render.brief_html import budget_stopped_brief, render_brief
from agent.state import AdvisorState, RunContext

NODE = "render"


def inside_published(path: Path) -> bool:
    """True when path is a published page or lies in a published folder (case folded)."""
    target = [p.casefold() for p in path.expanduser().resolve().parts]
    for name in config.PUBLISHED_PATHS:
        root = [p.casefold() for p in (config.REPO_ROOT / name).resolve().parts]
        if target[: len(root)] == root:
            return True
    return False


def output_path(state: AdvisorState) -> Path:
    given = state.get("html_path")
    path = Path(given) if given else config.BRIEFS_OUT_DIR / f"{state.get('run_id')}.html"
    if inside_published(path):
        raise ValueError(f"refusing to write a brief inside the published pages: {path}")
    return path


def render(state: AdvisorState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Render the validated (or budget stopped) brief and write it to disk."""
    started = time.perf_counter()
    update: dict[str, Any] = {}
    brief = state.get("brief")
    if brief is None:
        if state.get("status") != "budget_stopped":
            raise ValueError(f"render needs a brief; status is {state.get('status')!r}")
        brief = budget_stopped_brief(state.get("sources") or [])
        update["brief"] = brief
    run = {
        "records": state.get("history_hits") or [],
        "search_trail": state.get("search_trail") or [],
        "stop_reason": state.get("stop_reason"),
    }
    page = render_brief(
        brief,
        identity=state.get("identity"),
        symptom=state.get("symptom") or "",
        generated_at=datetime.now(UTC).date().isoformat(),
        run=run,
    )
    path = output_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    update["html_path"] = str(path)
    update["latency"] = {NODE: time.perf_counter() - started}
    return update
