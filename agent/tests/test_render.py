"""SC10 and the v1 render rows (PLAN section 5 SC10 rows, section 6.1 tests 1, 2, 9, 10, 15, 16).

- The external load check is an allowlist over the parsed tree: a fixed set of
  elements and attributes; the only link is rel="icon" href="data:,"; meta is
  limited to charset, viewport, robots and the CSP; no on* attribute; no meta
  refresh or base; the only URL bearing attribute is a[href] matching
  ^https?:// (protocol relative // rejected); no url(, @import, image-set( or
  @font-face in a style element or style attribute.
- Escaping is checked in text and attribute context, over every string field
  found by walking the Pydantic models plus the untrusted non model fields.
- Legacy mode must equal the published briefs byte for byte; v2 mode, run
  through validate, must equal the goldens in fixtures/goldens/ (pending
  Roanuk's review at the Phase 4 gate; see the README there).

Recorded briefs come from src/fixtures.js (public). Everything else here is
synthetic, labeled as such, on example.com style URLs.

Regenerate the v2 goldens (only after a reviewed format change):
    .venv/bin/python -m agent.tests.test_render --write-goldens

Each test names the mutation(s) in mutations.toml that turn it red.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import types
import typing
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from agent import config
from agent.nodes.refuse import build_budget_stopped_brief
from agent.render.brief_html import CSP, esc, render_brief
from agent.render.property_page import render_property_page
from agent.rules.pipeline import run_rules
from agent.schemas import Brief
from agent.tests.helpers import split_sc1b_payload

REPO = config.REPO_ROOT
GOLDENS = Path(__file__).resolve().parent / "fixtures" / "goldens"
FIXTURES_JS = REPO / "src" / "fixtures.js"
RECORDED_DATE = "2026-08-18"
TODAY = date(2026, 8, 18)
V1_PROVENANCE = {"derived_from": "v1 lookup (src/fixtures.js recorded brief)"}
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)


# ---------------------------------------------------------------------------
# The allowlist over the parsed tree
# ---------------------------------------------------------------------------

ELEMENTS = {"html", "head", "meta", "link", "title", "style", "body", "section", "div", "h1", "h2", "h3",
            "p", "strong", "ul", "ol", "li", "a", "span", "table", "thead", "tbody", "tr", "th", "td",
            "footer"}
GLOBAL_ATTRS = {"class", "style"}
EXTRA_ATTRS = {"html": {"lang"}, "a": {"href"}}
META_FORMS = [
    {"charset": "utf-8"},
    {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    {"name": "robots", "content": "noindex"},
    {"http-equiv": "Content-Security-Policy", "content": CSP},
]
BANNED_CSS = ("url(", "@import", "image-set(", "@font-face", "expression(", "\\")
WEB_URL = re.compile(r"^https?://", re.IGNORECASE | re.ASCII)


class Tree(HTMLParser):
    """Parses a page and records every allowlist violation, plus the text and hrefs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.css: list[str] = []
        self.texts: list[str] = []
        self.attr_values: list[str] = []
        self.hrefs: list[str] = []
        self.links: list[dict[str, str | None]] = []
        self.metas: list[dict[str, str | None]] = []
        self.headings: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if len(values) != len(attrs):
            self.problems.append(f"duplicate attribute on {tag}")
        if tag not in ELEMENTS:
            self.problems.append(f"element {tag}")
        for name, value in attrs:
            self.attr_values.append(value or "")
            if name.startswith("on"):
                self.problems.append(f"event handler {name} on {tag}")
            if tag in ("meta", "link"):
                continue
            if name not in GLOBAL_ATTRS | EXTRA_ATTRS.get(tag, set()):
                self.problems.append(f"attribute {name} on {tag}")
            if name == "style":
                self.css.append(value or "")
        if tag == "link":
            self.links.append(values)
            if values != {"rel": "icon", "href": "data:,"}:
                self.problems.append(f"link {values}")
        if tag == "meta":
            self.metas.append(values)
            if values not in META_FORMS:
                self.problems.append(f"meta {values}")
        if tag == "a":
            href = values.get("href") or ""
            self.hrefs.append(href)
            if not WEB_URL.match(href):
                self.problems.append(f"a href {href!r}")
        self._stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        current = self._stack[-1] if self._stack else ""
        if current == "style":
            self.css.append(data)
        else:
            self.texts.append(data)
        if current == "h2":
            self.headings.append(data)

    def handle_comment(self, data: str) -> None:
        self.problems.append("comment")

    def handle_pi(self, data: str) -> None:
        self.problems.append("processing instruction")

    def unknown_decl(self, data: str) -> None:
        self.problems.append(f"declaration {data}")


def parse(page: str) -> Tree:
    tree = Tree()
    tree.feed(page)
    tree.close()
    for css in tree.css:
        for banned in BANNED_CSS:
            if banned in css.lower():
                tree.problems.append(f"css {banned}")
    return tree


def assert_self_contained(page: str, *, v2: bool = True) -> Tree:
    tree = parse(page)
    assert tree.problems == [], tree.problems
    assert page.startswith("<!doctype html>\n")
    if v2:
        assert tree.links == [{"rel": "icon", "href": "data:,"}]
        assert {"http-equiv": "Content-Security-Policy", "content": CSP} in tree.metas
    return tree


# ---------------------------------------------------------------------------
# Recorded briefs (public, src/fixtures.js) and the synthetic fixtures
# ---------------------------------------------------------------------------


def _reject_constant(name: str) -> None:
    raise ValueError(f"non JSON constant {name} in fixtures.js")


