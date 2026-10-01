# Recorded demo v2: design

Status: installed 18 September 2026 as `agent/replay/build_demo.py` (builder version 3), `agent/replay/check_demo.py`,
`agent/replay/demo_page.html` (the page template) and `agent/replay/demo_selection.json`, and built from the fixed
build rerun of 18 September 2026 (build `839b1854a0aaf25e`). Every run ID below is a run of that build. The
builder refuses a run whose own records name any other build unless `--stand-in` is passed, and a stand-in build
is stamped as not a result on every surface (section 6).

## 0. Shape of the thing

```
demo_selection.json ──►  build_demo.py (offline, read only on the repo)  ──►  out/ (repo relative layout)
   roles → run IDs          reads data/runs, data/eval, data/recordings,        tool.html
                            data/lookups, data/graph.json, data/briefs,         src/demo.js, src/demo.css
                            ledger (sqlite, mode=ro)                             src/fixtures.js
                            copies by allowlist only                             briefs/v2/<run>.html
                                                                                 build_report.json
```

* The page is one static `tool.html` plus local scripts. Data arrives through `<script src="src/fixtures.js">`,
  which sets `window.ADVISOR_DEMO`. No fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon, dynamic
  import, or any other origin. Images and iframes point only at same origin relative paths.
* The builder never calls a model, Tavily, or any network, and never runs a `--live` command. It opens the
  ledger with `sqlite3.connect("file:...?mode=ro", uri=True)` (the plain `sqlite3 -readonly` CLI fails on this
  file; the Python URI form works). It never reads `.env`.
* **File names are kept** (`src/demo.js`, `src/demo.css`, `src/fixtures.js`) because
  `agent/tests/test_published_pages.py` scans those exact paths and looks for `"plate"` and `"share"` keys in
  `src/fixtures.js`. The fixtures format below keeps both key names so that test keeps biting.

## 1. Sources the builder reads, and what each is trusted for

| Source (read only) | Present for | Used for |
|---|---|---|
| `data/runs/<run>.json` (persist.py `run_record`) | every run that reached END (not paused runs) | status, refusal_origin, stop_reason, route, route_reason, research_limits, grounding, brief (structured), cost_usd, tavily_credits, searches, fetches, latency_s, sc11 cap check, graph_edges report, html_path (basename only) |
| `data/eval/<suite>-<run>.json` | runs started by `advisor eval` | build_id, role, label, input (symptom, typed identity, photo basename), extraction raw and final, pauses, answers (what the owner confirmed), search_trail, not_reached (blocked calls with their query), status for paused runs, cost for paused runs |
| `data/recordings/live_t_<run>.json` (recorder.py cassette) | runs that reached END and were recorded | input.symptom, input.plate_sha256, read_plate.extraction (value and confidence per field, exactly what the plate reader returned), resume.identity and resume.observed_code (what the owner confirmed), classifier[].structured.verdict, research.tool_results (query, credits, result url and title, fetch url, failed_results), research.blocked_calls (counts) |
| `data/recordings/t-<run>.capture.jsonl` | every live segment | only the `segment` events (`ask`, `resume`) to prove the run paused and was finished later; and `read_plate` content when there is no cassette (paused runs such as the blurry plate) |
| `data/lookups/<run>.jsonl` | runs that searched or fetched | fallback for the trail: tool, status, query or url, credits, n_results, result urls; `at` timestamps for ordering |
| ledger `entries` table | every live run | cost by node and provider, Tavily credits, token counts, model name; cost for paused runs; sum must equal run record cost_usd |
| (not used) `agent/live/preflight.run_plan` | none | **No estimate is shown.** No run or eval record stores the planner's estimate, and one computed at build time would come from the current config, not from the run (finding H1). |
| `data/graph.json` | case 2 and 3 | the stored fact: HAS_CODE (and later DOCUMENTED_CAUSE) edge fields `evidence`, `source_url`, `retrieved_at`, `validated_at`, `brief_run_id` |
| `data/briefs/<run>.html` | runs that rendered | copied byte for byte to `briefs/v2/<run>.html` |
| `demo-assets/plate-clear.jpg`, `plate-blurry.jpg` | already published | not copied; sha256 is compared with the cassette's `input.plate_sha256` (clear plate = `3a142b56...`, matches today) so the page never shows a photo that was not the one sent |

