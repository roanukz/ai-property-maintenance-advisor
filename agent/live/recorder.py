"""Turn a finished live run into a cassette candidate (PLAN 8.10, Phase 6 "new cassettes").

While a live ask or resume runs, `capturing` attaches a callback handler to
every model call in the process (a langchain_core configure hook on a context
variable, so the graph and nodes are the ones replay uses, unchanged). For
each call it appends the node, the reply text, the tool calls and the usage
to `<recordings>/<run_id>.capture.jsonl`. It never stores the request, so the
photo bytes, prompts and headers are never read, and each line is redacted
of the loaded key values before it is written.

When the run reaches END, `write_candidate` builds a version 1 cassette from
that capture, the run's lookup log and its page files:

- provenance.derived_from is "recorded live <date> run <run_id>";
- input: the symptom, and the plate by sha256 only, or the typed identity;
- read_plate, classifier, research.script and synthesize: the recorded
  replies and usage, in call order;
- research.tool_results: each Tavily call from the lookup log, success or
  failure, in the cassette shapes (a result, "error", "string", "raise");
- page text: decision 15 lets a cassette carry text only on example hosts,
  so for a real URL the snippet and page text are left out of the cassette
  and listed by sha256 (under config.PAGES_DIR) in `<case>.texts.json`;
  every result whose page text was saved keeps its `text_sha256`, so a
  replay can read that text from the local pages folder when it is there;
- URLs lose tracking and session parameters (the privacy diff's own rule).

Then it scans every string, key and long number with the privacy diff's
patterns and the local denylist, and writes `<case>.privacy.json`. A key
pattern or auth header hit withholds the cassette entirely. The candidate is
checked with load_cassette. Beside it goes `copy_logs/<case>.copies.json` in
the privacy diff's own format: every string copied from the run (the
capture, the lookup log or the run's state) with the source it came from,
and what was read to hash only, so the staged pair passes the PLAN 8.10 diff
(which blocks a staged cassette with no copy log). Nothing here writes under
agent/tests: a candidate is tracked only after Roanuk approves that diff.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from agent import config
from agent.live.env import redact, redact_value
from agent.models import NODES, max_tokens_for
from agent.replay import privacy_diff
from agent.replay.cassettes import (
    CASSETTE_VERSION,
    IDENTITY_FIELDS,
    CassetteError,
    is_example_url,
    load_cassette,
)
from agent.research.tools import load_page_text, save_page_text

BUILT_BY = "agent/live/recorder.py"
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "input_token_details", "output_token_details")
UNPARSED_KEY = "unparsed_reply"
# Lookup log statuses for calls the ledger refused before Tavily was called.
NOT_SENT_STATUSES = ("run_cap", "build_cap", "run_credit_cap", "build_credit_cap")
KEY_HIT_KINDS = (*privacy_diff.KEY_PREFIXES, "auth header name")
REDACTED_MATCH = "[redacted]"
VERDICT_WITHHELD = "WITHHELD: a key pattern or auth header was found; no cassette was written"

_CAPTURE: ContextVar[BaseCallbackHandler | None] = ContextVar("advisor_live_capture", default=None)
_HOOK_LOCK = threading.Lock()
_HOOK_REGISTERED = False


def capture_path(run_id: str) -> Path:
    return config.RECORDINGS_DIR / f"{run_id}.capture.jsonl"


def case_name(run_id: str) -> str:
    return "live_" + re.sub(r"[^a-z0-9]+", "_", run_id.lower()).strip("_")


def _text(content: Any) -> str:
    """The text of a reply: a string, or the text blocks of a content list joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Usage in the cassette shape: token counts and their detail dicts only."""
    out: dict[str, Any] = {}
    for key in USAGE_KEYS:
        value = (usage or {}).get(key)
        if key.endswith("_details"):
            if isinstance(value, Mapping):
                out[key] = {k: int(v) for k, v in value.items() if isinstance(v, int) and not isinstance(v, bool)}
        elif isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    out.setdefault("input_tokens", 0)
    out.setdefault("output_tokens", 0)
    return out


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


class CaptureHandler(BaseCallbackHandler):
    """Appends every model reply of a live run to its capture file; never the request."""

    def __init__(self, path: Path, mode: str, secrets: Sequence[str] = ()) -> None:
        super().__init__()
        self.path = Path(path)
        self.secrets = list(secrets)
        self.node_by_max_tokens = {max_tokens_for(node, mode): node for node in NODES}
        self._nodes: dict[str, str] = {}
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = redact(json.dumps(record, ensure_ascii=False, sort_keys=True), self.secrets)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        max_tokens = params.get("max_tokens") or (kwargs.get("metadata") or {}).get("ls_max_tokens")
        self._nodes[str(run_id)] = self.node_by_max_tokens.get(max_tokens, "unknown")

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        node = self._nodes.pop(str(run_id), "unknown")
        message = getattr(response.generations[0][0], "message", None)
        if message is None:
            return
        calls = [{"name": c["name"], "args": dict(c.get("args") or {}), "id": c.get("id")}
                 for c in getattr(message, "tool_calls", None) or []]
        self.append({"event": "model", "node": node, "content": _text(message.content),
                     "tool_calls": calls, "usage": _usage(getattr(message, "usage_metadata", None))})


def _register_hook() -> None:
    global _HOOK_REGISTERED
    with _HOOK_LOCK:
        if not _HOOK_REGISTERED:
            register_configure_hook(_CAPTURE, True)
            _HOOK_REGISTERED = True


@contextlib.contextmanager
def capturing(run_id: str, mode: str, segment: str, secrets: Sequence[str] = ()) -> Iterator[CaptureHandler]:
    """Record every model reply made in this block to the run's capture file."""
    _register_hook()
    handler = CaptureHandler(capture_path(run_id), mode, secrets)
    handler.append({"event": "segment", "segment": segment})
    token = _CAPTURE.set(handler)
    try:
        yield handler
    finally:
        _CAPTURE.reset(token)