def recorded_cases() -> dict[str, dict]:
    """{case id: {identity, symptom, brief, share}} for the four recorded briefs."""
    text = FIXTURES_JS.read_text(encoding="utf-8")
    start = text.index("{", text.index("window.BRIEFCASE_FIXTURES"))
    cases = json.loads(text[start:text.rindex("}") + 1], parse_constant=_reject_constant)["cases"]
    return {c["id"]: c["brief"] for c in cases if c.get("brief")}


RECORDED = recorded_cases()
RECORDED_IDS = sorted(RECORDED)


def validated(payload: dict, *, identity: dict | None = None, observed_code: str | None = None,
              searched: list[str] | None = None, history_hits: list[dict] | None = None,
              registry: dict | None = None, retrieved_at: str | None = None,
              provenance: dict | None = None) -> dict:
    """A v1 shaped payload through the whole v2 rules pipeline (validate)."""
    draft, sources, _ = split_sc1b_payload(payload)
    if retrieved_at:
        for source in sources:
            source["retrieved_at"] = retrieved_at
    result = run_rules(
        draft, sources=sources, observed_code=observed_code, history_hits=history_hits or [],
        registry=registry, mode="replay", provenance=provenance, pass_kind="synthesize",
        identity=identity or {}, search_trail=[{"query": q, "tool": "search"} for q in searched or []],
        page_texts={}, today=TODAY,
    )
    assert result.errors == [], result.errors
    return result.brief


def recorded_through_validate(case: str) -> dict:
    rec = RECORDED[case]
    brief = rec["brief"]
    identity = {k: (v or None) for k, v in rec["identity"].items()}
    searched = (brief.get("no_reliable_answer") or {}).get("searched")
    return validated(brief, identity=identity, observed_code=brief.get("observed_code"), searched=searched,
                     retrieved_at=RECORDED_DATE, provenance=V1_PROVENANCE)


SYN_IDENTITY = {"manufacturer": "Synthetic Appliance Co.", "model": "SX-100 (synthetic)",
                "serial": "SYN-SX-000004", "manufacture_date": "2014-03"}
SYN_SOURCE = {"title": "SX-100 manual (synthetic example)", "url": "https://maker.example.com/sx-100/manual",
              "tier": "manufacturer"}
SYN_RECORD = {"kind": "service", "record_id": "service:svc-syn-1", "appliance_id": "appl-sx100",
              "date": "2026-01-12", "symptom": "Unit showed E4 and stopped (synthetic)", "observed_code": "E4",
              "work_done": "Cleared the drain line (synthetic)", "performed_by": "Synthetic technician",
              "synthetic": True}
SYN_MAINT = {"kind": "maintenance", "record_id": "maintenance:mnt-syn-1", "appliance_id": "appl-sx100",
             "task": "Cleaned the filter (synthetic)", "done_on": "2026-03-01", "synthetic": True}


def _syn_ok(**overrides: Any) -> dict:
    payload = {
        "status": "ok",
        "matched_identity": "SX-100 (synthetic), synthetic fixture",
        "observed_code": None,
        "warranty_caution": None,
        "happened_before": None,
        "try_first": [{"step": "Unplug the unit before opening the filter door", "detail": "Synthetic step.",
                       "safety_flag": True, "source_index": 0}],
        "candidates": [{"code": None, "documented_meaning": "Filter clogged (synthetic)",
                        "documented_action": "Clean the filter (synthetic)", "who": "anyone",
                        "why_shown": "Matches the symptom (synthetic)", "source_index": 0, "confirmed": False}],
        "warranty": {"cautions": [{"text": "Rental use may change coverage (synthetic).", "source_index": 0}],
                     "verify": ["Find the purchase paperwork (synthetic)."]},
        "no_reliable_answer": None,
        "sources": [dict(SYN_SOURCE)],
    }
    payload.update(overrides)
    return payload


def fixture_budget_stopped() -> tuple[dict, dict]:
    state = {
        "identity": SYN_IDENTITY, "observed_code": None, "stop_reason": "run_cap",
        "search_trail": [{"query": "SX-100 synthetic manual", "tool": "search"},
                         {"query": "SX-100 synthetic E4", "tool": "search"}],
        "sources": [{"url": "https://maker.example.com/sx-100/manual", "title": SYN_SOURCE["title"],
                     "host": "maker.example.com", "retrieved_at": "2026-08-18T10:00:00Z", "origin": "search"}],
    }
    brief = build_budget_stopped_brief(state)
    return brief, {"stop_reason": state["stop_reason"], "search_trail": state["search_trail"]}


def fixture_happened_before() -> tuple[dict, dict]:
    payload = _syn_ok(happened_before={"matches": True, "summary": "E4 was cleared by the drain line fix (synthetic).",
                                       "record_id": SYN_RECORD["record_id"]})
    brief = validated(payload, identity=SYN_IDENTITY, history_hits=[SYN_RECORD],
                      registry={"id": "appl-sx100"}, retrieved_at=RECORDED_DATE)
    return brief, {"records": [SYN_RECORD]}


def fixture_all_forum() -> tuple[dict, dict]:
    sources = [{"title": "SX-100 thread (synthetic)", "url": "https://forum.example.com/t/sx-100", "tier": "forum"},
               {"title": "SX-100 answers (synthetic)", "url": "https://forums.example.org/q/2", "tier": "forum"}]
    payload = _syn_ok(sources=sources)
    payload["candidates"][0]["source_index"] = 1
    return validated(payload, identity=SYN_IDENTITY, retrieved_at=RECORDED_DATE), {}


