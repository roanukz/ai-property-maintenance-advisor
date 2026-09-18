"""Build the five SC2 cassettes and the SC1b payload file into staging.

Run as `python -m agent.replay.build_cassettes` with V1_BRIEFCASE_DIR set to
the private v1 folder. It opens only `data/lookups/*.json` and
`scripts/guardrail-tests.mjs` there, copies named fields by allowlist (never
a whole object), and writes to config.STAGING_DIR, which is gitignored. The
privacy diff (`python -m agent.replay.privacy_diff`) then reports on what was
written; nothing moves into agent/tests/ until Roanuk approves it.

What is copied and what is synthetic follows PLAN section 8.10:

- typed identities and symptoms, blank serial and date kept;
- read_plate extractions (and their measured usage) from the v1 extract logs,
  with the plate referenced by the sha256 of the byte identical public file
  in demo-assets/ (the photo is hashed, never copied). The extract lookup is
  found by that hash and labeled by the public file name, because its v1
  lookup ID names a private file (PLAN 8.10 never copies file names);
- the recorded brief, converted to a BriefDraft dict, with every source index
  remapped by URL into the source registry code builds from the tool results;
- cited source URLs and the model written titles;
- everything else is synthetic: search queries (checked to differ from v1's
  recorded `searched` strings), decoy results on example hosts interleaved
  between the cited sources, and per call usage split to the section 9
  typical estimate.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent import config
from agent.replay.cassettes import CASSETTE_VERSION, cassette_from_dict, registry_urls
from agent.replay.privacy_diff import (
    CASSETTES_SUBDIR,
    COPY_LOG_SUBDIR,
    DEMO_ASSETS_DIR,
    PRIVATE_HOST_RE,
    SC1B_FILE,
    jpeg_has_app1,
    strip_tracking,
)
from agent.schemas import BriefDraft, Extraction

BUILT_BY = "agent/replay/build_cassettes.py"
LOOKUPS_GLOB = "data/lookups/*.json"
GUARDRAIL_FILE = "scripts/guardrail-tests.mjs"

# Section 9 "typical" research pass: 5 searches, no fetch, then a final turn.
# Each research call starts at 2,000 tokens and each search adds about 2,150.
RESULTS_PER_SEARCH = config.TAVILY_SEARCH_SETTINGS["max_results"]
RESEARCH_CALL_BASE_TOKENS = config.RESEARCH_CALL_BASE_TOKENS
RESEARCH_TOKENS_PER_SEARCH = config.RESEARCH_TOKENS_PER_SEARCH
RESEARCH_OUTPUT_TOKENS = config.RESEARCH_OUTPUT_TOKENS
SYNTH_EXCERPT_TOKENS_TYPICAL = config.SYNTH_EXCERPT_TOKENS_TYPICAL
SYNTH_OUTPUT_TOKENS_TYPICAL = config.SYNTH_OUTPUT_TOKENS_TYPICAL

DECOY_HOSTS = ("example.com", "example.org", "example.net")
DECOY_CONTENT = (
    "Synthetic decoy search result for replay tests. It is not a real page, "
    "and no brief may cite it."
)


class NotAllowlisted(ValueError):
    """The builder tried to read a v1 field that is not on the allowlist."""


# ---------------------------------------------------------------------------
# Allowlist copier
# ---------------------------------------------------------------------------

# Paths in a v1 lookup that may be copied, with list indexes written [*].
# Only scalar values are ever copied; a path that holds an object or a list
# is refused, so a whole object can never be carried over by accident.
ALLOWLIST: frozenset[str] = frozenset(
    [f"$.input.identity.{k}" for k in ("manufacturer", "model", "serial", "manufacture_date")]
    + ["$.input.symptom"]
    + [f"$.extracted_identity.{k}" for k in ("manufacturer", "model", "serial", "manufacture_date")]
    + [f"$.extracted_identity.confidence.{k}" for k in ("manufacturer", "model", "serial", "manufacture_date")]
    + ["$.usage.input_tokens", "$.usage.output_tokens"]
    + ["$.brief.status", "$.brief.matched_identity", "$.brief.observed_code"]
    + ["$.brief.warranty_caution", "$.brief.warranty_caution.text", "$.brief.warranty_caution.source_index"]
    + ["$.brief.happened_before"]
    + [f"$.brief.try_first[*].{k}" for k in ("step", "detail", "safety_flag", "source_index")]
    + [
        f"$.brief.candidates[*].{k}"
        for k in ("code", "documented_meaning", "documented_action", "who", "why_shown", "source_index", "confirmed")
    ]
    + ["$.brief.warranty", "$.brief.warranty.age_statement", "$.brief.warranty.verify[*]"]
    + ["$.brief.warranty.cautions[*].text", "$.brief.warranty.cautions[*].source_index"]
    + ["$.brief.no_reliable_answer", "$.brief.no_reliable_answer.searched[*]"]
    + ["$.brief.no_reliable_answer.found[*]", "$.brief.no_reliable_answer.why_insufficient"]
    + [f"$.brief.sources[*].{k}" for k in ("url", "title", "tier")]
)
# Read only to hash, never copied: proves the plate is a public file.
HASH_ONLY: frozenset[str] = frozenset(["$.input.photo_base64"])

_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def _pattern(path: str) -> str:
    return re.sub(r"\[\d+\]", "[*]", path)


def _walk(record: Any, path: str) -> tuple[bool, Any]:
    """Return (present, value) for a path like $.brief.try_first[0].step."""
    if not path.startswith("$"):
        raise ValueError(f"path must start with $: {path}")
    node = record
    for key, index in _TOKEN.findall(path[1:]):
        if key:
            if not isinstance(node, dict) or key not in node:
                return False, None
            node = node[key]
        else:
            if not isinstance(node, list) or int(index) >= len(node):
                return False, None
            node = node[int(index)]
    return True, node


@dataclass
class AllowlistCopier:
    """Reads named scalar fields from one v1 record and logs every copy."""

    record: dict[str, Any]
    label: str  # for example "v1 lookup 549892815cb6"
    allowlist: frozenset[str] = ALLOWLIST
    copies: list[dict[str, Any]] = field(default_factory=list)
    hashed: list[dict[str, Any]] = field(default_factory=list)

    def _check(self, path: str) -> None:
        if _pattern(path) not in self.allowlist:
            raise NotAllowlisted(f"{self.label}: {path} is not on the copy allowlist")

    def take(self, path: str, dest: str | None = None, *, default: Any = None) -> Any:
        """Copy one scalar. Objects and lists are refused, never copied whole."""
        self._check(path)
        present, value = _walk(self.record, path)
        if not present:
            return default
        if isinstance(value, (dict, list)):
            raise NotAllowlisted(f"{self.label}: {path} holds a {type(value).__name__}; only scalars are copied")
        if dest is not None:
            self.copies.append({"dest": dest, "source": f"{self.label} {path}", "value": value})
        return value

    def present(self, path: str) -> bool:
        """True when the path holds a non null value. Reads nothing into output."""
        pattern = _pattern(path)
        if pattern not in self.allowlist and not any(p.startswith(pattern + ".") or p.startswith(pattern + "[")
                                                     for p in self.allowlist):
            raise NotAllowlisted(f"{self.label}: {path} is not on the copy allowlist")
        found, value = _walk(self.record, path)
        return found and value is not None

    def length(self, path: str) -> int:
        """Length of a list whose items are allowlisted; 0 when absent or not a list."""
        if not any(p.startswith(_pattern(path) + "[*]") for p in self.allowlist):
            raise NotAllowlisted(f"{self.label}: {path} has no allowlisted items")
        _, value = _walk(self.record, path)
        return len(value) if isinstance(value, list) else 0

    def log(self, dest: str, source: str, value: Any) -> None:
        """Record a derived copy (for example a URL reached through an index)."""
        self.copies.append({"dest": dest, "source": f"{self.label} {source}", "value": value})

    def sha256_of_base64(self, path: str) -> str:
        if path not in HASH_ONLY:
            raise NotAllowlisted(f"{self.label}: {path} may not be read, even to hash")
        present, value = _walk(self.record, path)
        if not present or not isinstance(value, str):
            raise ValueError(f"{self.label}: {path} holds no base64 text")
        digest = hashlib.sha256(base64.b64decode(value)).hexdigest()
        self.hashed.append({"source": f"{self.label} {path}", "sha256": digest})
        return digest


# ---------------------------------------------------------------------------
# Search results: cited sources interleaved with decoys
# ---------------------------------------------------------------------------


def cited_positions(n_cited: int, total_slots: int) -> list[int]:
    """Odd slots spread evenly, so every cited result has a decoy on each side."""
    available = total_slots // 2  # slots 1, 3, 5, ... each flanked by even slots
    if n_cited > available:
        raise ValueError(f"{n_cited} cited sources do not fit in {total_slots} result slots")
    if n_cited == 0:
        return []
    if n_cited == 1:
        return [1]
    picks = [round(k * (available - 1) / (n_cited - 1)) for k in range(n_cited)]
    if len(set(picks)) != n_cited:
        raise ValueError("cited slot spread collided")
    return [2 * p + 1 for p in picks]


def decoy_result(case: str, k: int, score: float) -> dict[str, Any]:
    host = DECOY_HOSTS[k % len(DECOY_HOSTS)]
    return {
        "url": f"https://{host}/replay/{case}/decoy-{k:02d}",
        "title": f"Synthetic decoy result {k:02d} ({case})",
        "content": DECOY_CONTENT,
        "raw_content": None,
        "score": score,
    }


def interleave_results(
    case: str, cited: list[dict[str, str]], n_searches: int, per_search: int
) -> list[list[dict[str, Any]]]:
    """Lay out search results: cited sources (in reverse v1 order) between decoys.

    The reverse order makes the v1 index to registry index map something other
    than a fixed offset, so only a remap by URL gets every citation right.
    """
    total = n_searches * per_search
    positions = cited_positions(len(cited), total)
    by_position = dict(zip(positions, reversed(cited)))
    flat: list[dict[str, Any]] = []
    decoys = 0
    for pos in range(total):
        # Synthetic relevance scores, falling within and across searches.
        score = (950 - 10 * (pos % per_search) - 2 * (pos // per_search)) / 1000
        if pos in by_position:
            src = by_position[pos]
            flat.append({"url": src["url"], "title": src["title"], "content": "", "raw_content": None,
                         "score": score})
        else:
            decoys += 1
            flat.append(decoy_result(case, decoys, score))
    return [flat[i : i + per_search] for i in range(0, total, per_search)]


def remap_index(v1_index: Any, v1_sources: list[dict[str, str]], registry: list[str]) -> int | None:
    """Map a v1 source index to the code built registry, by URL."""
    if v1_index is None:
        return None
    if isinstance(v1_index, bool) or not isinstance(v1_index, int) or not 0 <= v1_index < len(v1_sources):
        raise ValueError(f"v1 source index {v1_index!r} is out of range")
    return registry.index(v1_sources[v1_index]["url"])


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseSpec:
    case: str
    brief_id: str | None  # public: briefs/<brief_id>.html
    plate_file: str | None  # demo-assets file whose v1 extract lookup is used
    queries: tuple[str, ...]
    public_already: str
    symptom: str | None = None  # synthetic symptom when no v1 brief exists


SC2_CASES = (
    CaseSpec(
        "flo", "549892815cb6", "plate-clear.jpg",
        (
            "Sundance Optima 880 FLO code meaning",
            "Sundance 880 series flow error troubleshooting steps",
            "Optima 880 heater flow switch check",
            "Sundance spa FLO message filter clogged test",
            "Sundance 880 series warranty rental use",
        ),
        "src/fixtures.js case flo; briefs/549892815cb6.html; demo-assets/plate-clear.jpg",
    ),
    CaseSpec(
        "notheating", "db572cf7b81b", "plate-clear.jpg",
        (
            "Sundance Optima 880 spa heater not working",
            "Sundance 880 series owner manual heating troubleshooting",
            "Optima 880 cabinet indicator light red meaning",
            "Sundance spa COOL ICE message at startup",
            "Sundance hot tub warranty commercial use terms",
        ),
        "src/fixtures.js case notheating; briefs/db572cf7b81b.html; demo-assets/plate-clear.jpg",
    ),
    CaseSpec(
        "hvac", "c45b900046bf", None,
        (
            "Trane XR16 4TTR6036 not cooling troubleshooting",
            "Trane 4TTR6 outdoor unit owner guide",
            "Trane air conditioner upstairs warm causes",
            "Trane XR16 condenser coil cleaning guidance",
            "Trane residential AC warranty registration terms",
        ),
        "src/fixtures.js case hvac; briefs/c45b900046bf.html",
    ),
    CaseSpec(
        "unknown", "a601fd524225", None,
        (
            "Aquarest ZX-9000 Pro spa service manual",
            "Aquarest spa ZX-9000 Pro heater fault",
            "AquaRest spas current model list",
            "AquaRest spa control pack owner manual",
            "ZX-9000 Pro hot tub manufacturer",
        ),
        "src/fixtures.js case unknown; briefs/a601fd524225.html",
    ),
    CaseSpec(
        "blurry", None, "plate-blurry.jpg", (),
        "src/fixtures.js case blurry; demo-assets/plate-blurry.jpg",
        symptom="not heating",
    ),
)

IDENTITY_FIELDS = ("manufacturer", "model", "serial", "manufacture_date")


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens}


def research_usage(call: int) -> dict[str, int]:
    """Section 9 typical split: call k (from 0) sees k searches' results."""
    return _usage(RESEARCH_CALL_BASE_TOKENS + RESEARCH_TOKENS_PER_SEARCH * call, RESEARCH_OUTPUT_TOKENS)


