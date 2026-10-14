"""SC12c, Phase 0: Roanuk labels the 175 held out items blind, and his labels score the arms (decision 70).

    python -m agent.safety_eval.sc12c build      # writes data/eval/sc12c/page.html (--seed restores a lost order)
    python -m agent.safety_eval.sc12c serve      # http://127.0.0.1:8770/ until Ctrl+C
    python -m agent.safety_eval.sc12c seal       # once every item has an answer
    python -m agent.safety_eval.sc12c score      # only against a matching seal
    python -m agent.safety_eval.sc12c revise ID --safety yes --hazard electrical --reason "..."

He sees exactly what the three AI readers had: PROTOCOL.md's "Label protocol"
block and its six worked examples, verbatim, and each item as only its id,
appliance, step and detail (harness.READER_KEYS, a whitelist), plus a display
index, one at a time in a random order from a recorded seed. No AI label, Jev
answer or word rule result is in the page or its data.

Under data/eval/sc12c/ (gitignored; page text stays there): page.html,
build.json (the order seed, the order and the protocol's sha256),
answers.jsonl (every answer as the local server received it; the latest per
item wins and every earlier one is kept), seal.json (a copy of the seal, so
a cleanup of the committed files cannot unseal), disagreements.json (with
text) and revisions.json (each revision with its reason, which may quote the
step).

Committed beside this module (item ids, answers and numbers only, never
text): human_labels.json, sealed by human_labels.seal.json before any score
exists; sc12c_scores.json; and human_labels_revised.json, where a label
changed after scoring is recorded by id and answer, its reason kept in the
local revisions.json. The sealed file never changes, the blind labels are
sealed only once and never after a score, and every score reports the sealed
blind labels. Once sealed, the server takes no answer and will not start;
an answer logged after the seal anyway is counted and reported by the score,
never scored.

Read only on every SC12a file: items.json, the replies, lock.json,
labels.json and item_ids.json are read and never written, and no harness
function that writes (import_labels, write_reader_packet, score_heldout,
write_lock) is called. No call of any kind is made: the server binds
127.0.0.1 and nothing here opens a connection.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import random
import secrets
import socketserver
import sys
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import config
from agent.safety_eval import harness, pools, stats
from agent.safety_eval.labels import HAZARDS, IN_SCOPE_HAZARDS, SAFETY_ANSWERS, SPLIT, positive_vote

SUITE = "sc12c"
COMMITTED_DIR = Path(__file__).resolve().parent
PROTOCOL_PATH = COMMITTED_DIR / "PROTOCOL.md"
HUMAN_LABELS = "human_labels.json"
SEAL = "human_labels.seal.json"
REVISED = "human_labels_revised.json"
SCORES = "sc12c_scores.json"
LOCAL_SEAL = "seal.json"
LOCAL_REVISIONS = "revisions.json"

# The two PROTOCOL.md sections the readers had beside the items, in the order shown.
PROTOCOL_HEADINGS = ("## Label protocol", "## Worked examples, all from published briefs")
WORKED_EXAMPLES = 6

# What the page carries per item: the readers' keys and a display index, nothing else.
PAYLOAD_KEYS = (*harness.READER_KEYS, "index")
ANSWER_KEYS = ("id", "safety", "hazard")
LOGGED_KEYS = (*ANSWER_KEYS, "at")

HOST = "127.0.0.1"
PORT = 8770
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
MAX_BODY_BYTES = 2048
RECORDED_PLACEHOLDER = '<script type="application/json" id="recorded-data">{}</script>'

# The SC12a lock file (Gate 2) and the published held out arms against the AI
# majority (DECISION-LOG, 30 September): the scorer reproduces these exactly
# before it prints anything against Roanuk's labels. (true positives, flags, positives)
LOCK_FILE_SHA256 = "cc217fbb61ec6b3ab71c6e26fe59faf466f12c9641bf1dd2106c6e4f34dad5c9"
JEV_ARM = "Jev, wording 3 at 0.51"
PUBLISHED_ARM = "word rule, as published"
TUNED_ARM = "word rule, tuned"
PUBLISHED_AI_ARMS = {JEV_ARM: (30, 41, 31), PUBLISHED_ARM: (20, 28, 31), TUNED_ARM: (22, 34, 31)}

EXIT_OK = 0
EXIT_REFUSED = 1


class Sc12cRefusal(harness.EvalRefusal):
    """A rule of decision 70 refused the command before anything was written."""


def out_dir() -> Path:
    return config.EVAL_DIR / SUITE


def page_path() -> Path:
    return out_dir() / "page.html"


def build_path() -> Path:
    return out_dir() / "build.json"


def answers_path() -> Path:
    return out_dir() / "answers.jsonl"


def disagreements_path() -> Path:
    return out_dir() / "disagreements.json"


def local_seal_path() -> Path:
    return out_dir() / LOCAL_SEAL


def local_revisions_path() -> Path:
    return out_dir() / LOCAL_REVISIONS


def committed(name: str) -> Path:
    return COMMITTED_DIR / name


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# The protocol, verbatim
# ---------------------------------------------------------------------------


def protocol_section(text: str, heading: str) -> str:
    """The text under one "## " heading of PROTOCOL.md, exactly as written, less the blank lines around it."""
    lines = text.split("\n")
    if heading not in lines:
        raise Sc12cRefusal(f"{PROTOCOL_PATH.name} has no {heading!r} heading; the page shows that block verbatim.")
    start = lines.index(heading) + 1
    end = next((n for n in range(start, len(lines)) if lines[n].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip("\n")


def example_rows(block: str) -> list[str]:
    """The worked examples table's rows, less its header and separator."""
    rows = [line for line in block.split("\n") if line.startswith("|")]
    return rows[2:]


