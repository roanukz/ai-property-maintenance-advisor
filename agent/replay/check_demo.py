"""Offline checks on a built v2 demo folder (DEMO_V2_DESIGN.md section 9).

    .venv/bin/python -m agent.replay.check_demo <built folder>

Run it on the builder's --preview folder before installing, and on the repo
root after. Reads only. Runs no network call and no live command. Exit 0 when
every check passes. A missing denylist is a failure, not a skip.

1. The builder's own scan (keys, paths, denylist, project words, dashes per field,
   network calls, off origin loads) over everything in the folder.
2. agent/tests/test_published_pages.py's page scan over tool.html and every brief
   in briefs/v2, its script scan over src/demo.js, and its local-path check on the
   fixtures' plate and share entries.
3. Labels: a stand-in build carries the stand-in label in the fixtures' first line,
   the page title and banner, noindex, no og: tags, and briefs/v2/STAND-IN.txt; a
   rerun build carries none of them.
4. The brief frame is sandboxed before it loads: in src/demo.js the sandbox
   attribute is set before src, and the frame is attached after both.
5. The fixtures parse, every case has a takeaway, and no estimate reaches the page.
6. A case run against synthetic property records says so on its tab, its header,
   its records step, its numbers and its brief caption (the fixtures carry the
   label and src/demo.js shows it in each place).
7. A rerun build names no superseded build or run anywhere.
8. The safety check is shown: every live case whose brief has steps to try first
   carries the run record's safety section with one row per step, every flagged
   row names the layer that raised it, a failed check carries the brief's notice
   line (and the brief file has it), and src/demo.js draws the safety step.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from agent import config
from agent.replay import build_demo, privacy_diff
from agent.tests import test_published_pages as tpp


def check(site: Path, denylist: list[str] | None = None) -> list[str]:
    """Every problem found in the built folder; empty when it passes.

    `denylist` defaults to the local denylist in the staging folder; when that
    file is missing the check reports it as a problem.
    """
    problems: list[str] = []
    repo_root = Path(config.REPO_ROOT)
    if denylist is None:
        path = Path(config.STAGING_DIR) / privacy_diff.DENYLIST_FILE
        if not path.is_file():
            problems.append(f"privacy: no denylist at {privacy_diff.DENYLIST_FILE} in the staging folder")
            denylist = []
        else:
            denylist = privacy_diff.load_denylist(path)

    # 1. the builder's scan
    files = site_files(site)
    missing = [rel for rel in SITE_FILES if not (site / rel).is_file()]
    problems += [f"missing: {rel}" for rel in missing]
    if missing:
        return problems
    problems += [f"scan: {h}" for h in build_demo.scan_output(site, denylist, repo_root, only=files)]

    # 2. the published pages test's scan
    pages = [site / "tool.html", *sorted((site / "briefs" / "v2").glob("*.html"))]
    for page in pages:
        problems += [f"published pages: {p}" for p in tpp.page_problems(page)]
    demo_js = (site / "src" / "demo.js").read_text(encoding="utf-8")
    problems += [f"published pages: src/demo.js: {p}" for p in tpp.script_problems(demo_js)]
    fixtures = (site / "src" / "fixtures.js").read_text(encoding="utf-8")
    loaded = re.findall(r'"(?:plate|share)"\s*:\s*"([^"]*)"', fixtures)
    if not loaded:
        problems.append("published pages: no plate or share entries in src/fixtures.js")
    problems += [f"published pages: off origin {u}" for u in loaded if tpp.is_off_origin(u)]

    # 3. labels
    demo = build_demo.parse_fixtures(fixtures)
    page = (site / "tool.html").read_text(encoding="utf-8")
    label = build_demo.STAND_IN_LABEL
    marker = site / "briefs" / "v2" / "STAND-IN.txt"
    if demo.get("data_status") != "rerun":
        if not fixtures.startswith(f"// {label}\n"):
            problems.append("label: fixtures.js does not open with the stand-in label")
        title = re.search(r"<title>(.*?)</title>", page, re.S)
        if not title or label not in title.group(1):
            problems.append("label: the page title lacks the stand-in label")
        if 'class="standin-bar"' not in page:
            problems.append("label: the page has no stand-in banner")
        if 'name="robots" content="noindex"' not in page:
            problems.append("label: the stand-in page is not noindex")
        if 'property="og:' in page:
            problems.append("label: the stand-in page carries og: tags")
        if not marker.is_file() or label not in marker.read_text(encoding="utf-8"):
            problems.append("label: briefs/v2/STAND-IN.txt is missing or unlabeled")
        if "stand-in data from a superseded build, not a result" not in demo_js:
            problems.append("label: demo.js does not label the brief as stand-in")
    else:
        if label in page or label in fixtures or marker.exists():
            problems.append("label: a rerun build still carries the stand-in label")

    # 4. sandbox before src, frame attached after both
    body = demo_js[demo_js.index("function stepBrief"):demo_js.index("function stepNumbers")]
    i_sandbox = body.find('frame.setAttribute("sandbox"')
    i_src = body.find("frame.src =")
    i_attach = body.find("add(fig, frame)")
    if not (0 <= i_sandbox < i_src < i_attach):
        problems.append("sandbox: in stepBrief the sandbox must be set before src, and the frame attached last")
    if re.search(r'add\([^)]*el\("iframe"', body):
        problems.append("sandbox: the iframe is attached to the page as it is created")

    # 5. fixtures content
    for case in demo.get("cases") or []:
        if not case.get("takeaway"):
            problems.append(f"fixtures: case {case.get('id')} has no takeaway")
        if "estimate" in (case.get("numbers") or {}):
            problems.append(f"fixtures: case {case.get('id')} still carries an estimate")
    if "run_cap_usd" in (demo.get("build") or {}):
        problems.append("fixtures: build.run_cap_usd is back")

    # 6. synthetic property records are labeled wherever the case appears
    for case in demo.get("cases") or []:
        if case.get("role") in build_demo.SYNTHETIC_RECORDS_ROLES or case.get("records"):
            if case.get("records_label") != build_demo.SYNTHETIC_RECORDS_LABEL:
                problems.append(f"label: case {case.get('id')} uses synthetic records without the label")
            if (case.get("records") or {}).get("label") != build_demo.SYNTHETIC_RECORDS_LABEL:
                problems.append(f"label: case {case.get('id')}'s records step lacks the label")
    if any(c.get("records_label") for c in demo.get("cases") or []):
        for where, needle in (("tab", 'add(btn, el("span", "case-kind", " (" + c.records_label'),
                              ("header", 'chip("chip-synthetic", c.records_label)'),
                              ("records step", 'chip("chip-synthetic", r.label)'),
                              ("brief caption", "c.records_label.toLowerCase()"),
                              ("glance table", 'el("span", "case-kind", "(" + c.records_label')):
            if needle not in demo_js:
                problems.append(f"label: src/demo.js does not show the synthetic records label on the {where}")

    # 8. the safety check, from the run record, wherever a brief has steps to try first
    problems += safety_problems(site, demo, demo_js)

    # 7. no superseded build or run named anywhere in a rerun build
    if demo.get("data_status") == "rerun":
        forbidden = set(build_demo.KNOWN_SUPERSEDED_BUILDS)
        if Path(config.DATA_DIR).is_dir():
            forbidden |= build_demo.superseded_ids(Path(config.DATA_DIR))
        problems += [f"superseded: {h}" for h in build_demo.superseded_hits(site, forbidden, files)]
    return problems


def safety_problems(site: Path, demo: dict, demo_js: str) -> list[str]:
    """Check 8: the safety display is present and says only what the run recorded."""
    from agent.render.brief_html import SAFETY_NOTICE

    problems: list[str] = []
    shown = False
    for case in demo.get("cases") or []:
        steps = (case.get("outcome") or {}).get("try_first_steps") or 0
        safety = case.get("safety")
        if steps and not safety:
            problems.append(f"safety: case {case.get('id')} has {steps} steps to try first but no safety check shown")
            continue
        if not safety:
            continue
        shown = True
        rows = safety.get("steps") or []
        if steps and len(rows) != steps:
            problems.append(f"safety: case {case.get('id')} shows {len(rows)} safety rows for {steps} steps to try first")
        for i, row in enumerate(rows):
            if row.get("flag") and row.get("raised_by") not in build_demo.SAFETY_LAYER_NAMES:
                problems.append(f"safety: case {case.get('id')} step {i + 1} is flagged with no layer named")
        if safety.get("notice") not in (None, SAFETY_NOTICE):
            problems.append(f"safety: case {case.get('id')}'s notice line is not the brief's notice line")
        share = (case.get("brief") or {}).get("share")
        if safety.get("notice") and share and (site / share).is_file() \
                and SAFETY_NOTICE not in (site / share).read_text(encoding="utf-8"):
            problems.append(f"safety: case {case.get('id')} shows the notice line but its brief file lacks it")
    if shown:
        for needle in ("function stepSafety", "stepSafety(c), stepBrief(c)", "Jev raised a flag the writer left off"):
            if needle not in demo_js:
                problems.append(f"safety: src/demo.js does not draw the safety check ({needle!r} missing)")
    return problems


def site_files(site: Path) -> list[str]:
    """The demo's own files in a site folder: SITE_FILES plus everything in briefs/v2."""
    briefs = site / "briefs" / "v2"
    extra = sorted(p.relative_to(site).as_posix() for p in briefs.rglob("*") if p.is_file()) if briefs.is_dir() else []
    return list(SITE_FILES) + extra


# What the installed demo is made of, relative to the site root. The check reads
# only these (and briefs/v2), so it can run on the repo root after install.
SITE_FILES = ("tool.html", "src/demo.js", "src/demo.css", "src/fixtures.js")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    problems = check(Path(argv[0]).resolve())
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