# Synthetic page text (not a real document, not written by any maker), served
# only at example.com style URLs, so rules 2 and 9 can verify the authorship
# quote, the upgrade evidence and the maintenance interval offline.
SYN_MANUAL_URL = SYN_SOURCE["url"]
SYN_NOTICE_URL = "https://maker.example.com/sx-100/notice"
SYN_AUTHORSHIP = "This synthetic page is published by Synthetic Appliance Co."
SYN_UPGRADE_QUOTE = "The SX-100 (synthetic) is discontinued and is replaced by the SX-200 (synthetic)."
SYN_MAINT_QUOTE = "Clean the filter every 3 months to keep the SX-100 (synthetic) running."
SYN_PAGES = {
    SYN_MANUAL_URL: ("SYNTHETIC PAGE TEXT for tests (not a real document, not written by any maker).\n"
                     f"{SYN_AUTHORSHIP}\nCare schedule\n{SYN_MAINT_QUOTE}\n"),
    SYN_NOTICE_URL: ("SYNTHETIC PAGE TEXT for tests (not a real document, not written by any maker).\n"
                     f"{SYN_AUTHORSHIP}\n{SYN_UPGRADE_QUOTE}\n"),
}
SYN_REGISTRY = {"id": "appl-sx100", "record_id": "appliance:appl-sx100", "install_date": "2014-03-11"}


def validated_with_entries(payload: dict, *, upgrade_options: list[dict] | None = None,
                           maintenance_due: list[dict] | None = None, maintenance_log: list[dict] | None = None,
                           source_meta: dict[int, dict] | None = None, pass_kind: str = "synthesize") -> dict:
    """A synthetic payload plus upgrade or maintenance entries through validate (rules 9 and 10 included).

    Every entry must survive: a golden that silently lost its section would
    not show the section it exists to show.
    """
    draft, sources, _ = split_sc1b_payload(payload)
    for index, meta in (source_meta or {}).items():
        sources[index].update(meta)
    for tier in draft["source_tiers"]:
        if sources[tier["source_index"]]["url"] in SYN_PAGES:
            tier["authorship_quote"] = SYN_AUTHORSHIP
    draft["upgrade_options"] = upgrade_options or []
    draft["maintenance_due"] = maintenance_due or []
    result = run_rules(
        draft, sources=sources, observed_code=None, history_hits=[], registry=SYN_REGISTRY, mode="replay",
        provenance=None, pass_kind=pass_kind, identity=SYN_IDENTITY, search_trail=[], page_texts=SYN_PAGES,
        today=TODAY, maintenance_log=maintenance_log,
    )
    assert result.errors == [], result.errors
    assert result.dropped == [], result.dropped
    return result.brief


def fixture_upgrade_options() -> tuple[dict, dict]:
    # The notice is the graph source upgrade_check registers; the option comes
    # back through validate on the "upgrade" pass (decision 27).
    payload = _syn_ok(
        sources=[dict(SYN_SOURCE),
                 {"title": "SX-100 discontinuation notice (synthetic)", "url": SYN_NOTICE_URL, "tier": "manufacturer"}],
    )
    upgrades = [{"successor_manufacturer": "Synthetic Appliance Co.", "successor_model": "SX-200 (synthetic)",
                 "reason": "discontinued", "summary": "The maker names the SX-200 as the replacement.",
                 "source_index": 1, "evidence": SYN_UPGRADE_QUOTE}]
    brief = validated_with_entries(
        payload, upgrade_options=upgrades, pass_kind="upgrade",
        source_meta={0: {"retrieved_at": RECORDED_DATE},
                     1: {"retrieved_at": "2026-07-01", "origin": "graph"}},
    )
    return brief, {}


def fixture_maintenance_due() -> tuple[dict, dict]:
    # As on a live run: the model never sees maintenance_log record IDs, so it
    # leaves last_done_record_id empty and code finds the row by its task.
    maintenance = [{"task": "Clean the filter (synthetic)", "interval": "every 3 months", "source_index": 0,
                    "evidence": SYN_MAINT_QUOTE, "last_done_record_id": ""}]
    brief = validated_with_entries(
        _syn_ok(), maintenance_due=maintenance, maintenance_log=[SYN_MAINT],
        source_meta={0: {"retrieved_at": RECORDED_DATE}},
    )
    # Code found the row and computed the due date from it (2026-03-01 plus 3 months).
    [item] = brief["maintenance_due"]
    assert (item["last_done_record_id"], item["last_done_on"], item["due_date"]) == (
        SYN_MAINT["record_id"], "2026-03-01", "2026-06-01")
    # The run context the render node builds: history_hits only (none here), never maintenance rows.
    return brief, {"records": []}


CONDITIONAL = {
    "budget_stopped": fixture_budget_stopped,
    "happened_before": fixture_happened_before,
    "upgrade_options": fixture_upgrade_options,
    "maintenance_due": fixture_maintenance_due,
    "all_forum": fixture_all_forum,
}


def v2_page(name: str) -> str:
    """The v2 page for a golden name: a recorded case through validate, or a conditional fixture."""
    if name in RECORDED:
        rec = RECORDED[name]
        return render_brief(recorded_through_validate(name), identity=rec["identity"], symptom=rec["symptom"],
                            generated_at=RECORDED_DATE)
    brief, run = CONDITIONAL[name]()
    return render_brief(brief, identity=SYN_IDENTITY, symptom="Unit stopped and shows E4 (synthetic)",
                        generated_at=RECORDED_DATE, run=run)


def legacy_page(case: str) -> str:
    rec = RECORDED[case]
    return render_brief(rec["brief"], identity=rec["identity"], symptom=rec["symptom"],
                        generated_at=RECORDED_DATE, legacy=True)


GOLDEN_NAMES = [*RECORDED_IDS, *CONDITIONAL]


def golden_path(name: str) -> Path:
    return GOLDENS / f"v2_{name}.html"