# ---------------------------------------------------------------------------
# Building the cassette
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def raw_reply(run_id: str, node: str) -> dict[str, Any] | None:
    """The first captured reply of one node, parsed: read_plate's extraction before code nulls fields."""
    for event in _read_jsonl(capture_path(run_id)):
        if event.get("event") == "model" and event.get("node") == node:
            value = _structured(str(event.get("content") or ""))
            return None if UNPARSED_KEY in value else value
    return None


def _structured(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        return {UNPARSED_KEY: text}
    return value if isinstance(value, dict) else {UNPARSED_KEY: text}


@dataclass
class _Build:
    cassette: dict[str, Any]
    withheld: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # (cassette path prefix, where its strings came from), longest prefix wins.
    origins: list[tuple[str, str]] = field(default_factory=list)


def _search_result(result: Mapping[str, Any], pages_dir: Path, path: str, build: _Build) -> dict[str, Any]:
    url = str(result.get("url") or "")
    content = result.get("content") or ""
    raw = load_page_text(pages_dir, result.get("text_sha256"))
    out = {"url": url, "title": result.get("title") or "", "content": content, "raw_content": raw,
           "score": result.get("score"), **_text_hash(result)}
    if not is_example_url(url) and (content or raw):
        build.withheld.append({
            "path": path, "url": url,
            "content_sha256": save_page_text(pages_dir, content) if content else None,
            "raw_content_sha256": result.get("text_sha256") if raw else None,
        })
        out["content"], out["raw_content"] = "", None
    return out


def _fetch_result(result: Mapping[str, Any], pages_dir: Path, path: str, build: _Build) -> dict[str, Any]:
    url = str(result.get("url") or "")
    raw = load_page_text(pages_dir, result.get("text_sha256"))
    if raw and not is_example_url(url):
        build.withheld.append({"path": path, "url": url, "content_sha256": None,
                               "raw_content_sha256": result.get("text_sha256")})
        raw = None
    return {"url": url, "raw_content": raw, **_text_hash(result)}


def _text_hash(result: Mapping[str, Any]) -> dict[str, str]:
    """{"text_sha256": hash} for a result whose page text the run saved, else nothing.

    Replay reads the text back from the local pages folder by this hash (D4),
    so a real page's text can be checked offline without entering the cassette.
    """
    sha = result.get("text_sha256")
    return {"text_sha256": sha} if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) else {}


def in_call_order(lookups: Sequence[Mapping[str, Any]],
                  script: Sequence[Mapping[str, Any]]) -> list[tuple[int, Mapping[str, Any]]]:
    """Lookup log entries, each with its log line, in the order the model issued the calls.

    The log is written as calls finish, and calls issued together run in
    parallel, so it can hold them out of order (Phase 5 finding). Each scripted
    call takes the first unused entry for the same tool and query or URL;
    entries no call claims keep their log order after the rest.
    """
    numbered = list(enumerate(lookups, start=1))
    used: set[int] = set()
    ordered: list[tuple[int, Mapping[str, Any]]] = []
    for response in script:
        for call in response["message"]["tool_calls"]:
            arg = call.get("args", {}).get("query" if call["name"] == "search" else "url")
            for i, (line, entry) in enumerate(numbered):
                if i not in used and entry.get("tool") == call["name"] and str(entry.get("query") or "") == str(arg):
                    used.add(i)
                    ordered.append((line, entry))
                    break
    ordered += [pair for i, pair in enumerate(numbered) if i not in used]
    return ordered


