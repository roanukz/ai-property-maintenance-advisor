"""Build the recorded v2 demo's data from recorded live runs (DEMO_V2_DESIGN.md).

    .venv/bin/python -m agent.replay.build_demo --selection agent/replay/demo_selection.json \
        --out <dir> [--stand-in] [--preview] [--src-dir src]

Install (after check_demo passes on <dir>): copy tool.html, src/fixtures.js and
briefs/v2/ from <dir> into the repo, with src/demo.js and src/demo.css. Never
install build_report.json or README.txt.

Reads the live data folder (config.DATA_DIR) read only and writes only under
--out:

    <out>/src/fixtures.js          window.ADVISOR_DEMO, the page's only data
    <out>/briefs/v2/<run>.html     each selected run's brief, byte for byte
    <out>/briefs/v2/STAND-IN.txt   stand-in builds only: the label the verbatim briefs cannot carry
    <out>/tool.html                the page template, with the stand-in stamps applied
    <out>/build_report.json        what was read, what was written (with data_status), every warning

With --preview it also copies demo.js and demo.css (from --src-dir), the repo's
tokens.css and plate photos, and a README, so the folder can be served alone.

Rules this file keeps:

* It never calls a model, Tavily or any network, runs no live command, never
  opens .env, and opens the ledger with sqlite's read only URI form.
* It copies named fields only (an allowlist), so raw page text, search
  snippets, the research model's narration, local paths, ledger notes and
  eval scaffolding never reach the output.
* It refuses to build unless every selected and also_count run names the
  current build (build_info.build_id(), which leaves out this tooling and
  agent/safety_eval/) in its own records (the run record's build_id, written by
  agent/build_info.py, or else its eval record's build_id; when both exist they
  must agree), unless --stand-in is passed, which
  stamps "STAND-IN DATA FROM A SUPERSEDED BUILD, DO NOT PUBLISH" on the page
  banner, the page title, the first line of the fixtures file and the brief
  folder. Outside --stand-in every disagreement between records is a failure,
  including a memory fact stored by a run that is not on the current build.
* It shows no cost estimate: no run records the planner's estimate, and one
  computed here would come from this build's config, not from the run.
* It writes into a fresh folder next to --out, runs the privacy scanner's key
  and pattern checks (agent.replay.privacy_diff), the dash rule and the
  network checks there, and moves the files into --out only when all pass.
  A missing denylist is a failure. Nothing flagged ever lands in --out.

The fixtures file keeps the name src/fixtures.js and the "plate" and "share"
keys because agent/tests/test_published_pages.py reads exactly those.

Standard library plus the agent package only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent import build_info, config
from agent.replay import privacy_diff

BUILDER_VERSION = "build_demo.py 4"
STAND_IN_LABEL = "STAND-IN DATA FROM A SUPERSEDED BUILD, DO NOT PUBLISH"
REPLAY_LABEL = "Replay of synthetic property records, no live call"
# Builds already known to be superseded. The current build check below would
# refuse them anyway; this keeps them refused even if agent/ were rolled back.
KNOWN_SUPERSEDED_BUILDS = frozenset({"42a27185d1e87be5"})

ROLES = (
    "hot_tub_code_first",
    "hot_tub_code_repeat",
    "hot_tub_vague",
    "ac_first",
    "ac_new_symptom",
    "no_such_model",
    "blurry_plate",
)
# Roles a selection may leave out. Shown after the required ones, in this order.
OPTIONAL_ROLES = ("hot_tub_with_history",)
ALL_ROLES = ROLES + OPTIONAL_ROLES
# A live run whose property records are the seeded synthetic registry rows. Said
# the same way on the tab, the case header, the records step, the numbers panel,
# the brief caption and the glance table.
SYNTHETIC_RECORDS_LABEL = "Live run against synthetic property records"
SYNTHETIC_RECORDS_ROLES = ("hot_tub_with_history",)
# The seed the synthetic records come from; every cited row must be marked synthetic there.
REGISTRY_SEED = Path("seed") / "registry_seed.json"
HOT_TUB_ROLES = ("hot_tub_code_first", "hot_tub_code_repeat", "hot_tub_vague")
AC_ROLES = ("ac_first", "ac_new_symptom")
# Cases whose page shows what memory could and could not answer.
MEMORY_ROLES = ("hot_tub_code_repeat", "hot_tub_vague", "ac_first", "ac_new_symptom", "hot_tub_with_history")

# Authored copy for each role. Scanned like everything else the builder writes.
ROLE_COPY = {
    "hot_tub_code_first": ("Hot tub, code on the panel", "Hot tub: first question, with a code on the panel"),
    "hot_tub_code_repeat": ("Same question again", "Hot tub: the same question, asked again"),
    "hot_tub_vague": ("Hot tub, vague symptom", "Hot tub: a vague symptom on the same unit"),
    "ac_first": ("Air conditioner, first question", "Air conditioner: first question"),
    "ac_new_symptom": ("Air conditioner, new symptom", "Air conditioner: a new symptom on the same unit"),
    "no_such_model": ("A model that does not exist", "A model that does not exist"),
    "blurry_plate": ("Blurry plate", "A blurry plate photo"),
    "hot_tub_with_history": ("Hot tub, with its property records",
                             "Hot tub: the same code, with the property's service records attached"),
}

PLATE_FIELDS = (
    ("manufacturer", "Manufacturer"),
    ("model", "Model"),
    ("serial", "Serial"),
    ("manufacture_date", "Manufacture date"),
)
# Published photos, relative to the repo root; the page shows one only when its
# sha256 matches the photo the run recorded.
PHOTO_ASSETS = ("demo-assets/plate-clear.jpg", "demo-assets/plate-blurry.jpg")

# The two project words that must never appear in anything published, held as
# character codes so the words themselves are not in this file.
_FORBIDDEN_WORDS = (
    "".join(map(chr, (99, 111, 119, 111, 114, 107))),
    "".join(map(chr, (114, 101, 110, 116, 97, 108, 114, 117, 110, 110, 101, 114))),
)
DASHES = privacy_diff.DASHES
# Same pattern as agent/tests/test_published_pages.py.
NETWORK_API_RE = re.compile(
    r"\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|\bimport\s*\(|importScripts"
)
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
DOTENV_RE = re.compile(r"(?<![\w.])\.env\b")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


class BuildError(RuntimeError):
    """The build cannot go on; the message says why."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def https_only(url: str, where: str) -> str:
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise BuildError(f"{where}: {url!r} is not an https URL")
    clean, removed = privacy_diff.strip_tracking(url)
    if removed:
        raise BuildError(f"{where}: {url!r} still carries tracking or session parameters {removed}")
    return url


def long_date(iso: str) -> str:
    day = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def recorded_label(iso: str | None, recorded_on: str | None, mode_label: str, where: str,
                   notes: Notes) -> str | None:
    """The case's recorded line. The selection's recorded_on is the day the runs
    were made in the time zone the published pages use (README.md and index.html
    give that day); the run records and briefs keep UTC. The line leads with
    recorded_on and adds the UTC day only when it differs, so the demo and the
    pages give one date for one recording. Without recorded_on it falls back to
    the UTC day, marked UTC."""
    if not iso:
        return None
    utc_day = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC).date()
    if not recorded_on:
        return f"Recorded {day_text(utc_day)} (UTC), on {mode_label}"
    local_day = date.fromisoformat(recorded_on)
    if abs((utc_day - local_day).days) > 1:
        notes.fail(f"{where}: recorded on {utc_day.isoformat()} in UTC, more than a day from the "
                   f"selection's recorded_on {recorded_on}")
    text = f"Recorded {day_text(local_day)}"
    if utc_day != local_day:
        utc_text = day_text(utc_day) if utc_day.year != local_day.year else day_text(utc_day).rsplit(" ", 1)[0]
        text += f" ({utc_text} in UTC, the clock the run records and briefs use)"
    return f"{text}, on {mode_label}"