# ---------------------------------------------------------------------------
# SC10: external loads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_brief_loads_nothing_external(name: str) -> None:
    """Mutations: render_stylesheet_link (a stylesheet link in the v2 head),
    render_v2_web_font (an @font-face url() in the v2 CSS), render_v2_onload
    (an onload attribute on body), render_v2_style_url (style="background:url(...)"
    on a section), render_v2_no_csp (the CSP meta dropped), render_v2_base_tag
    (a base element), render_v2_meta_refresh (a meta refresh), render_v2_img_tag
    (an img in the header), render_legacy_script (a script element in the legacy
    head), render_protocol_relative_link (safe_url accepts //host links)."""
    page = v2_page(name)
    tree = assert_self_contained(page)
    assert all(WEB_URL.match(h) for h in tree.hrefs)
    if name in RECORDED:
        assert_self_contained(legacy_page(name), v2=False)
    # A protocol relative source URL must never become a link.
    brief, run = CONDITIONAL["all_forum"]() if name in RECORDED else CONDITIONAL[name]()
    brief = copy.deepcopy(brief)
    for source in brief["sources"]:
        source["url"] = "//evil.example/" + source["url"].split("/", 3)[-1]
    probe = render_brief(brief, identity=SYN_IDENTITY, symptom="s", generated_at=RECORDED_DATE, run=run)
    assert_self_contained(probe)


# ---------------------------------------------------------------------------
# SC10: escaping, over every string field of the Pydantic models
# ---------------------------------------------------------------------------


