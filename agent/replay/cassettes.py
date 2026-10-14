"""Replay cassettes: load, validate strictly, and serve (PLAN section 8.10).

A cassette is one JSON file per case. It holds the run's input, the scripted
model responses for each paid node, the recorded tool results, the expected
outcome, and a provenance block that says which parts were copied from a v1
lookup and which are synthetic. The loader refuses anything malformed with a
message that names the file and the JSON path, so a broken cassette fails at
load time instead of halfway through a replay.

Format (cassette_version 1):

    {
      "cassette_version": 1,
      "case": "flo",
      "provenance": {"derived_from", "copied_fields", "synthetic_fields",
                     "built_by", "reviewed", "public_already" (optional)},
      "input": {"identity": {...} | null, "symptom": str, "plate_sha256": hex | null},
      "read_plate": null | {"extraction": {...}, "usage": {...}},
      "classifier": [response, ...]                       (optional),
      "resume": null | {"identity": {...}, "observed_code": str | null},
      "research": {"script": [response, ...], "tool_results": [result, ...]},
      "synthesize": [{"attempt": 1, "draft": {...}, "usage": {...}}, ...],
      "page_texts": {example_url: text},
      "caps": null | {"run_cap_usd": number, ...},
      "expect": {...},
      "safety_check": [answer, ...]                      (optional)
    }

A response is in the ReplayChatModel format: {"message": {"content",
"tool_calls", "usage"}} or {"structured": {...}, "usage": {...}}. A tool
result is {"tool": "search", "query", "results"} or {"tool": "fetch", "url",
"results", "failed_results"}, either with optional "credits" (and each result
with an optional "text_sha256", the hash of its page text under the pages
folder, written by agent/live/recorder.py); or one of the
failure shapes the wrappers must handle (PLAN 8.7): {"tool", "error": "..."},
{"tool", "string": "..."} (a bare string return) or {"tool", "raise":
"timeout"}. Each failure shape may also name its query or url.

A safety_check answer is one recorded Jev call (agent/safety_judge.py):
{"step_sha256", "question_hash", "model", "noul", "input_tokens",
"output_tokens", "request_id", "error"}. It names the step by hash only and
holds no step or page text; exactly one of noul and error is set. A cassette
without the key replays with the check not recorded, which is not a failure.

Claude Haiku 5.5 recordings (decision 69) add four optional shapes; a cassette
recorded on Haiku 4.5 uses none of them and loads and replays as before:

- Content blocks. A reply the API returned as a list of blocks keeps that
  list as message.content: {"type": "text", "text"}, {"type": "tool_use",
  "id", "name", "input"} (which must repeat the message's tool_calls, in
  order, with their ids) and the thinking markers below. A reply that was one
  string stays a string.
- Thinking markers. A thinking block is kept as {"type": "thinking",
  "thinking": ""} and a redacted one as {"type": "redacted_thinking", "data":
  ""}: where it stood, never what it held. Replay never sends a request, so
  nothing needs the signature or the thinking text, and neither is kept (the
  signature is an opaque encrypted value; summarized thinking may quote a
  page). Their tokens are in the usage, where output_tokens counts them and
  output_token_details.reasoning breaks them out. The one reader that sees
  the missing characters is a replayed research turn's input estimate, which
  counts each block's characters; it comes out lower than the live turn's
  by the signatures' length. A structured reply (classifier, read_plate,
  synthesize) carries its markers in an optional "thinking" list.
- A recorded decline: {"stop_reason": "refusal", "stop_details": null |
  {"type": "refusal", "category": null | token}, "usage"} in place of a
  response (read_plate's object, or a synthesize attempt with its
  "attempt"). It is the last response of its node, and no later node has
  any, since a declined call ends the run. Nothing the model wrote with it is
  kept, and neither is the stop_details explanation.
- "recorded" (optional): {"mode": "cheap" | "full", "models": {node: model
  ID}}, the live mode and the model each node's replies named. Replay serves
  the replies whatever it says, and holds the run to that mode
  (ledger.price_basis): each call is reserved and charged at the card of the
  model its node names, with that mode's output budgets, estimate settings
  and run cap, so a replay spends and stops as the live run did. A cassette
  without it replays on the frozen Haiku 4.5 settings (config.REPLAY_PRICES).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent import config

CASSETTE_VERSION = 1
NODES = ("read_plate", "classifier", "research", "synthesize")
TOOLS = ("search", "fetch")

REQUIRED_KEYS = (
    "cassette_version",
    "case",
    "provenance",
    "input",
    "read_plate",
    "resume",
    "research",
    "synthesize",
    "page_texts",
    "caps",
    "expect",
)
OPTIONAL_KEYS = ("classifier", "safety_check", "recorded")
PROVENANCE_REQUIRED = ("derived_from", "copied_fields", "synthetic_fields", "built_by", "reviewed")
PROVENANCE_OPTIONAL = ("public_already",)
IDENTITY_FIELDS = ("manufacturer", "model", "serial", "manufacture_date")
CAPS_KEYS = ("run_cap_usd", "research_budget_usd", "run_credit_cap")
FAILURE_SHAPES = ("error", "string", "raise")
# A live recording names each result's saved page text by hash (D4); the text
# itself stays in the local pages folder (decision 15).
RESULT_OPTIONAL = ("text_sha256",)
RAISE_KINDS = ("timeout",)

# One recorded Jev answer, and the failure kinds a recording may hold (a
# replay's own "not_recorded" is never recorded).
SAFETY_CHECK_KEYS = (
    "step_sha256", "question_hash", "model", "noul", "input_tokens", "output_tokens", "request_id", "error",
)
SAFETY_CHECK_ERRORS = (
    "reservation_refused", "timeout", "rate_limited", "api_error", "unparseable", "model_mismatch",
    "missing_key", "other",
)

# Claude Haiku 5.5 reply shapes (decision 69): content blocks, thinking
# markers, a recorded decline and the recorded models.
TEXT_BLOCK = "text"
TOOL_USE_BLOCK = "tool_use"
THINKING_MARKERS = {"thinking": {"type": "thinking", "thinking": ""},
                    "redacted_thinking": {"type": "redacted_thinking", "data": ""}}
BLOCK_TYPES = (TEXT_BLOCK, TOOL_USE_BLOCK, *THINKING_MARKERS)
REFUSAL_STOP_REASON = "refusal"
REFUSAL_KEYS = ("stop_reason", "stop_details", "usage")
STOP_DETAILS_KEYS = ("type", "category")
REFUSAL_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RECORDED_KEYS = ("mode", "models")
RECORDED_MODES = config.LIVE_MODES

CASE_RE = re.compile(r"^[a-z0-9_]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUESTION_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
# A model name or request ID is one opaque token: no spaces, so no text.
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
# Synthetic page text may only sit on reserved example hosts (RFC 2606 and
# RFC 6761), never on a real URL (decision 15).
EXAMPLE_HOSTS = ("example.com", "example.org", "example.net")
EXAMPLE_TLDS = (".example", ".test", ".invalid")


class CassetteError(ValueError):
    """A cassette file is malformed. The message names the file and JSON path."""


def is_example_url(url: str) -> bool:
    """True when the URL's host is a reserved example host."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if any(host == h or host.endswith("." + h) for h in EXAMPLE_HOSTS):
        return True
    return host.endswith(EXAMPLE_TLDS)


