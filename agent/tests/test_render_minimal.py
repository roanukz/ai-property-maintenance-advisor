"""The Phase 2 minimal renderer: escaping, URL allowlist, no external loads.

The full SC10 suite (legacy byte identical render, reviewed v2 goldens) is
Phase 4. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import copy
import re
from html.parser import HTMLParser
from typing import Any

import pytest

from agent.render.brief_html import render_brief_html

HOSTILE = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'"
DASHES = (chr(0x2014), chr(0x2013))


def full_brief() -> dict[str, Any]:
    """A brief with every section present and a string in every field the page shows."""
    return {
        "status": "ok",
        "matched_identity": "matched",
        "observed_code": "FLO",
        "warranty_caution": {"text": "caution", "source_index": 0},
        "happened_before": {"matches": True, "summary": "summary", "record_id": "service:r1"},
        "try_first": [
            {"step": "safety step", "detail": "safety detail", "safety_flag": True, "source_index": 0},
            {"step": "step", "detail": "detail", "safety_flag": False, "source_index": 1},
        ],
        "candidates": [{"code": "FLO", "documented_meaning": "meaning", "documented_action": "action",
                        "who": "anyone", "why_shown": "why", "source_index": 0, "confirmed": True,
                        "evidence": "evidence"}],
        "upgrade_options": [{"successor_manufacturer": "maker", "successor_model": "successor",
                             "reason": "discontinued", "summary": "upgrade summary", "source_index": 1,
                             "evidence": "evidence"}],
        "warranty": {"age_statement": "age", "cautions": [{"text": "warranty caution", "source_index": 1}],
                     "verify": ["verify item"]},
        "maintenance_due": [{"task": "task", "interval": "interval", "source_index": 0, "evidence": "e",
                             "last_done_record_id": None, "due_date": "2026-10-01"}],
        "no_reliable_answer": None,
        "sources": [
            {"url": "https://example.com/a", "tier": "manufacturer", "title": "title a", "host": "example.com",
             "retrieved_at": "2026-09-18", "origin": "search"},
            {"url": "https://example.org/b", "tier": "dealer", "title": "title b", "host": "example.org",
             "retrieved_at": "2026-09-17", "origin": "extract"},
        ],
    }


def refusal_brief() -> dict[str, Any]:
    brief = full_brief()
    brief.update(status="no_reliable_answer", try_first=[], candidates=[], upgrade_options=[],
                 maintenance_due=[], no_reliable_answer={"searched": ["query one"], "found": ["found one"],
                                                         "why_insufficient": "why not"})
    return brief


IDENTITY = {"manufacturer": "maker name", "model": "model name", "serial": "serial no",
            "manufacture_date": "2014-06"}

# Fields the page must show, as (brief, path) pairs; paths walk the brief dict.
SHOWN = [
    (full_brief, ("matched_identity",)),
    (full_brief, ("observed_code",)),
    (full_brief, ("warranty_caution", "text")),
    (full_brief, ("happened_before", "summary")),
    (full_brief, ("try_first", 0, "step")),
    (full_brief, ("try_first", 0, "detail")),
    (full_brief, ("candidates", 0, "code")),
    (full_brief, ("candidates", 0, "documented_meaning")),
    (full_brief, ("candidates", 0, "documented_action")),
    (full_brief, ("candidates", 0, "why_shown")),
    (full_brief, ("upgrade_options", 0, "successor_manufacturer")),
    (full_brief, ("upgrade_options", 0, "successor_model")),
    (full_brief, ("upgrade_options", 0, "summary")),
    (full_brief, ("warranty", "age_statement")),
    (full_brief, ("warranty", "cautions", 0, "text")),
    (full_brief, ("warranty", "verify", 0)),
    (full_brief, ("maintenance_due", 0, "task")),
    (full_brief, ("maintenance_due", 0, "interval")),
    (full_brief, ("maintenance_due", 0, "due_date")),
    (full_brief, ("sources", 0, "title")),
    (full_brief, ("sources", 0, "host")),
    (full_brief, ("sources", 0, "retrieved_at")),
    (refusal_brief, ("no_reliable_answer", "searched", 0)),
    (refusal_brief, ("no_reliable_answer", "found", 0)),
    (refusal_brief, ("no_reliable_answer", "why_insufficient")),
]


def _set(obj: Any, path: tuple, value: Any) -> None:
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


def _string_paths(obj: Any, prefix: tuple = ()) -> list[tuple]:
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in _string_paths(v, prefix + (k,))]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in _string_paths(v, prefix + (i,))]
    return [prefix] if isinstance(obj, str) else []


def _render(brief: dict, **kw: Any) -> str:
    args = {"identity": IDENTITY, "symptom": "symptom", "generated_at": "2026-09-18"}
    args.update(kw)
    return render_brief_html(brief, **args)


def _escaped(marker: str) -> str:
    return (marker.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#x27;"))


@pytest.mark.parametrize("make,path", SHOWN, ids=[".".join(map(str, p)) for _, p in SHOWN])
def test_every_shown_string_field_is_escaped(make, path: tuple) -> None:
    """Mutations render_meaning_unescaped (one field without esc) and render_quote_false (quote=False)."""
    brief = make()
    marker = f"[{'.'.join(map(str, path))}]{HOSTILE}"
    _set(brief, path, marker)
    page = _render(brief)
    assert _escaped(marker) in page, "the field must be shown, fully escaped"
    assert "<script>" not in page and "<img" not in page and "onerror=alert" not in page.replace(
        "onerror=alert(2)&gt;", "")


@pytest.mark.parametrize("make", [full_brief, refusal_brief])
def test_every_string_leaf_is_safe_even_when_not_shown(make) -> None:
    """Mutation render_reason_unescaped: the upgrade reason (a leaf not in SHOWN) drops esc."""
    brief = make()
    for path in _string_paths(brief):
        if path[-1] == "url":
            continue
        mutated = copy.deepcopy(brief)
        _set(mutated, path, HOSTILE)
        page = _render(mutated)
        assert "<script>" not in page and "<img" not in page, path
        _Allowlist().check(page)


def test_untrusted_non_model_fields_are_escaped() -> None:
    """Mutation render_symptom_unescaped: the symptom line drops esc."""
    identity = {k: f"{k}{HOSTILE}" for k in IDENTITY}
    page = _render(refusal_brief(), identity=identity, symptom=f"symptom{HOSTILE}")
    for value in [*identity.values(), f"symptom{HOSTILE}"]:
        assert _escaped(value) in page
    budget = dict(refusal_brief(), status="budget_stopped", no_reliable_answer=None)
    page = _render(budget, stop_reason=f"reason{HOSTILE}", search_trail=[{"query": f"q{HOSTILE}"}])
    assert _escaped(f"reason{HOSTILE}") in page and _escaped(f"q{HOSTILE}") in page
    assert "<script>" not in page


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "//evil.example/x",
    "ftp://example.com/x",
])
def test_non_web_url_renders_as_text(url: str) -> None:
    """Mutation render_any_url_is_a_link: safe_url returns every string."""
    brief = full_brief()
    brief["sources"][0].update(url=url, title=None)
    page = _render(brief)
    hrefs = re.findall(r'href="([^"]*)"', page)
    assert all(h == "data:," or h.startswith(("https://", "http://")) for h in hrefs), hrefs
    assert _escaped(url) in page, "the URL is still shown, as text"
    assert "https://example.org/b" in hrefs


def test_hostile_web_url_stays_inside_its_attribute() -> None:
    """Mutation render_quote_false: a quote in the URL closes the href early."""
    brief = full_brief()
    brief["sources"][0]["url"] = 'https://example.com/"onmouseover="alert(1)'
    page = _render(brief)
    assert 'onmouseover="' not in page
    _Allowlist().check(page)


class _Allowlist(HTMLParser):
    """Parses the page and fails on anything that could load or run something."""

    TAGS = {"html", "head", "meta", "link", "title", "style", "body", "section", "div", "h1", "h2", "h3", "p",
            "strong", "ul", "ol", "li", "a", "span", "table", "thead", "tbody", "tr", "th", "td", "footer"}
    META = ({"charset"}, {"name", "content"}, {"http-equiv", "content"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.styles: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {k for k, _ in attrs}
        values = dict(attrs)
        if tag not in self.TAGS:
            self.problems.append(f"tag {tag}")
        if any(k.startswith("on") for k in names):
            self.problems.append(f"event handler on {tag}")
        if "style" in names:
            self.styles.append(values["style"] or "")
        if tag == "link" and values != {"rel": "icon", "href": "data:,"}:
            self.problems.append(f"link {values}")
        if tag == "meta":
            if names not in self.META:
                self.problems.append(f"meta {values}")
            if values.get("http-equiv") and values["http-equiv"].lower() != "content-security-policy":
                self.problems.append(f"meta http-equiv {values}")
            if values.get("name") and values["name"] not in ("viewport", "robots"):
                self.problems.append(f"meta name {values}")
        for key in ("src", "srcset", "action", "formaction", "background", "poster", "data"):
            if key in names:
                self.problems.append(f"{key} on {tag}")
        if "href" in names and tag == "a" and not re.match(r"^https?://", values["href"] or "", re.I):
            self.problems.append(f"a href {values['href']}")
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.styles.append(data)

    def check(self, page: str) -> None:
        self.feed(page)
        self.close()
        for css in self.styles:
            for banned in ("url(", "@import", "image-set(", "@font-face", "expression("):
                if banned in css.lower():
                    self.problems.append(f"css {banned}")
        assert self.problems == [], self.problems


@pytest.mark.parametrize("make", [full_brief, refusal_brief])
def test_page_loads_nothing_external(make) -> None:
    """Mutation render_stylesheet_link: a web font stylesheet link is added to the head."""
    page = _render(make())
    _Allowlist().check(page)
    assert '<meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;; ' \
           'style-src &#x27;unsafe-inline&#x27;">' in page
    assert '<link rel="icon" href="data:,">' in page


def test_sections_follow_the_v1_order() -> None:
    """Mutation render_warranty_before_candidates: the warranty section moves above the candidates."""
    page = _render(full_brief())
    headings = re.findall(r"<h2>(.*?)</h2>", page)
    assert headings == ["Warranty caution", "This has happened before", "Try this first",
                        "Documented candidates", "Upgrade options", "Warranty", "Maintenance due", "Sources"]
    refusal = re.findall(r"<h2>(.*?)</h2>", _render(refusal_brief()))
    assert refusal[:2] == ["No reliable answer", "Warranty caution"]
    safety = page.index("SAFETY: safety step")
    assert safety < page.index("<strong>step</strong>")


def test_refusal_and_budget_stop_footers_do_not_claim_reproduced_material() -> None:
    """Mutation render_one_footer: the ok footer is used for every status."""
    assert "reproduces documented material" in _render(full_brief())
    assert "reproduces documented material" not in _render(refusal_brief())
    budget = dict(refusal_brief(), status="budget_stopped", no_reliable_answer=None)
    page = _render(budget, stop_reason="run_cap", search_trail=[{"query": "q"}])
    assert "reproduces documented material" not in page
    assert "Stopped at a budget limit" in page and "No reliable answer" not in page


def test_placeholders_have_no_dashes_and_lists_mean_no_match() -> None:
    """Mutation render_list_happened_before: a list in happened_before renders as a match."""
    brief = full_brief()
    brief["candidates"][0]["code"] = None
    brief["happened_before"] = [{"matches": True, "summary": "listed summary"}]
    page = _render(brief, identity={"manufacturer": "maker", "model": "model", "serial": None,
                                    "manufacture_date": ""})
    assert "Serial not recorded &middot; Manufactured not recorded" in page
    assert "<td>no code &#10003;</td>" in page
    assert "This has happened before" not in page
    for dash in DASHES:
        assert dash not in page
    assert "<title>Service brief: maker model</title>" in page


def test_out_of_range_index_gets_no_label() -> None:
    """Mutation render_index_unchecked: _is_index accepts any int."""
    brief = full_brief()
    brief["try_first"][1]["source_index"] = 7
    brief["warranty"]["cautions"][0]["source_index"] = True
    page = _render(brief)
    assert "[8]" not in page
    assert page.count('class="ref"') == 5  # caution, safety step, candidate, upgrade, maintenance
