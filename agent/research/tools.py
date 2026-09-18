"""The research agent's search and fetch tools (PLAN 8.7).

The same wrappers run in every mode. Only the inner client changes: live
TavilySearch and TavilyExtract, or the replay stubs in agent/replay/tool_stubs.py,
which return, raise or give back exactly what a cassette recorded. So failure
mapping, the Tavily plan limit stop, the ledger's credit holds and the
collector writes run under replay the same way they run live.

Failure shapes (the model gets an error ToolMessage with no artifact):
the worker timeout, an {"error": ...} response, a bare string response (the
live Tavily tools catch their own ToolException and return its text), and
empty results. "Error 432" or "Error 433" in an error, and a credit or dollar
cap refused by the ledger, raise BudgetExceeded instead, which is not a
ToolException, so it ends the run as budget_stopped.

Phase 2 additions: every page text is written once to
<pages_dir>/<sha256>.txt, every tool call (success or failure) is appended to
the run's lookup log in the data dir as it happens, and the model sees verbatim
excerpts of a fetched page (decision 24), never the whole page.
"""

from __future__ import annotations

import functools
import hashlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, Field, PrivateAttr

from agent import config
from agent.ledger import BudgetExceeded, Ledger, Reservation
from agent.redaction import redact, scrub_exception
from agent.research.excerpts import Terms, cut_excerpts, render_excerpts
from agent.state import RunContext

NODE = "research"


class ToolInner(Protocol):
    """What a wrapper calls: a live Tavily tool or a replay stub."""

    def invoke(self, input: dict[str, Any]) -> Any: ...


def call_with_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    """Run fn on a daemon thread; raise TimeoutError if it outlives timeout_s.

    A daemon thread is abandoned, not joined, so a hung request cannot keep
    the run (or the interpreter) waiting (decision 38).
    """
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # handed back to the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"no response within {timeout_s} seconds")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _cap_overrides(ctx: RunContext) -> dict[str, Any]:
    caps = ctx.caps or {}
    return {"run_cap_usd": caps.get("run_cap_usd"), "run_credit_cap": caps.get("run_credit_cap")}


def _credits_used(response: dict[str, Any], reserved: int) -> int:
    """Credits Tavily reported (include_usage is pinned on), else the reservation."""
    used = (response.get("usage") or {}).get("credits")
    return used if isinstance(used, int) and not isinstance(used, bool) else reserved


def _is_plan_limit(message: str) -> bool:
    return any(marker in message for marker in config.TAVILY_PLAN_LIMIT_MARKERS)


# The credits a failed call still spent ride on its exception, so the lookup
# log can record this call's own charge. Reading the run's ledger total before
# and after is wrong when searches run in parallel (Phase 5 finding).
SPENT_CREDITS_ATTR = "tavily_credits_spent"


def _spent(exc: BaseException, credits: int) -> BaseException:
    setattr(exc, SPENT_CREDITS_ATTR, credits)
    return exc


def guarded_call(
    ctx: RunContext, tool: str, inner: ToolInner, payload: dict[str, Any], *,
    credits: int, timeout_s: float,
) -> tuple[dict[str, Any], int]:
    """Reserve credits, call the inner client, settle the credits, map failures.

    Returns the response dict and the credits charged. Raises ToolException for
    an ordinary failure and BudgetExceeded for a cap or a Tavily plan limit.
    Every error text is redacted of the run's key values first: a
    ToolException's text goes back to the research model as a ToolMessage.
    """
    ledger = Ledger(ctx.ledger_path)
    ids = {"mode": ctx.mode, "node": NODE}
    hold: Reservation = ledger.reserve_credits(ctx.run_id, credits=credits, **ids, **_cap_overrides(ctx))
    try:
        response = call_with_timeout(lambda: inner.invoke(payload), timeout_s)
    except TimeoutError as exc:
        ledger.charge_credits(ctx.run_id, credits=config.TAVILY_TIMEOUT_CREDITS, reservation=hold,
                              note=f"{tool} timed out", **ids)
        raise _spent(ToolException(f"{tool} timed out: {exc}"), config.TAVILY_TIMEOUT_CREDITS) from exc
    except BaseException as exc:
        ledger.release(hold)
        clean = scrub_exception(exc, ctx.secrets)
        if clean is exc:
            raise
        raise clean from None
    if isinstance(response, dict) and "error" in response:
        message = redact(str(response["error"]), ctx.secrets)
        ledger.release(hold)
        if _is_plan_limit(message):
            ledger.stop(ctx.run_id, reason="tavily_plan_limit", **ids)
            raise BudgetExceeded("tavily_plan_limit", message)
        raise ToolException(message)
    if not isinstance(response, dict):
        # The request reached Tavily (it answered), so the credits are spent.
        ledger.charge_credits(ctx.run_id, credits=credits, reservation=hold,
                              note=f"{tool} returned text", **ids)
        raise _spent(ToolException(redact(str(response), ctx.secrets)), credits)
    used = _credits_used(response, credits)
    ledger.charge_credits(ctx.run_id, credits=used, reservation=hold, note=tool, **ids)
    return response, used