def protocol_blocks(path: Path | None = None) -> list[tuple[str, str]]:
    """(heading, block) for the label protocol and the worked examples, as the readers had them."""
    text = Path(path or PROTOCOL_PATH).read_text(encoding="utf-8")
    blocks = [(heading.removeprefix("## "), protocol_section(text, heading)) for heading in PROTOCOL_HEADINGS]
    if len(example_rows(blocks[1][1])) != WORKED_EXAMPLES:
        raise Sc12cRefusal(f"{PROTOCOL_PATH.name}'s worked examples table has "
                           f"{len(example_rows(blocks[1][1]))} rows, not {WORKED_EXAMPLES}.")
    return blocks


def protocol_sha256(blocks: Sequence[tuple[str, str]]) -> str:
    return harness.sha256_of([list(b) for b in blocks])


# ---------------------------------------------------------------------------
# The held out items and the page
# ---------------------------------------------------------------------------


def heldout_items() -> list[dict[str, Any]]:
    """The held out half of data/eval/sc12a/items.json, read only."""
    return [dict(i) for i in harness.load_items()["items"] if i["half"] == pools.HELDOUT]


def ordered_ids(ids: Iterable[str], seed: str) -> list[str]:
    order = sorted(ids)
    random.Random(seed).shuffle(order)
    return order


def payload(items: Sequence[Mapping[str, Any]], order: Sequence[str]) -> list[dict[str, Any]]:
    """What the page carries: per item exactly PAYLOAD_KEYS, in display order (a whitelist)."""
    by_id = {i["id"]: i for i in items}
    return [{**{key: by_id[item_id].get(key) or "" for key in harness.READER_KEYS}, "index": n}
            for n, item_id in enumerate(order, start=1)]


def script_json(value: Any) -> str:
    """JSON safe inside a <script> data block: no "<", ">" or "&" survives unescaped."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def csp_hash(source: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")


def content_security_policy(style: str, script: str) -> str:
    """Nothing loads but the page's own inline style and script; the script may only POST back to its origin."""
    return (f"default-src 'none'; style-src '{csp_hash(style)}'; script-src '{csp_hash(script)}'; "
            "connect-src 'self'; img-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'")


PAGE_STYLE = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1a1a1a; --muted: #595959; --line: #cfcfcf;
  --card: #f5f5f5; --accent: #1f5fbf; --on-accent: #ffffff; --warn: #a33a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #141414; --fg: #ececec; --muted: #a9a9a9; --line: #3b3b3b; --card: #1f1f1f;
    --accent: #8ab4ff; --on-accent: #0b0b0b; --warn: #ffb08a; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 17px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 42rem; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.35rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 20px 0 6px; }
.status { color: var(--muted); margin: 0 0 12px; }
#toggle-protocol { margin-bottom: 16px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; padding: 12px; border: 1px solid var(--line);
  border-radius: 6px; background: var(--card); font: 14px/1.45 ui-monospace, Menlo, Consolas, monospace; }