def registry_urls(tool_results: list[dict[str, Any]]) -> list[str]:
    """Source registry order that code builds from a run's tool results.

    Results are taken in call order, search results then fetched pages as they
    occur, keeping the first appearance of each URL. Draft source indexes in a
    cassette point into this list.
    """
    seen: dict[str, None] = {}
    for entry in tool_results:
        for result in entry.get("results", []):
            url = result.get("url")
            if isinstance(url, str) and url not in seen:
                seen[url] = None
    return list(seen)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class _Checker:
    """Raises CassetteError with the file name and JSON path."""

    def __init__(self, source: str) -> None:
        self.source = source

    def fail(self, path: str, message: str) -> None:
        raise CassetteError(f"{self.source}: {path}: {message}")

    def dict_(self, value: Any, path: str) -> dict:
        if not isinstance(value, dict):
            self.fail(path, f"must be an object, got {type(value).__name__}")
        return value

    def list_(self, value: Any, path: str) -> list:
        if not isinstance(value, list):
            self.fail(path, f"must be a list, got {type(value).__name__}")
        return value

    def str_(self, value: Any, path: str, *, empty_ok: bool = True) -> str:
        if not isinstance(value, str):
            self.fail(path, f"must be text, got {type(value).__name__}")
        if not empty_ok and not value.strip():
            self.fail(path, "must not be empty")
        return value

    def opt_str(self, value: Any, path: str) -> str | None:
        return None if value is None else self.str_(value, path)

    def int_(self, value: Any, path: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.fail(path, f"must be a non negative integer, got {value!r}")
        return value

    def keys(self, obj: dict, path: str, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> None:
        missing = [k for k in required if k not in obj]
        if missing:
            self.fail(path, f"missing required key(s): {', '.join(missing)}")
        extra = sorted(set(obj) - set(required) - set(optional))
        if extra:
            self.fail(path, f"unknown key(s): {', '.join(extra)}")


def _check_usage(c: _Checker, usage: Any, path: str) -> None:
    usage = c.dict_(usage, path)
    for key in ("input_tokens", "output_tokens"):
        if key not in usage:
            c.fail(path, f"missing {key}")
        c.int_(usage[key], f"{path}.{key}")
    for key, value in usage.items():
        if key in ("input_token_details", "output_token_details"):
            c.dict_(value, f"{path}.{key}")
        elif key not in ("input_tokens", "output_tokens", "total_tokens"):
            c.fail(path, f"unknown usage key {key}")
        elif key == "total_tokens":
            c.int_(value, f"{path}.{key}")


def _check_identity(c: _Checker, identity: Any, path: str) -> None:
    identity = c.dict_(identity, path)
    c.keys(identity, path, IDENTITY_FIELDS)
    for key in IDENTITY_FIELDS:
        c.opt_str(identity[key], f"{path}.{key}")


def _check_response(c: _Checker, response: Any, path: str) -> None:
    response = c.dict_(response, path)
    if "stop_reason" in response:
        _check_refusal(c, response, path)
        return
    has_message = "message" in response
    has_structured = "structured" in response
    if has_message == has_structured:
        c.fail(path, "needs exactly one of message or structured")
    if has_structured:
        c.keys(response, path, ("structured", "usage"), ("thinking",))
        c.dict_(response["structured"], f"{path}.structured")
        _check_usage(c, response["usage"], f"{path}.usage")
        _check_thinking(c, response, path)
        return
    c.keys(response, path, ("message",))
    message = c.dict_(response["message"], f"{path}.message")
    c.keys(message, f"{path}.message", ("content", "tool_calls", "usage"))
    _check_usage(c, message["usage"], f"{path}.message.usage")
    calls = c.list_(message["tool_calls"], f"{path}.message.tool_calls")
    for i, call in enumerate(calls):
        cpath = f"{path}.message.tool_calls[{i}]"
        call = c.dict_(call, cpath)
        c.keys(call, cpath, ("name", "args"), ("id",))
        if call["name"] not in TOOLS:
            c.fail(f"{cpath}.name", f"must be one of {', '.join(TOOLS)}, got {call['name']!r}")
        c.dict_(call["args"], f"{cpath}.args")
    if isinstance(message["content"], list):
        _check_blocks(c, message["content"], calls, f"{path}.message.content")
    else:
        c.str_(message["content"], f"{path}.message.content")


def _check_blocks(c: _Checker, blocks: list, calls: list, path: str) -> None:
    """A reply recorded as content blocks: text, tool_use (repeating tool_calls) and thinking markers."""
    uses = []
    for i, block in enumerate(blocks):
        bpath = f"{path}[{i}]"
        block = c.dict_(block, bpath)
        kind = block.get("type")
        if kind not in BLOCK_TYPES:
            c.fail(f"{bpath}.type", f"must be one of {', '.join(BLOCK_TYPES)}, got {kind!r}")
        if kind == TEXT_BLOCK:
            c.keys(block, bpath, ("type", "text"))
            c.str_(block["text"], f"{bpath}.text")
        elif kind == TOOL_USE_BLOCK:
            c.keys(block, bpath, ("type", "id", "name", "input"))
            c.str_(block["id"], f"{bpath}.id", empty_ok=False)
            c.dict_(block["input"], f"{bpath}.input")
            uses.append((i, block))
        else:
            _check_marker(c, block, bpath)
    if len(uses) != len(calls):
        c.fail(path, f"holds {len(uses)} tool_use block(s) for {len(calls)} tool call(s)")
    for (i, block), call in zip(uses, calls):
        if (block["id"], block["name"], block["input"]) != (call.get("id"), call["name"], call["args"]):
            c.fail(f"{path}[{i}]", "does not repeat its tool call (id, name and args, in order)")


def _check_marker(c: _Checker, block: Any, path: str) -> None:
    """A thinking marker: where a thinking block stood, never its text or signature."""
    block = c.dict_(block, path)
    marker = THINKING_MARKERS.get(block.get("type"))
    if marker is None:
        c.fail(f"{path}.type", f"must be one of {', '.join(THINKING_MARKERS)}, got {block.get('type')!r}")
    elif block != marker:
        c.fail(path, f"a thinking marker is exactly {marker}; its text and signature are never kept")


def _check_thinking(c: _Checker, response: dict, path: str) -> None:
    if "thinking" in response:
        for i, block in enumerate(c.list_(response["thinking"], f"{path}.thinking")):
            _check_marker(c, block, f"{path}.thinking[{i}]")


def _check_refusal(c: _Checker, response: dict, path: str, extra: tuple[str, ...] = ()) -> None:
    """A recorded decline: stop_reason "refusal", its category at most, and the billed usage."""
    c.keys(response, path, (*extra, *REFUSAL_KEYS))
    if response["stop_reason"] != REFUSAL_STOP_REASON:
        c.fail(f"{path}.stop_reason", f"must be {REFUSAL_STOP_REASON!r}, got {response['stop_reason']!r}")
    details = response["stop_details"]
    if details is not None:
        details = c.dict_(details, f"{path}.stop_details")
        c.keys(details, f"{path}.stop_details", STOP_DETAILS_KEYS)
        if details["type"] != REFUSAL_STOP_REASON:
            c.fail(f"{path}.stop_details.type", f"must be {REFUSAL_STOP_REASON!r}, got {details['type']!r}")
        category = details["category"]
        if category is not None and (not isinstance(category, str) or not REFUSAL_CATEGORY_RE.match(category)):
            c.fail(f"{path}.stop_details.category", "must be null or one lower case token")
    _check_usage(c, response["usage"], f"{path}.usage")


def is_refusal(response: Any) -> bool:
    """True for a recorded decline (in any node's shape)."""
    return isinstance(response, dict) and response.get("stop_reason") == REFUSAL_STOP_REASON


def _refusal_response(response: dict) -> dict[str, Any]:
    """A recorded decline in the ReplayChatModel format."""
    return {key: copy.deepcopy(response[key]) for key in REFUSAL_KEYS}


def _closes_a_pass(response: Any) -> bool:
    """True for a research turn that ends a pass: a reply that is not a decline and calls no tool."""
    message = response.get("message") if isinstance(response, dict) else None
    return isinstance(message, dict) and not is_refusal(response) and not message.get("tool_calls")


def _synthesized_before_the_retry(data: dict) -> bool:
    """True when a research decline came in the retry pass, after one synthesize attempt.

    The nodes are listed in a fixed order, not the order the calls were made:
    a run that declines in the retry pass (retry_gate, then research with the
    retry limits) made its one synthesize attempt between the passes. So that
    attempt is allowed when it is the only one, is not itself a decline, and
    an earlier research turn closed the first pass.
    """
    script, drafts = data["research"]["script"], data["synthesize"]
    return (len(drafts) == 1 and not is_refusal(drafts[0])
            and any(_closes_a_pass(response) for response in script[:-1]))


def _check_declines_end_the_run(c: _Checker, data: dict) -> None:
    """A decline is the last response of its node, and no node after it has any (decision 69), except
    the one synthesize attempt made before a retry pass that declined in research."""
    lists = [
        ("read_plate", [] if data["read_plate"] is None else [data["read_plate"]]),
        ("classifier", data.get("classifier", [])),
        ("research.script", data["research"]["script"]),
        ("synthesize", data["synthesize"]),
    ]
    declined_at = None
    for name, responses in lists:
        retry_pass = declined_at == "research.script" and name == "synthesize" and _synthesized_before_the_retry(data)
        if declined_at is not None and responses and not retry_pass:
            c.fail(name, f"holds responses after the decline in {declined_at}, which ended the run")
        for i, response in enumerate(responses):
            if is_refusal(response):
                if i != len(responses) - 1:
                    c.fail(f"{name}[{i}]", "a decline must be the last response of its node")
                declined_at = name


def _check_recorded(c: _Checker, recorded: Any) -> None:
    recorded = c.dict_(recorded, "recorded")
    c.keys(recorded, "recorded", RECORDED_KEYS)
    if recorded["mode"] not in RECORDED_MODES:
        c.fail("recorded.mode", f"must be one of {', '.join(RECORDED_MODES)}, got {recorded['mode']!r}")
    for node, model in c.dict_(recorded["models"], "recorded.models").items():
        if node not in NODES:
            c.fail(f"recorded.models.{node}", f"must name one of {', '.join(NODES)}")
        if not isinstance(model, str) or not MODEL_NAME_RE.match(model):
            c.fail(f"recorded.models.{node}", "must be a model name with no spaces")
        elif model not in config.PRICES_PER_MTOK:
            c.fail(f"recorded.models.{node}", f"names {model!r}, which has no price in config.PRICES_PER_MTOK, "
                   "so a replay could not charge it")


def _check_tool_result(c: _Checker, entry: Any, path: str) -> None:
    entry = c.dict_(entry, path)
    tool = entry.get("tool")
    if tool not in TOOLS:
        c.fail(f"{path}.tool", f"must be one of {', '.join(TOOLS)}, got {tool!r}")
    arg = "query" if tool == "search" else "url"
    shapes = [k for k in FAILURE_SHAPES if k in entry]
    if len(shapes) > 1:
        c.fail(path, f"holds more than one failure shape: {', '.join(shapes)}")
    if shapes:
        shape = shapes[0]
        c.keys(entry, path, ("tool", shape), (arg,))
        if arg in entry:
            c.str_(entry[arg], f"{path}.{arg}", empty_ok=False)
        if shape == "string":
            c.str_(entry["string"], f"{path}.string")
        else:
            c.str_(entry[shape], f"{path}.{shape}", empty_ok=False)
        if shape == "raise" and entry["raise"] not in RAISE_KINDS:
            c.fail(f"{path}.raise", f"must be one of {', '.join(RAISE_KINDS)}, got {entry['raise']!r}")
        return
    optional = ("credits",) if tool == "search" else ("credits", "failed_results")
    c.keys(entry, path, ("tool", arg, "results"), optional)
    c.str_(entry[arg], f"{path}.{arg}", empty_ok=False)
    if "credits" in entry:
        c.int_(entry["credits"], f"{path}.credits")
    if "failed_results" in entry:
        c.list_(entry["failed_results"], f"{path}.failed_results")
    for i, result in enumerate(c.list_(entry["results"], f"{path}.results")):
        rpath = f"{path}.results[{i}]"
        result = c.dict_(result, rpath)
        url = c.str_(result.get("url"), f"{rpath}.url", empty_ok=False)
        if not re.match(r"^https?://", url, re.IGNORECASE):
            c.fail(f"{rpath}.url", "must be an http or https URL")
        if tool == "search":
            c.keys(result, rpath, ("url", "title", "content", "raw_content", "score"), RESULT_OPTIONAL)
            c.str_(result["title"], f"{rpath}.title")
            c.str_(result["content"], f"{rpath}.content")
            c.opt_str(result["raw_content"], f"{rpath}.raw_content")
        else:
            c.keys(result, rpath, ("url", "raw_content"), RESULT_OPTIONAL)
            c.opt_str(result["raw_content"], f"{rpath}.raw_content")
        _check_text_sha256(c, result, rpath)
        # Recorded text belongs only to example hosts (decision 15).
        text = result.get("raw_content") or result.get("content")
        if text and not is_example_url(url):
            c.fail(rpath, "page text is attached to a real URL; only example hosts may carry text")


def _check_text_sha256(c: _Checker, result: dict, rpath: str) -> None:
    """A recorded page text hash is 64 lower case hex, and names the recorded text when there is one."""
    if "text_sha256" not in result:
        return
    sha = result["text_sha256"]
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        c.fail(f"{rpath}.text_sha256", "must be 64 lower case hex characters")
    raw = result.get("raw_content")
    if raw and hashlib.sha256(raw.encode("utf-8")).hexdigest() != sha:
        c.fail(f"{rpath}.text_sha256", "does not match the sha256 of raw_content")


def _check_script_matches_results(c: _Checker, research: dict) -> None:
    """Every scripted tool call has a recorded result, in the same order, except
    the trailing calls that the run's tool cap blocked (research.blocked_calls)."""
    calls = [
        (i, call)
        for i, response in enumerate(research["script"])
        for call in response.get("message", {}).get("tool_calls", [])
    ]
    for tool in TOOLS:
        arg = "query" if tool == "search" else "url"
        scripted = [(i, call) for i, call in calls if call["name"] == tool]
        recorded = [r for r in research["tool_results"] if r["tool"] == tool]
        blocked = research.get("blocked_calls", {}).get(tool, 0) if isinstance(research.get("blocked_calls"), dict) else 0
        if len(scripted) != len(recorded) + blocked:
            c.fail(
                "research",
                f"script makes {len(scripted)} {tool} call(s) but tool_results holds {len(recorded)}"
                + (f" plus {blocked} blocked" if blocked else ""),
            )
        for n, ((i, call), result) in enumerate(zip(scripted, recorded)):
            if arg in result and call["args"].get(arg) != result[arg]:
                c.fail(
                    f"research.script[{i}]",
                    f"{tool} call {n + 1} uses {arg} {call['args'].get(arg)!r} "
                    f"but its recorded result is for {result[arg]!r}",
                )


def _check_safety_check(c: _Checker, entries: Any) -> None:
    """Each recorded Jev answer: its exact keys, hashes, one token strings and numbers; never text."""
    for i, entry in enumerate(c.list_(entries, "safety_check")):
        path = f"safety_check[{i}]"
        entry = c.dict_(entry, path)
        c.keys(entry, path, SAFETY_CHECK_KEYS)
        if not isinstance(entry["step_sha256"], str) or not SHA256_RE.match(entry["step_sha256"]):
            c.fail(f"{path}.step_sha256", "must be 64 lower case hex characters")
        if not isinstance(entry["question_hash"], str) or not QUESTION_HASH_RE.match(entry["question_hash"]):
            c.fail(f"{path}.question_hash", "must be 16 lower case hex characters")
        if not isinstance(entry["model"], str) or not MODEL_NAME_RE.match(entry["model"]):
            c.fail(f"{path}.model", "must be a model name with no spaces")
        noul = entry["noul"]
        if noul is not None and (
            isinstance(noul, bool) or not isinstance(noul, (int, float)) or not 0 <= noul <= 1
        ):
            c.fail(f"{path}.noul", "must be null or a probability from 0 to 1")
        for key in ("input_tokens", "output_tokens"):
            if entry[key] is not None:
                c.int_(entry[key], f"{path}.{key}")
        request_id = entry["request_id"]
        if request_id is not None and (not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id)):
            c.fail(f"{path}.request_id", "must be null or one token of at most 128 characters")
        error = entry["error"]
        if error is not None and error not in SAFETY_CHECK_ERRORS:
            c.fail(f"{path}.error", f"must be null or one of {', '.join(SAFETY_CHECK_ERRORS)}, got {error!r}")
        if (noul is None) == (error is None):
            c.fail(path, "needs exactly one of noul and error")


def check_safety_check_entries(entries: Any, source: str = "<safety_check>") -> None:
    """Raise CassetteError unless `entries` is a well formed safety_check list."""
    _check_safety_check(_Checker(source), entries)


def _check_provenance(c: _Checker, prov: Any) -> None:
    prov = c.dict_(prov, "provenance")
    c.keys(prov, "provenance", PROVENANCE_REQUIRED, PROVENANCE_OPTIONAL)
    derived = c.opt_str(prov["derived_from"], "provenance.derived_from")
    copied = c.list_(prov["copied_fields"], "provenance.copied_fields")
    for i, item in enumerate(copied):
        c.str_(item, f"provenance.copied_fields[{i}]", empty_ok=False)
    for i, item in enumerate(c.list_(prov["synthetic_fields"], "provenance.synthetic_fields")):
        c.str_(item, f"provenance.synthetic_fields[{i}]", empty_ok=False)
    c.str_(prov["built_by"], "provenance.built_by", empty_ok=False)
    if prov["reviewed"] is not None:
        c.str_(prov["reviewed"], "provenance.reviewed", empty_ok=False)
    c.opt_str(prov.get("public_already"), "provenance.public_already")
    if derived is None and copied:
        c.fail("provenance.copied_fields", "a cassette with no derived_from cannot list copied fields")
    if derived is not None and not derived.strip():
        c.fail("provenance.derived_from", "must be null or name a source")


def validate_cassette(data: Any, source: str = "<cassette>") -> None:
    """Raise CassetteError unless `data` is a well formed version 1 cassette."""
    c = _Checker(source)
    data = c.dict_(data, "$")
    c.keys(data, "$", REQUIRED_KEYS, OPTIONAL_KEYS)
    if data["cassette_version"] != CASSETTE_VERSION:
        c.fail("cassette_version", f"must be {CASSETTE_VERSION}, got {data['cassette_version']!r}")
    case = c.str_(data["case"], "case", empty_ok=False)
    if not CASE_RE.match(case):
        c.fail("case", "must be lower case letters, digits and underscores")
    _check_provenance(c, data["provenance"])

    inp = c.dict_(data["input"], "input")
    c.keys(inp, "input", ("identity", "symptom", "plate_sha256"))
    if inp["identity"] is not None:
        _check_identity(c, inp["identity"], "input.identity")
    c.str_(inp["symptom"], "input.symptom", empty_ok=False)
    if inp["plate_sha256"] is not None and not SHA256_RE.match(str(inp["plate_sha256"])):
        c.fail("input.plate_sha256", "must be null or 64 lower case hex characters")
    if inp["identity"] is not None and inp["plate_sha256"] is not None:
        c.fail("input", "give a plate or a typed identity, not both")

    if data["read_plate"] is not None:
        rp = c.dict_(data["read_plate"], "read_plate")
        if "stop_reason" in rp:
            _check_refusal(c, rp, "read_plate")
        else:
            c.keys(rp, "read_plate", ("extraction", "usage"), ("thinking",))
            c.dict_(rp["extraction"], "read_plate.extraction")
            _check_usage(c, rp["usage"], "read_plate.usage")
            _check_thinking(c, rp, "read_plate")
    for i, response in enumerate(c.list_(data.get("classifier", []), "classifier")):
        _check_response(c, response, f"classifier[{i}]")

    if data["resume"] is not None:
        resume = c.dict_(data["resume"], "resume")
        c.keys(resume, "resume", ("identity", "observed_code"))
        _check_identity(c, resume["identity"], "resume.identity")
        c.opt_str(resume["observed_code"], "resume.observed_code")

    research = c.dict_(data["research"], "research")
    c.keys(research, "research", ("script", "tool_results"), ("blocked_calls",))
    if "blocked_calls" in research:
        blocked = c.dict_(research["blocked_calls"], "research.blocked_calls")
        for tool, n in blocked.items():
            if tool not in TOOLS or not isinstance(n, int) or isinstance(n, bool) or n < 0:
                c.fail(f"research.blocked_calls.{tool}", "must name search or fetch with a whole number of calls")
    for i, response in enumerate(c.list_(research["script"], "research.script")):
        _check_response(c, response, f"research.script[{i}]")
    for i, entry in enumerate(c.list_(research["tool_results"], "research.tool_results")):
        _check_tool_result(c, entry, f"research.tool_results[{i}]")
    _check_script_matches_results(c, research)

    for i, attempt in enumerate(c.list_(data["synthesize"], "synthesize")):
        path = f"synthesize[{i}]"
        attempt = c.dict_(attempt, path)
        if "stop_reason" in attempt:
            _check_refusal(c, attempt, path, ("attempt",))
        else:
            c.keys(attempt, path, ("attempt", "draft", "usage"), ("thinking",))
            c.dict_(attempt["draft"], f"{path}.draft")
            _check_usage(c, attempt["usage"], f"{path}.usage")
            _check_thinking(c, attempt, path)
        if attempt["attempt"] != i + 1:
            c.fail(f"{path}.attempt", f"must be {i + 1}, got {attempt['attempt']!r}")

    for url, text in c.dict_(data["page_texts"], "page_texts").items():
        if not is_example_url(url):
            c.fail(f"page_texts[{url!r}]", "synthetic page text may only sit on an example host")
        c.str_(text, f"page_texts[{url!r}]")

    if data["caps"] is not None:
        caps = c.dict_(data["caps"], "caps")
        c.keys(caps, "caps", (), CAPS_KEYS)
        for key, value in caps.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                c.fail(f"caps.{key}", f"must be a non negative number, got {value!r}")

    expect = c.dict_(data["expect"], "expect")
    if not expect:
        c.fail("expect", "must state at least one expected outcome")

    if "safety_check" in data:
        _check_safety_check(c, data["safety_check"])
    if "recorded" in data:
        _check_recorded(c, data["recorded"])
    _check_declines_end_the_run(c, data)


# ---------------------------------------------------------------------------
# The Cassette object
# ---------------------------------------------------------------------------


@dataclass
class Cassette:
    """A validated cassette. Accessors return deep copies, never shared state."""

    path: Path | None
    data: dict[str, Any] = field(repr=False)

    @property
    def case(self) -> str:
        return self.data["case"]

    @property
    def provenance(self) -> dict[str, Any]:
        return copy.deepcopy(self.data["provenance"])

    @property
    def input(self) -> dict[str, Any]:
        return copy.deepcopy(self.data["input"])

    @property
    def resume(self) -> dict[str, Any] | None:
        return copy.deepcopy(self.data["resume"])

    @property
    def caps(self) -> dict[str, Any] | None:
        return copy.deepcopy(self.data["caps"])

    @property
    def expect(self) -> dict[str, Any]:
        return copy.deepcopy(self.data["expect"])

    @property
    def page_texts(self) -> dict[str, str]:
        return copy.deepcopy(self.data["page_texts"])

    @property
    def derived_from_v1(self) -> bool:
        """True when provenance names a v1 lookup (the decision 25 exemption)."""
        derived = self.data["provenance"]["derived_from"] or ""
        return derived.startswith(("v1 lookup ", "v1 extract lookup "))

    @property
    def safety_check(self) -> list[dict[str, Any]] | None:
        """Recorded Jev answers in call order, or None when the cassette has none."""
        entries = self.data.get("safety_check")
        return None if entries is None else copy.deepcopy(entries)

    @property
    def recorded(self) -> dict[str, Any] | None:
        """The live mode and the model each node's replies named, or None (every Haiku 4.5 cassette)."""
        value = self.data.get("recorded")
        return None if value is None else copy.deepcopy(value)

    def responses_for(self, node: str) -> list[dict[str, Any]]:
        """Scripted responses for one node, in the ReplayChatModel format."""
        if node == "read_plate":
            rp = self.data["read_plate"]
            out = [] if rp is None else [_structured_response(rp, "extraction")]
        elif node == "classifier":
            out = self.data.get("classifier", [])
        elif node == "research":
            out = self.data["research"]["script"]
        elif node == "synthesize":
            out = [_structured_response(a, "draft") for a in self.data["synthesize"]]
        else:
            raise ValueError(f"unknown node {node!r}; expected one of {', '.join(NODES)}")
        return copy.deepcopy(out)

    def tool_results_for(self, tool: str) -> list[dict[str, Any]]:
        """Recorded results for one tool, in call order, without the tool key."""
        if tool not in TOOLS:
            raise ValueError(f"unknown tool {tool!r}; expected one of {', '.join(TOOLS)}")
        return [
            {k: copy.deepcopy(v) for k, v in entry.items() if k != "tool"}
            for entry in self.data["research"]["tool_results"]
            if entry["tool"] == tool
        ]

    def registry_urls(self) -> list[str]:
        """The source registry code builds from this cassette's tool results."""
        return registry_urls(self.data["research"]["tool_results"])


def _structured_response(entry: dict[str, Any], key: str) -> dict[str, Any]:
    """read_plate's extraction or a synthesize draft as a ReplayChatModel response (or its decline)."""
    if is_refusal(entry):
        return _refusal_response(entry)
    out = {"structured": entry[key], "usage": entry["usage"]}
    if "thinking" in entry:
        out["thinking"] = entry["thinking"]
    return out


# A lifecycle cassette's symptom line: a lookup has no symptom, and the loader requires one.
LIFECYCLE_SYMPTOM = "lifecycle lookup: is this model discontinued, and which models succeed it"
LIFECYCLE_DECLINED_STATUS = "model_declined"


def lookup_recording_state(identity: dict[str, Any], result: dict[str, Any], *,
                           research_limits: str) -> dict[str, Any]:
    """What the live recorder reads from one lifecycle lookup (agent/lifecycle.py), in a run state's keys.

    The recording it makes is a lifecycle cassette: the unit's typed identity
    (which `lifecycle.cassette_for` matches in a replay sweep), the research
    turns and the one structured extraction, filed under research and
    synthesize as the sweep's cassettes are, and the lookup's status.
    """
    status = result.get("status")
    return {
        "photo": None,
        "identity": dict(identity),
        "symptom": LIFECYCLE_SYMPTOM,
        "status": status,
        "stop_reason": result.get("stop_reason"),
        "refusal_origin": LIFECYCLE_DECLINED_STATUS if status == LIFECYCLE_DECLINED_STATUS else None,
        "route": [],
        "search_trail": list(result.get("search_trail") or []),
        "research_limits": research_limits,
    }


def response_usage(response: dict[str, Any]) -> dict[str, Any]:
    """The recorded usage of one ReplayChatModel response, whatever its shape."""
    if "message" in response:
        return response["message"]["usage"]
    return response["usage"]


def cassette_from_dict(data: dict[str, Any], source: str = "<cassette>") -> Cassette:
    """Validate an in memory cassette and wrap it."""
    validate_cassette(data, source)
    return Cassette(path=None, data=copy.deepcopy(data))


def load_cassette(path: str | Path) -> Cassette:
    """Read, validate and return a cassette, or raise CassetteError."""
    path = Path(path)
    if path.suffix != ".json":
        raise CassetteError(f"{path}: cassettes are .json files")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CassetteError(f"{path}: not valid JSON: {exc}") from exc
    validate_cassette(data, str(path))
    return Cassette(path=path, data=data)