def day_text(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def long_time(iso: str | None) -> str:
    """The page's one time format: 18 September 2026 at 17:51 UTC."""
    if not iso:
        return "a time not recorded"
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
    return f"{long_date(iso)} at {moment:%H:%M} UTC"


def usd6(value: float) -> float:
    return round(float(value), 6)


def dollars(usd: float | None) -> str:
    """A ledger amount in US dollars to four places, the rounding every page uses (0.058713 shows as 0.0587 with a dollar sign)."""
    if usd is None:
        return "an amount not recorded"
    return f"${float(usd):.4f}"


def cap_dollars(usd: float) -> str:
    """A cap as configured, to two places with a dollar sign."""
    return f"${float(usd):.2f}"


def count(n: int | None, one: str, many: str) -> str:
    n = int(n or 0)
    return f"{n} {one if n == 1 else many}"


def seconds(s: float | None) -> str:
    return "an unrecorded time" if s is None else f"{float(s):.1f} seconds"


# How the page names each live mode, with the cap in the numbers panel.
MODE_LABELS = {"cheap": "the lower cost model setting", "full": "the higher quality model setting"}


def safe_run_id(run_id: str) -> str:
    if not re.fullmatch(r"t-[0-9a-f]{16}", run_id or ""):
        raise BuildError(f"{run_id!r} is not a run ID of the form t- followed by 16 hex characters")
    return run_id


class Notes:
    """Warnings and failures. In a stand-in build, data disagreements are warnings."""

    def __init__(self, stand_in: bool) -> None:
        self.stand_in = stand_in
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def disagree(self, message: str) -> str:
        """A disagreement between two records: a failure on a real build, a warning on stand-in data."""
        (self.warnings if self.stand_in else self.errors).append(message)
        return message

    def fail(self, message: str) -> None:
        self.errors.append(message)


# ---------------------------------------------------------------------------
# Reading one run
# ---------------------------------------------------------------------------


class RunFiles:
    """Everything recorded about one run, read only."""

    def __init__(self, run_id: str, data_dir: Path) -> None:
        self.run_id = safe_run_id(run_id)
        self.data_dir = data_dir
        hex_id = run_id.split("-", 1)[1]
        self.paths: dict[str, Path] = {
            "run": data_dir / "runs" / f"{run_id}.json",
            "cassette": data_dir / "recordings" / f"live_t_{hex_id}.json",
            "privacy": data_dir / "recordings" / f"live_t_{hex_id}.privacy.json",
            "capture": data_dir / "recordings" / f"{run_id}.capture.jsonl",
            "lookups": data_dir / "lookups" / f"{run_id}.jsonl",
            "brief": data_dir / "briefs" / f"{run_id}.html",
        }
        evals = sorted(p for p in (data_dir / "eval").glob(f"*-{run_id}.json"))
        self.eval_path = evals[0] if evals else None
        self.run = read_json(self.paths["run"]) if self.paths["run"].is_file() else None
        self.cassette = read_json(self.paths["cassette"]) if self.paths["cassette"].is_file() else None
        self.privacy = read_json(self.paths["privacy"]) if self.paths["privacy"].is_file() else None
        self.capture = read_jsonl(self.paths["capture"])
        self.lookups = read_jsonl(self.paths["lookups"])
        self.eval = read_json(self.eval_path) if self.eval_path else None
        if self.eval is not None and self.eval.get("kind") != "run":
            self.eval = None

    def inputs_read(self) -> dict[str, str]:
        """sha256 of every input file this run was built from."""
        files = {k: p for k, p in self.paths.items() if p.is_file()}
        if self.eval_path:
            files["eval"] = self.eval_path
        return {p.relative_to(self.data_dir).as_posix(): sha256_file(p) for p in files.values()}

    @property
    def segments(self) -> list[str]:
        return [str(e.get("segment")) for e in self.capture if e.get("event") == "segment"]

    def capture_read_plate(self) -> dict[str, Any] | None:
        for event in self.capture:
            if event.get("event") == "model" and event.get("node") == "read_plate":
                try:
                    return json.loads(event.get("content") or "")
                except ValueError:
                    return None
        return None


class Ledger:
    """The live ledger, opened read only through sqlite's URI form."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise BuildError(f"no ledger at {path.name}")
        self.conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def charges(self, run_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT ts, mode, node, provider, model, input_tokens, cache_read, cache_write, output_tokens, "
            "tavily_credits, usd FROM entries WHERE run_id = ? AND kind = 'charge' ORDER BY ts, id",
            (run_id,),
        ))

    def stops(self, run_id: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM entries WHERE run_id = ? AND kind = 'stop'", (run_id,)).fetchone()[0])


def ledger_numbers(rows: list[sqlite3.Row]) -> dict[str, Any]:
    total = usd6(sum(r["usd"] for r in rows))
    credits = sum(int(r["tavily_credits"]) for r in rows)
    by_node: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["node"] or "", r["provider"] or "", r["model"] or "")
        row = by_node.setdefault(key, {
            "node": key[0], "provider": key[1], "model": key[2] or None,
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "tavily_credits": 0, "usd": 0.0,
        })
        row["calls"] += 1
        row["input_tokens"] += int(r["input_tokens"]) + int(r["cache_read"]) + int(r["cache_write"])
        row["output_tokens"] += int(r["output_tokens"])
        row["tavily_credits"] += int(r["tavily_credits"])
        row["usd"] += float(r["usd"])
    for row in by_node.values():
        row["usd"] = usd6(row["usd"])
    return {"total_usd": total, "credits": credits, "by_node": list(by_node.values()),
            "modes": sorted({r["mode"] for r in rows})}


# ---------------------------------------------------------------------------
# Building the parts of one case
# ---------------------------------------------------------------------------


def photo_for(sha: str | None, repo_root: Path) -> str | None:
    if not sha:
        return None
    for rel in PHOTO_ASSETS:
        path = repo_root / rel
        if path.is_file() and sha256_file(path) == sha:
            return rel
    raise BuildError(f"no published photo matches the recorded plate sha256 {sha[:12]}...")


def plate_read(extraction: dict[str, Any] | None) -> dict[str, Any] | None:
    if not extraction:
        return None
    conf = extraction.get("confidence") or {}
    fields = []
    for key, label in PLATE_FIELDS:
        value = extraction.get(key)
        confidence = conf.get(key)
        if confidence not in ("high", "low", "unreadable"):
            raise BuildError(f"plate field {key} has an unknown confidence {confidence!r}")
        fields.append({"key": key, "label": label,
                       "value": value if isinstance(value, str) and value.strip() else None,
                       "confidence": confidence})
    return {"fields": fields}


def identity_of(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    out = {key: value.get(key) for key, _ in PLATE_FIELDS if value.get(key)}
    return out or None


def trail_for(role: str, rf: RunFiles, notes: Notes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every research tool call in call order: sent (with its outcome) or blocked by the run's
    search or fetch limit (a count limit, not the spending cap).

    Notes are prefixed with the role so a stand-in build attaches them to the right case.
    """
    cassette = rf.cassette or {}
    research = cassette.get("research") or {}
    script_calls = [c for step in research.get("script") or []
                    for c in (step.get("message") or {}).get("tool_calls") or []]
    results = list(research.get("tool_results") or [])
    used = [False] * len(results)
    lookups = list(rf.lookups)
    lookup_used = [False] * len(lookups)

    def arg_of(call: dict[str, Any]) -> str:
        args = call.get("args") or {}
        return str(args.get("query") if call.get("name") == "search" else args.get("url"))

    def take_result(tool: str, arg: str) -> dict[str, Any] | None:
        for i, res in enumerate(results):
            key = res.get("query") if tool == "search" else res.get("url")
            if not used[i] and res.get("tool") == tool and key == arg:
                used[i] = True
                return res
        return None

    def take_lookup(tool: str, arg: str) -> dict[str, Any] | None:
        for i, entry in enumerate(lookups):
            if not lookup_used[i] and entry.get("tool") == tool and entry.get("query") == arg \
                    and entry.get("status") != "blocked":
                lookup_used[i] = True
                return entry
        return None

    trail: list[dict[str, Any]] = []
    for call in script_calls:
        tool = call.get("name")
        if tool not in ("search", "fetch"):
            continue
        arg = arg_of(call)
        res = take_result(tool, arg)
        if res is None:
            item: dict[str, Any] = {"tool": tool, "status": "blocked",
                                    "note": "Blocked by the run's search or fetch limit before it was sent. Never sent."}
            if tool == "search":
                item["query"] = arg
            else:
                item["url"] = https_only(arg, f"{rf.run_id} blocked fetch")
                item["host"] = host_of(arg)
            trail.append(item)
            continue
        entry = take_lookup(tool, arg)
        item = {"tool": tool, "status": "sent"}
        if entry is not None:
            item["credits"] = int(entry.get("credits") or 0)
        elif res.get("credits") is not None:
            item["credits"] = int(res["credits"])
        if tool == "search":
            item["query"] = arg
            hosts: list[str] = []
            for r in res.get("results") or []:
                h = host_of(str(r.get("url") or ""))
                if h and h not in hosts:
                    hosts.append(h)
            item["results"] = len(res.get("results") or [])
            item["hosts"] = hosts
        else:
            item["url"] = https_only(arg, f"{rf.run_id} fetch")
            item["host"] = host_of(arg)
            failed = bool(res.get("failed_results")) or not (res.get("results") or []) or \
                (entry is not None and entry.get("status") == "error")
            item["ok"] = not failed
        trail.append(item)

    check: dict[str, Any] = {}
    unmatched = [i for i, u in enumerate(used) if not u]
    if unmatched:
        notes.fail(f"{role}: {rf.run_id}: {len(unmatched)} recorded tool result(s) match no call in the research script")
    blocked = [t for t in trail if t["status"] == "blocked"]
    recorder_blocked = sum(int(v) for v in (research.get("blocked_calls") or {}).values())
    check["blocked_in_script"] = len(blocked)
    check["blocked_recorder_count"] = recorder_blocked
    if len(blocked) != recorder_blocked:
        notes.disagree(f"{role}: {rf.run_id}: {len(blocked)} unanswered calls in the script, recorder counted "
                       f"{recorder_blocked} blocked calls")
    if rf.eval is not None:
        not_reached = [(n.get("tool"), n.get("query")) for n in rf.eval.get("not_reached") or []]
        mine = [(t["tool"], t.get("query") or t.get("url")) for t in blocked]
        check["blocked_eval_not_reached"] = len(not_reached)
        if sorted(map(str, not_reached)) != sorted(map(str, mine)):
            notes.disagree(f"{role}: {rf.run_id}: blocked calls differ between the research script and eval not_reached")
    # Per call credits are shown only when they add up to the ledger's total.
    return trail, check


def strip_trail_credits(trail: list[dict[str, Any]]) -> None:
    for item in trail:
        item.pop("credits", None)


def source_list(brief: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(brief.get("sources") or []):
        out.append({
            "title": s.get("title"),
            "url": https_only(s.get("url"), f"source {i}"),
            "host": s.get("host") or host_of(s.get("url") or ""),
            "tier": s.get("tier"),
            "origin": s.get("origin"),
        })
    return out


def memory_facts(role: str, rf: RunFiles, graph: dict[str, Any], runs_by_id: dict[str, RunFiles],
                 notes: Notes, *, exact: bool) -> list[dict[str, Any]]:
    """Facts this run's brief took from the graph, as stored in graph.json.

    Notes are prefixed with the role so a stand-in build attaches them to the right case.
    """
    brief = (rf.run or {}).get("brief") or {}
    sources = brief.get("sources") or []
    nodes = {n.get("key"): n for n in graph.get("nodes") or []}
    facts = []
    for ci, cand in enumerate(brief.get("candidates") or []):
        idx = cand.get("source_index")
        if not isinstance(idx, int) or idx >= len(sources) or sources[idx].get("origin") != "graph":
            continue
        src = sources[idx]
        matches = [e for e in graph.get("edges") or []
                   if e.get("source_url") == src.get("url")
                   and nodes.get(e.get("dst"), {}).get("code") == cand.get("code")]
        if len(matches) != 1:
            notes.fail(f"{role}: {rf.run_id}: candidate {ci} cites the graph but {len(matches)} stored edges match it")
            continue
        edge = matches[0]
        stored = edge.get("evidence") or ""
        quoted = cand.get("evidence") or ""
        relation = ("equal" if stored == quoted
                    else "prefix" if quoted and stored.startswith(quoted)
                    else "within" if quoted and quoted in stored
                    else "differs")
        if exact and relation != "equal":
            notes.disagree(f"{role}: the brief's evidence and the stored fact's evidence are not byte for byte equal "
                           f"(run {rf.run_id})")
        elif relation == "differs":
            notes.disagree(f"{role}: the brief quotes evidence that is not in the stored fact (run {rf.run_id})")
        source_node = next((n for n in nodes.values()
                            if n.get("type") == "source" and n.get("url") == edge.get("source_url")), {})
        first = edge.get("brief_run_id")
        fact = {
            "kind": edge.get("kind"),
            "code": cand.get("code"),
            "evidence": stored,
            "evidence_chars": len(stored),
            # True when the stored span stops without closing punctuation, so the
            # page can say the cut is in the stored text, not in the page.
            "evidence_ends_mid_text": bool(stored) and not re.search(r"[.!?)\]\"'\u201d]\s*$", stored),
            "brief_quote": quoted if relation != "equal" else None,
            "brief_quote_relation": relation,
            "source_title": source_node.get("title") or src.get("title"),
            "source_url": https_only(edge.get("source_url"), f"{rf.run_id} edge"),
            "host": host_of(edge.get("source_url") or ""),
            "tier": source_node.get("tier") or src.get("tier"),
            "retrieved_at": edge.get("retrieved_at"),
            "validated_at": edge.get("validated_at"),
            "retrieved": long_time(edge.get("retrieved_at")),
            "validated": long_time(edge.get("validated_at")),
            "first_run_id": first,
            "first_run_disagrees": False,
        }
        first_files = runs_by_id.get(first) if first else None
        if first_files is not None and first_files.run is not None:
            dropped = [d for d in (first_files.run.get("graph_edges") or {}).get("dropped") or []
                       if d.get("source_url") == edge.get("source_url") and d.get("code") == cand.get("code")]
            if dropped:
                fact["first_run_disagrees"] = True
                # One plain note, shown once in the case's builder notes (stand-in only;
                # on a rerun build this is a build failure).
                notes.disagree(
                    f"{role}: the first run's log (run {first}) says it discarded the fact for code "
                    f"{cand.get('code')}, yet memory holds that fact under the same run, validated "
                    f"{long_time(edge.get('validated_at'))}, after that run finished "
                    f"{long_time(first_files.run.get('generated_at'))}. Shown as recorded."
                )
        facts.append(fact)
    return facts


def outcome_of(rf: RunFiles) -> dict[str, Any]:
    run = rf.run or {}
    brief = run.get("brief") or {}
    candidates = brief.get("candidates") or []
    sources = brief.get("sources") or []
    grounding = run.get("grounding") or []

    def origin(c: dict[str, Any]) -> str | None:
        i = c.get("source_index")
        return sources[i].get("origin") if isinstance(i, int) and 0 <= i < len(sources) else None

    nra = brief.get("no_reliable_answer")
    return {
        "status": run.get("status"),
        "refusal_origin": run.get("refusal_origin"),
        "stop_reason": run.get("stop_reason"),
        "matched_identity": brief.get("matched_identity"),
        "observed_code": brief.get("observed_code"),
        "candidates": len(candidates),
        "confirmed_candidates": sum(1 for c in candidates if c.get("confirmed")),
        "try_first_steps": len(brief.get("try_first") or []),
        "candidate_list": [{"code": c.get("code"), "confirmed": bool(c.get("confirmed")),
                            "why_shown": c.get("why_shown"),
                            "source_origin": origin(c)}
                           for c in candidates],
        "grounding_status": run.get("grounding_status"),
        "grounding_reasons": sorted({g.get("reason") for g in grounding if g.get("reason")}),
        "sources": source_list(brief),
        "no_reliable_answer": ({"found": list(nra.get("found") or []),
                                "why_insufficient": nra.get("why_insufficient"),
                                "searched": list(nra.get("searched") or [])} if nra else None),
    }


# How the page names the layer that raised a step's safety flag (the run record's raised_by).
SAFETY_LAYER_NAMES = {"writer": "the writer", "word": "the word rule", "jev": "Jev",
                      "word_fallback": "the word rule, for a failed Jev call"}


def safety_section(role: str, rf: RunFiles, rows: list[sqlite3.Row], brief_bytes: bytes | None,
                   notes: Notes) -> dict[str, Any] | None:
    """What the run record's safety section says about the brief's steps to try first.

    Copies, per step, the step's own words, the flag on the brief, the layer that
    raised it and Jev's probability, all from the run record; never a judgment of
    whether a step is a safety step. None when the run has no steps to try first and
    no failed check to report. The record must agree with itself, with the brief's
    steps, with the brief file's notice line, with the ledger's Jev calls and with
    the threshold and model this build ships; any disagreement is a note.
    """
    from agent.nodes.safety_check import NOTICE_STATUSES, STATUSES
    from agent.render.brief_html import SAFETY_NOTICE

    run = rf.run
    if run is None:
        return None
    try_first = [s for s in (run.get("brief") or {}).get("try_first") or [] if isinstance(s, dict)]
    rec = run.get("safety")
    if not isinstance(rec, dict):
        if try_first:
            notes.disagree(f"{role}: {rf.run_id}'s brief has steps to try first but its run record has no "
                           "safety section")
        return None
    status = rec.get("status")
    steps = [s for s in rec.get("steps") or [] if isinstance(s, dict)]
    if status not in STATUSES:
        notes.disagree(f"{role}: {rf.run_id}'s safety check status {status!r} is not a known status")
    notice = status in NOTICE_STATUSES
    if not steps and not notice:
        if try_first:
            notes.disagree(f"{role}: {rf.run_id}'s brief has {len(try_first)} steps to try first but its safety "
                           f"section lists none (status {status!r})")
        return None
    if rec.get("model") != config.JEV_MODEL:
        notes.disagree(f"{role}: {rf.run_id}'s safety check names model {rec.get('model')!r}, not the pinned "
                       f"{config.JEV_MODEL}")
    threshold = config.JEV_THRESHOLD
    jev_on = "jev" in config.SAFETY_LAYERS and threshold is not None
    if [s.get("step") for s in steps] != [s.get("step") for s in try_first]:
        notes.disagree(f"{role}: {rf.run_id}'s safety section and its brief list different steps to try first")
    for i, (s, t) in enumerate(zip(steps, try_first)):
        if (s.get("final_flag") is True) != (t.get("safety_flag") is True):
            notes.disagree(f"{role}: {rf.run_id}: step {i + 1}'s flag differs between the safety section and the brief")
    shown = []
    for i, s in enumerate(steps):
        raised_by, flag, writer = s.get("raised_by"), s.get("final_flag") is True, s.get("writer_flag") is True
        noul = s.get("jev_noul")
        noul = None if isinstance(noul, bool) or not isinstance(noul, (int, float)) else float(noul)
        where = f"{role}: {rf.run_id}: step {i + 1}"
        if raised_by not in (None, *SAFETY_LAYER_NAMES):
            notes.disagree(f"{where} names an unknown layer {raised_by!r}")
        if flag != (raised_by is not None):
            notes.disagree(f"{where}'s flag and the layer that raised it disagree")
        if (raised_by == "writer") != writer:
            notes.disagree(f"{where}: the writer's flag and the layer that raised it disagree")
        if raised_by == "jev" and not (jev_on and noul is not None and noul >= threshold):
            notes.disagree(f"{where} is put down to Jev, but Jev's answer {noul} is not at or above "
                           f"the shipped threshold {threshold}")
        if raised_by is None and jev_on and noul is not None and noul >= threshold:
            notes.disagree(f"{where} has no flag, but Jev's answer {noul} is at or above the shipped "
                           f"threshold {threshold}")
        if raised_by == "word" and "word" not in config.SAFETY_LAYERS:
            notes.disagree(f"{where} is put down to the word rule, which this build does not ship")
        if raised_by == "word_fallback" and not (
                config.SAFETY_WORD_FALLBACK and jev_on and "word" not in config.SAFETY_LAYERS and notice
                and noul is None and s.get("jev_error") not in (None, "not_recorded")
                and s.get("word_rule") is True):
            notes.disagree(f"{where} is put down to the word fallback, but its Jev call did not fail in a "
                           "partial or failed check")
        shown.append({"step": s.get("step"), "flag": flag, "raised_by": raised_by, "writer_flag": writer,
                      "jev_probability": noul})
    answered = sum(1 for s in shown if s["jev_probability"] is not None)
    jev_calls = sum(1 for r in rows if r["node"] == "safety_check")
    if jev_calls < answered:
        notes.disagree(f"{role}: {rf.run_id}'s ledger has {jev_calls} Jev calls for {answered} answered steps")
    if brief_bytes is not None and (SAFETY_NOTICE in brief_bytes.decode("utf-8")) != notice:
        notes.disagree(f"{role}: {rf.run_id}'s brief file {'lacks' if notice else 'carries'} the safety notice "
                       f"line, but the safety check status is {status!r}")
    return {
        "status": status,
        "model": rec.get("model"),
        "threshold": threshold,
        "steps_checked": len(shown),
        "answered": answered,
        "notice": SAFETY_NOTICE if notice else None,
        "steps": shown,
        "flags_by_writer": sum(1 for s in shown if s["raised_by"] == "writer"),
        "flags_by_jev": sum(1 for s in shown if s["raised_by"] == "jev"),
        "jev_added": [{"step": s["step"], "jev_probability": s["jev_probability"]}
                      for s in shown if s["raised_by"] == "jev" and not s["writer_flag"]],
    }


def safety_takeaway(case: dict[str, Any]) -> str:
    """The sentence the takeaway gains when Jev added a flag or the safety check failed; else empty."""
    safety = case.get("safety") or {}
    out = ""
    added = safety.get("jev_added") or []
    if added:
        out += (f" Jev raised a safety flag the writer left off, on {count(len(added), 'step', 'steps')} "
                "to try first.")
    if safety.get("notice"):
        out += " The automatic safety check failed on this run, so the brief carries its notice line."
    return out


def label_nra_searched(nra: dict[str, Any], trail: list[dict[str, Any]], rf: RunFiles) -> None:
    sent = {t.get("query") for t in trail if t["tool"] == "search" and t["status"] == "sent"}
    sent |= {e.get("query") for e in rf.lookups if e.get("tool") == "search" and e.get("status") != "blocked"}
    blocked = {t.get("query") for t in trail if t["tool"] == "search" and t["status"] == "blocked"}
    if rf.eval is not None:
        blocked |= {n.get("query") for n in rf.eval.get("not_reached") or [] if n.get("tool") == "search"}
    labeled = []
    for q in nra["searched"]:
        status = "sent" if q in sent else ("blocked" if q in blocked else "not_in_trail")
        labeled.append({"query": q, "status": status})
    nra["searched"] = labeled


# ---------------------------------------------------------------------------
# Brief files
# ---------------------------------------------------------------------------


class _BriefScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.csp: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "iframe", "frame", "object", "embed", "img", "video", "audio", "source",
                   "base", "form", "input"):
            self.problems.append(f"<{tag}>")
        for attr in ("src", "srcset", "data", "poster", "action", "formaction"):
            if attr in values:
                self.problems.append(f"{attr}= on <{tag}>")
        if any(k.startswith("on") for k in values):
            self.problems.append(f"event handler on <{tag}>")
        if "style" in values and "url(" in values["style"].lower():
            self.problems.append(f"style url( on <{tag}>")
        if tag == "meta" and values.get("http-equiv", "").lower() == "content-security-policy":
            self.csp = values.get("content", "")
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.problems.append("meta refresh")
        if "href" in values:
            href = values["href"]
            if tag == "link":
                if href != "data:,":
                    self.problems.append(f"<link href={href}>")
            elif tag == "a":
                if not (href.startswith("https://") or href.startswith("#")):
                    self.problems.append(f"<a href={href}>")
            else:
                self.problems.append(f"href on <{tag}>")


def brief_problems(data: bytes) -> list[str]:
    text = data.decode("utf-8")
    scanner = _BriefScanner()
    scanner.feed(text)
    problems = list(scanner.problems)
    csp = re.sub(r"\s+", " ", (scanner.csp or "")).strip().rstrip(";")
    if csp != "default-src 'none'; style-src 'unsafe-inline'":
        problems.append(f"content security policy is {scanner.csp!r}")
    lowered = text.lower()
    for needle in ("@import", "url(", "<script"):
        if needle in lowered:
            problems.append(needle)
    return problems


# ---------------------------------------------------------------------------
# The selection and the stand-in guard
# ---------------------------------------------------------------------------


def load_selection(path: Path) -> dict[str, Any]:
    sel = read_json(path)
    if sel.get("format") != 1:
        raise BuildError("selection format must be 1")
    roles = sel.get("roles") or {}
    missing = [r for r in ROLES if not roles.get(r)]
    if missing:
        raise BuildError(f"selection names no run for: {', '.join(missing)}")
    unknown = sorted(set(roles) - set(ALL_ROLES))
    if unknown:
        raise BuildError(f"selection names unknown roles: {', '.join(unknown)}")
    for role, run_id in roles.items():
        safe_run_id(run_id)
    for role, extra in (sel.get("also_count") or {}).items():
        if role not in roles:
            raise BuildError(f"also_count names a role the selection does not use: {role}")
        for run_id in extra:
            safe_run_id(run_id)
    recorded_on = sel.get("recorded_on")
    if recorded_on is not None:
        try:
            date.fromisoformat(str(recorded_on))
        except ValueError:
            raise BuildError(f"recorded_on {recorded_on!r} is not a date such as 2026-09-30") from None
    return sel


def selected_roles(sel: dict[str, Any]) -> tuple[str, ...]:
    """The required roles, then the optional ones the selection names, in page order."""
    return ROLES + tuple(r for r in OPTIONAL_ROLES if (sel.get("roles") or {}).get(r))


# The demo tooling: files under agent/ that build this page and never run inside
# the advisor. agent/build_info.py leaves them out of the build fingerprint
# (build_info.EXCLUDED), so installing or editing them does not make the runs
# they show look like another build.
DEMO_TOOLING = frozenset(p for p in build_info.EXCLUDED if p.startswith("replay/"))


def current_build_id() -> str:
    """The build the advisor's own code is on now: build_info.build_id(), the same
    fingerprint every run record carries, so there is one definition of a build."""
    return build_info.build_id()


def superseded_builds(data_dir: Path) -> set[str]:
    out: set[str] = set(KNOWN_SUPERSEDED_BUILDS)
    for path in (data_dir / "eval").glob("summary-*.json"):
        try:
            summary = read_json(path)
        except ValueError:
            continue
        for b in (summary.get("score") or {}).get("superseded_builds") or []:
            out.add(str(b.get("build_id") if isinstance(b, dict) else b))
        for b in (summary.get("rerun") or {}).get("superseded") or []:
            out.add(str(b.get("build_id") if isinstance(b, dict) else b))
    out.discard("None")
    return out


def superseded_ids(data_dir: Path) -> set[str]:
    """Every superseded build ID, and every run ID filed under data/superseded/.

    None of them may appear anywhere in a rerun build's output.
    """
    ids = set(superseded_builds(data_dir))
    folder = data_dir / "superseded"
    if folder.is_dir():
        for path in folder.rglob("*"):
            ids |= set(re.findall(r"t-[0-9a-f]{16}", path.name))
            ids |= set(re.findall(r"build-([0-9a-f]{16})", path.name))
    return ids


def _files_under(root: Path, only: list[str] | tuple[str, ...] | None) -> list[Path]:
    """Every file under root, or only the listed relative paths that exist."""
    if only is None:
        return sorted(p for p in root.rglob("*") if p.is_file())
    return sorted(root / rel for rel in only if (root / rel).is_file())


def superseded_hits(root: Path, forbidden: set[str], only: list[str] | tuple[str, ...] | None = None) -> list[str]:
    hits = []
    for path in _files_under(root, only):
        if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            continue
        text = path.read_text(encoding="utf-8")
        for ident in sorted(forbidden):
            if ident in text:
                hits.append(f"{path.relative_to(root).as_posix()}: names superseded build or run {ident}")
    return hits


def recorded_builds(rf: RunFiles) -> dict[str, str]:
    """The build each of the run's own records names: {"run record": id, "eval record": id}."""
    out = {}
    if rf.run is not None and rf.run.get("build_id"):
        out["run record"] = str(rf.run["build_id"])
    if rf.eval is not None and rf.eval.get("build_id"):
        out["eval record"] = str(rf.eval["build_id"])
    return out


def build_of_run(rf: RunFiles) -> str | None:
    """The build a run was made on: its run record's build_id, else its eval record's.

    None when neither record names one, or when the two disagree.
    """
    builds = recorded_builds(rf)
    if len(set(builds.values())) > 1:
        return None
    return builds.get("run record") or builds.get("eval record")


def guard(sel: dict[str, Any], runs: dict[str, RunFiles], data_dir: Path, current: str,
          *, stand_in: bool = False) -> list[str]:
    """Reasons the selection is not live runs on the current build; empty when it is.

    Every selected and also_count run must name its build in its own records (run
    record build_id, else eval record build_id), and that build must be the current
    build, the selection's build_id, and not a superseded build.
    """
    reasons = []
    old = superseded_builds(data_dir)
    if sel.get("data_status") != "rerun":
        reasons.append(f"the selection's data_status is {sel.get('data_status')!r}, not 'rerun'")
    if sel.get("build_id") != current:
        reasons.append(f"the selection names build {sel.get('build_id')}, the current build is {current}")
    if current in old:
        reasons.append(f"the current build {current} is listed as superseded")
    for run_id, rf in runs.items():
        if rf.run is None and rf.eval is None:
            reasons.append(f"{run_id} has no run record and no eval record")
            continue
        mode = (rf.run or rf.eval or {}).get("mode")
        if mode not in config.LIVE_MODES:
            reasons.append(f"{run_id} is not a live run (mode {mode!r})")
        builds = recorded_builds(rf)
        build = build_of_run(rf)
        if not builds:
            reasons.append(f"{run_id} has no record naming its build")
        elif build is None:
            reasons.append(f"{run_id}'s records disagree on its build: "
                           + ", ".join(f"{k} {v}" for k, v in builds.items()))
        else:
            if build != current:
                reasons.append(f"{run_id} ran on build {build}, not the current build {current}")
            if build != sel.get("build_id"):
                reasons.append(f"{run_id} ran on build {build}, the selection names {sel.get('build_id')}")
            if build in old:
                reasons.append(f"{run_id} ran on superseded build {build}")
    return sorted(set(reasons), key=reasons.index)


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


def plate_sha_of(rf: RunFiles) -> str | None:
    return ((rf.cassette or {}).get("input") or {}).get("plate_sha256")


# Roles whose case is about the plate photo; each must have one.
PHOTO_ROLES = ("hot_tub_code_first", "hot_tub_code_repeat", "blurry_plate")


def check_role(role: str, rf: RunFiles, notes: Notes, ledger: Ledger) -> None:
    run, ev = rf.run, rf.eval
    if role == "blurry_plate":
        if ev is None:
            notes.fail(f"{role}: {rf.run_id} has no eval record")
            return
        if run is not None:
            notes.fail(f"{role}: {rf.run_id} has a run record, so it did not halt at the confirmation step")
        if ev.get("status") != "paused":
            notes.fail(f"{role}: {rf.run_id} status is {ev.get('status')!r}, not paused")
        if not (ev.get("input") or {}).get("photo"):
            notes.fail(f"{role}: {rf.run_id} sent no photo")
        if "ask" not in rf.segments:
            notes.fail(f"{role}: {rf.run_id}'s capture has no confirmation pause (ask segment)")
        conf = ((ev.get("extraction") or {}).get("raw") or {}).get("confidence") or {}
        readable = [k for k, v in conf.items() if v != "unreadable" and k != "manufacturer"]
        if readable:
            notes.fail(f"{role}: {rf.run_id} read fields {readable}, so the plate was not blurry")
        # The page says nothing was searched: hold the records and the ledger to it.
        if ev.get("searches") or ev.get("fetches") or ev.get("credits"):
            notes.fail(f"{role}: {rf.run_id} recorded searches {ev.get('searches')}, fetches "
                       f"{ev.get('fetches')}, credits {ev.get('credits')}; a halted run has none")
        rows = ledger.charges(rf.run_id)
        if any(int(r["tavily_credits"]) for r in rows) or any(r["node"] != "read_plate" for r in rows):
            notes.fail(f"{role}: {rf.run_id}'s ledger has charges other than the plate read")
        return
    if run is None:
        notes.fail(f"{role}: {rf.run_id} has no run record")
        return
    if rf.cassette is None:
        notes.fail(f"{role}: {rf.run_id} has no recording")
    if rf.privacy is None:
        notes.fail(f"{role}: {rf.run_id} has no privacy report for its recording")
    elif rf.privacy.get("hits"):
        notes.fail(f"{role}: {rf.run_id}'s recording has privacy hits; it cannot be shown")
    if role in PHOTO_ROLES and not plate_sha_of(rf):
        notes.fail(f"{role}: {rf.run_id} sent no plate photo")
    if plate_sha_of(rf):
        # The page shows a confirmation pause and a resume for every photo run.
        missing = [seg for seg in ("ask", "resume") if seg not in rf.segments]
        if missing:
            notes.fail(f"{role}: {rf.run_id} sent a photo but its capture has no {' or '.join(missing)} segment")
    route = run.get("route") or []
    if role in ("hot_tub_code_first", "ac_first") and "research" not in route:
        notes.fail(f"{role}: {rf.run_id} route {route} does not include research")
    if role == "hot_tub_code_repeat" and (route != ["graph"] or run.get("searches") != 0):
        notes.fail(f"{role}: {rf.run_id} route {route} with {run.get('searches')} searches is not a repeat from memory")
    if role == "no_such_model" and run.get("status") != "no_reliable_answer":
        notes.fail(f"{role}: {rf.run_id} status is {run.get('status')!r}")
    if role != "no_such_model" and run.get("status") != "ok":
        notes.fail(f"{role}: {rf.run_id} status is {run.get('status')!r}, not ok")
    if role == "hot_tub_code_first" and not ((rf.cassette or {}).get("resume") or {}).get("observed_code"):
        notes.fail(f"{role}: {rf.run_id} has no confirmed code")
    if role == "hot_tub_with_history":
        if route != ["history", "graph"] or run.get("searches") != 0:
            notes.fail(f"{role}: {rf.run_id} route {route} with {run.get('searches')} searches is not "
                       "history plus memory with no search")
        brief = run.get("brief") or {}
        if not ((brief.get("happened_before") or {}).get("matches")):
            notes.fail(f"{role}: {rf.run_id}'s brief does not cite a prior service record")
        if not ((brief.get("warranty") or {}).get("age_statement")):
            notes.fail(f"{role}: {rf.run_id}'s brief has no age statement")
    if (run.get("sc11") or {}).get("result") != "pass":
        notes.fail(f"{role}: {rf.run_id} did not pass the per run cost cap check")


def load_seed(repo_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """The synthetic registry seed, keyed by citation id (appliance:<id>, service:<id>)."""
    path = repo_root / REGISTRY_SEED
    if not path.is_file():
        raise BuildError(f"no registry seed at {REGISTRY_SEED.as_posix()}")
    seed = read_json(path)
    return {
        "appliance": {f"appliance:{a['id']}": a for a in seed.get("appliances") or []},
        "service": {f"service:{s['id']}": s for s in seed.get("service_records") or []},
    }


def property_records(role: str, rf: RunFiles, repo_root: Path, notes: Notes) -> dict[str, Any]:
    """What the brief took from the property's records, checked against the synthetic seed.

    Copies the brief's own words (the prior record's summary, the code written age
    statement, the warranty terms) and the cited rows' dates. Every cited row must be
    in the seed and marked synthetic, and the age statement must be exactly what
    agent/rules/citations.py writes from the cited install date on the run's date.
    """
    from datetime import date

    from agent.rules.citations import age_statement

    seed = load_seed(repo_root)
    brief = (rf.run or {}).get("brief") or {}
    before = brief.get("happened_before") or {}
    warranty = brief.get("warranty") or {}
    out: dict[str, Any] = {"label": SYNTHETIC_RECORDS_LABEL, "happened_before": None, "age": None,
                           "warranty_terms": None}
    service = seed["service"].get(str(before.get("record_id")))
    if not before.get("matches") or service is None:
        notes.fail(f"{role}: {rf.run_id} cites service record {before.get('record_id')!r}, which is not in the seed")
    elif service.get("synthetic") is not True:
        notes.fail(f"{role}: {rf.run_id} cites service record {before.get('record_id')}, which is not marked synthetic")
    else:
        out["happened_before"] = {"record_id": before["record_id"], "summary": before.get("summary"),
                                  "record_date": service.get("date"), "code": service.get("observed_code")}
    appliance = seed["appliance"].get(str(warranty.get("record_id")))
    if appliance is None:
        notes.fail(f"{role}: {rf.run_id} cites appliance record {warranty.get('record_id')!r}, which is not in the seed")
        return out
    if appliance.get("synthetic") is not True:
        notes.fail(f"{role}: {rf.run_id} cites appliance record {warranty.get('record_id')}, which is not marked synthetic")
        return out
    if not same_unit(identity_of(appliance), confirmed_identity(rf)):
        notes.fail(f"{role}: {rf.run_id} cites appliance record {warranty.get('record_id')}, which is not the run's unit")
        return out
    run_day = date.fromisoformat(str((rf.run or {}).get("generated_at"))[:10])
    expected, _ = age_statement(registry={**appliance, "record_id": warranty["record_id"]},
                                manufacture_date=None, today=run_day)
    if warranty.get("age_statement") != expected:
        notes.fail(f"{role}: {rf.run_id}'s age statement is not the one code writes from the install date")
    else:
        out["age"] = {"record_id": warranty["record_id"], "install_date": appliance.get("install_date"),
                      "as_of": run_day.isoformat(), "statement": warranty["age_statement"]}
    if warranty.get("terms") != appliance.get("warranty_terms"):
        notes.fail(f"{role}: {rf.run_id}'s warranty terms differ from the seed's")
    elif warranty.get("terms"):
        out["warranty_terms"] = {"record_id": warranty["record_id"], "terms": warranty["terms"]}
    return out


def confirmed_identity(rf: RunFiles) -> dict[str, Any] | None:
    cassette = rf.cassette or {}
    resume = cassette.get("resume") or {}
    return identity_of(resume.get("identity")) or identity_of((cassette.get("input") or {}).get("identity"))


def same_unit(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b) and all((a.get(k) or "").lower() == (b.get(k) or "").lower()
                                 for k in ("manufacturer", "model"))


def build_case(index: int, role: str, rf: RunFiles, ctx: dict[str, Any], notes: Notes) -> dict[str, Any]:
    repo_root: Path = ctx["repo_root"]
    ledger: Ledger = ctx["ledger"]
    button, title = ROLE_COPY[role]
    synthetic_records = role in SYNTHETIC_RECORDS_ROLES
    run = rf.run or {}
    ev = rf.eval or {}
    cassette = rf.cassette or {}
    rows = ledger.charges(rf.run_id)
    led = ledger_numbers(rows)
    if not rows:
        notes.fail(f"{role}: the ledger has no charges for {rf.run_id}")

    # --- what the owner sent -------------------------------------------------
    inp = cassette.get("input") or {}
    if cassette:
        symptom = inp.get("symptom")
        plate_sha = inp.get("plate_sha256")
        typed = identity_of(inp.get("identity"))
    else:  # a paused run has no recording: its eval record and capture stand in
        symptom = (ev.get("input") or {}).get("symptom")
        photo_name = Path((ev.get("input") or {}).get("photo") or "").name
        plate_sha = None
        if photo_name:
            match = [p for p in PHOTO_ASSETS if Path(p).name == photo_name]
            if not match:
                raise BuildError(f"{role}: photo {photo_name} is not a published photo")
            plate_sha = sha256_file(repo_root / match[0])
        typed = identity_of((ev.get("input") or {}).get("identity"))
    if ev and (ev.get("input") or {}).get("symptom") not in (None, symptom):
        notes.disagree(f"{role}: the eval record and the recording give different symptoms")
    plate = photo_for(plate_sha, repo_root)

    # --- what the plate reader read -----------------------------------------
    extraction = (cassette.get("read_plate") or {}).get("extraction") if cassette else None
    if extraction is None and plate:
        extraction = rf.capture_read_plate() or ((ev.get("extraction") or {}).get("raw"))
    read = plate_read(extraction) if plate else None

    # --- the confirmation pause ---------------------------------------------
    segments = rf.segments
    confirmation = None
    if plate or ("ask" in segments and "resume" in segments):
        resume = cassette.get("resume") or {}
        read_plate_usd = usd6(sum(r["usd"] for r in rows if r["node"] == "read_plate"))
        resume_at = next((i for i, e in enumerate(rf.capture)
                          if e.get("event") == "segment" and e.get("segment") == "resume"), len(rf.capture))
        before = [e for e in rf.capture[:resume_at] if e.get("event") == "model"]
        if any(e.get("node") != "read_plate" for e in before):
            notes.fail(f"{role}: a model call other than the plate read ran before the confirmation pause")
        confirmation = {
            "paused": "ask" in segments,
            "resumed": "resume" in segments,
            "confirmed_identity": identity_of(resume.get("identity")),
            "confirmed_code": resume.get("observed_code"),
            "spent_before_pause_usd": read_plate_usd,
            "from_photo": bool(plate),
        }
        if ev and ev.get("pauses") is not None and bool(ev.get("pauses")) != confirmation["resumed"]:
            notes.disagree(f"{role}: eval counts {ev.get('pauses')} pauses, the capture says resumed="
                           f"{confirmation['resumed']}")

    # --- route, trail, outcome ----------------------------------------------
    route = None
    trail: list[dict[str, Any]] = []
    trail_check: dict[str, Any] = {}
    outcome: dict[str, Any] | None = None
    if run:
        verdicts = [((c.get("structured") or {}).get("verdict")) for c in cassette.get("classifier") or []]
        route = {"steps": list(run.get("route") or []), "limits": run.get("research_limits"),
                 "reason": run.get("route_reason"),
                 "classifier_verdict": verdicts[-1] if verdicts else None}
        for field in ("route", "route_reason", "research_limits"):
            if ev and ev.get(field) != run.get(field):
                notes.disagree(f"{role}: {field} differs between the run record and the eval record")
        trail, trail_check = trail_for(role, rf, notes)
        outcome = outcome_of(rf)
        if outcome["no_reliable_answer"]:
            label_nra_searched(outcome["no_reliable_answer"], trail, rf)

    # --- the numbers ----------------------------------------------------------
    mode = run.get("mode") or ev.get("mode")
    # The cap comes from the run's own records. Only a stand-in build may fall back
    # to the current config, and it says so in the builder notes.
    cap = (run.get("sc11") or {}).get("run_cap_usd") or ev.get("run_cap_usd")
    if cap is None:
        if notes.stand_in and mode in config.RUN_CAP_USD:
            cap = config.RUN_CAP_USD[mode]
            notes.disagree(f"{role}: {rf.run_id} records no cap; the page shows the current config's "
                           f"{mode} cap, {cap} USD")
        else:
            notes.fail(f"{role}: {rf.run_id} records no run cap (neither sc11.run_cap_usd nor the eval's run_cap_usd)")
            cap = 0.0
    if run:
        cost = usd6(run["cost_usd"])
        searches, fetches, credits = run.get("searches"), run.get("fetches"), run.get("tavily_credits")
        processing = run.get("latency_s")
        recorded = run.get("generated_at")
    else:
        cost = usd6(ev.get("cost_usd") or 0)
        searches, fetches, credits = ev.get("searches"), ev.get("fetches"), ev.get("credits")
        processing = ev.get("latency_s")
        recorded = ev.get("finished_at") or ev.get("started_at")
    if abs(led["total_usd"] - cost) > 1e-6:
        notes.fail(f"{role}: the ledger total {led['total_usd']} differs from the recorded cost {cost}")
    if ev and ev.get("cost_usd") is not None and abs(usd6(ev["cost_usd"]) - cost) > 1e-6:
        notes.fail(f"{role}: the eval cost {ev['cost_usd']} differs from the run record cost {cost}")
    if led["credits"] != (credits or 0):
        notes.fail(f"{role}: ledger credits {led['credits']} differ from the recorded {credits}")
    if run:
        sent_searches = sum(1 for t in trail if t["tool"] == "search" and t["status"] == "sent")
        sent_fetches = sum(1 for t in trail if t["tool"] == "fetch" and t["status"] == "sent")
        if (sent_searches, sent_fetches) != (searches, fetches):
            notes.fail(f"{role}: the trail has {sent_searches} searches and {sent_fetches} fetches, "
                       f"the run record {searches} and {fetches}")
        if ev and (ev.get("searches"), ev.get("fetches"), ev.get("credits")) != (searches, fetches, credits):
            notes.fail(f"{role}: searches, fetches or credits differ between the run and eval records")
    per_call = [t["credits"] for t in trail if t["status"] == "sent" and "credits" in t]
    per_call_note = None
    if trail and (len(per_call) != sum(1 for t in trail if t["status"] == "sent") or sum(per_call) != (credits or 0)):
        per_call_note = notes.disagree(
            f"{role}: per call credits in the lookup log add up to {sum(per_call)}, the ledger charged "
            f"{credits}; per call credits are left off the page")
        strip_trail_credits(trail)
    # No estimate is shown: no run records the planner's estimate, and one computed
    # here would come from this build's config, not from the run.
    paused_and_resumed = bool(confirmation and confirmation["paused"] and confirmation["resumed"])
    numbers = {
        "cost_usd": cost,
        "cost_text": dollars(cost),
        "run_cap_usd": cap,
        "cap_text": f"{cap_dollars(cap)} cap" if cap else "no cap recorded",
        "cap_check": (run.get("sc11") or {}).get("result") or ev.get("sc11"),
        "searches": searches, "fetches": fetches, "tavily_credits": credits,
        "per_call_credits_shown": per_call_note is None,
        "processing_s": round(float(processing), 1) if processing is not None else None,
        "processing_note": ("Time the tool spent working. The owner's confirmation pause is not counted."
                            if paused_and_resumed else "Time the tool spent working."),
        "by_node": led["by_node"],
    }

    # --- the brief file -------------------------------------------------------
    brief = None
    src = rf.paths["brief"]
    if run:
        if not src.is_file():
            notes.fail(f"{role}: {rf.run_id} rendered no brief file")
        else:
            data = src.read_bytes()
            problems = brief_problems(data)
            if problems:
                notes.fail(f"{role}: brief {src.name} is not safe to frame: {problems}")
            else:
                ctx["briefs"][rf.run_id] = data
                brief = {"share": f"briefs/v2/{rf.run_id}.html", "sha256": sha256_bytes(data)}
        html_name = Path(run.get("html_path") or "").name
        if html_name and html_name != src.name:
            notes.fail(f"{role}: the run record's brief is {html_name}, not {src.name}")
    safety = safety_section(role, rf, rows, ctx["briefs"].get(rf.run_id), notes)

    case: dict[str, Any] = {
        "id": f"c{index + 1}",
        "role": role,
        "kind": "live",
        "kind_label": f"Live run {rf.run_id}",
        "records_label": SYNTHETIC_RECORDS_LABEL if synthetic_records else None,
        "records": property_records(role, rf, repo_root, notes) if synthetic_records else None,
        "run_id": rf.run_id,
        "recorded": recorded_label(recorded, ctx.get("recorded_on"), MODE_LABELS.get(mode, mode + " mode"),
                                   f"{role}: {rf.run_id}", notes),
        "button": button,
        "title": title,
        "compare_with": None,
        "ask": {"symptom": symptom, "plate": plate, "plate_sha256": plate_sha, "typed_identity": typed,
                "same_as": None},
        "plate_read": read,
        "confirmation": confirmation,
        "route": route,
        "trail": trail,
        "memory": None,
        "takeaway": None,
        "graph_edges": graph_edge_counts(rf),
        "outcome": outcome if outcome else {"status": ev.get("status"), "refusal_origin": None,
                                            "stop_reason": ev.get("stop_reason"), "candidates": 0,
                                            "confirmed_candidates": 0, "try_first_steps": 0, "sources": [],
                                            "no_reliable_answer": None},
        "brief": brief,
        "safety": safety,
        "numbers": numbers,
        "also_count": None,
        "warnings": [],
    }
    ctx["report"]["runs"][role] = {"run_id": rf.run_id, "inputs": rf.inputs_read(),
                                   "build": build_of_run(rf),
                                   "trail_check": trail_check}
    return case


def graph_edge_counts(rf: RunFiles) -> dict[str, int] | None:
    """How many facts this run stored in memory, from its own record (counts only)."""
    report = (rf.run or {}).get("graph_edges")
    if not isinstance(report, dict):
        return None
    return {"written": int(report.get("written") or 0),
            "already_present": int(report.get("already_present") or 0),
            "dropped": len(report.get("dropped") or [])}


def fact_is_from_this_build(fact: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """A shown fact must come from the selected first question, or another run on the current build."""
    first = fact.get("first_run_id")
    if first and first == ctx["first_question_run"]:
        return True
    rf = ctx["all_runs"].get(first) if first else None
    if rf is None:
        return False
    build = build_of_run(rf)
    return build == ctx["current_build"] and build not in ctx["superseded"]


def add_memory(case: dict[str, Any], rf: RunFiles, ctx: dict[str, Any], notes: Notes) -> None:
    role = case["role"]
    facts = memory_facts(role, rf, ctx["graph"], ctx["all_runs"], notes,
                         exact=role == "hot_tub_code_repeat")
    for fact in facts:
        if not fact_is_from_this_build(fact, ctx):
            notes.disagree(f"{role}: the fact for code {fact.get('code')} was stored by run "
                           f"{fact.get('first_run_id')}, which is not the selected first question and not a "
                           f"run on the current build")
    searched = [t["query"] for t in case["trail"] if t["tool"] == "search" and t["status"] == "sent"]
    outcome = case["outcome"] or {}
    from_graph = [c for c in outcome.get("candidate_list") or [] if c.get("source_origin") == "graph"]
    confirmed_from_graph = [c for c in from_graph if c.get("confirmed")]
    # The heading follows the brief's outcome (confirmed or only a possibility), not the route alone.
    if not facts:
        heading = None
    elif confirmed_from_graph and searched:
        heading = "Memory answered part of this"
    elif confirmed_from_graph:
        heading = "Memory answered this"
    else:
        heading = "What memory could offer"
    case["memory"] = {
        "heading": heading,
        "facts": facts,
        "confirmed_from_memory": len(confirmed_from_graph),
        "offered_from_memory": len(from_graph),
        "searched_instead": searched,
        "prior": None,
    }


def compare_rows(case: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    def col(c: dict[str, Any]) -> dict[str, Any]:
        n = c["numbers"]
        return {"case_id": c["id"], "run_id": c["run_id"], "title": c["button"],
                "route": " then ".join(c["route"]["steps"]) if c["route"] else None,
                "searches": n["searches"], "fetches": n["fetches"], "tavily_credits": n["tavily_credits"],
                "cost_usd": n["cost_usd"], "processing_s": n["processing_s"],
                "try_first_steps": (c["outcome"] or {}).get("try_first_steps")}
    return {"with": other["id"], "columns": [col(other), col(case)]}


def add_prior(case: dict[str, Any], other: dict[str, Any]) -> None:
    """For a case that searched again after an earlier run on the same unit: what that run stored."""
    mem = case.get("memory")
    if not mem or not case["route"] or "research" not in case["route"]["steps"]:
        return
    counts = other.get("graph_edges")
    if counts is None:
        return
    mem["prior"] = {"case_id": other["id"], "run_id": other["run_id"], "button": other["button"], **counts}


def outcome_phrase(c: dict[str, Any]) -> str:
    o = c["outcome"] or {}
    if o.get("status") == "no_reliable_answer":
        return "no reliable answer"
    if o.get("confirmed_candidates"):
        return "a brief with " + count(o["confirmed_candidates"], "confirmed cause", "confirmed causes")
    return "a brief with " + count(o.get("candidates"), "documented possibility", "documented possibilities")


def blocked_count(c: dict[str, Any]) -> int:
    return sum(1 for t in c["trail"] if t["status"] == "blocked")


def takeaway(c: dict[str, Any], by_role: dict[str, dict[str, Any]]) -> str:
    """One line, above the stepper, that states the case's point from its own numbers.

    Each branch is taken only when the run's data supports it; the fallback states
    the route and the numbers without a claim.
    """
    n = c["numbers"]
    role = c["role"]
    steps = (c["route"] or {}).get("steps") or []
    searches = n["searches"] or 0
    money = n["cost_text"]
    time = seconds(n["processing_s"])
    k = c["confirmation"] or {}
    first = by_role.get("hot_tub_code_first")
    by_id = {x["id"]: x for x in by_role.values()}
    other = by_id.get(c["compare_with"]["with"]) if c.get("compare_with") else None

    recs = c.get("records") or {}
    if role == "hot_tub_with_history" and searches == 0 and recs.get("happened_before") and recs.get("age"):
        return (f"Answered from memory and the property's synthetic records, with no search, for {money} "
                f"in {time}. The brief cites the prior service record for the same code and gives the "
                "unit's age, worked out from its install date.")
    if role == "blurry_plate" and c["route"] is None and searches == 0:
        line = f"Stopped at confirmation after {money}; nothing was searched."
        if first is not None:
            line += f" The first hot tub question (case {first['id'][1:]}) cost {first['numbers']['cost_text']} in full."
        return line
    if role == "no_such_model" and (c["outcome"] or {}).get("status") == "no_reliable_answer":
        line = f"No reliable answer after {count(searches, 'search', 'searches')}"
        if blocked_count(c):
            line += f" ({blocked_count(c)} more blocked by the search limit)"
        line += f", for {money}."
        ac = c.get("also_count")
        if ac:
            line += (f" {ac['no_reliable_answer']} of {ac['total']} live runs of this question ended the same way.")
        return line
    if other is not None and steps and "research" not in steps:
        on = other["numbers"]
        lead = "Answered from memory" if searches == 0 else f"Memory answered part, and {count(searches, 'search', 'searches')} topped it up"
        line = (f"{lead}: {count(searches, 'search', 'searches')}, {money} and {time}, against "
                f"{count(on['searches'], 'search', 'searches')}, {on['cost_text']} and {seconds(on['processing_s'])} "
                f"the first time (case {other['id'][1:]}).")
        mine = (c["outcome"] or {}).get("try_first_steps")
        theirs = (other["outcome"] or {}).get("try_first_steps")
        if isinstance(mine, int) and isinstance(theirs, int) and mine < theirs:
            listed = "none" if mine == 0 else str(mine)
            line += (f" The brief was thinner, though: it listed {listed} of the {count(theirs, 'step', 'steps')} "
                     "to try first that the first brief gave.")
        return line
    top_up = "graph" in steps and "research" in steps and (c["route"] or {}).get("limits") == "top_up"
    if other is not None and top_up:
        on = other["numbers"]
        mem = c.get("memory") or {}
        prior = mem.get("prior") or {}
        against = (f"{count(searches, 'search', 'searches')}, {money} and {time}, against "
                   f"{count(on['searches'], 'search', 'searches')}, {on['cost_text']} and "
                   f"{seconds(on['processing_s'])} for the first question (case {other['id'][1:]}).")
        if mem.get("offered_from_memory"):
            cands = (c["outcome"] or {}).get("candidate_list") or []
            offered = count(mem["offered_from_memory"], "documented possibility", "documented possibilities")
            if cands and all(x.get("source_origin") == "graph" for x in cands):
                codes = [str(x["code"]) for x in cands if x.get("code")]
                what = (f"the stored {'code' if len(codes) == 1 else 'codes'} {', '.join(codes)}"
                        if len(codes) == len(cands) else "what memory held")
                left_open = ", unconfirmed" if not any(x.get("confirmed") for x in cands) else ""
                added = any(x.get("origin") == "search" for x in (c["outcome"] or {}).get("sources") or [])
                blocked = blocked_count(c)
                held = (f", and the top up limit blocked {count(blocked, 'more call', 'more calls')}"
                        if blocked else "")
                return (f"Memory offered {offered}. A short top up of {count(searches, 'search', 'searches')} "
                        f"{'added a source but' if added else 'added'} no other cause{held}, so the brief lists "
                        f"only {what}{left_open}. In all: {against}")
            return (f"Memory offered {offered}, and a short top up search added the rest: {against}")
        if prior.get("written"):
            verdict = (c["route"] or {}).get("classifier_verdict")
            why = ("the symptom check could not tell whether any fits this new symptom"
                   if verdict == "unsure" else "none of them settled this new symptom")
            line = (f"Memory held {count(prior['written'], 'fact', 'facts')} from case {other['id'][1:]}, but {why}, "
                    f"so a short top up search ran instead of a full one: {against}")
            cands = (c["outcome"] or {}).get("candidate_list") or []
            if cands and not mem.get("offered_from_memory") and all(x.get("source_origin") == "search" for x in cands):
                line += (" The brief used none of the stored facts: its "
                         f"{count(len(cands), 'documented possibility', 'documented possibilities')} "
                         f"{'all ' if len(cands) > 1 else ''}came from the new searches.")
            return line
    if other is not None and "research" in steps:
        on = other["numbers"]
        return (f"Memory had nothing that fits this new question, so the tool searched again: "
                f"{count(searches, 'search', 'searches')}, {money} and {time}, against "
                f"{count(on['searches'], 'search', 'searches')}, {on['cost_text']} and {seconds(on['processing_s'])} "
                f"for the first question (case {other['id'][1:]}).")
    mem = c.get("memory") or {}
    if steps == ["graph"] and searches == 0 and mem.get("facts") and not mem.get("confirmed_from_memory"):
        return (f"Memory offered {count(mem.get('offered_from_memory'), 'documented possibility', 'documented possibilities')} "
                f"without a search, and confirmed nothing, so the brief leaves it open. {money}, {time}.")
    lead = ""
    if k.get("paused") and k.get("resumed"):
        lead = "Read the plate, paused for the owner to confirm, then "
    if "research" in steps:
        body = f"searched {count(searches, 'time', 'times')} and returned {outcome_phrase(c)}"
        if not lead:
            body = "Memory had nothing that fits, so the tool " + body
    else:
        body = f"answered from memory with {count(searches, 'search', 'searches')} and returned {outcome_phrase(c)}"
    text = (lead + body) if lead else body
    text = text[0].upper() + text[1:]
    stored = (c.get("graph_edges") or {}).get("written") or 0
    tail = ""
    if "research" in steps and stored and (c["outcome"] or {}).get("status") == "ok":
        each = "each " if stored > 1 else ""
        tail = (f" It saved {count(stored, 'fact', 'facts')} to memory, {each}checked word for word "
                "against its source page.")
    return f"{text}, for {money} of its {n['cap_text']} in {time}.{tail}"


# ---------------------------------------------------------------------------
# Writing and checking the output
# ---------------------------------------------------------------------------


def stamp_page(template: str, stand_in: bool, build_id: str, roles: tuple[str, ...] = ROLES) -> str:
    """Keep the stand-in or the rerun blocks of the page template, keep each
    `if:role:<role>` block only when that role is on the page, and fill in the
    build ID and the case count."""
    keep, drop = ("stand_in", "rerun") if stand_in else ("rerun", "stand_in")
    page = re.sub(rf"[ \t]*<!-- if:{drop} -->.*?<!-- /if:{drop} -->\n?", "", template, flags=re.S)
    page = re.sub(rf"[ \t]*<!-- /?if:{keep} -->\n?", "", page)
    for role in OPTIONAL_ROLES:
        if role in roles:
            page = re.sub(rf"[ \t]*<!-- /?if:role:{role} -->\n?", "", page)
        else:
            page = re.sub(rf"[ \t]*<!-- if:role:{role} -->.*?<!-- /if:role:{role} -->\n?", "", page, flags=re.S)
    if "<!-- if:" in page or "<!-- /if:" in page:
        raise BuildError("the page template has an unbalanced or unknown if block")
    words = ("zero one two three four five six seven eight nine ten").split()
    count = words[len(roles)] if len(roles) < len(words) else str(len(roles))
    return (page.replace("{{BUILD_ID}}", build_id).replace("{{STAND_IN_LABEL}}", STAND_IN_LABEL)
            .replace("{{CASE_COUNT}}", count).replace("{{CASE_COUNT_TITLE}}", count.capitalize()))


def fixtures_text(demo: dict[str, Any], stand_in: bool, sel_sha: str) -> str:
    head = []
    if stand_in:
        head.append(f"// {STAND_IN_LABEL}")
    head.append(f"// Generated by {BUILDER_VERSION} from a selection file (sha256 {sel_sha[:16]}). "
                "Replays recorded runs. This page makes no network request.")
    body = json.dumps(demo, indent=2, ensure_ascii=False, sort_keys=False)
    return "\n".join(head) + "\nwindow.ADVISOR_DEMO = " + body + ";\n"


def parse_fixtures(text: str) -> Any:
    body = text.split("window.ADVISOR_DEMO = ", 1)[1].rstrip().rstrip(";")
    return json.loads(body)


# Fixture fields that carry recorded text word for word (model output, page quotes,
# the owner's own words, search queries, page titles). The dash rule (DESIGN section 7)
# still applies to them, since the page shows them, but a hit names the field so it
# can be found and decided on at once; the builder never edits them.
VERBATIM_KEYS = frozenset({
    "reason", "classifier_verdict", "evidence", "brief_quote", "found", "why_insufficient", "why_shown",
    "symptom", "query", "queries", "searched_instead", "source_title", "stop_reason",
    "manufacturer", "model", "serial", "manufacture_date", "value", "confirmed_code",
    "summary", "statement", "terms", "step",
})


def _field_key(spath: str) -> str:
    return re.sub(r"\[\d+\]", "", spath).rsplit(".", 1)[-1]


def is_verbatim_field(spath: str) -> bool:
    key = _field_key(spath)
    # A source's title is the page's own title; a case title is the builder's.
    return key in VERBATIM_KEYS or (key == "title" and ".sources[" in spath)


def is_off_origin(url: str) -> bool:
    url = url.strip()
    return url.startswith("//") or (bool(SCHEME_RE.match(url)) and not url.lower().startswith("data:"))


class _PageScan(HTMLParser):
    """Off origin loads in a page: src, srcset, poster, data, <link href>, style url(, inline scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        for attr in ("src", "poster", "data"):
            if attr in values and is_off_origin(values[attr]):
                self.problems.append(f"off origin load <{tag} {attr}={values[attr]}>")
        if "srcset" in values:
            for part in values["srcset"].split(","):
                url = part.strip().split()[0] if part.strip() else ""
                if url and is_off_origin(url):
                    self.problems.append(f"off origin load <{tag} srcset={url}>")
        if tag == "link" and "href" in values and is_off_origin(values["href"]):
            self.problems.append(f"off origin load <link href={values['href']}>")
        if "style" in values:
            for m in re.finditer(r"""url\(\s*["']?([^"')\s]+)""", values["style"], re.I):
                if is_off_origin(m.group(1)):
                    self.problems.append(f"off origin css load {m.group(1)}")
        if tag in ("script", "style"):
            self._in = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._in:
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in == "script":
            self.problems += [f"network call {m.group()} in an inline script" for m in NETWORK_API_RE.finditer(data)]
        elif self._in == "style":
            for m in re.finditer(r"""url\(\s*["']?([^"')\s]+)""", data, re.I):
                if is_off_origin(m.group(1)):
                    self.problems.append(f"off origin css load {m.group(1)}")


def scan_text_file(rel: str, text: str, denylist: list[str], *, verbatim_brief: bool) -> list[str]:
    """Key, path, denylist, project word, dash and network checks over one text file."""
    hits: list[str] = []
    lowered = text.lower()
    for name, pattern in privacy_diff.KEY_RES.items():
        if pattern.search(text):
            hits.append(f"{rel}: {name}")
    if privacy_diff.HEADER_RE.search(text) or "bearer " in lowered:
        hits.append(f"{rel}: auth header")
    if "/users/" in lowered:
        hits.append(f"{rel}: a local path")
    if DOTENV_RE.search(text):
        hits.append(f"{rel}: .env")
    for entry in denylist:
        if entry in lowered:
            hits.append(f"{rel}: a denylist entry")
    for word in _FORBIDDEN_WORDS:
        if word in lowered:
            hits.append(f"{rel}: a forbidden project word")
    if verbatim_brief:
        return hits
    if rel.endswith("fixtures.js"):
        # Dashes are checked per field below, so the hit names the field.
        header = text.split("window.ADVISOR_DEMO = ", 1)[0]
        hits += [f"{rel}: header comment: dash U+{ord(d):04X} in authored text" for d in DASHES if d in header]
    else:
        hits += [f"{rel}: dash U+{ord(d):04X} in authored text" for d in DASHES if d in text]
    suffix = Path(rel).suffix.lower()
    if suffix == ".js":
        hits += [f"{rel}: network call {m.group()}" for m in NETWORK_API_RE.finditer(text)]
    if suffix == ".html":
        scanner = _PageScan()
        scanner.feed(text)
        hits += [f"{rel}: {p}" for p in scanner.problems]
    if suffix == ".css":
        for m in re.finditer(r"""(?:url\(|@import)\s*["']?([^"')\s;]+)""", text, re.I):
            if is_off_origin(m.group(1)):
                hits.append(f"{rel}: off origin css load {m.group(1)}")
    return hits


def scan_fixtures(rel: str, demo: Any, denylist: list[str], root: Path, repo_root: Path) -> list[str]:
    hits: list[str] = []
    for spath, s in privacy_diff.iter_strings(demo):
        for kind, _match in privacy_diff.scan_string(s, denylist):
            hits.append(f"{rel}: {spath}: {kind}")
        for dash in DASHES:
            if dash in s:
                where = "verbatim field, not edited by the builder" if is_verbatim_field(spath) else "authored text"
                hits.append(f"{rel}: {spath}: dash U+{ord(dash):04X} ({where})")
        key = spath.rsplit(".", 1)[-1]
        if key in ("plate", "share") and s:
            exists = (root / s).is_file() or (key == "plate" and s in PHOTO_ASSETS and (repo_root / s).is_file())
            if SCHEME_RE.match(s) or s.startswith("/") or ".." in s or not exists:
                hits.append(f"{rel}: {spath} is not a local published file")
    for hit in privacy_diff.scan_keys_and_numbers(demo, denylist):
        hits.append(f"{rel}: {hit['path']}: {hit['kind']}")
    return hits


def scan_output(root: Path, denylist: list[str], repo_root: Path,
                extra: dict[str, Path] | None = None, only: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Every check over everything in root (or only the listed relative paths), plus
    extra files the page loads from elsewhere."""
    hits: list[str] = []
    files = {p.relative_to(root).as_posix(): p for p in _files_under(root, only)}
    for rel, path in {**(extra or {}), **files}.items():
        if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            continue
        text = path.read_text(encoding="utf-8")
        hits += scan_text_file(rel, text, denylist, verbatim_brief=rel.startswith("briefs/v2/") and rel.endswith(".html"))
        if rel == "src/fixtures.js":
            hits += scan_fixtures(rel, parse_fixtures(text), denylist, root, repo_root)
    return hits


def readme_text(stand_in: bool, builds: list[str]) -> str:
    lines = []
    if stand_in:
        lines += [STAND_IN_LABEL, ""]
    lines += ["Recorded demo v2, local preview.", ""]
    if stand_in:
        lines += ["This folder is a browsable build of the v2 demo page made from stand-in runs",
                  f"of superseded build {', '.join(builds) or 'unknown'}. None of its numbers are results.", ""]
    else:
        lines += [f"Built from live runs on build {', '.join(builds)}.", ""]
    lines += [
        "To view it, serve this folder over http from inside it:",
        "",
        "    cd <this folder>",
        "    python3 -m http.server 8000",
        "",
        "then open http://localhost:8000/tool.html in a browser. Opened straight from disk",
        "(file://), browsers treat the brief frames and images as another origin and some",
        "will not show them. The page itself makes no network request beyond its own folder.",
        "",
        "What is here:",
        "",
        "    tool.html                  the page" + (", stamped as stand-in" if stand_in else ""),
        "    src/fixtures.js            the recorded data (window.ADVISOR_DEMO)",
        "    src/demo.js, src/demo.css  the page's script and styles",
        "    src/tokens.css             preview copy of the repo's design tokens, unchanged",
        "    demo-assets/*.jpg          preview copies of the published plate photos, unchanged",
        "    briefs/v2/<run>.html       each run's brief, byte for byte",
        "    build_report.json          what the builder read and wrote, and its warnings",
        "",
    ]
    return "\n".join(lines)


# Files only the builder writes into --out. A stale one carrying the stand-in label is
# removed when a later build does not write it.
BUILDER_OWNED = ("README.txt", "build_report.json")


def install(tmp: Path, out: Path) -> None:
    """Move a checked build into place, file by file; briefs/v2 is replaced as a whole."""
    out.mkdir(parents=True, exist_ok=True)
    this_build = {p.relative_to(tmp).as_posix() for p in tmp.rglob("*") if p.is_file()}
    new_briefs = tmp / "briefs" / "v2"
    if new_briefs.is_dir():
        old = out / "briefs" / "v2"
        if old.exists():
            shutil.rmtree(old)
        old.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(new_briefs), str(old))
    for path in sorted(p for p in tmp.rglob("*") if p.is_file()):
        dest = out / path.relative_to(tmp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
    for name in BUILDER_OWNED:
        stale = out / name
        if stale.is_file() and name not in this_build:
            try:
                if STAND_IN_LABEL in stale.read_text(encoding="utf-8"):
                    stale.unlink()
            except UnicodeDecodeError:
                pass


def build(args: argparse.Namespace) -> int:
    data_dir = Path(config.DATA_DIR)
    repo_root = Path(config.REPO_ROOT)
    out = Path(args.out).resolve()
    in_repo = out == repo_root or repo_root in out.parents
    if in_repo and not args.allow_repo_out:
        raise BuildError("--out is inside the repo; write somewhere else, or pass --allow-repo-out at install time")
    if in_repo and args.preview:
        raise BuildError("--preview copies repo files into --out; it cannot write into the repo")
    if not args.page and (out / "tool.html").exists():
        raise BuildError(f"--page is empty but {out / 'tool.html'} exists; it could be a page stamped for "
                         "other data. Pass the page template, or remove that file first.")
    sel_path = Path(args.selection)
    sel = load_selection(sel_path)
    sel_sha = sha256_file(sel_path)
    stand_in = bool(args.stand_in)

    runs = {role: RunFiles(run_id, data_dir) for role, run_id in sel["roles"].items()}
    also = {role: [RunFiles(r, data_dir) for r in ids] for role, ids in (sel.get("also_count") or {}).items()}
    all_runs = {rf.run_id: rf for rf in runs.values()}
    for extra in also.values():
        all_runs.update({rf.run_id: rf for rf in extra})
    # A fact's first run may not be selected; read it so its record can be checked.
    graph = read_json(config.GRAPH_PATH) if Path(config.GRAPH_PATH).is_file() else {"nodes": [], "edges": []}
    for edge in graph.get("edges") or []:
        first = edge.get("brief_run_id")
        if first and first not in all_runs and re.fullmatch(r"t-[0-9a-f]{16}", first):
            all_runs[first] = RunFiles(first, data_dir)

    current = current_build_id()
    reasons = guard(sel, {rf.run_id: rf for rf in list(runs.values()) + [x for v in also.values() for x in v]},
                    data_dir, current, stand_in=stand_in)
    if reasons and not stand_in:
        print("Refusing to build: the selected runs are not live runs on the current build.", file=sys.stderr)
        for r in reasons:
            print(f"  {r}", file=sys.stderr)
        print("Pass --stand-in to build a page stamped as stand-in data that must not be published.",
              file=sys.stderr)
        return 2
    if not reasons and stand_in:
        print("note: every run is on the current build; --stand-in still stamps the stand-in label.")

    denylist_path = Path(config.STAGING_DIR) / privacy_diff.DENYLIST_FILE
    if not denylist_path.is_file():
        raise BuildError(f"no denylist at {privacy_diff.DENYLIST_FILE} in the staging folder; the privacy scan "
                         "cannot run without it")
    denylist = privacy_diff.load_denylist(denylist_path)

    notes = Notes(stand_in)
    ledger = Ledger(Path(config.LEDGER_PATH))
    ctx: dict[str, Any] = {
        "repo_root": repo_root, "data_dir": data_dir, "ledger": ledger,
        "graph": graph, "all_runs": all_runs, "briefs": {},
        "report": {"runs": {}},
        "current_build": current, "superseded": superseded_builds(data_dir),
        "first_question_run": sel["roles"]["hot_tub_code_first"],
        "recorded_on": sel.get("recorded_on"),
    }

    for role, rf in runs.items():
        check_role(role, rf, notes, ledger)
    if notes.errors:
        raise BuildError("the selection does not fit its roles:\n  " + "\n  ".join(notes.errors))

    hot = [confirmed_identity(runs[r]) for r in HOT_TUB_ROLES + SYNTHETIC_RECORDS_ROLES if r in runs]
    if not all(same_unit(hot[0], h) for h in hot[1:]):
        notes.fail("the hot tub runs do not share one confirmed identity")
    ac = [confirmed_identity(runs[r]) for r in AC_ROLES]
    if not same_unit(ac[0], ac[1]):
        notes.fail("the air conditioner runs do not share one identity")
    if (runs["ac_first"].cassette or {}).get("input", {}).get("symptom") == \
            (runs["ac_new_symptom"].cassette or {}).get("input", {}).get("symptom"):
        notes.fail("the air conditioner's new symptom is the same as its first")

    cases = [build_case(i, role, runs[role], ctx, notes) for i, role in enumerate(selected_roles(sel))]
    by_role = {c["role"]: c for c in cases}
    for case in cases:
        if case["role"] in MEMORY_ROLES:
            add_memory(case, runs[case["role"]], ctx, notes)

    first, repeat = by_role["hot_tub_code_first"], by_role["hot_tub_code_repeat"]
    if repeat["ask"]["symptom"] != first["ask"]["symptom"] or repeat["ask"]["plate_sha256"] != first["ask"]["plate_sha256"]:
        notes.fail("the repeat run did not ask the same question with the same photo as the first question")
    repeat["ask"]["same_as"] = first["id"]
    for role, other in (sel.get("compare") or {"hot_tub_code_repeat": "hot_tub_code_first",
                                                "ac_new_symptom": "ac_first"}).items():
        if role not in by_role or other not in by_role:
            raise BuildError(f"compare names an unknown role: {role} or {other}")
        by_role[role]["compare_with"] = compare_rows(by_role[role], by_role[other])
        add_prior(by_role[role], by_role[other])

    nsm = by_role["no_such_model"]
    extra_runs = also.get("no_such_model") or []
    if extra_runs:
        main_rf = runs["no_such_model"]
        main_build = build_of_run(main_rf)
        main_question = (asked_symptom(main_rf), asked_identity(main_rf))
        rows = [{"run_id": nsm["run_id"], "status": nsm["outcome"]["status"]}]
        for rf in extra_runs:
            if build_of_run(rf) != main_build:
                notes.fail(f"no_such_model: also_count run {rf.run_id} is not on the same build as {nsm['run_id']}")
                continue
            if (asked_symptom(rf), asked_identity(rf)) != main_question:
                notes.disagree(f"no_such_model: also_count run {rf.run_id} did not ask the same question "
                               f"(symptom and unit) as {nsm['run_id']}")
                continue
            status = (rf.run or rf.eval or {}).get("status")
            rows.append({"run_id": rf.run_id, "status": status})
        nra_count = sum(1 for r in rows if r["status"] == "no_reliable_answer")
        nsm["also_count"] = {"runs": rows, "no_reliable_answer": nra_count, "total": len(rows)}

    for case in cases:
        case["takeaway"] = takeaway(case, by_role) + safety_takeaway(case)

    if notes.errors:
        raise BuildError("build checks failed:\n  " + "\n  ".join(notes.errors))
    if stand_in:
        # Every note starts with its role, so none is lost from its case.
        for case in cases:
            case["warnings"] = sorted(set(case["warnings"] + [w.split(": ", 1)[1] for w in notes.warnings
                                                               if w.startswith(case["role"] + ": ")]))
    unplaced = [w for w in notes.warnings if not any(w.startswith(r + ": ") for r in ALL_ROLES)]

    builds = sorted({b for b in (build_of_run(rf) for rf in runs.values()) if b})
    first_rec = next((c["recorded"] for c in cases if c["recorded"]), None)
    demo = {
        "format": 2,
        "data_status": "stand_in" if stand_in else "rerun",
        "stand_in_label": STAND_IN_LABEL if stand_in else None,
        "stand_in_reasons": reasons if stand_in else [],
        "build": {"build_id": builds[0] if len(builds) == 1 else None, "builds": builds,
                  "recorded": first_rec},
        "generated": {"builder": BUILDER_VERSION, "selection_sha256": sel_sha,
                      "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "replay_label": REPLAY_LABEL,
        "cases": cases,
    }
    fixtures = fixtures_text(demo, stand_in, sel_sha)
    if parse_fixtures(fixtures) != json.loads(json.dumps(demo)):
        raise BuildError("the fixtures file does not parse back to what was written")

    # --- write into a fresh folder next to --out, scan it, and only then install --------
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.name}.building-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    status = demo["data_status"]
    try:
        written: dict[str, dict[str, str]] = {}

        def put(rel: str, data: bytes, role: str) -> None:
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            written[rel] = {"sha256": sha256_bytes(data), "data_status": status, "use": role}

        for run_id, data in ctx["briefs"].items():
            put(f"briefs/v2/{run_id}.html", data, "install")
        if stand_in:
            # The brief files are verbatim and cannot carry the label, so the folder does.
            put("briefs/v2/STAND-IN.txt",
                (f"{STAND_IN_LABEL}\n\nEvery brief in this folder was written by a run on superseded build "
                 f"{', '.join(builds) or 'unknown'}. Do not install these files.\n").encode("utf-8"), "marker")
        put("src/fixtures.js", fixtures.encode("utf-8"), "install")
        if args.page:
            page = stamp_page(Path(args.page).read_text(encoding="utf-8"), stand_in, builds[0] if builds else "",
                              selected_roles(sel))
            put("tool.html", page.encode("utf-8"), "install")
        src_dir = Path(args.src_dir) if args.src_dir else None
        if args.preview:
            if src_dir is None or not src_dir.is_dir():
                raise BuildError("--preview needs --src-dir with demo.js and demo.css")
            for name in ("demo.js", "demo.css"):
                put(f"src/{name}", (src_dir / name).read_bytes(), "install")
            put("src/tokens.css", (repo_root / "src" / "tokens.css").read_bytes(), "preview copy")
            for rel in PHOTO_ASSETS:
                put(rel, (repo_root / rel).read_bytes(), "preview copy")
            put("README.txt", readme_text(stand_in, builds).encode("utf-8"), "preview")

        # The page's script and styles, when this build does not write them, are scanned
        # where they already are.
        extra = {}
        for name in ("src/demo.js", "src/demo.css"):
            if not (tmp / name).exists():
                candidate = (src_dir / Path(name).name) if src_dir else out / name
                if candidate.is_file():
                    extra[name] = candidate
        hits = scan_output(tmp, denylist, repo_root, extra)
        if not stand_in:
            selected = set(sel["roles"].values()) | {r for ids in (sel.get("also_count") or {}).values() for r in ids}
            hits += superseded_hits(tmp, superseded_ids(data_dir) - selected)
        report = {
            "builder": BUILDER_VERSION,
            "data_status": status,
            "why": reasons if stand_in else ["every selected run is a live run on the current build"],
            "current_build": current,
            "selection": {"sha256": sel_sha, "roles": sel["roles"]},
            "runs": ctx["report"]["runs"],
            "written": written,
            "warnings": notes.warnings,
            "warnings_not_on_the_page": unplaced,
            "denylist_entries": len(denylist),
            "scan_hits": hits,
            "estimate": "not shown: no run records the planner's estimate",
            "left_out": ["optional replay cases R1 and R2: not built. The live run with the seeded synthetic "
                         "records attached (role hot_tub_with_history) shows the prior service record, the "
                         "age from the install date and the warranty terms from a real run, so a replay of "
                         "the same records would add nothing."],
        }
        if hits:
            print("Scan failed; nothing was written to the output folder:", file=sys.stderr)
            for h in hits:
                print(f"  {h}", file=sys.stderr)
            return 3
        (tmp / "build_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        install(tmp, out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    label = "STAND-IN" if stand_in else "rerun"
    print(f"built {len(cases)} cases ({label}) into {out}")
    for w in notes.warnings:
        print(f"  warning: {w}")
    return 0


def asked_symptom(rf: RunFiles) -> str | None:
    return ((rf.cassette or {}).get("input") or {}).get("symptom") or ((rf.eval or {}).get("input") or {}).get("symptom")


def asked_identity(rf: RunFiles) -> tuple[str, str]:
    ident = confirmed_identity(rf) or identity_of(((rf.eval or {}).get("input") or {}).get("identity")) or {}
    return ((ident.get("manufacturer") or "").lower(), (ident.get("model") or "").lower())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--selection", required=True, help="the JSON file mapping roles to run IDs")
    parser.add_argument("--out", required=True, help="the folder to write into")
    parser.add_argument("--stand-in", action="store_true",
                        help="build from runs that are not live runs on the current build, stamped as stand-in data")
    parser.add_argument("--page", default=str(Path(__file__).with_name("demo_page.html")),
                        help="the page template to stamp into <out>/tool.html (empty to skip; refused when "
                             "<out>/tool.html already exists)")
    parser.add_argument("--src-dir", default=None,
                        help="folder holding demo.js and demo.css (copied with --preview, scanned either way)")
    parser.add_argument("--preview", action="store_true",
                        help="also copy demo.js, demo.css, the repo's tokens.css and plate photos, and a README, "
                             "so the output folder can be served on its own (never into the repo)")
    parser.add_argument("--allow-repo-out", action="store_true", help="allow --out inside the repo (install only)")
    args = parser.parse_args(argv)
    try:
        return build(args)
    except BuildError as exc:
        print(f"build_demo: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