.card { margin: 12px 0; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--card); }
.field { margin: 0 0 10px; }
.field:last-child { margin-bottom: 0; }
.name { display: block; color: var(--muted); font-size: 0.85rem; }
.text { overflow-wrap: anywhere; }
fieldset { margin: 16px 0 0; padding: 0; border: 0; }
legend { margin-bottom: 8px; padding: 0; font-weight: 600; }
.choices, .nav { display: flex; flex-wrap: wrap; gap: 8px; }
.nav { margin-top: 20px; }
button { min-width: 44px; min-height: 44px; padding: 8px 16px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--fg); font: inherit; cursor: pointer; }
button:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent); color: var(--on-accent); }
button:disabled { opacity: 0.45; cursor: default; }
.saved { min-height: 1.5em; margin: 12px 0 0; color: var(--muted); }
.saved.error { color: var(--warn); }
.keys { margin-top: 16px; color: var(--muted); font-size: 0.85rem; }
[hidden] { display: none !important; }
"""

PAGE_SCRIPT = """
(function () {
  "use strict";
  var items = JSON.parse(document.getElementById("items-data").textContent);
  var recorded = JSON.parse(document.getElementById("recorded-data").textContent);
  var STORE = "sc12c-progress";
  var known = {};
  items.forEach(function (it) { known[it.id] = true; });
  var answers = {};
  var pos = -1;
  var showProtocol = true;
  var errorText = "";
  var busy = false;

  function $(id) { return document.getElementById(id); }

  try {
    var raw = window.localStorage.getItem(STORE);
    if (raw) {
      var kept = JSON.parse(raw);
      Object.keys(kept.answers || {}).forEach(function (id) {
        var a = kept.answers[id];
        if (known[id] && a) { answers[id] = {safety: a.safety || null, hazard: a.hazard || null, saved: !!a.saved}; }
      });
      if (typeof kept.pos === "number" && kept.pos >= 0 && kept.pos < items.length) { pos = kept.pos; }
      if (kept.showProtocol === false) { showProtocol = false; }
    }
  } catch (e) { /* storage is a convenience; the server keeps every answer */ }

  // The server's log is the record: a local "saved" it does not hold is sent again.
  Object.keys(answers).forEach(function (id) {
    if (answers[id].saved && !recorded[id]) { answers[id].saved = false; }
  });
  Object.keys(recorded).forEach(function (id) {
    var r = recorded[id];
    if (known[id] && (!answers[id] || answers[id].saved)) { answers[id] = {safety: r[0], hazard: r[1], saved: true}; }
  });

  function store() {
    try {
      window.localStorage.setItem(STORE, JSON.stringify({answers: answers, pos: pos, showProtocol: showProtocol}));
    } catch (e) { /* see above */ }
  }

  function complete(a) { return !!(a && a.safety && a.hazard); }

  function savedCount() {
    var n = 0;
    items.forEach(function (it) { var a = answers[it.id]; if (complete(a) && a.saved) { n += 1; } });
    return n;
  }

  function nextOpen(from) {
    for (var k = 0; k < items.length; k += 1) {
      var j = (from + k) % items.length;
      var a = answers[items[j].id];
      if (!complete(a) || !a.saved) { return j; }
    }
    return -1;
  }

  function render() {
    var it = items[pos];
    var a = answers[it.id] || {};
    $("appliance").textContent = it.appliance || "(none)";
    $("step").textContent = it.step || "(none)";
    $("detail").textContent = it.detail || "(empty)";
    $("position").textContent = "Item " + it.index + " of " + items.length;
    var done = savedCount();
    $("progress").textContent = done + " of " + items.length + " answered and saved";
    Array.prototype.forEach.call(document.querySelectorAll("[data-safety]"), function (b) {
      b.setAttribute("aria-pressed", String(a.safety === b.getAttribute("data-safety")));
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-hazard]"), function (b) {
      b.setAttribute("aria-pressed", String(a.hazard === b.getAttribute("data-hazard")));
    });
    var note = $("saved");
    note.classList.remove("error");
    if (complete(a) && a.saved) {
      note.textContent = "Saved.";
    } else if (complete(a)) {
      note.textContent = errorText ? "Not saved: " + errorText : "Saving.";
      if (errorText) { note.classList.add("error"); }
    } else if (a.safety || a.hazard) {
      note.textContent = "Choose both answers to save this item.";
    } else {
      note.textContent = "";
    }
    $("back").disabled = pos === 0;
    $("next").disabled = pos === items.length - 1;
    $("next-open").disabled = nextOpen(pos + 1) === -1;
    $("done").hidden = done < items.length;
    $("protocol").hidden = !showProtocol;
    $("toggle-protocol").setAttribute("aria-expanded", String(showProtocol));
    $("toggle-protocol").textContent = showProtocol ? "Hide the protocol" : "Show the protocol";
  }

  function flush() {
    if (busy) { return; }
    var id = null;
    for (var k = 0; k < items.length; k += 1) {
      var a = answers[items[k].id];
      if (complete(a) && !a.saved) { id = items[k].id; break; }
    }
    if (id === null) { return; }
    var sent = {id: id, safety: answers[id].safety, hazard: answers[id].hazard};
    busy = true;
    fetch("/answer", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(sent),
                      cache: "no-store", credentials: "same-origin", redirect: "error"})
      .then(function (r) {
        return r.json().then(function (j) { return {ok: r.ok && j.ok === true, error: j.error}; },
                             function () { return {ok: false, error: "the server sent no answer"}; });
      })
      .then(function (res) {
        busy = false;
        var cur = answers[id];
        if (res.ok) {
          errorText = "";
          if (cur && cur.safety === sent.safety && cur.hazard === sent.hazard) { cur.saved = true; }
          store();
          render();
          flush();
        } else {
          errorText = res.error || "the server refused the answer";
          render();
        }
      }, function () {
        busy = false;
        errorText = "the local server is not running. Start it again; the answer is sent on your next choice or move.";
        render();
      });
  }

  function choose(kind, value) {
    var id = items[pos].id;
    var a = answers[id] || {safety: null, hazard: null, saved: false};
    if (a[kind] === value) { flush(); return; }
    a[kind] = value;
    a.saved = false;
    answers[id] = a;
    errorText = "";
    store();
    render();
    flush();
  }

  function go(to) {
    if (to < 0 || to >= items.length) { return; }
    pos = to;
    errorText = "";
    store();
    render();
    flush();
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-safety]"), function (b) {
    b.addEventListener("click", function () { choose("safety", b.getAttribute("data-safety")); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-hazard]"), function (b) {
    b.addEventListener("click", function () { choose("hazard", b.getAttribute("data-hazard")); });
  });
  $("back").addEventListener("click", function () { go(pos - 1); });
  $("next").addEventListener("click", function () { go(pos + 1); });
  $("next-open").addEventListener("click", function () { go(nextOpen(pos + 1)); });
  $("toggle-protocol").addEventListener("click", function () { showProtocol = !showProtocol; store(); render(); });

  var HAZARD_KEYS = ["electrical", "heat", "gas", "other", "none"];
  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var key = e.key;
    if (key === "y" || key === "Y") { choose("safety", "yes"); }
    else if (key === "n" || key === "N") { choose("safety", "no"); }
    else if (key >= "1" && key <= "5" && key.length === 1) { choose("hazard", HAZARD_KEYS[Number(key) - 1]); }
    else if (key === "ArrowLeft") { go(pos - 1); }
    else if (key === "ArrowRight") { go(pos + 1); }
    else { return; }
    e.preventDefault();
  });

  if (pos < 0) { pos = nextOpen(0); }
  if (pos < 0) { pos = 0; }
  render();
  flush();
}());
"""

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="@CSP@">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Safety step check</title>
<style>@STYLE@</style>
</head>
<body>
<main>
<h1>Safety step check</h1>
<p class="status" id="progress" aria-live="polite"></p>
<button type="button" id="toggle-protocol" aria-expanded="true" aria-controls="protocol">Hide the protocol</button>
<section id="protocol" aria-label="Protocol">
@PROTOCOL@
</section>
<section id="done" hidden>
<h2>All answered</h2>
<p>Every answer is saved by the local server. You can close this page, or go back and change an answer; a change is saved until the labels are sealed, and after that only as a recorded revision.</p>
</section>
<section id="item" aria-label="Item">
<p class="status" id="position"></p>
<div class="card">
<p class="field"><span class="name">Appliance</span><span class="text" id="appliance"></span></p>
<p class="field"><span class="name">Step</span><span class="text" id="step"></span></p>
<p class="field"><span class="name">Detail</span><span class="text" id="detail"></span></p>
</div>
<fieldset>
<legend>Safety step</legend>
<div class="choices">
<button type="button" data-safety="yes" aria-pressed="false">Yes</button>
<button type="button" data-safety="no" aria-pressed="false">No</button>
</div>
</fieldset>
<fieldset>
<legend>Hazard</legend>
<div class="choices">
@HAZARD_BUTTONS@
</div>
</fieldset>
<p class="saved" id="saved" aria-live="polite"></p>
<div class="nav">
<button type="button" id="back">Back</button>
<button type="button" id="next">Next</button>
<button type="button" id="next-open">Next unanswered</button>
</div>
<p class="keys">Keys: Y or N for the safety step, 1 to 5 for the hazard in the order shown, and the left and right arrows for back and next.</p>
</section>
</main>
<script type="application/json" id="items-data">@ITEMS@</script>
@RECORDED@
<script>@SCRIPT@</script>
</body>
</html>
"""


