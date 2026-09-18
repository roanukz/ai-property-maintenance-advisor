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
      "expect": {...}
    }

A response is in the ReplayChatModel format: {"message": {"content",
"tool_calls", "usage"}} or {"structured": {...}, "usage": {...}}. A tool
result is {"tool": "search", "query", "results"} or {"tool": "fetch", "url",
"results", "failed_results"}, either with optional "credits"; or one of the
failure shapes the wrappers must handle (PLAN 8.7): {"tool", "error": "..."},
{"tool", "string": "..."} (a bare string return) or {"tool", "raise":
"timeout"}. Each failure shape may also name its query or url.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
OPTIONAL_KEYS = ("classifier",)
PROVENANCE_REQUIRED = ("derived_from", "copied_fields", "synthetic_fields", "built_by", "reviewed")
PROVENANCE_OPTIONAL = ("public_already",)
IDENTITY_FIELDS = ("manufacturer", "model", "serial", "manufacture_date")
CAPS_KEYS = ("run_cap_usd", "research_budget_usd", "run_credit_cap")
FAILURE_SHAPES = ("error", "string", "raise")
RAISE_KINDS = ("timeout",)

CASE_RE = re.compile(r"^[a-z0-9_]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
    has_message = "message" in response
    has_structured = "structured" in response
    if has_message == has_structured:
        c.fail(path, "needs exactly one of message or structured")
    if has_structured:
        c.keys(response, path, ("structured", "usage"))
        c.dict_(response["structured"], f"{path}.structured")
        _check_usage(c, response["usage"], f"{path}.usage")
        return
    c.keys(response, path, ("message",))
    message = c.dict_(response["message"], f"{path}.message")
    c.keys(message, f"{path}.message", ("content", "tool_calls", "usage"))
    c.str_(message["content"], f"{path}.message.content")
    _check_usage(c, message["usage"], f"{path}.message.usage")
    for i, call in enumerate(c.list_(message["tool_calls"], f"{path}.message.tool_calls")):
        cpath = f"{path}.message.tool_calls[{i}]"
        call = c.dict_(call, cpath)
        c.keys(call, cpath, ("name", "args"), ("id",))
        if call["name"] not in TOOLS:
            c.fail(f"{cpath}.name", f"must be one of {', '.join(TOOLS)}, got {call['name']!r}")
        c.dict_(call["args"], f"{cpath}.args")


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
            c.keys(result, rpath, ("url", "title", "content", "raw_content", "score"))
            c.str_(result["title"], f"{rpath}.title")
            c.str_(result["content"], f"{rpath}.content")
            c.opt_str(result["raw_content"], f"{rpath}.raw_content")
        else:
            c.keys(result, rpath, ("url", "raw_content"))
            c.opt_str(result["raw_content"], f"{rpath}.raw_content")
        # Recorded text belongs only to example hosts (decision 15).
        text = result.get("raw_content") or result.get("content")
        if text and not is_example_url(url):
            c.fail(rpath, "page text is attached to a real URL; only example hosts may carry text")


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
        c.keys(rp, "read_plate", ("extraction", "usage"))
        c.dict_(rp["extraction"], "read_plate.extraction")
        _check_usage(c, rp["usage"], "read_plate.usage")
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
        c.keys(attempt, path, ("attempt", "draft", "usage"))
        if attempt["attempt"] != i + 1:
            c.fail(f"{path}.attempt", f"must be {i + 1}, got {attempt['attempt']!r}")
        c.dict_(attempt["draft"], f"{path}.draft")
        _check_usage(c, attempt["usage"], f"{path}.usage")

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

    def responses_for(self, node: str) -> list[dict[str, Any]]:
        """Scripted responses for one node, in the ReplayChatModel format."""
        if node == "read_plate":
            rp = self.data["read_plate"]
            out = [] if rp is None else [{"structured": rp["extraction"], "usage": rp["usage"]}]
        elif node == "classifier":
            out = self.data.get("classifier", [])
        elif node == "research":
            out = self.data["research"]["script"]
        elif node == "synthesize":
            out = [{"structured": a["draft"], "usage": a["usage"]} for a in self.data["synthesize"]]
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