# ---------------------------------------------------------------------------
# What the model sees
# ---------------------------------------------------------------------------


def _search_result(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": raw["url"],
        "title": raw.get("title", ""),
        "content": raw.get("content", ""),
        "raw_content": raw.get("raw_content"),
        "score": raw.get("score"),
    }


def search_text(results: list[dict[str, Any]]) -> str:
    """Compact numbered text for the model: title, URL, snippet per result."""
    blocks = []
    for i, r in enumerate(results, start=1):
        block = f"[{i}] {r['title']}\n{r['url']}\n{r['content']}"
        blocks.append(block[: config.SEARCH_RESULT_MAX_CHARS])
    return "\n\n".join(blocks)


def fetch_text(url: str, results: list[dict[str, Any]], terms: Terms | None = None) -> str:
    """The URL, then verbatim excerpts of the page, within FETCH_EXCERPT_MAX_CHARS in all."""
    head = f"{url}\n"
    budget = config.FETCH_EXCERPT_MAX_CHARS - len(head)
    body = render_excerpts(cut_excerpts(results[0].get("raw_content"), terms or Terms(), budget))
    return (head + body)[: config.FETCH_EXCERPT_MAX_CHARS]


# ---------------------------------------------------------------------------
# Page text and the lookup log (runtime data, gitignored)
# ---------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()


def page_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_page_text(pages_dir: Path, text: str) -> str:
    """Write a page's raw text once, named by its sha256, and return the hash."""
    sha = page_sha256(text)
    path = Path(pages_dir) / f"{sha}.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return sha


def load_page_text(pages_dir: Path, sha: str | None) -> str | None:
    """The raw text saved under this hash, or None when it is not on disk."""
    if not sha:
        return None
    path = Path(pages_dir) / f"{sha}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None


def lookup_log_path(ctx: RunContext) -> Path:
    """<data dir>/lookups/<run_id>.jsonl, beside the pages folder."""
    return Path(ctx.pages_dir).parent / config.LOOKUPS_DIR.name / f"{ctx.run_id}.jsonl"


def _append_lookup(ctx: RunContext, record: dict[str, Any]) -> None:
    path = lookup_log_path(ctx)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with _LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _failure_status(exc: BaseException) -> str:
    if isinstance(exc, BudgetExceeded):
        return exc.reason
    if isinstance(exc.__cause__, TimeoutError):
        return "timeout"
    if str(exc).startswith(("No search results for ", "No text extracted from ")):
        return "empty"
    return "error"


def _logged(ctx: RunContext, tool: str, func: Callable[..., tuple[str, dict[str, Any]]],
            trail: list[dict[str, Any]] | None) -> Callable[..., tuple[str, dict[str, Any]]]:
    """Record every call of a tool: page files, the lookup log, and the in memory trail.

    Credits are this call's own charge: the artifact's on success, and on a
    failure the credits its exception says were still spent (0 when the hold
    was released). The ledger stays the record of truth for totals.
    """

    @functools.wraps(func)
    def run(**kwargs: str) -> tuple[str, dict[str, Any]]:
        (arg,) = kwargs.values()  # query for search, url for fetch
        entry: dict[str, Any] = {"tool": tool, "query": arg, "n_results": 0, "credits": 0,
                                 "at": _now(), "status": "ok", "artifact": None, "pages": {}}
        try:
            content, artifact = func(**kwargs)
        except BaseException as exc:
            entry["status"] = _failure_status(exc)
            entry["error"] = redact(str(exc), ctx.secrets)
            entry["credits"] = int(getattr(exc, SPENT_CREDITS_ATTR, 0) or 0)
            raise
        else:
            entry["credits"] = int(artifact.get("credits") or 0)
            entry["artifact"] = artifact
            entry["n_results"] = len(artifact["results"])
            for result in artifact["results"]:
                if result.get("raw_content"):
                    entry["pages"][result["url"]] = save_page_text(ctx.pages_dir, result["raw_content"])
            return content, artifact
        finally:
            if trail is not None:
                trail.append(entry)
            _append_lookup(ctx, _log_record(ctx, entry))

    return run


