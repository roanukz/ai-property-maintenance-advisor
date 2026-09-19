"""validate node: the only place rules are enforced (PLAN 8.3 to 8.5, decision 27).

Wraps the pure `run_rules`. It writes `brief`, `validation_errors`,
`validation_failures`, `status` and `refusal_origin`, rule 4's `grounding`
report and `grounding_status` (Phase 3), plus its latency entry.

- Rules pass: `brief` is the validated brief, `status` its status, errors
  cleared; `refusal_origin` is "model" when the draft itself refused.
- Rules fail, or synthesize produced no draft (its parsing error counts as a
  validation failure): `validation_failures` goes up by one, `brief` is None,
  `status` stays "running", and the routing after validate decides between
  the retry, `budget_stopped` and the refuse node (section 8.4).
- Status already `budget_stopped` on entry: nothing is validated; the node
  writes the budget stop brief so render has something to show.
- After upgrade_check (Phase 4, decision 27) the node runs once more on the
  draft with the graph derived upgrade options added, as pass kind "upgrade";
  the rules are the same, so those options are cited, verified, pruned and
  renumbered like any other entry. The appliance's maintenance_log is read
  from the registry so rule 9 can write due dates.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from agent.nodes.refuse import build_budget_stopped_brief
from agent.nodes.upgrade_check import upgrade_pass_done
from agent.rules.pipeline import run_rules
from agent.state import AdvisorState, RunContext, latency_entry

NODE = "validate"
NO_DRAFT = "no_draft: synthesize returned nothing to validate"


def page_texts_for(sources: list[dict[str, Any]], ctx: RunContext) -> dict[str, str]:
    """Raw page text by source URL: data/pages/<sha256>.txt, else the cassette's synthetic text.

    A source with no text hash of its own (a replayed live recording keeps
    page text out of the cassette, decision 15) is looked up by the hash the
    recorder wrote for its URL, and read only when the file there still
    hashes to it (D4).
    """
    texts: dict[str, str] = {}
    cassette_texts = _cassette_attr(ctx.cassette, "page_texts") or {}
    recorded = recorded_text_hashes(ctx.cassette)
    for source in sources:
        url = source.get("url")
        sha = source.get("text_sha256")
        if not isinstance(url, str):
            continue
        if sha and ctx.pages_dir is not None:
            path = Path(ctx.pages_dir) / f"{sha}.txt"
            if path.is_file():
                texts[url] = path.read_text(encoding="utf-8")
                continue
        if url in cassette_texts:
            texts[url] = cassette_texts[url]
            continue
        text = _recorded_page(ctx.pages_dir, recorded.get(url))
        if text is not None:
            texts[url] = text
    return texts


def recorded_text_hashes(cassette: Any) -> dict[str, str]:
    """URL to the page text hash a live recording names (first one wins).

    Read from the text_sha256 of its tool results, then, for URLs still
    missing, from the recorder's `<case>.texts.json` beside the cassette file
    (each withheld entry's raw_content_sha256): a recording made before the
    recorder wrote text_sha256 inline still finds its pages (D4).
    """
    data = cassette if isinstance(cassette, dict) else getattr(cassette, "data", None)
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for entry in (data.get("research") or {}).get("tool_results") or []:
        for result in entry.get("results") or []:
            url, sha = result.get("url"), result.get("text_sha256")
            if isinstance(url, str) and isinstance(sha, str) and sha and url not in out:
                out[url] = sha
    for url, sha in _sidecar_hashes(getattr(cassette, "path", None)):
        out.setdefault(url, sha)
    return out


def _sidecar_hashes(path: Any) -> list[tuple[str, str]]:
    """(url, raw_content_sha256) pairs from the `<case>.texts.json` beside a cassette, in file order."""
    if path is None:
        return []
    sidecar = Path(path).with_name(f"{Path(path).stem}.texts.json")
    if not sidecar.is_file():
        return []
    try:
        withheld = json.loads(sidecar.read_text(encoding="utf-8")).get("withheld") or []
    except (ValueError, AttributeError):
        return []
    return [(e["url"], e["raw_content_sha256"]) for e in withheld
            if isinstance(e, dict) and isinstance(e.get("url"), str)
            and isinstance(e.get("raw_content_sha256"), str) and e["raw_content_sha256"]]


def _recorded_page(pages_dir: Path | None, sha: str | None) -> str | None:
    # The hash came from a cassette, not from this run, so the file is checked
    # against it: a stale or edited page must not verify a quote.
    if not sha or pages_dir is None:
        return None
    path = Path(pages_dir) / f"{sha}.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text if hashlib.sha256(text.encode("utf-8")).hexdigest() == sha else None


def _cassette_attr(cassette: Any, name: str) -> Any:
    if cassette is None:
        return None
    if isinstance(cassette, dict):
        return cassette.get(name)
    return getattr(cassette, name, None)


def load_appliance(state: AdvisorState, ctx: RunContext) -> dict[str, Any] | None:
    """The appliance registry row for this run, when there is one to read."""
    appliance_id = state.get("appliance_id")
    if not appliance_id or ctx.registry_path is None or not Path(ctx.registry_path).is_file():
        return None
    from agent.registry import Registry

    return Registry(ctx.registry_path).get_appliance(appliance_id)


def load_maintenance_log(state: AdvisorState, ctx: RunContext) -> list[dict[str, Any]]:
    """The appliance's maintenance_log rows, newest first, or [] with no appliance."""
    appliance_id = state.get("appliance_id")
    if not appliance_id or ctx.registry_path is None or not Path(ctx.registry_path).is_file():
        return []
    from agent.registry import Registry

    return Registry(ctx.registry_path).maintenance_log(appliance_id)


def validate(state: AdvisorState, runtime: Runtime[RunContext]) -> dict:
    """Run the rules over the latest draft and record the outcome."""
    started = time.perf_counter()
    ctx = runtime.context
    out = _validate(state, ctx)
    out.update(latency_entry(state, NODE, time.perf_counter() - started))
    return out


def _failure(state: AdvisorState, errors: list[str]) -> dict:
    return {
        "brief": None,
        "validation_errors": errors,
        "validation_failures": int(state.get("validation_failures") or 0) + 1,
        "status": "running",
        "refusal_origin": None,
    }


def _validate(state: AdvisorState, ctx: RunContext) -> dict:
    if state.get("status") == "budget_stopped":
        return {"brief": build_budget_stopped_brief(state)}
    draft = state.get("draft")
    if not isinstance(draft, dict):
        return _failure(state, list(state.get("validation_errors") or []) or [NO_DRAFT])
    sources = list(state.get("sources") or [])
    result = run_rules(
        draft,
        sources=sources,
        observed_code=state.get("observed_code"),
        history_hits=list(state.get("history_hits") or []),
        registry=load_appliance(state, ctx),
        mode=ctx.mode,
        provenance=_cassette_attr(ctx.cassette, "provenance"),
        pass_kind="upgrade" if upgrade_pass_done(state) else "synthesize",
        identity=state.get("identity") or {},
        search_trail=list(state.get("search_trail") or []),
        page_texts=page_texts_for(sources, ctx),
        maintenance_log=load_maintenance_log(state, ctx),
        recorded_text_urls=set(recorded_text_hashes(ctx.cassette)),
    )
    report = {"grounding": result.grounding, "grounding_status": result.grounding_status}
    if result.errors:
        return {**_failure(state, result.errors), **report}
    return {
        "brief": result.brief,
        "validation_errors": [],
        "status": result.brief["status"],
        "refusal_origin": result.refusal_origin,
        **report,
    }
