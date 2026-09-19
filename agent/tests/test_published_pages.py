"""The published pages stay byte for byte as they are (PLAN section 12).

These tests only read index.html, tool.html, src/, briefs/ and demo-assets/.

Mutations that turn these red:
- editing one byte of any published file, or adding or deleting a file in
  those paths: test_published_files_unchanged fails.
- adding an absolute or protocol relative load to a published page (for
  example <img src="https://example.com/x.png"> in a brief, at any depth
  under briefs/ such as the v2 demo's briefs/v2/, or
  <link rel="stylesheet" href="//cdn.example.com/a.css"> in tool.html), a CSS
  url() or @import to another origin, or a fetch(), XMLHttpRequest, WebSocket
  or sendBeacon call in src/demo.js or src/fixtures.js, or an off origin load
  in src/demo.css: the demo test lists it.
- the tag, CSS or script checks removed from the scanner:
  test_scanner_catches_each_kind fails.
"""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from agent import config

ROOT = config.REPO_ROOT
MANIFEST = Path(__file__).parent / "fixtures" / "published_manifest.json"
# Finder litter; not part of the published site and never committed.
IGNORED_NAMES = {".DS_Store"}


def _published_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for name in config.PUBLISHED_PATHS:
        path = ROOT / name
        paths = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for p in paths:
            if p.name in IGNORED_NAMES:
                continue
            files[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files


def test_published_files_unchanged() -> None:
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    actual = _published_files()
    assert sorted(actual) == sorted(expected), "published file set changed"
    changed = [name for name in expected if actual[name] != expected[name]]
    assert not changed, f"published files changed: {changed}"


# ---------------------------------------------------------------------------
# No off origin loads and no network calls in the demo
# ---------------------------------------------------------------------------

LOAD_ATTRS = {
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "iframe": ("src",),
    "frame": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "input": ("src",),
    "link": ("href",),
}
# <link> rels that name a page rather than load it.
NON_LOADING_RELS = {"canonical", "alternate", "author", "license", "me", "help"}
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
CSS_URL_RE = re.compile(r"""url\(\s*["']?([^"')\s]+)""", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?["']?([^"');\s]+)""", re.IGNORECASE)
NETWORK_API_RE = re.compile(
    r"\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|\bimport\s*\(|importScripts"
)


def is_off_origin(url: str) -> bool:
    """True for an absolute or protocol relative URL; data: URIs stay on the page."""
    url = url.strip()
    if url.startswith("//"):
        return True
    return bool(SCHEME_RE.match(url)) and not url.lower().startswith("data:")


def _srcset_urls(value: str) -> list[str]:
    return [part.strip().split()[0] for part in value.split(",") if part.strip()]


def css_problems(css: str) -> list[str]:
    urls = CSS_URL_RE.findall(css) + CSS_IMPORT_RE.findall(css)
    return [f"css load {u}" for u in urls if is_off_origin(u)]


def script_problems(js: str) -> list[str]:
    return [f"network call {m.group().strip()}" for m in NETWORK_API_RE.finditer(js)]


class _PageScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.local_scripts: list[str] = []
        self.local_styles: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        if tag == "base":
            self.problems.append("<base> changes where relative links resolve")
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.problems.append("meta refresh")
        if "style" in values:
            self.problems += css_problems(values["style"])
        rels = set(values.get("rel", "").lower().split())
        for attr in LOAD_ATTRS.get(tag, ()):
            if attr not in values:
                continue
            if tag == "link" and rels and rels <= NON_LOADING_RELS:
                continue
            urls = _srcset_urls(values[attr]) if attr == "srcset" else [values[attr]]
            for url in urls:
                if is_off_origin(url):
                    self.problems.append(f"<{tag} {attr}={url}>")
                elif tag == "script":
                    self.local_scripts.append(url)
                elif tag == "link" and "stylesheet" in rels:
                    self.local_styles.append(url)
        if tag in ("script", "style"):
            self._in = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._in:
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in == "script":
            self.problems += script_problems(data)
        elif self._in == "style":
            self.problems += css_problems(data)


def page_problems(path: Path) -> list[str]:
    """Scan an HTML page and the local scripts and stylesheets it loads."""
    scanner = _PageScanner()
    scanner.feed(path.read_text(encoding="utf-8"))
    problems = [f"{path.name}: {p}" for p in scanner.problems]
    for rel in scanner.local_scripts:
        js = path.parent / rel
        problems += [f"{rel}: {p}" for p in script_problems(js.read_text(encoding="utf-8"))]
    for rel in scanner.local_styles:
        problems += [f"{rel}: {p}" for p in stylesheet_problems(path.parent / rel)]
    return problems


def stylesheet_problems(path: Path, seen: set[Path] | None = None) -> list[str]:
    """Scan a stylesheet and every local stylesheet it @imports."""
    seen = seen if seen is not None else set()
    if path in seen:
        return []
    seen.add(path)
    css = path.read_text(encoding="utf-8")
    problems = css_problems(css)
    for url in CSS_IMPORT_RE.findall(css):
        if not is_off_origin(url):
            problems += stylesheet_problems((path.parent / url).resolve(), seen)
    return problems


def test_demo_makes_no_off_origin_or_script_requests() -> None:
    # briefs/**: v1's four briefs sit in briefs/, the v2 demo's in briefs/v2/.
    pages = [ROOT / "tool.html", ROOT / "index.html", *sorted((ROOT / "briefs").rglob("*.html"))]
    assert any(p.parent.name == "v2" for p in pages), "expected the v2 demo's briefs in briefs/v2/"
    problems: list[str] = []
    for page in pages:
        problems += page_problems(page)
    # The demo's script, data and styles are loaded by tool.html, but scan them
    # directly too so a change to a script or link tag cannot hide them.
    for rel in ("src/demo.js", "src/fixtures.js"):
        problems += [f"{rel}: {p}" for p in script_problems((ROOT / rel).read_text(encoding="utf-8"))]
    problems += [f"src/demo.css: {p}" for p in stylesheet_problems(ROOT / "src" / "demo.css")]
    assert not problems, "\n".join(problems)

    # The demo sets img.src and iframe.src from the fixtures; both must be local.
    fixtures = (ROOT / "src" / "fixtures.js").read_text(encoding="utf-8")
    loaded = re.findall(r'"(?:plate|share)"\s*:\s*"([^"]*)"', fixtures)
    assert loaded, "expected plate and share entries in src/fixtures.js"
    assert not [u for u in loaded if is_off_origin(u)]


def test_scanner_catches_each_kind(tmp_path: Path) -> None:
    (tmp_path / "ok.js").write_text("var a = 1;\n", encoding="utf-8")
    (tmp_path / "bad.js").write_text("navigator.sendBeacon('/x');\n", encoding="utf-8")
    (tmp_path / "far.css").write_text("@import url('https://cdn.example.com/a.css');\n", encoding="utf-8")
    (tmp_path / "near.css").write_text("@import './far.css';\n", encoding="utf-8")
    page = tmp_path / "page.html"
    page.write_text(
        """<html><head>
        <link rel="stylesheet" href="//cdn.example.com/x.css">
        <link rel="stylesheet" href="near.css">
        <link rel="icon" href="data:image/svg+xml,%3Csvg%3E">
        <link rel="canonical" href="https://example.com/page">
        <script src="ok.js"></script><script src="bad.js"></script>
        <style>body { background: url(https://example.com/bg.png); }</style>
        </head><body>
        <a href="https://example.com/allowed">a plain link is fine</a>
        <img src="demo-assets/local.jpg" srcset="a.jpg 1x, https://example.com/b.jpg 2x">
        <iframe src="https://example.com/frame"></iframe>
        <div style="background-image: url('//example.com/c.png')"></div>
        <script>fetch('/api'); new XMLHttpRequest(); new WebSocket('wss://x');</script>
        </body></html>""",
        encoding="utf-8",
    )
    problems = "\n".join(page_problems(page))
    for expected in (
        "<link href=//cdn.example.com/x.css>",
        "cdn.example.com/a.css",
        "sendBeacon",
        "https://example.com/bg.png",
        "https://example.com/b.jpg",
        "<iframe src=https://example.com/frame>",
        "//example.com/c.png",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
    ):
        assert expected in problems, (expected, problems)
    for allowed in ("example.com/allowed", "example.com/page", "data:image", "local.jpg", "ok.js:"):
        assert allowed not in problems, (allowed, problems)