def _unwrap(annotation: Any) -> list[Any]:
    """The concrete types inside Optional, Union and Annotated."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _unwrap(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return [t for arg in typing.get_args(annotation) for t in _unwrap(arg)]
    return [annotation]


def model_string_paths(model: type[BaseModel], prefix: tuple = ()) -> list[tuple]:
    """Every path to a string (or Any) leaf, walking nested models and lists (index 0)."""
    out: list[tuple] = []
    for name, field in model.model_fields.items():
        for kind in _unwrap(field.annotation):
            if isinstance(kind, type) and issubclass(kind, BaseModel):
                out += model_string_paths(kind, prefix + (name,))
            elif typing.get_origin(kind) is list:
                (item,) = typing.get_args(kind) or (Any,)
                for inner in _unwrap(item):
                    if isinstance(inner, type) and issubclass(inner, BaseModel):
                        out += model_string_paths(inner, prefix + (name, 0))
                    elif inner is str:
                        out.append(prefix + (name, 0))
            elif kind is str or kind is Any or typing.get_origin(kind) is typing.Literal:
                out.append(prefix + (name,))
    return list(dict.fromkeys(out))


BRIEF_PATHS = model_string_paths(Brief)
# Untrusted fields that are not model output: identity, symptom, run context,
# service records (the page date), page titles and hosts are in BRIEF_PATHS.
CONTEXT_PATHS = [("identity", k) for k in ("manufacturer", "model", "serial", "manufacture_date")] + [
    ("symptom",), ("generated_at",), ("run", "records", 0, "date"), ("run", "records", 0, "record_id"),
    ("run", "stop_reason"), ("run", "search_trail", 0, "query"), ("budget_stop", "why_insufficient"),
]
# Paths drawn only on a budget stop page, which legacy (v1) mode cannot render.
BUDGET_PATHS = {("run", "stop_reason"), ("run", "search_trail"), ("budget_stop", "why_insufficient")}
# Fields a page need not show: enums shown only when valid, flags, a record ID
# that no longer matches a loaded record, candidate evidence, source origin.
NOT_SHOWN = {("status",), ("sources", 0, "tier"), ("sources", 0, "proposed_tier"), ("sources", 0, "origin"),
             ("candidates", 0, "who"), ("candidates", 0, "evidence"), ("happened_before", "record_id")}
# v2 only: legacy (v1) pages never showed these.
LEGACY_UNSHOWN_PREFIXES = ("upgrade_options", "maintenance_due", "run")
LEGACY_UNSHOWN = {("sources", 0, "host"), ("sources", 0, "retrieved_at"), ("warranty", "record_id"),
                  ("warranty", "terms")}


def test_walk_finds_every_model_string_field_used_below() -> None:
    """Mutation render_test_walk_skips_lists: the walk stops descending into lists of models."""
    for path in (("candidates", 0, "documented_meaning"), ("warranty", "verify", 0), ("sources", 0, "title"),
                 ("sources", 0, "host"), ("upgrade_options", 0, "evidence"), ("maintenance_due", 0, "due_date"),
                 ("no_reliable_answer", "searched", 0), ("warranty", "cautions", 0, "text"), ("matched_identity",),
                 ("candidates", 0, "code")):
        assert path in BRIEF_PATHS, path


def full_brief() -> dict:
    """An ok brief with every section, and a record for happened_before and maintenance."""
    return {
        "status": "ok",
        "matched_identity": "matched",
        "observed_code": "E4",
        "warranty_caution": {"text": "caution", "source_index": 0},
        "happened_before": {"matches": True, "summary": "summary", "record_id": "service:r1"},
        "try_first": [{"step": "safety step", "detail": "detail", "safety_flag": True, "source_index": 0}],
        "candidates": [{"code": "E4", "documented_meaning": "meaning", "documented_action": "action",
                        "who": "anyone", "why_shown": "why", "source_index": 0, "confirmed": True,
                        "evidence": "evidence"}],
        "upgrade_options": [{"successor_manufacturer": "maker", "successor_model": "successor",
                             "reason": "discontinued", "summary": "summary", "source_index": 0,
                             "evidence": "evidence"}],
        "warranty": {"age_statement": "age", "cautions": [{"text": "warranty caution", "source_index": 0}],
                     "verify": ["verify"], "record_id": "appliance:a1", "terms": "terms"},
        "maintenance_due": [{"task": "task", "interval": "interval", "source_index": 0, "evidence": "e",
                             "last_done_record_id": "maintenance:m1", "last_done_on": "2026-03-01",
                             "due_date": "2026-10-01"}],
        "no_reliable_answer": {"searched": ["query"], "found": ["found"], "why_insufficient": "why"},
        "sources": [{"url": "https://maker.example.com/manual", "tier": "manufacturer", "title": "title",
                     "host": "maker.example.com", "retrieved_at": "2026-09-18", "origin": "search",
                     "proposed_tier": "manufacturer"}],
    }


def _context() -> dict:
    return {
        "identity": {"manufacturer": "maker", "model": "model", "serial": "serial", "manufacture_date": "2014-06"},
        "symptom": "symptom",
        "generated_at": "2026-09-18",
        "run": {"records": [{"kind": "service", "record_id": "service:r1", "date": "2025-11-14"}],
                "stop_reason": "run_cap", "search_trail": [{"query": "trail query"}]},
    }


def _base_for(path: tuple) -> dict:
    brief = full_brief()
    if path[0] == "no_reliable_answer":
        brief["status"] = "no_reliable_answer"
    elif path[:2] in (("run", "stop_reason"), ("run", "search_trail")):
        brief.update(status="budget_stopped", no_reliable_answer=None)
    elif path[0] == "budget_stop":
        # The block build_budget_stopped_brief writes: code built today, escaped all the same.
        brief.update(status="budget_stopped",
                     no_reliable_answer={"searched": ["query"], "found": [], "why_insufficient": "why"})
    return brief


def _set(obj: Any, path: tuple, value: Any) -> None:
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


HOSTILE_PIECES = st.sampled_from([
    "<script>alert(1)</script>", "<img src=x onerror=alert(2)>", '"', "'", '" onmouseover="alert(3)',
    "' onfocus='alert(4)", "</td></tr></table><b>", "&lt;", "&", "<!--", "javascript:alert(5)", "<style>",
    "url(https://evil.example/x)", EM_DASH,
])
HOSTILE = st.lists(st.one_of(HOSTILE_PIECES, st.text(max_size=4)), min_size=1, max_size=6).map("".join).filter(
    lambda s: any(ch in s for ch in "<\"'"))
V1_TEST_10 = ['<img src=x onerror="alert(1)">', "<script>alert(1)</script>"]


def _render_with(path: tuple, value: str, *, legacy: bool) -> tuple[str, str]:
    """Render the full brief with `value` at `path`; returns (page, the value as placed)."""
    brief, ctx = _base_for(path), _context()
    placed = value
    if path == ("sources", 0, "url"):
        placed = "https://maker.example.com/" + value  # attribute context: stays a link
    if path[0] in ("identity", "symptom", "generated_at", "run"):
        if len(path) == 1:
            ctx[path[0]] = placed
        else:
            _set(ctx, path, placed)
        if path == ("run", "records", 0, "record_id"):
            brief["happened_before"]["record_id"] = placed  # the record happened_before cites
    elif path[0] == "budget_stop":
        _set(brief, ("no_reliable_answer", *path[1:]), placed)
    else:
        _set(brief, path, placed)
    page = render_brief(brief, identity=ctx["identity"], symptom=ctx["symptom"],
                        generated_at=ctx["generated_at"], legacy=legacy, run=ctx["run"])
    return page, placed


@pytest.mark.parametrize("path", BRIEF_PATHS + CONTEXT_PATHS, ids=lambda p: ".".join(map(str, p)))
@settings(max_examples=12, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
@given(value=HOSTILE)
@example(value=V1_TEST_10[0])
@example(value=V1_TEST_10[1])
def test_all_model_text_escaped(path: tuple, value: str) -> None:
    """Mutations: render_quote_false (esc without quote=True), render_meaning_unescaped
    (documented_meaning printed without esc), render_reason_unescaped (the upgrade
    reason unescaped), render_title_raw (source title unescaped), render_host_raw
    (host unescaped), render_summary_raw (upgrade summary unescaped),
    render_interval_raw (maintenance interval unescaped), render_record_date_raw
    (the record date unescaped), render_symptom_unescaped (the symptom unescaped),
    render_href_raw (the source href unescaped), render_legacy_no_quot (legacy
    esc stops escaping double quotes), render_budget_why_raw (the budget stop
    box prints why_insufficient raw), render_record_id_raw (the happened_before
    record ID printed raw), render_last_done_on_raw (the maintenance last done
    date printed raw)."""
    marker = "Q" + "_".join(map(str, path)) + "Q"
    value = marker + value
    for legacy in (False, True):
        if legacy and path[:2] in BUDGET_PATHS:
            continue  # v1 had no budget stop
        page, placed = _render_with(path, value, legacy=legacy)
        tree = parse(page)
        assert tree.problems == [], (legacy, tree.problems)
        hostile = "<\"" if legacy else "<\"'"
        if any(ch in placed for ch in hostile):
            assert placed not in page, f"{path} printed unescaped (legacy={legacy})"
        shown = path not in NOT_SHOWN and not (
            legacy and (path[0] in LEGACY_UNSHOWN_PREFIXES or path in LEGACY_UNSHOWN))
        if shown:
            decoded = tree.texts + tree.attr_values
            assert any(placed in chunk for chunk in decoded), f"{path} not shown intact (legacy={legacy})"
        if path == ("sources", 0, "url"):
            assert placed in tree.hrefs, "the web URL stays one intact href"


@pytest.mark.parametrize("value", [["<b>list</b>"], {"k": "<b>dict</b>"}], ids=["list", "dict"])
def test_non_string_values_render_without_error(value: Any) -> None:
    """Mutation render_reason_label_unhashable: the upgrade reason label lookup
    loses its str guard, so a list or dict reason raises TypeError. Validate
    never lets such a value through (the schema types these fields), so this
    is robustness only; escaping still holds."""
    for path in (("upgrade_options", 0, "reason"), ("maintenance_due", 0, "last_done_record_id"),
                 ("run", "records", 0, "record_id")):
        brief, ctx = full_brief(), _context()
        _set(ctx if path[0] == "run" else brief, path, copy.deepcopy(value))
        page = render_brief(brief, identity=ctx["identity"], symptom=ctx["symptom"],
                            generated_at=ctx["generated_at"], run=ctx["run"])
        tree = parse(page)
        assert tree.problems == [], (path, tree.problems)
        assert "<b>" not in page, path


# ---------------------------------------------------------------------------
# v1 tests 9, 15, 16: URLs, confirmed codes, warranty citations
# ---------------------------------------------------------------------------

BAD_URLS = ["javascript:alert(1)", "JavaScript:alert(document.cookie)", " javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "//evil.example/x",
            "ftp://example.com/x", "java\tscript:alert(1)"]


@pytest.mark.parametrize("url", BAD_URLS)
@pytest.mark.parametrize("legacy", [False, True], ids=["v2", "legacy"])
def test_non_web_url_renders_as_text(url: str, legacy: bool) -> None:
    """Mutations: render_safe_url_any_string (safe_url returns every string, so a
    javascript: URL becomes a link), render_protocol_relative_link (safe_url
    accepts //host) (v1 guardrail test 9, renderer half)."""
    brief = full_brief()
    brief["sources"][0].update(url=url, title=None, host=None)
    page = render_brief(brief, identity=_context()["identity"], symptom="s", generated_at="2026-09-18",
                        legacy=legacy, run=_context()["run"])
    tree = parse(page)
    assert tree.problems == [], tree.problems
    assert tree.hrefs == [], tree.hrefs
    assert any(url in text for text in tree.texts), "the URL is shown, as text"
    assert re.search(r"<td>(<span[^>]*>[^<]*</span>)? \[1\]</td>", page), "the citation stays a plain label"


def test_observed_code_confirmed_candidate_renders_confirmed() -> None:
    """Mutation render_confirmed_ignored: the renderer treats every row as not
    confirmed (v1 guardrail test 15, renderer half)."""
    payload = {"status": "ok", "matched_identity": "Sundance Optima 880", "observed_code": "FLO",
               "warranty_caution": None, "happened_before": None, "try_first": [],
               "candidates": [{"code": "FLO", "documented_meaning": "Flow switch open",
                               "documented_action": "Clean filter, check water level.", "who": "anyone",
                               "why_shown": "Observed on the panel.", "source_index": 0, "confirmed": True}],
               "warranty": None, "no_reliable_answer": None,
               "sources": [{"title": "Maker manual", "url": "https://manufacturer.example.com/m",
                            "tier": "manufacturer"}]}
    brief = validated(payload, observed_code="FLO")
    assert brief["candidates"][0]["confirmed"] is True
    identity = {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "", "manufacture_date": ""}
    page = render_brief(brief, identity=identity, symptom="panel shows FLO", generated_at="2026-09-18")
    assert '<tr class="confirmed">\n        <td>FLO &#10003;</td>' in page
    assert "Code <strong>FLO</strong> was reported on the unit&#x27;s display" not in page
    assert "Code <strong>FLO</strong> was reported on the unit's display" in page
    legacy = render_brief(brief, identity=identity, symptom="panel shows FLO", generated_at="2026-09-18",
                          legacy=True)
    assert f'<tr class="confirmed">\n        <td>FLO {chr(0x2713)}</td>' in legacy


def test_warranty_caution_keeps_citation() -> None:
    """Mutation render_warranty_caution_no_ref: warranty cautions lose their
    citation link (v1 guardrail test 16, renderer half)."""
    payload = {"status": "ok", "matched_identity": None, "observed_code": None, "warranty_caution": None,
               "happened_before": None, "try_first": [], "candidates": [],
               "warranty": {"age_statement": "About 12 years old.",
                            "cautions": [{"text": "Rental use may void coverage.", "source_index": 0}],
                            "verify": ["Purchase date"]},
               "no_reliable_answer": None,
               "sources": [{"title": "Maker manual", "url": "https://manufacturer.example.com/m",
                            "tier": "manufacturer"}]}
    brief = validated(payload)
    assert brief["warranty"]["cautions"][0]["source_index"] == 0
    for legacy in (False, True):
        page = render_brief(brief, identity={}, symptom="s", generated_at="2026-09-18", legacy=legacy)
        assert ('<li>Rental use may void coverage. <a class="ref" '
                'href="https://manufacturer.example.com/m">[1]</a></li>') in page


# ---------------------------------------------------------------------------
# v1 tests 1 and 2: the refusal page
# ---------------------------------------------------------------------------

ANSWER_HEADINGS = ("Try this first", "Documented candidates", "Upgrade options", "Maintenance due")


def test_nra_renders_banner_without_candidates_or_steps() -> None:
    """Mutations: render_steps_always (the steps section is drawn even when
    empty), render_upgrades_always (the upgrade section is drawn even when
    empty), render_maintenance_always (likewise maintenance), render_nra_box_dropped
    (the refusal box is not drawn), render_one_footer (the refusal gets the ok
    footer) (v1 guardrail test 1, renderer half)."""
    payload = {"status": "no_reliable_answer", "matched_identity": None, "observed_code": None,
               "warranty_caution": None, "happened_before": None, "try_first": [], "candidates": [],
               "warranty": None,
               "no_reliable_answer": {"searched": ['"Aquarest ZX-9000 Pro" manual'], "found": [],
                                      "why_insufficient": "No documentation exists for this model."},
               "sources": []}
    brief = validated(payload, searched=['"Aquarest ZX-9000 Pro" manual'])
    identity = {"manufacturer": "Aquarest", "model": "ZX-9000 Pro", "serial": "", "manufacture_date": ""}
    page = render_brief(brief, identity=identity, symptom="not heating", generated_at="2026-09-18")
    tree = assert_self_contained(page)
    assert tree.headings[0] == "No reliable answer"
    assert "That is the honest result: nothing below is guessed." in page
    assert "&quot;Aquarest ZX-9000 Pro&quot; manual" in page
    for heading in ANSWER_HEADINGS:
        assert heading not in tree.headings, heading
    assert "reproduces documented material" not in page
    legacy = render_brief(brief, identity=identity, symptom="not heating", generated_at="2026-09-18", legacy=True)
    assert "<h2>No reliable answer</h2>" in legacy and "Try this first" not in legacy


def test_refusal_html_omits_invented_candidate_text() -> None:
    """Mutation render_answer_on_refusal: the v2 renderer draws the answer
    sections whatever the status, so an unvalidated refusal shows the invented
    text (v1 guardrail test 2, renderer half; the clearing itself is SC3a in
    test_validate.py)."""
    payload = {"status": "no_reliable_answer", "matched_identity": "Aquarest ZX series", "observed_code": None,
               "warranty_caution": None, "happened_before": None,
               "try_first": [{"step": "Check the filter", "detail": "Generic hot tub advice.",
                              "safety_flag": False, "source_index": 0}],
               "candidates": [{"code": "FLO", "documented_meaning": "Invented meaning",
                               "documented_action": "Invented action", "who": "anyone",
                               "why_shown": "Guessed from the category", "source_index": 0, "confirmed": False}],
               "warranty": None,
               "no_reliable_answer": {"searched": ["aquarest zx-9000"], "found": [], "why_insufficient": "Nothing found."},
               "sources": [{"title": "Maker manual", "url": "https://manufacturer.example.com/m",
                            "tier": "manufacturer"}]}
    invented = ["Check the filter", "Generic hot tub advice.", "Invented meaning", "Invented action",
                "Guessed from the category", "Upgrade summary (synthetic)", "Invented interval"]
    unvalidated = copy.deepcopy(payload)
    unvalidated["upgrade_options"] = [{"successor_model": "X", "reason": "discontinued",
                                       "summary": "Upgrade summary (synthetic)", "source_index": 0}]
    unvalidated["maintenance_due"] = [{"task": "t", "interval": "Invented interval", "source_index": 0}]
    for brief in (validated(payload, searched=["aquarest zx-9000"]), unvalidated):
        page = render_brief(brief, identity={"manufacturer": "Aquarest", "model": "ZX-9000 Pro"}, symptom="s",
                            generated_at="2026-09-18")
        assert "<h2>No reliable answer</h2>" in page
        for text in invented:
            assert text not in page, text
    budget = dict(unvalidated, status="budget_stopped")
    page = render_brief(budget, identity={}, symptom="s", generated_at="2026-09-18", run={"stop_reason": "run_cap"})
    assert "Stopped at a budget limit" in page
    for text in invented:
        assert text not in page, text


# ---------------------------------------------------------------------------
# SC10 v1 format: legacy byte identity and the reviewed v2 goldens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", RECORDED_IDS)
def test_legacy_render_is_byte_identical(case: str) -> None:
    """Mutations: render_legacy_escapes_apostrophe (legacy esc also escapes '),
    render_warranty_before_candidates (section order changed), render_footer_words
    (footer text changed), render_legacy_no_blank_line (legacy drops v1's blank
    placeholder line), render_css_changed (one CSS color changed),
    render_legacy_no_quot (legacy stops escaping double quotes),
    render_confirmed_ignored (the confirmed row loses its class and check mark)."""
    published = (REPO / "briefs" / RECORDED[case]["share"]).read_bytes()
    assert legacy_page(case).encode("utf-8") == published


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_v2_render_matches_reviewed_golden(name: str) -> None:
    """Mutations: render_v2_upgrades_before_candidates (section order),
    render_footer_words (ok footer text changed), render_css_changed (one CSS
    color changed), render_v2_host_dropped
    (no host next to the badge), render_v2_retrieved_dropped (no per source
    retrieval date), render_v2_budget_footer (the budget stop footer claims
    reproduced material), render_v2_record_date_dropped (happened_before
    loses the record date), render_v2_dash_raw (a model dash printed raw)."""
    path = golden_path(name)
    assert path.is_file(), f"missing golden {path.name}; see fixtures/goldens/README.md"
    page = v2_page(name)
    assert EM_DASH not in page and EN_DASH not in page
    assert page == path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The property page
# ---------------------------------------------------------------------------

PAGE_HOSTILE = "<script>alert(1)</script>\"'<img src=x onerror=alert(2)>"


HOSTILE_SERVICE_ID = "svc-hostile" + PAGE_HOSTILE  # record_id is "service:" + id (schema CHECK)


def _hostile_seed(tmp_path: Path) -> Path:
    seed = json.loads(config.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    seed["properties"][0]["label"] += " P" + PAGE_HOSTILE
    for i, appliance in enumerate(seed["appliances"]):
        for key in ("manufacturer", "model", "serial", "notes", "category"):
            if appliance.get(key):
                appliance[key] += f" A{i}{key}" + PAGE_HOSTILE
        appliance["serial"] = f"A{i}serial" + PAGE_HOSTILE
        appliance["install_date"] = f"A{i}installed" + PAGE_HOSTILE
        appliance["warranty_terms"] = f"T{i}" + PAGE_HOSTILE
    for record in seed["service_records"]:
        for key in ("symptom", "observed_code", "work_done", "performed_by", "date"):
            record[key] = f"S{key}" + PAGE_HOSTILE
    # One more record whose id, and so its record_id, is hostile; it joins an existing appliance.
    seed["service_records"].append({
        "id": HOSTILE_SERVICE_ID, "appliance_id": "appl-optima880", "date": "2026-01-02",
        "symptom": "Synthetic hostile record", "observed_code": None, "work_done": None,
        "performed_by": None, "synthetic": True})
    for entry in seed["maintenance_log"]:
        entry["task"] += " Mtask" + PAGE_HOSTILE
    path = tmp_path / "hostile_seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    return path


# Not an ISO date, so the page prints it whole rather than cutting it to its first 10 characters.
HOSTILE_RETRIEVED = "R" + PAGE_HOSTILE
HOSTILE_URL = "https://maker.example.com/notice" + PAGE_HOSTILE


def _graph_with_successor(maker: str, model: str, successor: str, url: str, evidence: str,
                          retrieved_at: str = "2026-07-01"):
    from agent.kg import SUPERSEDED_BY, KnowledgeGraph, hash_evidence

    graph = KnowledgeGraph()
    src = graph.add_model(maker, model)
    dst = graph.add_model(maker, successor)
    graph.add_source(url, host="maker.example.com", title="Notice (synthetic)" + PAGE_HOSTILE,
                     retrieved_at=retrieved_at, text_sha256=None, tier="manufacturer")
    graph.add_edge(SUPERSEDED_BY, src, dst, source_url=url, retrieved_at=retrieved_at, evidence=evidence,
                   evidence_sha256=hash_evidence(evidence), brief_run_id="run-syn", validated_at="2026-07-01")
    return graph


def test_property_page_self_contained_escaped_and_labeled_synthetic(tmp_path: Path) -> None:
    """Mutations: property_page_synthetic_label_dropped (the synthetic notice
    removed), property_page_stylesheet_link (an external stylesheet link),
    property_page_symptom_raw (a service record symptom unescaped),
    property_page_label_raw (the property label unescaped in the title),
    property_page_successor_dropped (verified successors left off the page),
    property_page_cell_quote_false (table cells escaped without quote=True),
    property_page_href_raw (the successor source URL printed raw in href),
    property_page_interval_raw (a maintenance due interval printed raw),
    property_page_retrieved_raw (the edge retrieval date printed raw),
    property_page_record_id_raw (a service record ID printed raw)."""
    from agent.registry import open_registry

    clean = open_registry(tmp_path / "clean.sqlite")
    for prop in clean.list_properties():
        page = render_property_page(prop["id"], clean, generated_at="2026-09-18")
        tree = assert_self_contained(page)
        assert "Synthetic data" in tree.headings
        assert "Every record on this page is synthetic test data" in page
        for appliance in clean.list_appliances(prop["id"]):
            assert esc(appliance["model"]) in page

    hostile = open_registry(tmp_path / "hostile.sqlite", seed=_hostile_seed(tmp_path))
    optima = hostile.get_appliance("appl-optima880")
    evidence = f"The {optima['model']} is replaced by the Optima 990 (synthetic){PAGE_HOSTILE}."
    graph = _graph_with_successor(optima["manufacturer"], optima["model"], "Optima 990 (synthetic)",
                                  HOSTILE_URL, evidence, retrieved_at=HOSTILE_RETRIEVED)
    due = {"appl-optima880": [{"task": "Clean the filter (synthetic)", "interval": "Iv" + PAGE_HOSTILE,
                               "due_date": "Dd" + PAGE_HOSTILE, "source_index": 0}]}
    for prop in hostile.list_properties():
        page = render_property_page(prop["id"], hostile, graph=graph, generated_at="2026-09-18",
                                    maintenance_due=due)
        tree = assert_self_contained(page)
        assert "Synthetic data" in tree.headings
        assert PAGE_HOSTILE not in page and "<script>" not in page
        assert esc(PAGE_HOSTILE) in page
        assert any(prop["label"] in t for t in tree.texts), "the label is shown intact, as text"
    page = render_property_page("prop-a", hostile, graph=graph, generated_at="2026-09-18", maintenance_due=due)
    # Each field on its own, in text and attribute context: escaped, never raw.
    fields = [HOSTILE_RETRIEVED, HOSTILE_URL, due["appl-optima880"][0]["interval"],
              due["appl-optima880"][0]["due_date"]]
    for appliance in hostile.list_appliances("prop-a"):
        fields += [appliance["serial"], appliance["install_date"]]
    records = hostile.service_history("appl-optima880")
    assert "service:" + HOSTILE_SERVICE_ID in [r["record_id"] for r in records]
    for record in records:
        fields += [record[k] for k in ("date", "observed_code", "work_done", "performed_by", "record_id",
                                       "symptom") if PAGE_HOSTILE in (record.get(k) or "")]
    assert len(fields) == 4 + 2 * len(hostile.list_appliances("prop-a")) + 5 + 1, fields
    for value in fields:
        assert esc(value) in page, value
        assert value not in page, value
    tree = parse(page)
    assert HOSTILE_URL in tree.hrefs, "the successor source URL stays one intact href"
    assert tree.problems == [], tree.problems
    with pytest.raises(KeyError):
        render_property_page("prop-missing", hostile, generated_at="2026-09-18")


# ---------------------------------------------------------------------------
# Writing the goldens (run by hand, never by pytest)
# ---------------------------------------------------------------------------


def write_goldens() -> list[Path]:
    GOLDENS.mkdir(parents=True, exist_ok=True)
    written = []
    for name in GOLDEN_NAMES:
        path = golden_path(name)
        path.write_text(v2_page(name), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-goldens"]:
        raise SystemExit("usage: python -m agent.tests.test_render --write-goldens")
    for p in write_goldens():
        print(p.relative_to(REPO))