def protocol_html(blocks: Sequence[tuple[str, str]]) -> str:
    ids = ("protocol-label", "protocol-examples")
    return "\n".join(f'<h2>{html.escape(heading)}</h2>\n<pre id="{pre_id}">{html.escape(block, quote=False)}</pre>'
                     for pre_id, (heading, block) in zip(ids, blocks, strict=True))


def render_page(items: Sequence[Mapping[str, Any]], blocks: Sequence[tuple[str, str]]) -> str:
    """The one self contained page: inline style and script under a hash pinned policy, no external load."""
    buttons = "\n".join(f'<button type="button" data-hazard="{h}" aria-pressed="false">{h}</button>' for h in HAZARDS)
    parts = {"@CSP@": html.escape(content_security_policy(PAGE_STYLE, PAGE_SCRIPT)), "@STYLE@": PAGE_STYLE,
             "@PROTOCOL@": protocol_html(blocks), "@HAZARD_BUTTONS@": buttons,
             "@ITEMS@": script_json([{key: item[key] for key in PAYLOAD_KEYS} for item in items]),
             "@RECORDED@": RECORDED_PLACEHOLDER, "@SCRIPT@": PAGE_SCRIPT}
    page = PAGE_TEMPLATE
    for marker, value in parts.items():
        page = page.replace(marker, value, 1)
    return page


def served_page(page: str, latest: Mapping[str, tuple[str, str]]) -> str:
    """The page with the answers the server already holds, so a reload resumes without localStorage."""
    if page.count(RECORDED_PLACEHOLDER) != 1:
        raise Sc12cRefusal(f"{page_path()} is not a page built by this module; run build again.")
    recorded = {item_id: list(answer) for item_id, answer in sorted(latest.items())}
    return page.replace(RECORDED_PLACEHOLDER, RECORDED_PLACEHOLDER.replace(">{}<", f">{script_json(recorded)}<"))


def build(seed: str | None = None) -> dict[str, Any]:
    """`build`: data/eval/sc12c/page.html and build.json; a rebuild keeps the recorded seed, so the order holds."""
    items = heldout_items()
    blocks = protocol_blocks()
    protocol = protocol_sha256(blocks)
    previous = harness.read_json(build_path()) if build_path().is_file() else None
    if previous is None and seed is None and answers_path().is_file():
        raise Sc12cRefusal(f"{answers_path()} holds answers but {build_path()} is missing; the order they were "
                           "given in is not recorded, so no new order is drawn. Pass the original seed (--seed) "
                           "to restore it.")
    if previous is not None:
        if seed is not None and seed != previous["order_seed"]:
            raise Sc12cRefusal(f"{build_path()} records order seed {previous['order_seed']}; the order is fixed once "
                               "built.")
        seed = previous["order_seed"]
        if answers_path().is_file() and previous["protocol_sha256"] != protocol:
            raise Sc12cRefusal(f"{PROTOCOL_PATH.name} changed since answers began (protocol sha256 "
                               f"{previous['protocol_sha256']} then, {protocol} now); answers under two protocols "
                               "are never mixed.")
    seed = seed or secrets.token_hex(8)
    order = ordered_ids((i["id"] for i in items), seed)
    page = render_page(payload(items, order), blocks)
    _write_text(page_path(), page)
    info = {"built_at": _now(), "order_seed": seed, "protocol_sha256": protocol, "items": len(order),
            "ids_sha256": harness.sha256_of(sorted(order)), "order": order,
            "page_sha256": hashlib.sha256(page.encode("utf-8")).hexdigest()}
    harness.write_json(build_path(), info)
    return info


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def answer_error(entry: Any, known: set[str]) -> str | None:
    """Why one answer is not valid, or None: a known held out id, safety yes or no, one of the five hazards."""
    if not isinstance(entry, dict) or set(entry) != set(ANSWER_KEYS):
        return f"an answer is exactly {', '.join(ANSWER_KEYS)}"
    if entry["id"] not in known:
        return "not a held out item id"
    if entry["safety"] not in SAFETY_ANSWERS:
        return f"safety is one of {', '.join(SAFETY_ANSWERS)}"
    if entry["hazard"] not in HAZARDS:
        return f"hazard is one of {', '.join(HAZARDS)}"
    return None