def tool_results(lookups: Sequence[Mapping[str, Any]], pages_dir: Path, build: _Build,
                 script: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """The cassette's research.tool_results from the run's lookup log, in call order."""
    out: list[dict[str, Any]] = []
    for line, entry in in_call_order(lookups, script):
        tool = entry.get("tool")
        if tool not in ("search", "fetch"):
            continue
        arg = "query" if tool == "search" else "url"
        status = entry.get("status")
        base = {"tool": tool, arg: str(entry.get("query") or "")}
        if status in NOT_SENT_STATUSES:
            build.notes.append(f"{tool} {base[arg]!r} was refused by the ledger ({status}) and never sent")
            continue
        build.origins.append((f"research.tool_results[{len(out)}]", f"lookup log line {line}"))
        if status == "timeout":
            out.append({**base, "raise": "timeout"})
        elif status == "empty":
            out.append({**base, "results": [], "credits": int(entry.get("credits") or 0)})
        elif status in ("error", "tavily_plan_limit"):
            shape = "string" if status == "error" and int(entry.get("credits") or 0) > 0 else "error"
            out.append({**base, shape: str(entry.get("error") or "")})
        else:
            index = len(out)
            results = [
                (_search_result if tool == "search" else _fetch_result)(
                    r, pages_dir, f"research.tool_results[{index}].results[{i}]", build)
                for i, r in enumerate(entry.get("results") or [])
            ]
            record = {**base, "results": results, "credits": int(entry.get("credits") or 0)}
            if tool == "fetch":
                record["failed_results"] = list(entry.get("failed_results") or [])
            out.append(record)
    return out


def blocked_calls(script: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]],
                  limits_key: str | None) -> dict[str, int]:
    """Scripted tool calls the run's tool cap blocked before they reached Tavily.

    ToolCallLimitMiddleware answers a call over its cap with an error message
    and never runs the tool, so the call is in the model script but not in the
    lookup log. A replay under the same caps blocks it again before the stub.
    Counted only when the sent calls reached the cap exactly; any other gap is
    left for the cassette loader to reject.
    """
    limits = config.RESEARCH_LIMITS.get(limits_key or "", {})
    out: dict[str, int] = {}
    for tool in ("search", "fetch"):
        scripted = sum(1 for response in script for call in response["message"]["tool_calls"]
                       if call["name"] == tool)
        sent = sum(1 for r in results if r["tool"] == tool)
        if scripted > sent and tool in limits and sent == limits[tool]:
            out[tool] = scripted - sent
    return out