Precedence when two sources disagree: run record, then cassette, then eval, then lookups. Any disagreement on a
number shown on the page (cost, searches, credits, route) is a **build failure**, not a silent pick. Rules the
builder keeps where the records legitimately differ:

* `eval extraction.final` for the plates suite is title cased (`Sundance Spas`) while sc7b keeps the raw case
  (`SUNDANCE SPAS`). The page shows **raw** (what the reader read) and the **confirmed** identity separately.
* An edge whose `brief_run_id` run record lists that edge as dropped is shown exactly as stored, and the
  disagreement is a build failure outside `--stand-in` (a warning in `build_report.json` on a stand-in build).
* A brief may quote a stored fact in full, as its opening words, or as a span inside it (the vague symptom run
  quotes the FLO fact without its leading code label). The page says which. A quote that is not in the stored
  fact at all is a build failure.
* A no reliable answer brief's `searched` list may hold queries the run's search or fetch limit blocked. The page labels each query
  "sent" or "blocked, never sent" (blocked by the run's search or fetch limit, never by the spending cap).
* `latency_s` is the sum of node times. It does not include the owner's confirmation pause. The page calls it
  "processing time" and says so.

## 2. The cases

Roles are fixed names. The selection file (section 4) maps each role to a run ID. Each case says **Live run**
and its run ID in its header, always.

### Case 1. `hot_tub_code_first`: hot tub, code on the panel, first question

Rerun: `t-267045726dd04118`.

| Step | What the reader sees | Fed by |
|---|---|---|
| 1. What the owner sends | the plate photo `demo-assets/plate-clear.jpg`; the symptom in quotes | cassette `input.symptom`; `input.plate_sha256` checked against the file |
| 2. What the plate reader read | four rows: manufacturer, model, serial, manufacture date, each with its value and a confidence badge (high, low, unreadable); unreadable shows "not readable", never a blank | cassette `read_plate.extraction` (else capture `read_plate` content) |
| 3. The confirmation pause | "The run stopped here and spent nothing more until the owner confirmed." Shows the confirmed identity and the confirmed code side by side with what was read, differences highlighted; the cost spent before the pause (the read_plate charge); and a line that the run can be finished later (it paused, then resumed as the same thread) | capture `segment` events `ask` then `resume`; cassette `resume.identity`, `resume.observed_code`; eval `pauses`, `answers`; ledger rows before the first `resume` charge (node `read_plate`) |
| 4. The route and why | route chips (for example "research"), the research limits used, and the route reason sentence verbatim | run record `route`, `research_limits`, `route_reason` |
| 5. The research trail | an ordered list: each search query with its Tavily credits, result count and the hosts it returned; each fetched page as host plus path (link to the public URL, `rel="noreferrer"`), ok or failed; each call **blocked by the run's search or fetch limit** with its query and "never sent" | cassette `research.tool_results` (ordering), eval `search_trail` and `not_reached`, lookups; blocked queries from eval `not_reached`, or when absent, the script tool calls with no matching result (recorder `blocked_calls` count must match) |
| 6. The brief | verdict chip ("1 confirmed candidate", or "No reliable answer"), grounding status ("the code was found in the cited page's text": `grounding[].reason`), then the real brief file in a same origin iframe with "Open the brief on its own" | run record `brief.status`, `brief.candidates`, `grounding`, `grounding_status`; `briefs/v2/<run>.html` |
| 7. The run's numbers | cost against its cap as a meter and in US dollars to four places ("$0.0587 of a $0.15 cap, within the cap"), the rounding the teardown uses, the exact ledger value in a caption; searches; fetches; search credits (Tavily) with a one line gloss; processing time (the pause note only when the run paused and resumed); model calls by step in plain names in a collapsed details | run record `cost_usd`, `sc11.run_cap_usd` (else eval `run_cap_usd`; a rerun build fails when neither exists), `searches`, `fetches`, `tavily_credits`, `latency_s`; ledger tokens by node |

### Case 2. `hot_tub_code_repeat`: the same question again

Rerun: `t-274c71d4e47f481e`. Compared with case 1.

* Step 1 and 2 collapsed to one line ("same photo, same symptom, same confirmation"), with the confirmation
  still shown because the run still paused.
* **Route from memory**: route chips ("graph"), route reason verbatim ("...the graph answers with no search").
* **The stored fact**: a card with the code, the word for word evidence quote in a `<blockquote>`, the source
  host and tier, when the page was retrieved, when the fact was validated, and which live run first stored it
  (a link that switches to case 1). Fed by the brief candidate whose source has `origin: "graph"`, matched to
  the `graph.json` edge by `source_url` and code; the quote shown is the edge's `evidence` and must equal the
  candidate's `evidence` byte for byte or the build fails.
* **Side by side table** (two columns, first question and same question again; rows: route, searches, fetches,
  Tavily credits, cost, processing time). On phone width it becomes stacked pairs, no horizontal scroll.
  Every cell names its run ID in the column header.
* The brief iframe and numbers as in case 1.

### Case 3. `hot_tub_vague`: a vague symptom on the same hot tub

Rerun: `t-43ffbe945ce64f29` ("not heating"). Compared with case 1.

* Plate read and confirmation as case 1 (confirmed code: none).
* Route: a plain sentence first, driven by the outcome (confirmed or only a possibility) and by the run's
  search count, then the routing reason verbatim inside a disclosure. When the route is from memory because the
  symptom was matched to a documented cause, show the classifier's verdict in words from cassette
  `classifier[].structured`. The memory step is headed "What memory could offer" when memory only offered a
  possibility.
* **What memory could answer, and what it could not**: two short lists built only from the record: facts used
  from memory (sources with `origin: "graph"`), and what needed a search (sources with `origin: "search"`,
  plus `research_limits` when the route included a top up). If the route was research, the page says memory
  had nothing verified for this symptom and quotes the route reason.
* Brief and numbers.

### Case 4. `ac_first`: the air conditioner's first question

Rerun: `t-32b097b6a454440e` (typed identity, "AC not cooling upstairs"). If the selected run has no
photo, step 2 reads "Typed by hand for this case" and shows the typed identity, as v1 did. Otherwise as case 1.
Research trail, candidates count, source tiers, brief, numbers.

### Case 5. `ac_new_symptom`: a new symptom on the air conditioner

Rerun: `t-a6cc7b63e63b41d0`. Same unit, different symptom. Shows the route and reason verbatim, and a
compact comparison with case 4 (route, searches, credits, cost, time). When memory held nothing, the route step
says why from the record: what memory keeps, and how many facts case 4's run stored (`graph_edges.written`),
and a line above the comparison says both runs had to search, so memory saved nothing. When some source has
`origin: "graph"`, the memory step appears, headed from the outcome. When the route is memory plus a top up
(`research_limits: "top_up"`), the route step says memory held facts for the unit (case 4's
`graph_edges.written`) but the symptom check could not match one to the new symptom, so the short top up search ran
instead of a full one; the side by side table says both runs searched but this one ran only the top up.

### Case 6. `no_such_model`: a model that does not exist

Rerun: `t-ea3dc31aae684e5a`, counted with `t-34c911e1f5384a68` and `t-cf986eb8dda9472d`.

* Big verdict: **NO RELIABLE ANSWER**.
* What was searched: every query from `brief.no_reliable_answer.searched`, each labeled "sent" (it appears in the
  lookup log) or "blocked, never sent" (blocked by the run's search or fetch limit; it appears in eval `not_reached`); fetched pages listed.
* What was found and why it was not enough: `no_reliable_answer.found` and `why_insufficient`, verbatim, with the
  label "the tool's own words".
* Who refused: `refusal_origin` ("the model declined" or "the rules forced a refusal"), and `stop_reason` when
  set (for example a cap stop).
* Optional: if the selection lists repeat runs for this role, a line "N of N runs of this question ended with no
  reliable answer", each run ID linked, from the eval summary rows. Only runs of the same build count.
* Brief (the NRA brief is itself useful: it tells the technician what was searched) and numbers.

### Case 7. `blurry_plate`: a blurry plate

Rerun: `t-d3f71b57c5c048f4`. No run record and no brief exist, because the run halted at the pause.

* Photo `demo-assets/plate-blurry.jpg` and the symptom.
* Plate read table: every field `unreadable` shows "not readable" with its badge (from eval `extraction.raw`, or
  capture `read_plate` content).
* **Halted at the confirmation step**: "Nothing was searched" (only when the record says 0 searches), what it
  spent against case 1's full run, both in dollars. Status `paused` from eval.
* Cost: ledger total for the run, labeled "spent reading the photo", with searches 0 and
  credits 0 from the ledger.
* No iframe. The brief panel reads "No brief: the run stopped before anything was searched."

### Case 8 (optional role). `hot_tub_with_history`: the same code with the property's records attached

Rerun: `t-550f902f22f846d5`. A live run with the seeded synthetic registry rows for the hot tub attached. Labeled
**"Live run against synthetic property records"** on its tab, its header, its records step, its numbers panel,
its brief caption and the glance table.

* What the owner sends: the unit as its property record names it (a real model name with a synthetic serial);
  no photo. The confirmation step shows the one pause, for the code the tool found in the symptom, and that
  nothing was spent before it.
* Route: history plus memory, no search, and the route reason verbatim.
* **What the property's records added**: the brief's "this has happened before" summary, cited to the prior
  service record by ID and date; the age statement, which code writes from the install date (the builder
  recomputes it with `agent/rules/citations.age_statement` on the run's date and fails on any difference); and
  the warranty terms quoted from the record. Every cited row must be in `seed/registry_seed.json` and marked
  synthetic, and the cited appliance must be the run's unit, or the build fails.
* Memory, brief and numbers as case 2.

The prototype's replay cases R1 (`replay_happened_before`) and R2 (`replay_upgrade`) are left out: case 8 shows
the same records on a real live run, so a replay would add nothing, and a replay's cassette draft is not what a
user sees.

## 3. Fixtures format: `src/fixtures.js`

```js
// Generated by build_demo.py 3 from a selection file (sha256 ...). Replays recorded runs. This page makes no network request.
window.ADVISOR_DEMO = {
  "format": 2,
  "data_status": "rerun",                     // "stand_in" | "rerun"
  "stand_in_label": null,                     // the stand-in label when data_status is "stand_in"
  "build": { "build_id": "839b1854a0aaf25e", "builds": ["839b1854a0aaf25e"], "recorded": "Recorded 18 September 2026, on the lower cost model setting" },
  "generated": { "builder": "build_demo.py 3", "selection_sha256": "...", "built_at": "..." },
  "cases": [
    {
      "id": "c1", "role": "hot_tub_code_first",
      "kind": "live",
      "kind_label": "Live run t-267045726dd04118",
      "records_label": null,                  // "Live run against synthetic property records" on case 8
      "records": null,                        // case 8: {label, happened_before, age, warranty_terms}
      "run_id": "t-267045726dd04118",
      "button": "Hot tub, code on the panel",
      "title": "Hot tub: first question, with a code on the panel",
      "recorded": "Recorded 18 September 2026, on the lower cost model setting",
      "takeaway": "Read the plate, paused for the owner to confirm, then searched 4 times ...",
      "compare_with": null,                   // {with: case id, columns: [...]} on c2 and c3 (c1) and c5 (c4)
      "graph_edges": { "written": 1, "already_present": 0, "dropped": 0 },
      "ask": { "symptom": "panel shows FLO", "plate": "demo-assets/plate-clear.jpg", "plate_sha256": "3a142b56...",
               "typed_identity": null, "same_as": null },
      "plate_read": { "fields": [ { "key": "model", "label": "Model", "value": "...", "confidence": "high" } ] },
      "confirmation": { "paused": true, "resumed": true, "confirmed_identity": { ... }, "confirmed_code": "FLO",
                        "spent_before_pause_usd": 0.002904, "from_photo": true },
      "route": { "steps": ["research"], "limits": "main", "classifier_verdict": null, "reason": "Route table row 5: ..." },
      "trail": [ { "tool": "search", "status": "sent", "query": "...", "results": 5, "hosts": ["..."] } ],
      "memory": null,                         // {heading, facts, confirmed_from_memory, offered_from_memory, searched_instead, prior}
      "outcome": { "status": "ok", "candidates": 1, "confirmed_candidates": 1, "grounding_status": "verified", "sources": [ ... ] },
      "brief": { "share": "briefs/v2/t-267045726dd04118.html", "sha256": "..." },
      "numbers": { "cost_usd": 0.058713, "cost_text": "$0.0587", "run_cap_usd": 0.15, "cap_text": "$0.15 cap", "cap_check": "pass",
                   "searches": 4, "fetches": 3, "tavily_credits": 4, "processing_s": 51.3, "processing_note": "...", "by_node": [ ... ] },
      "warnings": []                          // builder notes, shown on stand-in builds only
    }
  ]
};
```

Rules: every string is plain text (the page uses `textContent`, never `innerHTML`); every URL shown is `https:`
and rendered as a link only (never loaded); every local path (`plate`, `share`) is relative, has no `..`, no
scheme, no leading slash, and exists in `out/`. The file is JSON inside one assignment so the builder's check
can parse it back with `json.loads` after stripping the comment and the prefix.

## 4. Selection file: `agent/replay/demo_selection.json`

Written by hand after the rerun. The builder picks runs by role; nothing is hard coded. The file holds `format`,
`data_status` ("rerun"), `build_id` (`839b1854a0aaf25e`), `recorded_on` (2026-09-18), `roles` (the seven
required roles and the optional `hot_tub_with_history`), `also_count` (the other two runs of the invented model
question) and `compare` (case 2 and case 3 against case 1, case 5 against case 4).

Checks, each a build failure:

* Every role is present and names a run whose files exist; `blurry_plate` needs eval plus ledger only, every
  other role needs a run record, a ledger total and, when a brief was rendered, the brief file.
* Each role's record matches its role: first questions have a route that includes `research`; the repeat has
  route `graph` and 0 searches; the blurry run has status `paused` and every plate field `unreadable` except at
  most the manufacturer; the no such model run has status `no_reliable_answer`; the hot tub runs share one
  confirmed identity and the AC runs share another.
* Build guard: a build is refused unless every selected **and every also_count** run names its build in its own
  records (the run record's `build_id`, written by `agent/build_info.py`, else its eval record's `build_id`; when
  both exist they must agree), that build equals `selection.build_id` and the current build, and it is not a
  superseded build (listed in an eval summary, filed under `data/superseded/`, or known to the builder).
  Otherwise the builder refuses and prints why; `--stand-in` builds anyway and stamps the label.
* The current build is `build_info.build_id()`, the fingerprint every run record carries. It leaves out the
  demo tooling (`replay/build_demo.py`, `replay/check_demo.py`, `replay/demo_selection.json`) and the safety
  evaluation under `safety_eval/` (`build_info.EXCLUDED`), so installing or editing the tooling, or importing
  readers' labels, does not make the runs look like another build. The builder calls `build_info.build_id()`
  itself, so there is one definition of a build.
* A rerun build names no superseded build or run anywhere in its output.
* Each also_count run must have asked the same question (symptom and unit) as the main `no_such_model` run.
* Every memory fact shown must have been stored by the selected `hot_tub_code_first` run or by another run on
  the current build, and that run's record must not list the fact as dropped. Outside `--stand-in` both are
  build failures.
* Photo runs other than `blurry_plate` must show both an `ask` and a `resume` segment in the capture;
  `blurry_plate` must have an `ask` segment, no searches, fetches or credits in its eval record, and no ledger
  charge other than the plate read.
* Ledger total per run equals run record `cost_usd` within 0.000001; `sc11.result` is `pass`.

## 5. Briefs: copy and display

* Copy `data/briefs/<run>.html` to `out/briefs/v2/<run>.html` **byte for byte**; record sha256 and size in the
  fixtures and in `build_report.json`. Never edit, never re-render, never strip dashes (verbatim artifacts).
* Before copying, the builder verifies the brief is safe to frame and makes no request: it has the CSP meta
  `default-src 'none'; style-src 'unsafe-inline'`, no `<script>`, no `src=`, no `srcset`, no `@import`, no
  `url(`, and its only `href`s are `https:` anchors or the `data:,` favicon. A brief that fails is not copied and
  the build fails (it is a bug in the tool, which the report names).
* Display: `<iframe src="briefs/v2/<run>.html" title="Service brief from live run <run>" loading="lazy"
  sandbox="allow-popups allow-popups-to-escape-sandbox">` on a white panel (the brief is light only by design),
  under a caption "The file a technician would open, unedited". Below it, "Open the brief on its own" links to
  the same file with `target="_blank" rel="noreferrer"`. Height 40rem desktop, 32rem phone, as v1.
* `test_published_pages.py` scans `briefs/**/*.html`, so `briefs/v2/` is covered, and scans `src/demo.js`,
  `src/fixtures.js` and `src/demo.css` directly. `published_manifest.json` is regenerated on purpose after
  each install.

## 6. Labels: live, replay, stand-in

* **Stand-in banner** (only while `data_status` is `stand_in`): a full width `role="alert"` bar above the
  page header, warning tokens (`--warning-tint`, `--warning-bar`, `--warning-text`), text exactly
  "STAND-IN DATA FROM A SUPERSEDED BUILD, DO NOT PUBLISH", plus the build ID. The same string is the first
  comment line and the `stand_in_label` value in `src/fixtures.js`, is appended to the page `<title>`, sets
  `<meta name="robots" content="noindex">`, and replaces the intro's "every result is a real run" sentence with
  "These numbers come from a superseded build and are not results." The page's `og:` tags are omitted in a
  stand-in build.
* **Live chip** on every live case header and numbers panel: "Live run t-..." (info tokens), plus "Recorded
  18 September 2026, on the lower cost model setting".
* **Synthetic records chip** on case 8 everywhere it appears: "Live run against synthetic property records"
  (notice tokens, dashed border so it is not carried by color alone). A replay case, if one is ever added, carries
  "Replay of synthetic property records, no live call" the same way, with a dotted border.
* **Estimates**: none. The page shows only what the ledger and the run records hold. Caps are labeled "cap".
* **Takeaway**: one line under each case title, computed by the builder from the run's own numbers (and, for a
  comparison, the compared case's), for example "Answered from memory: 0 searches, $0.0082 and 5.2 seconds,
  against 4 searches, $0.0587 and 51.3 seconds the first time (case 1)."
* **Stand-in, everywhere a brief appears**: the brief caption, the "Open the brief on its own" link and the
  frame title say "(stand-in data from a superseded build, not a result)", and a stand-in build writes
  `briefs/v2/STAND-IN.txt` carrying the label, since the verbatim briefs cannot carry it.
* The top banner (as v1's info banner): "Nothing on this page calls an API or loads anything from another
  site. Each case replays one recorded live run, named by its run ID."

## 7. Page structure and look

* Reuses `src/tokens.css` untouched (role tokens only, dark mode via its `prefers-color-scheme` block) and v1's
  demo look: topbar, eyebrow "Recorded demo", h1, lede, banner, case picker buttons, stacked step cards.
* Case picker: a real ARIA tablist (`role="tablist"`, `role="tab"`, `aria-controls`, roving `tabindex`, arrow
  keys, Home and End), the panel is `role="tabpanel"`. Buttons wrap on phone width.
* Stepper inside a case: "Step n of m" with Previous and Next buttons, plus "Show all steps" (default on for
  no JavaScript and for print). Current step announced through an `aria-live="polite"` region ("Step 3 of 7:
  the confirmation pause"). Focus moves to the step heading on change. Each step is an `<h3>` under the case
  `<h2>`.
* Tables use `<th scope>`; confidence badges carry text, not color alone; the cost meter is a `<meter>` with a
  text equivalent ("0.040 USD of a 0.15 USD cap").
* Phone width: `.wrap` gutter 16px, tables collapse to label and value pairs, long URLs `overflow-wrap:
  anywhere`, iframe width 100%, the comparison table stacks. No horizontal scroll at 320px.
* Copy: plain prose for a business reader, American spelling, no em or en dashes anywhere a reader sees,
  "cut" never "kill". Only the brief files are exempt.
* **Dash policy (decided, finding C2)**: the builder never edits a recorded string. A dash in any string the
  page shows fails the build (exit 3, nothing is written to `--out`), whether the builder wrote it or it is
  verbatim model or page text. Each hit names its JSON path in `src/fixtures.js` (for example
  `cases[3].route.reason`) and says "authored text" or "verbatim field", so Roanuk can decide at once: leave
  that field off the page (the brief file, which is exempt, still carries it) or accept the dash.

## 8. Left out, and why (allowlist copying)

The builder copies named fields only (the paths listed in sections 1 to 3). Everything else is dropped by
construction, not filtered by pattern. In particular:

* **Keys and headers**: never read. `.env` is never opened; the capture never held requests.
* **Raw page text and search snippets**: `data/pages/*.txt`, the `content` and `raw_content` of lookups, and
  `texts.json` are never copied. Third party text reaches the page only where the tool itself already quoted it
  in the brief (candidate `evidence`, graph edge `evidence`) or in the NRA `found` lines, all public in the
  brief file.
* **The model's research narration** (`research.script[].message.content`, capture `content` for research) is
  left out: it is working chatter, not output a user sees.
* **Local paths and machine details**: `html_path`, `run_record`, `graph_edges.target`, `input.photo` absolute
  paths, `cassette`, `copy_log`, `next_step`, `pages_dir`; only basenames that are published are shown.
* **Ledger internals**: `note` ("reservation 4"), reserve and release rows, row IDs.
* **Test and eval scaffolding**: `v1_checks`, `expected_route`, `case` codes (B2, E1), `privacy.json` verdicts,
  staging files. The builder refuses to build a live case if that run's `privacy.json` has any `hits`.
* **Synthetic token counts and scripted text** in the synthetic cassettes (usage, "SCRIPTED AGE", "Synthetic
  match"); only the replayed, validated brief is shown.
* **Anything not in the recorded run**: no hand written numbers, no claim a run did not make.

Post build scan, run on a fresh folder next to `--out` before anything is moved into place: keys, auth
headers, `/Users/`, `.env` and the local denylist over every file, briefs included; U+2013 and U+2014 (per
field in `src/fixtures.js`, see the dash policy in section 7), the two forbidden project words (checked case
insensitively, written in the builder only as character codes), the network API regex of
`test_published_pages.py` over `.js` files **and inline `<script>` blocks**, and off origin loads in `src`,
`srcset`, `poster`, `data`, **`<link href>`**, style attributes and CSS `url(` / `@import`. The page's own
`src/demo.js` and `src/demo.css` are scanned too, wherever they are. A missing denylist is a build failure.
Only when the scan is clean and the fixtures parse back does the builder move its files into `--out`; on
any failure the fresh folder is deleted and `--out` is untouched. An empty `--page` is refused when
`<out>/tool.html` already exists.

## 9. Files, and how to rebuild

| Path | What |
|---|---|
| `agent/replay/build_demo.py` | the builder (reads `data/` read only; writes only `--out`) |
| `agent/replay/demo_selection.json` | the rerun's runs by role |
| `agent/replay/demo_page.html` | the page template, stamped into `tool.html` |
| `agent/replay/check_demo.py` | offline checks on a built folder or on the repo root: the builder's scan, `test_published_pages.py`'s page scan, stand-in labels, the synthetic records label, brief frame sandbox order in `demo.js`, fixtures parse, no superseded build or run named |
| `src/demo.js`, `src/demo.css` | the page's script and styles (edited in place; scanned by every build) |
| `tool.html`, `src/fixtures.js`, `briefs/v2/<run>.html` | the builder's output, installed |
| `agent/tests/fixtures/v1_fixtures.js` | v1's recorded briefs, which were `src/fixtures.js` until this page replaced it; read by the v1 tests |

Rebuild, from the repo root (never with `--live`, never into the repo directly):

```
.venv/bin/python -m agent.replay.build_demo --selection agent/replay/demo_selection.json \
    --out <temp folder> --src-dir src --preview
.venv/bin/python -m agent.replay.check_demo <temp folder>
```

Then copy `tool.html`, `src/fixtures.js` and `briefs/v2/` from the temp folder into the repo, run
`.venv/bin/python -m agent.replay.check_demo .`, and regenerate `agent/tests/fixtures/published_manifest.json`
on purpose. `build_report.json`, `README.txt`, `src/tokens.css` and `demo-assets/` in the temp folder are
preview files and are never installed.