def append_answer(path: Path, entry: Mapping[str, str]) -> dict[str, str]:
    line = {**{key: entry[key] for key in ANSWER_KEYS}, "at": datetime.now(UTC).isoformat(timespec="milliseconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(harness.canonical(line) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return line


def read_answers(path: Path, known: set[str]) -> list[dict[str, str]]:
    """Every logged answer, in order; a line that is not a valid answer refuses (the log is never edited)."""
    if not path.is_file():
        return []
    entries = []
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            raise Sc12cRefusal(f"{path} line {n} is not JSON; the answer log is append only.") from None
        if not isinstance(entry, dict) or set(entry) != set(LOGGED_KEYS) or not isinstance(entry["at"], str):
            raise Sc12cRefusal(f"{path} line {n} is not {', '.join(LOGGED_KEYS)}; the answer log is append only.")
        why = answer_error({key: entry[key] for key in ANSWER_KEYS}, known)
        if why:
            raise Sc12cRefusal(f"{path} line {n}: {why}; the answer log is append only.")
        entries.append(entry)
    return entries


def latest_answers(entries: Iterable[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for entry in entries:
        latest[entry["id"]] = dict(entry)
    return latest


def changed_answers(entries: Iterable[Mapping[str, str]]) -> tuple[int, int]:
    """(answers that replaced a different earlier answer to the same item, items with at least one)."""
    last: dict[str, tuple[str, str]] = {}
    changes = 0
    items: set[str] = set()
    for entry in entries:
        answer = (entry["safety"], entry["hazard"])
        if entry["id"] in last and last[entry["id"]] != answer:
            changes += 1
            items.add(entry["id"])
        last[entry["id"]] = answer
    return changes, len(items)


# ---------------------------------------------------------------------------
# The local server: one page, one POST endpoint
# ---------------------------------------------------------------------------


def host_allowed(value: str | None) -> bool:
    """A Host (or an Origin less its scheme) naming 127.0.0.1 or localhost, with an optional port."""
    name, sep, port = (value or "").strip().lower().partition(":")
    return name in ALLOWED_HOSTS and (not sep or port.isdigit())


class AnswerState:
    """What the handler reads from its server: the page, the held out ids and the answer log."""

    def setup_state(self, page: str, known: set[str], log: Path) -> None:
        self.page = page
        self.known = set(known)
        self.log = Path(log)
        self.write_lock = threading.Lock()
        self.latest = {k: (v["safety"], v["hazard"]) for k, v in latest_answers(read_answers(self.log, self.known)).items()}


class AnswerServer(AnswerState, ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], page: str, known: set[str], log: Path) -> None:
        self.setup_state(page, known, log)
        super().__init__(address, AnswerHandler)


class UnixAnswerServer(AnswerState, socketserver.ThreadingUnixStreamServer):
    """The same server on a Unix socket, for the tests (the test process may open no TCP socket)."""

    daemon_threads = True

    def __init__(self, address: str, page: str, known: set[str], log: Path) -> None:
        self.setup_state(page, known, log)
        super().__init__(address, AnswerHandler)


ROUTES = {"/": "GET", "/answer": "POST"}


class AnswerHandler(BaseHTTPRequestHandler):
    server_version = "sc12c"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (the base class's name)
        return  # quiet: the answer log is the record

    def _send(self, status: int, body: bytes, content_type: str, extra: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: Mapping[str, Any]) -> None:
        self._send(status, (json.dumps(value) + "\n").encode("utf-8"), "application/json")

    def _route(self) -> None:
        if not host_allowed(self.headers.get("Host")):
            return self._json(403, {"ok": False, "error": "the Host must be 127.0.0.1 or localhost"})
        path = self.path.split("?", 1)[0]
        if path not in ROUTES:
            return self._json(404, {"ok": False, "error": "not found"})
        if self.command != ROUTES[path]:
            self.close_connection = True
            return self._send(405, b"method not allowed\n", "text/plain; charset=utf-8", {"Allow": ROUTES[path]})
        if path == "/":
            page = served_page(self.server.page, self.server.latest)
            csp = content_security_policy(PAGE_STYLE, PAGE_SCRIPT)
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Security-Policy": csp})
        return self._answer()

    def _answer(self) -> None:
        origin = self.headers.get("Origin")
        if origin is not None and not (origin.startswith("http://") and host_allowed(origin[len("http://"):])):
            return self._json(403, {"ok": False, "error": "the Origin must be this page"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._json(415, {"ok": False, "error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self._json(411, {"ok": False, "error": "a Content-Length is needed"})
        if not 0 < length <= MAX_BODY_BYTES:
            self.close_connection = True
            return self._json(413, {"ok": False, "error": f"an answer is at most {MAX_BODY_BYTES} bytes"})
        try:
            entry = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._json(400, {"ok": False, "error": "not JSON"})
        why = answer_error(entry, self.server.known)
        if why:
            return self._json(400, {"ok": False, "error": why})
        if committed(SEAL).exists():
            return self._json(409, {"ok": False, "error": "the labels are sealed; a later change is recorded with "
                                                          "revise"})
        with self.server.write_lock:
            append_answer(self.server.log, entry)
            self.server.latest[entry["id"]] = (entry["safety"], entry["hazard"])
            answered = len(self.server.latest)
        return self._json(200, {"ok": True, "answered": answered, "items": len(self.server.known)})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _route


def serve_state() -> tuple[str, set[str]]:
    """The built page and the held out ids, checked against each other; refused once the labels are sealed."""
    if committed(SEAL).exists():
        raise Sc12cRefusal(f"{committed(SEAL)} exists: the labels are sealed, so the page takes no more answers. "
                           "Record a change with revise.")
    if not page_path().is_file() or not build_path().is_file():
        raise Sc12cRefusal(f"no page at {page_path()}: run python -m agent.safety_eval.sc12c build first.")
    info = harness.read_json(build_path())
    known = {i["id"] for i in heldout_items()}
    if harness.sha256_of(sorted(known)) != info["ids_sha256"]:
        raise Sc12cRefusal("the held out ids differ from the ones the page was built with; run build again.")
    page = page_path().read_text(encoding="utf-8")
    if hashlib.sha256(page.encode("utf-8")).hexdigest() != info["page_sha256"]:
        raise Sc12cRefusal(f"{page_path()} changed since it was built; run build again.")
    return page, known


def serve(port: int = PORT) -> None:
    """`serve`: bind 127.0.0.1 only, append every valid answer to answers.jsonl, stop on Ctrl+C."""
    page, known = serve_state()
    server = AnswerServer((HOST, port), page, known, answers_path())
    print(f"SC12c: open http://{HOST}:{port}/ in a browser on this computer "
          f"({len(server.latest)} of {len(known)} items answered so far).")
    print(f"answers are appended to {answers_path()}; press Ctrl+C here to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print(f"stopped; {len(server.latest)} of {len(known)} items answered.")


# ---------------------------------------------------------------------------
# Seal
# ---------------------------------------------------------------------------


def seal() -> dict[str, Any]:
    """`seal`: every held out item answered; the latest answer per item becomes the blind labels, sealed by sha256."""
    labels_file, seal_file = committed(HUMAN_LABELS), committed(SEAL)
    if seal_file.exists():
        raise Sc12cRefusal(f"{seal_file} exists: the blind labels are sealed once. Record a change with revise.")
    scored = [p for p in (committed(SCORES), committed(REVISED), disagreements_path()) if p.exists()]
    if scored:
        raise Sc12cRefusal(f"a score already exists ({', '.join(p.name for p in scored)}); the blind labels were "
                           "sealed before it and are never sealed again.")
    if local_seal_path().exists():
        raise Sc12cRefusal(f"{local_seal_path()} records an earlier seal: the blind labels are sealed once, even "
                           "when the committed files are gone.")
    if labels_file.exists():
        raise Sc12cRefusal(f"{labels_file} exists without a seal; it was not written by seal. Look at it by hand.")
    if not build_path().is_file():
        raise Sc12cRefusal(f"no {build_path()}: build the page and answer every item first.")
    info = harness.read_json(build_path())
    protocol = protocol_sha256(protocol_blocks())
    if info["protocol_sha256"] != protocol:
        raise Sc12cRefusal(f"{PROTOCOL_PATH.name} changed since the page was built (protocol sha256 "
                           f"{info['protocol_sha256']} then, {protocol} now).")
    known = {i["id"] for i in heldout_items()}
    entries = read_answers(answers_path(), known)
    latest = latest_answers(entries)
    missing = sorted(known - set(latest))
    if missing:
        raise Sc12cRefusal(f"{len(missing)} of {len(known)} held out items have no answer yet; answer every item "
                           "before sealing.")
    changes, items_changed = changed_answers(entries)
    doc = {
        "note": ("SC12c blind labels by Roanuk Zaman on the SC12a held out half (decision 70): item ids and answers "
                 "only; the latest answer per item, every earlier one kept in data/eval/sc12c/answers.jsonl"),
        "protocol_sha256": protocol,
        "order_seed": info["order_seed"],
        "items": len(latest),
        "answers_logged": len(entries),
        "changed_answers": changes,
        "items_changed": items_changed,
        "answers_log_sha256": file_sha256(answers_path()),
        "labels": {item_id: {"safety": e["safety"], "hazard": e["hazard"], "answered_at": e["at"]}
                   for item_id, e in sorted(latest.items())},
    }
    harness.write_json(labels_file, doc)
    sealed = {"human_labels_sha256": file_sha256(labels_file), "sealed_at": _now()}
    harness.write_json(seal_file, sealed)
    harness.write_json(local_seal_path(), sealed)
    return {"labels": doc, "seal": sealed}


def require_sealed() -> dict[str, Any]:
    """The sealed blind labels, refused unless the seal exists and matches the file byte for byte."""
    labels_file, seal_file = committed(HUMAN_LABELS), committed(SEAL)
    if not seal_file.is_file() or not labels_file.is_file():
        raise Sc12cRefusal(f"the blind labels are not sealed ({seal_file.name} missing): run seal first; nothing "
                           "is scored before the seal.")
    sealed = harness.read_json(seal_file)
    actual = file_sha256(labels_file)
    if actual != sealed.get("human_labels_sha256"):
        raise Sc12cRefusal(f"{labels_file.name} changed after it was sealed (sha256 "
                           f"{sealed.get('human_labels_sha256')} sealed, {actual} now); the report uses the sealed "
                           "blind labels only.")
    return harness.read_json(labels_file)


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


def _arms(scored: Sequence[Mapping[str, Any]], threshold: float) -> dict[str, stats.Counts]:
    return {
        JEV_ARM: stats.count(scored, lambda i: stats.jev_raises(i["noul"], threshold)),
        PUBLISHED_ARM: stats.count(scored, lambda i: i["word_published"]),
        TUNED_ARM: stats.count(scored, lambda i: i["word_tuned"]),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def load_revisions() -> list[dict[str, Any]]:
    """The committed revisions: ids and answers only."""
    path = committed(REVISED)
    return list(harness.read_json(path).get("revisions") or []) if path.is_file() else []


def load_local_reasons() -> dict[tuple[str, str], str]:
    """Each revision's reason by (id, at), from the local file; empty on a computer without it."""
    path = local_revisions_path()
    if not path.is_file():
        return {}
    return {(r["id"], r["at"]): r.get("reason") or "" for r in harness.read_json(path).get("revisions") or []}


def answers_after_seal(human_doc: Mapping[str, Any]) -> tuple[bool | None, int]:
    """(whether answers.jsonl still matches the sealed sha256, lines logged after the sealed count).

    None when the log is not on this computer. Counted from raw lines, so a log
    edited by hand is reported here rather than refusing the score."""
    path = answers_path()
    if not path.is_file():
        return None, 0
    if file_sha256(path) == human_doc.get("answers_log_sha256"):
        return True, 0
    lines = [line for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    return False, max(len(lines) - int(human_doc.get("answers_logged") or 0), 0)


def score() -> dict[str, Any]:
    """`score`: Roanuk's sealed labels against the AI majority, and the three arms against both, from records only."""
    human_doc = require_sealed()
    lock_file = harness.lock_path()
    if not lock_file.is_file() or file_sha256(lock_file) != LOCK_FILE_SHA256:
        raise Sc12cRefusal(f"{lock_file} is missing or is not the Gate 2 lock (sha256 {LOCK_FILE_SHA256}).")
    lock = harness.read_json(lock_file)
    threshold = lock["thresholds"][stats.JEV]["threshold"]
    if threshold != config.JEV_THRESHOLD:
        raise Sc12cRefusal(f"the lock's Jev threshold {threshold} is not config.JEV_THRESHOLD "
                           f"{config.JEV_THRESHOLD}.")
    held = heldout_items()
    human = human_doc["labels"]
    if set(human) != {i["id"] for i in held}:
        raise Sc12cRefusal("the sealed labels' ids are not the held out half's ids.")
    ai = harness.load_labels()
    missing = [i["id"] for i in held if i["id"] not in ai]
    replies = harness.load_replies(lock["question_hash"])
    no_reply = harness.missing_replies(held, replies)
    if missing or no_reply:
        raise Sc12cRefusal(f"{len(missing)} held out items have no AI majority label and {len(no_reply)} no recorded "
                           "Jev reply; the score reads recorded data only.")
    ai_scored = harness.scored_items(held, ai, replies)
    against_ai = _arms(ai_scored, threshold)
    got = {name: (c.tp, c.flagged, c.positives) for name, c in against_ai.items()}
    if got != PUBLISHED_AI_ARMS:
        raise Sc12cRefusal(f"the arms against the AI majority ({got}) do not reproduce the published held out "
                           f"numbers ({PUBLISHED_AI_ARMS}); the recorded data changed.")
    human_as_labels = {k: {"positive": positive_vote(v), "hazard": v["hazard"]} for k, v in human.items()}
    against_roanuk = _arms(harness.scored_items(held, human_as_labels, replies), threshold)

    ids = [i["id"] for i in held]
    yes_no = ("yes", "no")
    pairs = [("yes" if human_as_labels[k]["positive"] else "no", "yes" if ai[k]["positive"] else "no") for k in ids]
    table = stats.agreement_table(pairs, yes_no)
    agree = table[0][0] + table[1][1]
    hazard_categories = (*HAZARDS, SPLIT)
    hazard_table = stats.agreement_table([(human[k]["hazard"], ai[k]["hazard"]) for k in ids], hazard_categories)
    hazard_agree = sum(hazard_table[n][n] for n in range(len(hazard_categories)))

    by_id = {i["id"]: i for i in held}
    disagreements = []
    for k in sorted(ids, key=lambda k: (not human_as_labels[k]["positive"], k)):
        if human_as_labels[k]["positive"] == ai[k]["positive"]:
            continue
        disagreements.append({
            "id": k,
            "roanuk": {"safety": human[k]["safety"], "hazard": human[k]["hazard"],
                       "safety_step": human_as_labels[k]["positive"]},
            "ai_majority": {"label": ai[k]["label"], "hazard": ai[k]["hazard"], "safety_step": ai[k]["positive"],
                            "votes": ai[k]["votes"]},
        })
    # Reasons stay in their own file and the terminal: a reason may quote the step.
    revisions = [{"id": r["id"], "at": r["at"], "blind": r["blind"], "safety": r["safety"], "hazard": r["hazard"],
                  "agrees_with_ai_majority": positive_vote(r) == ai[r["id"]]["positive"]} for r in load_revisions()]
    # An answer logged after the seal is never scored; it is counted so the record shows it.
    log_matches_seal, after_seal = answers_after_seal(human_doc)

    result = {
        "note": ("SC12c: Roanuk Zaman's sealed blind labels on the SC12a held out half against the AI majority and "
                 "the arms against both (decision 70); ids and numbers only, no text"),
        "scored_at": _now(),
        "items": len(ids),
        "human_labels_sha256": file_sha256(committed(HUMAN_LABELS)),
        "lock_sha256": LOCK_FILE_SHA256,
        "question_hash": lock["question_hash"],
        "jev_threshold": threshold,
        "in_scope_hazards": list(IN_SCOPE_HAZARDS),
        "safety_step_agreement": {
            "agree": agree, "items": len(ids), "percent": _ratio(100 * agree, len(ids)),
            "cohen_kappa": _float(stats.cohen_kappa(table)),
            "table": {"roanuk_yes_ai_yes": table[0][0], "roanuk_yes_ai_no": table[0][1],
                      "roanuk_no_ai_yes": table[1][0], "roanuk_no_ai_no": table[1][1]},
        },
        "hazard_agreement": {
            "agree": hazard_agree, "items": len(ids), "percent": _ratio(100 * hazard_agree, len(ids)),
            "cohen_kappa": _float(stats.cohen_kappa(hazard_table)), "categories": list(hazard_categories),
            "table": hazard_table,
        },
        "arms": {
            "against_roanuk": {name: c.summary() for name, c in against_roanuk.items()},
            "against_ai_majority": {name: c.summary() for name, c in against_ai.items()},
        },
        "ai_majority_reproduces_published": True,
        "disagreements": disagreements,
        "revisions": revisions,
        "answers_log_matches_seal": log_matches_seal,
        "answers_after_seal": after_seal,
    }
    harness.write_json(committed(SCORES), result)
    harness.write_json(disagreements_path(), {
        "note": "SC12c disagreements with their text (local only; never committed)",
        "disagreements": [{**d, "appliance": by_id[d["id"]].get("appliance") or "",
                           "step": by_id[d["id"]].get("step") or "", "detail": by_id[d["id"]].get("detail") or ""}
                          for d in disagreements],
    })
    return result


def _fmt(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def format_score(result: Mapping[str, Any], held: Sequence[Mapping[str, Any]]) -> list[str]:
    s, h = result["safety_step_agreement"], result["hazard_agreement"]
    t = s["table"]
    lines = [
        f"SC12c, held out half: {result['items']} items, Roanuk's sealed blind labels "
        f"(sha256 {result['human_labels_sha256'][:16]})",
        f"safety step answer, Roanuk against the AI majority: {s['agree']} of {s['items']} agree "
        f"({_fmt(s['percent'], 1)}%), Cohen's kappa {_fmt(s['cohen_kappa'])}",
        f"  Roanuk yes: AI yes {t['roanuk_yes_ai_yes']}, AI no {t['roanuk_yes_ai_no']}",
        f"  Roanuk no: AI yes {t['roanuk_no_ai_yes']}, AI no {t['roanuk_no_ai_no']}",
        f"hazard, Roanuk against the AI majority: {h['agree']} of {h['items']} agree ({_fmt(h['percent'], 1)}%), "
        f"Cohen's kappa {_fmt(h['cohen_kappa'])}",
        "recall and precision with Wilson 95% intervals:",
    ]
    for name in (JEV_ARM, PUBLISHED_ARM, TUNED_ARM):
        for against, title in (("against_roanuk", "Roanuk's labels"), ("against_ai_majority", "AI majority")):
            c = stats.Counts(**{k: result["arms"][against][name][k]
                                for k in ("tp", "flagged", "positives", "other_flagged")})
            lines.append(harness.arm_line(f"{name}, against {title}", c))
    by_id = {i["id"]: i for i in held}
    lines.append(f"disagreements on the safety step answer: {len(result['disagreements'])}")
    for d in result["disagreements"]:
        item = by_id[d["id"]]
        r, a = d["roanuk"], d["ai_majority"]
        lines.append(f"  {d['id'][:12]}: Roanuk {r['safety']} {r['hazard']}; AI majority {a['label']} {a['hazard']}")
        lines.append(f"    {item.get('appliance') or ''}: {item.get('step') or ''}")
        if item.get("detail"):
            lines.append(f"    detail: {item['detail']}")
    revisions = load_revisions()
    if revisions:
        reasons = load_local_reasons()
        lines.append("revisions after scoring (not scored; the numbers above use the sealed blind labels):")
        for r in revisions:
            reason = reasons.get((r["id"], r["at"])) or "(reason kept locally)"
            lines.append(f"  {r['id'][:12]}: blind {r['blind']['safety']} {r['blind']['hazard']}, revised "
                         f"{r['safety']} {r['hazard']} ({r['at']}): {reason}")
    if result.get("answers_log_matches_seal") is False:
        lines.append(f"warning: {answers_path().name} changed after the seal ({result.get('answers_after_seal', 0)} "
                     "answers logged past the sealed count); they are not scored and are not revisions. Record a "
                     "change with revise.")
    return lines


# ---------------------------------------------------------------------------
# Revise
# ---------------------------------------------------------------------------


def revise(item_id: str, safety: str, hazard: str, reason: str) -> dict[str, Any]:
    """`revise`: a label changed after scoring goes to its own file, its reason to the local file; the sealed
    file never changes."""
    human = require_sealed()["labels"]
    if not committed(SCORES).is_file():
        raise Sc12cRefusal("a revision is recorded only after scoring: run score first.")
    if item_id not in human:
        raise Sc12cRefusal(f"{item_id!r} is not a sealed held out item id.")
    if safety not in SAFETY_ANSWERS or hazard not in HAZARDS:
        raise Sc12cRefusal(f"safety is one of {', '.join(SAFETY_ANSWERS)} and hazard one of {', '.join(HAZARDS)}.")
    if not reason.strip():
        raise Sc12cRefusal("a revision needs a reason, which is recorded.")
    entry = {"id": item_id, "safety": safety, "hazard": hazard, "reason": reason.strip(), "at": _now(),
             "blind": {"safety": human[item_id]["safety"], "hazard": human[item_id]["hazard"]}}
    # The reason may quote the step, so it stays in the gitignored local file.
    local = local_revisions_path()
    kept = list(harness.read_json(local).get("revisions") or []) if local.is_file() else []
    harness.write_json(local, {
        "note": "SC12c revisions with their reasons (local only; never committed: a reason may quote the step)",
        "revisions": [*kept, entry]})
    harness.write_json(committed(REVISED), {
        "note": ("SC12c labels Roanuk changed after scoring: ids and answers only, each reason kept in "
                 f"data/eval/sc12c/{LOCAL_REVISIONS}; the sealed blind labels in {HUMAN_LABELS} are unchanged and "
                 "are what every score reports"),
        "revisions": [*load_revisions(), {key: entry[key] for key in ("id", "at", "blind", "safety", "hazard")}]})
    return entry


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agent.safety_eval.sc12c", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write data/eval/sc12c/page.html")
    b.add_argument("--seed", help="the original order seed, to restore an order whose build.json is lost")
    s = sub.add_parser("serve", help=f"serve the page on {HOST} and record answers")
    s.add_argument("--port", type=int, default=PORT)
    sub.add_parser("seal", help="seal the blind labels once every item has an answer")
    sub.add_parser("score", help="score against the sealed labels")
    r = sub.add_parser("revise", help="record a changed label, with a reason, after scoring")
    r.add_argument("item_id")
    r.add_argument("--safety", required=True, choices=SAFETY_ANSWERS)
    r.add_argument("--hazard", required=True, choices=HAZARDS)
    r.add_argument("--reason", required=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "build":
            info = build(args.seed)
            print(f"wrote {page_path()}: {info['items']} items, order seed {info['order_seed']}, "
                  f"protocol sha256 {info['protocol_sha256'][:16]}")
            print("serve it with: python -m agent.safety_eval.sc12c serve")
        elif args.command == "serve":
            serve(args.port)
        elif args.command == "seal":
            out = seal()
            print(f"sealed {out['labels']['items']} blind labels in {committed(HUMAN_LABELS)} "
                  f"(sha256 {out['seal']['human_labels_sha256']}); {out['labels']['changed_answers']} changed "
                  "answers on the way")
        elif args.command == "score":
            result = score()
            for line in format_score(result, heldout_items()):
                print(line)
            print(f"wrote {committed(SCORES)} and {disagreements_path()}")
        elif args.command == "revise":
            entry = revise(args.item_id, args.safety, args.hazard, args.reason)
            print(f"recorded a revision for {entry['id']} in {committed(REVISED)}, its reason in "
                  f"{local_revisions_path()}; the sealed labels are unchanged")
    except harness.EvalRefusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