def _identity(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {f: (value.get(f) if isinstance(value.get(f), str) else None) for f in IDENTITY_FIELDS}


def build_cassette(run_id: str, state: Mapping[str, Any], *, capture: Sequence[Mapping[str, Any]],
                   lookups: Sequence[Mapping[str, Any]], pages_dir: Path, recorded_on: str) -> _Build:
    """A version 1 cassette dict for one finished live run."""
    models = [e for e in capture if e.get("event") == "model"]
    resumed = any(e.get("event") == "segment" and e.get("segment") == "resume" for e in capture)
    by_node: dict[str, list[Mapping[str, Any]]] = {}
    for event in models:
        by_node.setdefault(str(event.get("node")), []).append(event)
    build = _Build(cassette={})
    unknown = by_node.pop("unknown", [])
    if unknown:
        build.notes.append(f"{len(unknown)} model reply(ies) could not be matched to a node and were left out")

    plate = by_node.get("read_plate", [])
    photo = state.get("photo") or None
    research_script = [
        {"message": {"content": str(e.get("content") or ""),
                     "tool_calls": [{"name": c["name"], "args": c["args"], **({"id": c["id"]} if c.get("id") else {})}
                                    for c in e.get("tool_calls") or []],
                     "usage": _usage(e.get("usage"))}}
        for e in by_node.get("research", [])
    ]
    trail = list(state.get("search_trail") or [])
    results = tool_results(lookups, pages_dir, build, research_script)
    research_section: dict[str, Any] = {"script": research_script, "tool_results": results}
    blocked = blocked_calls(research_script, results, state.get("research_limits"))
    if blocked:
        research_section["blocked_calls"] = blocked
        build.notes.append(f"tool calls blocked by the run's caps before reaching Tavily: {blocked}")
    build.cassette = {
        "cassette_version": CASSETTE_VERSION,
        "case": case_name(run_id),
        "provenance": {
            "derived_from": f"recorded live {recorded_on} run {run_id}",
            "copied_fields": ["input", "read_plate", "classifier", "resume", "research.script",
                              "research.tool_results", "synthesize"],
            "synthetic_fields": [],
            "built_by": BUILT_BY,
            "reviewed": None,
        },
        "input": {
            "identity": None if photo else _identity(state.get("identity")),
            "symptom": str(state.get("symptom") or ""),
            "plate_sha256": photo.get("sha256") if isinstance(photo, Mapping) else None,
        },
        "read_plate": ({"extraction": _structured(str(plate[0].get("content") or "")),
                        "usage": _usage(plate[0].get("usage"))} if plate else None),
        "classifier": [{"structured": _structured(str(e.get("content") or "")), "usage": _usage(e.get("usage"))}
                       for e in by_node.get("classifier", [])],
        "resume": ({"identity": _identity(state.get("identity")), "observed_code": state.get("observed_code")}
                   if resumed else None),
        "research": research_section,
        "synthesize": [{"attempt": i + 1, "draft": _structured(str(e.get("content") or "")),
                        "usage": _usage(e.get("usage"))} for i, e in enumerate(by_node.get("synthesize", []))],
        "page_texts": {},
        "caps": None,
        "expect": {
            "recorded_from": "live",
            "status": state.get("status"),
            "refusal_origin": state.get("refusal_origin"),
            "stop_reason": state.get("stop_reason"),
            "route": list(state.get("route") or []),
            "searches": sum(1 for e in trail if e.get("tool") == "search" and e.get("status") != "blocked"),
            "fetches": sum(1 for e in trail if e.get("tool") == "fetch" and e.get("status") != "blocked"),
        },
    }
    if len(plate) > 1:
        build.notes.append(f"{len(plate)} read_plate replies; only the first is kept")
    index = {id(e): i for i, e in enumerate(capture)}
    capture_name = capture_path(run_id).name
    if plate:
        build.origins.append(("read_plate", f"{capture_name} line {index[id(plate[0])] + 1} (read_plate reply)"))
    for node, prefix, key in (("classifier", "classifier", ""), ("research", "research.script", ".message"),
                              ("synthesize", "synthesize", "")):
        for i, event in enumerate(by_node.get(node, [])):
            build.origins.append((f"{prefix}[{i}]{key}",
                                  f"{capture_name} line {index[id(event)] + 1} ({node} reply {i + 1})"))
    build.origins += [
        ("input.symptom", "the run's state: symptom (typed by the owner)"),
        ("input.identity", "the run's state: identity"),
        ("resume", "the run's state: the owner's confirmation"),
        ("expect.status", "the run's state: status"),
        ("expect.refusal_origin", "the run's state: refusal_origin"),
        ("expect.stop_reason", "the run's state: stop_reason"),
        ("expect.route", "the run's state: route"),
    ]
    return build


def _source_of(path: str, origins: Sequence[tuple[str, str]]) -> str | None:
    """Where the string at this cassette path came from, or None for text the recorder wrote."""
    best: tuple[str, str] | None = None
    for prefix, source in origins:
        if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, source)
    if best is None:
        return None
    rest = path[len(best[0]):].lstrip(".")
    return f"{best[1]}{': ' + rest if rest else ''}"


def copy_log(cassette: Mapping[str, Any], build: _Build) -> dict[str, Any]:
    """The privacy diff's copy log for the candidate: each copied string with its source, and the hashes."""
    copies = []
    for path, value in privacy_diff.iter_strings(cassette):
        source = _source_of(path, build.origins)
        if source is not None:
            copies.append({"dest": path, "source": source, "value": value})
    hashed = []
    plate = (cassette.get("input") or {}).get("plate_sha256")
    if plate:
        demo = {hashlib.sha256(p.read_bytes()).hexdigest(): p.name
                for p in sorted(privacy_diff.DEMO_ASSETS_DIR.glob("*")) if p.is_file()}
        hashed.append({"source": "the run's plate photo (read to hash only, never copied)", "sha256": plate,
                       "matches": f"demo-assets/{demo[plate]}" if plate in demo else "no demo asset"})
    for entry in build.withheld:
        for key in ("content_sha256", "raw_content_sha256"):
            if entry.get(key):
                hashed.append({"source": f"{key.split('_sha256')[0]} of {entry['url']} (left out, decision 15)",
                               "sha256": entry[key], "matches": "the local pages folder"})
    return {"file": f"{privacy_diff.CASSETTES_SUBDIR}/{cassette['case']}.json", "copies": copies, "hashed": hashed}