def _log_record(ctx: RunContext, entry: dict[str, Any]) -> dict[str, Any]:
    """A lookup log line: the call and its results, with page text by hash only."""
    record = {k: v for k, v in entry.items() if k not in ("artifact", "pages")}
    record["run_id"] = ctx.run_id
    artifact = entry.get("artifact")
    if artifact is not None:
        record["results"] = [
            {"url": r.get("url"), "title": r.get("title"), "content": r.get("content"),
             "score": r.get("score"), "text_sha256": entry["pages"].get(r.get("url"))}
            for r in artifact["results"]
        ]
        if artifact.get("failed_results"):
            record["failed_results"] = artifact["failed_results"]
    return record


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


class _SearchArgs(BaseModel):
    query: str = Field(description="What to search for.")


class _FetchArgs(BaseModel):
    url: str = Field(description="The page URL to fetch.")


class ResearchTool(StructuredTool):
    """A wrapper tool that keeps a handle on its inner client."""

    _inner: Any = PrivateAttr()

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def calls(self) -> int | None:
        """How often the inner client was called (replay stubs count; live ones do not)."""
        return getattr(self._inner, "calls", None)


def _build(name: str, func: Callable[..., Any], args: type[BaseModel], inner: Any) -> ResearchTool:
    tool = ResearchTool(
        name=name,
        description=func.__doc__,
        func=func,
        args_schema=args,
        response_format="content_and_artifact",
        handle_tool_error=True,
    )
    tool._inner = inner
    return tool


def make_research_tools(
    ctx: RunContext, *, search_inner: ToolInner, fetch_inner: ToolInner,
    timeout_s: float = config.TAVILY_TIMEOUT_S, terms: Terms | None = None,
    trail: list[dict[str, Any]] | None = None,
) -> list[BaseTool]:
    """The search and fetch tools for one run, around the given inner clients.

    `terms` anchors the excerpts a fetch shows the model. Each call, success
    or failure, is appended to `trail` when one is given, so the research node
    can build the search trail even if the agent raises.
    """

    def search(query: str) -> tuple[str, dict[str, Any]]:
        """Search the web for service information about an appliance."""
        response, used = guarded_call(ctx, "search", search_inner, {"query": query},
                                      credits=config.TAVILY_CREDITS["search_basic"], timeout_s=timeout_s)
        results = [_search_result(r) for r in response.get("results") or []]
        if not results:
            raise _spent(ToolException(f"No search results for {query!r}."), used)
        artifact = {"tool": "search", "query": query, "results": results, "credits": used}
        ctx.collector.append(artifact)
        return search_text(results), artifact

    def fetch(url: str) -> tuple[str, dict[str, Any]]:
        """Fetch the text of one web page by URL."""
        response, used = guarded_call(ctx, "fetch", fetch_inner, {"urls": [url]},
                                      credits=config.TAVILY_CREDITS["extract_basic_per_5_urls"],
                                      timeout_s=timeout_s)
        results = [
            {"url": r.get("url", url), "raw_content": r.get("raw_content") or ""}
            for r in response.get("results") or []
        ]
        failed = list(response.get("failed_results") or [])
        if not results:
            raise _spent(ToolException(f"No text extracted from {url}."), used)
        artifact = {"tool": "fetch", "url": url, "results": results, "failed_results": failed,
                    "credits": used}
        ctx.collector.append(artifact)
        return fetch_text(url, results, terms), artifact

    return [
        _build("search", _logged(ctx, "search", search, trail), _SearchArgs, search_inner),
        _build("fetch", _logged(ctx, "fetch", fetch, trail), _FetchArgs, fetch_inner),
    ]