def synthesize_usage() -> dict[str, int]:
    return _usage(config.SYNTH_PROMPT_TOKENS_ESTIMATE + SYNTH_EXCERPT_TOKENS_TYPICAL, SYNTH_OUTPUT_TOKENS_TYPICAL)


def _normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.replace('"', "").strip().lower())


def research_script(spec: CaseSpec, searches: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """A plausible scripted agent run: one search per turn, then a final message."""
    script, tool_results = [], []
    for n, (query, results) in enumerate(zip(spec.queries, searches), start=1):
        script.append({"message": {
            "content": "",
            "tool_calls": [{"name": "search", "args": {"query": query}, "id": f"call_{spec.case}_search_{n}"}],
            "usage": research_usage(n - 1),
        }})
        tool_results.append({"tool": "search", "query": query, "results": results,
                             "credits": config.TAVILY_CREDITS["search_basic"]})
    script.append({"message": {
        "content": f"Research finished after {len(spec.queries)} searches; the results above are what I found.",
        "tool_calls": [],
        "usage": research_usage(len(spec.queries)),
    }})
    return {"script": script, "tool_results": tool_results}


def _identity(cp: AllowlistCopier, dest: str) -> dict[str, str]:
    return {k: cp.take(f"$.input.identity.{k}", f"{dest}.{k}") for k in IDENTITY_FIELDS}


def _v1_sources(cp: AllowlistCopier) -> list[dict[str, str]]:
    sources = []
    for i in range(cp.length("$.brief.sources")):
        url = cp.take(f"$.brief.sources[{i}].url")
        clean, _ = strip_tracking(url)
        if not re.match(r"^https?://", clean, re.IGNORECASE) or PRIVATE_HOST_RE.match(
            re.sub(r"^https?://", "", clean, flags=re.IGNORECASE)
        ):
            raise ValueError(f"{cp.label}: source {i} is not a public web URL")
        sources.append({"url": clean, "title": cp.take(f"$.brief.sources[{i}].title"),
                        "tier": cp.take(f"$.brief.sources[{i}].tier")})
    urls = [s["url"] for s in sources]
    if len(set(urls)) != len(urls):
        raise ValueError(f"{cp.label}: the v1 brief lists a source URL twice")
    return sources


def _log_search_copies(cp: AllowlistCopier, tool_results: list[dict], v1_sources: list[dict]) -> None:
    by_url = {s["url"]: i for i, s in enumerate(v1_sources)}
    for t, entry in enumerate(tool_results):
        for r, result in enumerate(entry["results"]):
            i = by_url.get(result["url"])
            if i is not None:
                base = f"research.tool_results[{t}].results[{r}]"
                cp.log(f"{base}.url", f"$.brief.sources[{i}].url", result["url"])
                cp.log(f"{base}.title", f"$.brief.sources[{i}].title", result["title"])


def convert_brief(cp: AllowlistCopier, v1_sources: list[dict], registry: list[str]) -> tuple[dict, dict]:
    """The recorded brief as a BriefDraft dict, plus the cited URL per item."""
    d = "synthesize[0].draft"
    remap = lambda path: remap_index(cp.take(path), v1_sources, registry)  # noqa: E731
    if cp.present("$.brief.happened_before"):
        raise ValueError(f"{cp.label}: happened_before is set; the converter has no rule for it")
    urls: dict[str, Any] = {"warranty_caution": None, "try_first": [], "candidates": [], "warranty_cautions": []}

    def url_of(index: int | None) -> str | None:
        return None if index is None else registry[index]

    draft: dict[str, Any] = {
        "status": cp.take("$.brief.status", f"{d}.status"),
        "matched_identity": cp.take("$.brief.matched_identity", f"{d}.matched_identity"),
        "warranty_caution": None,
        "happened_before": None,
        "try_first": [],
        "candidates": [],
        "warranty": None,
        "no_reliable_answer": None,
        "upgrade_options": [],
        "maintenance_due": [],
        "source_tiers": [],
    }
    if cp.present("$.brief.warranty_caution"):
        idx = remap("$.brief.warranty_caution.source_index")
        draft["warranty_caution"] = {"text": cp.take("$.brief.warranty_caution.text", f"{d}.warranty_caution.text"),
                                     "source_index": idx}
        urls["warranty_caution"] = url_of(idx)
    for i in range(cp.length("$.brief.try_first")):
        p, q = f"$.brief.try_first[{i}]", f"{d}.try_first[{i}]"
        idx = remap(f"{p}.source_index")
        draft["try_first"].append({
            "step": cp.take(f"{p}.step", f"{q}.step"),
            "detail": cp.take(f"{p}.detail", f"{q}.detail", default=""),
            "safety_flag": cp.take(f"{p}.safety_flag", f"{q}.safety_flag") is True,
            "source_index": idx,
        })
        urls["try_first"].append(url_of(idx))
    for i in range(cp.length("$.brief.candidates")):
        p, q = f"$.brief.candidates[{i}]", f"{d}.candidates[{i}]"
        idx = remap(f"{p}.source_index")
        draft["candidates"].append({
            "code": cp.take(f"{p}.code", f"{q}.code"),
            "documented_meaning": cp.take(f"{p}.documented_meaning", f"{q}.documented_meaning"),
            "documented_action": cp.take(f"{p}.documented_action", f"{q}.documented_action"),
            "who": cp.take(f"{p}.who", f"{q}.who"),
            "why_shown": cp.take(f"{p}.why_shown", f"{q}.why_shown", default=""),
            "source_index": idx,
            "confirmed": cp.take(f"{p}.confirmed", f"{q}.confirmed") is True,
            "evidence": "",
        })
        urls["candidates"].append(url_of(idx))
    if cp.present("$.brief.warranty"):
        w = f"{d}.warranty"
        cautions = []
        for i in range(cp.length("$.brief.warranty.cautions")):
            idx = remap(f"$.brief.warranty.cautions[{i}].source_index")
            cautions.append({"text": cp.take(f"$.brief.warranty.cautions[{i}].text", f"{w}.cautions[{i}].text"),
                             "source_index": idx})
            urls["warranty_cautions"].append(url_of(idx))
        draft["warranty"] = {
            "age_statement": cp.take("$.brief.warranty.age_statement", f"{w}.age_statement"),
            "cautions": cautions,
            "verify": [cp.take(f"$.brief.warranty.verify[{i}]", f"{w}.verify[{i}]")
                       for i in range(cp.length("$.brief.warranty.verify"))],
        }
    if cp.present("$.brief.no_reliable_answer"):
        n = f"{d}.no_reliable_answer"
        draft["no_reliable_answer"] = {
            "found": [cp.take(f"$.brief.no_reliable_answer.found[{i}]", f"{n}.found[{i}]")
                      for i in range(cp.length("$.brief.no_reliable_answer.found"))],
            "why_insufficient": cp.take("$.brief.no_reliable_answer.why_insufficient", f"{n}.why_insufficient"),
        }
    for i, src in enumerate(v1_sources):
        reg = registry.index(src["url"])
        draft["source_tiers"].append({"source_index": reg, "tier": src["tier"], "authorship_quote": ""})
        cp.log(f"{d}.source_tiers[{i}].tier", f"$.brief.sources[{i}].tier", src["tier"])
    BriefDraft.model_validate(draft)  # fail the build, not the replay
    # Log the cited URL per item as a derived copy.
    for kind, values in urls.items():
        if kind == "warranty_caution":
            if values is not None:
                cp.log(f"expect.cited_urls.{kind}", "$.brief.sources[...].url via source_index", values)
            continue
        for i, url in enumerate(values):
            if url is not None:
                cp.log(f"expect.cited_urls.{kind}[{i}]", "$.brief.sources[...].url via source_index", url)
    return draft, urls


def _read_plate(cp: AllowlistCopier, spec: CaseSpec, demo_dir: Path) -> tuple[dict, str]:
    """The recorded extraction, and the sha256 of the byte identical public plate."""
    digest = cp.sha256_of_base64("$.input.photo_base64")
    plate = demo_dir / spec.plate_file
    public = hashlib.sha256(plate.read_bytes()).hexdigest()
    if digest != public:
        raise ValueError(f"{cp.label}: the recorded photo is not byte identical to {plate.name}")
    if jpeg_has_app1(plate.read_bytes()):
        raise ValueError(f"{plate.name} carries an APP1 (EXIF) segment")
    cp.hashed[-1]["matches"] = f"demo-assets/{plate.name}"
    extraction = {k: cp.take(f"$.extracted_identity.{k}", f"read_plate.extraction.{k}") for k in IDENTITY_FIELDS}
    extraction["confidence"] = {
        k: cp.take(f"$.extracted_identity.confidence.{k}", f"read_plate.extraction.confidence.{k}")
        for k in IDENTITY_FIELDS
    }
    Extraction.model_validate(extraction)
    usage = _usage(cp.take("$.usage.input_tokens", "read_plate.usage.input_tokens"),
                   cp.take("$.usage.output_tokens", "read_plate.usage.output_tokens"))
    return {"extraction": extraction, "usage": usage}, digest


def extract_label(plate_file: str) -> str:
    return f"v1 extract lookup for demo-assets/{plate_file}"


def find_extract_lookup(lookups: dict[str, dict], plate_file: str, demo_dir: Path) -> dict:
    """The one v1 extract lookup whose photo is byte identical to the public plate.

    Matching by the photo's hash, not by a hardcoded lookup ID, keeps the
    private lookup IDs out of the repo and out of every staged file.
    """
    public = hashlib.sha256((demo_dir / plate_file).read_bytes()).hexdigest()
    matches = []
    for record in lookups.values():
        probe = AllowlistCopier(record, "probe")
        if not probe.present("$.extracted_identity"):
            continue
        try:
            digest = probe.sha256_of_base64("$.input.photo_base64")
        except ValueError:
            continue
        if digest == public:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError(f"expected one v1 extract lookup for {plate_file}, found {len(matches)}")
    return matches[0]


def _collapse(dests: list[str]) -> list[str]:
    out: dict[str, None] = {}
    for dest in dests:
        out[re.sub(r"\[\d+\]", "[*]", dest)] = None
    return list(out)


def build_case(spec: CaseSpec, lookups: dict[str, dict], demo_dir: Path = DEMO_ASSETS_DIR) -> tuple[dict, dict]:
    """Build one cassette dict and its copy log."""
    brief_cp = AllowlistCopier(lookups[spec.brief_id], f"v1 lookup {spec.brief_id}") if spec.brief_id else None
    plate_cp = None
    if spec.plate_file:
        record = find_extract_lookup(lookups, spec.plate_file, demo_dir)
        plate_cp = AllowlistCopier(record, extract_label(spec.plate_file))
    derived = []
    if spec.brief_id:
        derived.append(f"v1 lookup {spec.brief_id}")
    if spec.plate_file:
        derived.append(extract_label(spec.plate_file))

    read_plate, plate_sha = (None, None)
    if plate_cp is not None:
        read_plate, plate_sha = _read_plate(plate_cp, spec, demo_dir)

    synthetic = []
    research: dict[str, Any] = {"script": [], "tool_results": []}
    synthesize: list[dict] = []
    resume = None
    expect: dict[str, Any]
    if brief_cp is None:
        symptom = spec.symptom
        synthetic.append("input.symptom (matches the public fixture's symptom; v1 recorded none for this plate)")
        identity = None
        expect = {
            "halts_at": "confirm_identity",
            "extraction_model": read_plate["extraction"]["model"],
            "model_confidence": read_plate["extraction"]["confidence"]["model"],
            "research_calls": 0, "synthesize_calls": 0, "search_calls": 0,
            "budget_stopped": False,
        }
    else:
        symptom = brief_cp.take("$.input.symptom", "input.symptom")
        v1_sources = _v1_sources(brief_cp)
        searches = interleave_results(spec.case, v1_sources, len(spec.queries), RESULTS_PER_SEARCH)
        research = research_script(spec, searches)
        registry = registry_urls(research["tool_results"])
        _log_search_copies(brief_cp, research["tool_results"], v1_sources)
        draft, cited = convert_brief(brief_cp, v1_sources, registry)
        synthesize = [{"attempt": 1, "draft": draft, "usage": synthesize_usage()}]
        if plate_cp is not None:
            identity = None
            resume = {"identity": _identity(brief_cp, "resume.identity"),
                      "observed_code": brief_cp.take("$.brief.observed_code", "resume.observed_code")}
        else:
            identity = _identity(brief_cp, "input.identity")
        v1_searched = [
            brief_cp.take(f"$.brief.no_reliable_answer.searched[{i}]", f"expect.v1_searched[{i}]")
            for i in range(brief_cp.length("$.brief.no_reliable_answer.searched"))
        ]
        clash = {_normalize_query(q) for q in spec.queries} & {_normalize_query(q) for q in v1_searched}
        if clash:
            raise ValueError(f"{spec.case}: synthetic queries repeat v1's searched strings: {sorted(clash)}")
        expect = {"status": draft["status"], "budget_stopped": False,
                  "candidates": len(draft["candidates"]), "try_first": len(draft["try_first"]),
                  "confirmed": sum(c["confirmed"] for c in draft["candidates"])}
        if draft["status"] == "ok":
            expect["candidate_codes"] = [c["code"] for c in draft["candidates"]]
            expect["safety_first"] = True
            expect["cited_urls"] = cited
            expect["observed_code"] = resume["observed_code"] if resume else None
            expect["grounding"] = "unverifiable"
        else:
            expect["searched"] = list(spec.queries)
            expect["v1_searched"] = v1_searched
            expect["refusal_origin"] = "model"
        synthetic += [
            "research.script (one search per turn, then a final message)",
            "research.tool_results[*].query (differ from v1's recorded searched strings)",
            "research.tool_results[*].results on example hosts (decoys, interleaved between cited sources)",
            "research.tool_results[*].results[*].score and credits",
            "research.tool_results[*].results[*].content (empty on real URLs; v1 recorded no text)",
            "research.script[*].message.usage and synthesize[*].usage (PLAN section 9 typical split)",
            "synthesize[*].draft evidence, authorship_quote (empty), upgrade_options and maintenance_due (empty)",
            "synthesize[*].draft source_index values (remapped by URL into the code built registry)",
            "expect",
        ]

    copies = (brief_cp.copies if brief_cp else []) + (plate_cp.copies if plate_cp else [])
    hashed = (brief_cp.hashed if brief_cp else []) + (plate_cp.hashed if plate_cp else [])
    cassette = {
        "cassette_version": CASSETTE_VERSION,
        "case": spec.case,
        "provenance": {
            "derived_from": "; ".join(derived) or None,
            "copied_fields": _collapse([c["dest"] for c in copies]),
            "public_already": spec.public_already,
            "synthetic_fields": synthetic,
            "built_by": BUILT_BY,
            "reviewed": None,
        },
        "input": {"identity": identity, "symptom": symptom, "plate_sha256": plate_sha},
        "read_plate": read_plate,
        "resume": resume,
        "research": research,
        "synthesize": synthesize,
        "page_texts": {},
        "caps": None,
        "expect": expect,
    }
    cassette_from_dict(cassette, spec.case)  # the loader's checks, at build time
    return cassette, {"file": f"{CASSETTES_SUBDIR}/{spec.case}.json", "copies": copies, "hashed": hashed}


def pass_cost_usd(cassette: dict) -> float:
    """What one replay pass costs at REPLAY_PRICES, from the scripted usage."""
    from agent.ledger import price_usage

    loaded = cassette_from_dict(cassette)
    usages = [r["usage"] for r in loaded.responses_for("read_plate")]
    usages += [r["message"]["usage"] for r in loaded.responses_for("research")]
    usages += [r["usage"] for r in loaded.responses_for("synthesize")]
    return sum(price_usage(config.REPLAY_PRICES, u) for u in usages)


def load_lookups(v1_dir: Path) -> dict[str, dict]:
    """Every v1 lookup keyed by its $.id (filenames are never used as keys)."""
    out = {}
    for path in sorted(v1_dir.glob(LOOKUPS_GLOB)):
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            out[record["id"]] = record
    return out


# ---------------------------------------------------------------------------
# SC1b payloads from the v1 guardrail tests
# ---------------------------------------------------------------------------


class _JsLiteral:
    """Parses JavaScript object, array, string, number and keyword literals.

    Enough for the guardrail test payloads: unquoted or quoted keys, single or
    double quoted strings, trailing commas, // and /* */ comments, and names
    resolved from a symbol table. Anything else is an error, never evaluated.
    """

    ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def __init__(self, text: str, symbols: dict[str, Any] | None = None) -> None:
        self.text = text
        self.symbols = symbols or {}

    def _skip(self, pos: int) -> int:
        t = self.text
        while pos < len(t):
            if t[pos].isspace():
                pos += 1
            elif t.startswith("//", pos):
                end = t.find("\n", pos)
                pos = len(t) if end < 0 else end + 1
            elif t.startswith("/*", pos):
                pos = t.index("*/", pos) + 2
            else:
                break
        return pos

    def parse(self, pos: int) -> tuple[Any, int]:
        pos = self._skip(pos)
        ch = self.text[pos]
        if ch == "{":
            return self._object(pos + 1)
        if ch == "[":
            return self._array(pos + 1)
        if ch in "'\"":
            return self._string(pos)
        m = re.compile(r"-?\d+(?:\.\d+)?").match(self.text, pos)
        if m:
            num = m.group()
            return (float(num) if "." in num else int(num)), m.end()
        m = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*").match(self.text, pos)
        if m:
            word = m.group()
            if word in ("true", "false", "null"):
                return {"true": True, "false": False, "null": None}[word], m.end()
            if word in self.symbols:
                return copy.deepcopy(self.symbols[word]), m.end()
            raise ValueError(f"unknown name {word!r} at offset {pos}")
        raise ValueError(f"unexpected {ch!r} at offset {pos}")

    def _string(self, pos: int) -> tuple[str, int]:
        quote, pos, out = self.text[pos], pos + 1, []
        while True:
            ch = self.text[pos]
            if ch == quote:
                return "".join(out), pos + 1
            if ch == "\n":
                raise ValueError(f"unterminated string at offset {pos}")
            if ch == "\\":
                nxt = self.text[pos + 1]
                if nxt == "u":
                    out.append(chr(int(self.text[pos + 2 : pos + 6], 16)))
                    pos += 6
                    continue
                out.append(self.ESCAPES.get(nxt, nxt))
                pos += 2
                continue
            out.append(ch)
            pos += 1

    def _object(self, pos: int) -> tuple[dict, int]:
        out: dict[str, Any] = {}
        while True:
            pos = self._skip(pos)
            if self.text[pos] == "}":
                return out, pos + 1
            if self.text[pos] in "'\"":
                key, pos = self._string(pos)
            else:
                m = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*").match(self.text, pos)
                if not m:
                    raise ValueError(f"bad object key at offset {pos}")
                key, pos = m.group(), m.end()
            pos = self._skip(pos)
            if self.text[pos] != ":":
                raise ValueError(f"expected ':' at offset {pos}")
            out[key], pos = self.parse(pos + 1)
            pos = self._skip(pos)
            if self.text[pos] == ",":
                pos += 1
            elif self.text[pos] != "}":
                raise ValueError(f"expected ',' or '}}' at offset {pos}")

    def _array(self, pos: int) -> tuple[list, int]:
        out: list[Any] = []
        while True:
            pos = self._skip(pos)
            if self.text[pos] == "]":
                return out, pos + 1
            item, pos = self.parse(pos)
            out.append(item)
            pos = self._skip(pos)
            if self.text[pos] == ",":
                pos += 1
            elif self.text[pos] != "]":
                raise ValueError(f"expected ',' or ']' at offset {pos}")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _json_object_in(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    return json.loads(text[start : end + 1])


def extract_sc1b(source_text: str, source_name: str = GUARDRAIL_FILE) -> tuple[dict, dict]:
    """Payloads of the v1 guardrail tests, parsed from their literal text only."""
    lit = _JsLiteral(source_text)
    m = re.search(r"const okSources\s*=\s*", source_text)
    if not m:
        raise ValueError("okSources fixture not found")
    ok_sources, _ = lit.parse(m.end())
    lit.symbols["okSources"] = ok_sources

    starts = [m.start() for m in re.finditer(r"^check\(", source_text, re.M)]
    ends = starts[1:] + [len(source_text)]
    payloads, copies = [], []
    for number, (start, end) in enumerate(zip(starts, ends), start=1):
        body = source_text[start:end]
        rejects = "expectThrow(" in body
        if "validateBrief(" in body:
            m = re.search(r"const brief\s*=\s*", body) or re.search(r"validateBrief\(\s*(?=\{)", body)
            payload, stop = lit.parse(start + m.end())
            matcher = re.compile(r"\)\s*,\s*(/(?:\\/|[^/\n])+/[a-z]*)\s*,").search(source_text, stop, end)
            kind, func = "validator", "validateBrief"
        elif "parseBriefJson(" in body:
            m_blocks = re.search(r"const blocks\s*=\s*", body)
            if m_blocks:
                parts, stop = lit.parse(start + m_blocks.end())
                raw = "".join(parts)
            else:
                m = re.search(r"parseBriefJson\(\s*", body)
                raw, stop = lit.parse(start + m.end())
            payload = _json_object_in(raw)
            if payload is None:
                continue  # a prose only response: there is no object to carry
            matcher, kind, func = None, "parse", "parseBriefJson"
        else:
            continue
        pid = f"v1_test_{number:02d}" + ("_parsed" if kind == "parse" else "")
        line = _line_of(source_text, start)
        entry = {
            "id": pid,
            "v1_test": number,
            "v1_line": line,
            "kind": kind,
            "v1_function": func,
            "v1_decision": "reject" if rejects else "accept",
            "v1_error_matcher": matcher.group(1) if (rejects and matcher) else None,
            "payload": payload,
            "provenance": source_name,
        }
        if kind == "parse":
            entry["note"] = (
                "The v1 test asserts parsing only; validateBrief would reject this object "
                "(ok with an empty bibliography)."
            )
        payloads.append(entry)
    for p, entry in enumerate(payloads):
        for spath, value in _iter_scalars(entry["payload"], f"payloads[{p}].payload"):
            if isinstance(value, str):
                copies.append({"dest": spath, "source": f"{source_name} test {entry['v1_test']} "
                               f"(line {entry['v1_line']}) {spath.split('.payload', 1)[1] or '(root)'}",
                               "value": value})
    doc = {
        "sc1b_version": 1,
        "provenance": {
            "source": f"{source_name} (relative to {config.ENV_V1_DIR})",
            "built_by": BUILT_BY,
            "method": "literal text parsed in Python; the v1 file was never imported or run",
            "reviewed": None,
        },
        "payloads": payloads,
    }
    return doc, {"file": SC1B_FILE, "copies": copies, "hashed": []}


def _iter_scalars(value: Any, path: str):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_scalars(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _iter_scalars(v, f"{path}[{i}]")
    else:
        yield path, value


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build_all(v1_dir: Path, staging: Path, demo_dir: Path = DEMO_ASSETS_DIR) -> list[Path]:
    """Write the SC2 cassettes and the SC1b payloads into staging."""
    if staging.resolve().is_relative_to((config.REPO_ROOT / "agent" / "tests").resolve()):
        raise ValueError("cassettes are staged first, never written into agent/tests")
    lookups = load_lookups(v1_dir)
    written = []
    for spec in SC2_CASES:
        cassette, log = build_case(spec, lookups, demo_dir)
        cost = pass_cost_usd(cassette)
        if cost > config.REPLAY_RUN_CAP_USD:
            raise ValueError(f"{spec.case}: one pass costs {cost:.4f} at replay prices, over the replay cap")
        path = staging / CASSETTES_SUBDIR / f"{spec.case}.json"
        _write_json(path, cassette)
        _write_json(staging / COPY_LOG_SUBDIR / f"{spec.case}.copies.json", log)
        written.append(path)
    doc, log = extract_sc1b((v1_dir / GUARDRAIL_FILE).read_text(encoding="utf-8"))
    path = staging / SC1B_FILE
    _write_json(path, doc)
    _write_json(staging / COPY_LOG_SUBDIR / (path.stem + ".copies.json"), log)
    written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    v1 = os.environ.get(config.ENV_V1_DIR)
    if not v1:
        print(f"build_cassettes: set {config.ENV_V1_DIR} to the v1 briefcase folder", file=sys.stderr)
        return 2
    written = build_all(Path(v1), config.STAGING_DIR)
    for path in written:
        print(f"{path} ({path.stat().st_size} bytes)")
    print("Next: python -m agent.replay.privacy_diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