def _strip_urls(value: Any) -> Any:
    """Tracking and session parameters removed from every string that is a whole URL."""
    if isinstance(value, str):
        return privacy_diff.strip_tracking(value)[0] if privacy_diff.URL_RE.fullmatch(value) else value
    if isinstance(value, list):
        return [_strip_urls(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_urls(v) for k, v in value.items()}
    return value


def scan(data: Any, denylist: Sequence[str]) -> list[dict[str, str]]:
    """Privacy diff hits for every string, key and long number in data."""
    hits = [{"path": p, "kind": kind, "match": match}
            for p, text in privacy_diff.iter_strings(data)
            for kind, match in privacy_diff.scan_string(text, list(denylist))]
    return hits + privacy_diff.scan_keys_and_numbers(data, list(denylist))


def _denylist() -> list[str]:
    path = config.STAGING_DIR / privacy_diff.DENYLIST_FILE
    return privacy_diff.load_denylist(path) if path.is_file() else []


@dataclass
class RecordingResult:
    """Where the candidate went and what the scans found."""

    path: Path | None
    report_path: Path
    verdict: str
    copy_log_path: Path | None
    loadable: bool
    load_error: str | None
    hits: int
    withheld_texts: int
    notes: list[str]


def next_step(case: str) -> str:
    """How the candidate reaches the PLAN 8.10 privacy diff: both files, in the staging layout."""
    staging = config.STAGING_DIR
    return (f"copy {case}.json to {staging / privacy_diff.CASSETTES_SUBDIR}/ and "
            f"{privacy_diff.COPY_LOG_SUBDIR}/{case}.copies.json to {staging / privacy_diff.COPY_LOG_SUBDIR}/, "
            "then run python -m agent.replay.privacy_diff; nothing is tracked before Roanuk approves it")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_candidate(run_id: str, state: Mapping[str, Any], *, lookup_log: Path, pages_dir: Path,
                    recorded_on: str, secrets: Sequence[str] = ()) -> RecordingResult:
    """Build, redact, scan and write the cassette candidate for a finished live run."""
    build = build_cassette(run_id, state, capture=_read_jsonl(capture_path(run_id)),
                           lookups=_read_jsonl(lookup_log), pages_dir=pages_dir, recorded_on=recorded_on)
    cassette = _strip_urls(redact_value(build.cassette, secrets))
    denylist = _denylist()
    # The run ID is random hex, not private; a run of 5 digits in it is not a ZIP code.
    hits = scan(redact_value(cassette, [cassette["case"], run_id]), denylist)
    key_hits = [h for h in hits if h["kind"] in KEY_HIT_KINDS]
    for hit in key_hits:
        hit["match"] = REDACTED_MATCH  # not even a prefix of a key goes into the report
    case = cassette["case"]
    out_dir = config.RECORDINGS_DIR
    path: Path | None = out_dir / f"{case}.json"
    report_path = out_dir / f"{case}.privacy.json"
    loadable, load_error = False, None
    log_path: Path | None = out_dir / privacy_diff.COPY_LOG_SUBDIR / f"{case}.copies.json"
    if key_hits:
        verdict, path, log_path = VERDICT_WITHHELD, None, None
    else:
        verdict = privacy_diff.verdict(len(hits), len(denylist))
        _write_json(path, cassette)
        _write_json(log_path, copy_log(cassette, build))
        _write_json(out_dir / f"{case}.texts.json", {"pages_dir": str(pages_dir), "withheld": _strip_urls(build.withheld)})
        try:
            load_cassette(path)
            loadable = True
        except CassetteError as exc:
            load_error = str(exc)
    _write_json(report_path, redact_value({
        "run_id": run_id, "cassette": str(path) if path else None, "verdict": verdict,
        "denylist_entries": len(denylist), "hits": hits, "loadable": loadable, "load_error": load_error,
        "withheld_texts": len(build.withheld), "notes": build.notes,
        "copy_log": str(log_path) if log_path else None,
        "next_step": next_step(case),
    }, secrets))
    return RecordingResult(path=path, report_path=report_path, verdict=verdict, copy_log_path=log_path,
                           loadable=loadable,
                           load_error=load_error, hits=len(hits), withheld_texts=len(build.withheld),
                           notes=build.notes)
