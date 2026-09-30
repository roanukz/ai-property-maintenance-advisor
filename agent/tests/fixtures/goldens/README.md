# v2 brief goldens: pending review

Status: **candidate goldens, pending Roanuk's review at the Phase 4 gate.**
Nothing here is approved yet. `test_render.py::test_v2_render_matches_reviewed_golden`
compares the v2 renderer's output with these files, so any change to v2 output
shows up as a failing test until the golden is regenerated and reviewed again.

## What each file is

| File | Brief | How it was made |
|---|---|---|
| `v2_flo.html` | recorded FLO brief (`fixtures/v1_fixtures.js`, v1's published `src/fixtures.js`, case `flo`) | through validate (`run_rules`, replay mode, v1 provenance), then `render_brief` |
| `v2_notheating.html` | recorded vague symptom brief (case `notheating`) | same |
| `v2_hvac.html` | recorded Trane brief (case `hvac`) | same |
| `v2_unknown.html` | recorded fabricated model refusal (case `unknown`) | same; the recorded search list is the search trail |
| `v2_budget_stopped.html` | synthetic budget stop | `refuse.build_budget_stopped_brief`, then `render_brief` |
| `v2_happened_before.html` | synthetic brief citing a synthetic service record | through validate, with the record loaded as history |
| `v2_upgrade_options.html` | synthetic brief with one graph derived upgrade option | through validate on the "upgrade" pass (rules 9 and 10), with synthetic page text for the authorship quote and the evidence quote; the option must survive |
| `v2_maintenance_due.html` | synthetic brief with one maintenance item | through validate (rules 9 and 10), with a synthetic maintenance_log row. As on a live run, the item carries no `last_done_record_id`: code finds the log row by its task, copies its date into `last_done_on` and computes the due date. Rendered with the run context the render node builds (history_hits only), so the page shows what production shows |
| `v2_all_forum.html` | synthetic brief whose sources are all forum hosts | through validate |

The recorded briefs are public (`fixtures/v1_fixtures.js`, which was v1's
published `src/fixtures.js`, and `briefs/`). Everything else
is synthetic: an invented maker and model, example.com URLs, and text labeled
"(synthetic)". Every recorded case is rendered with generated_at and
retrieval dates of 2026-08-18, the date of the recorded run, and validate is
given 2026-08-18 as today.

Validate changes the recorded briefs before they render: uncited sources are
pruned and every index renumbered (the FLO brief loses its first source; the
refusal cites no source, so it shows none), tiers are lowered to the host
ceiling, safety steps move first, and the warranty age statement is written by
code. Those are validate differences (Phase 2, decision 21), not renderer ones.

Safety step flagging also runs in validate. The configuration SC12a shipped
(`config.SAFETY_LAYERS = ("jev",)`, decision 62) raises a flag only from a recorded
Jev answer, and these replay cassettes hold none, so the goldens carry the writer's
own flags, unchanged. (While the word rule was the production layer, from 29 to 30
September 2026, it raised three flags here; that is recorded in DECISION-LOG.)

Model text keeps its U+2014 and U+2013 dashes as recorded; the v2 renderer
writes them as `&#8212;` and `&#8211;`, so the browser shows the same
character and no file here holds a raw dash (the repo style scan).

## Every rendered difference from v1, for approval (decision 21)

One row per difference between the v2 page and v1's page for the same brief.
"On the list" means decision 21 names it among the Phase 4 differences; "new"
means it is not on that list and needs its own approval. The legacy renderer
(`legacy=True`) has none of these and still matches `briefs/*.html` byte for
byte. No approved differences file is written until Roanuk approves.

| # | Difference | Decision 21 Phase 4 list | Golden that shows it |
|---|---|---|---|
| 1 | `html.escape(quote=True)` everywhere, so `'` becomes `&#x27;` | on the list (apostrophe escaping) | every golden (the code note "unit&#x27;s display" is in `v2_flo.html`) |
| 2 | CSP meta `default-src 'none'; style-src 'unsafe-inline'` | on the list | every golden |
| 3 | `<link rel="icon" href="data:,">` | on the list | every golden |
| 4 | Source host next to each tier badge | on the list | `v2_flo.html`, `v2_hvac.html` |
| 5 | Each source line shows its own retrieval date | on the list | `v2_flo.html`; `v2_upgrade_options.html` (graph source, 2026-07-01) |
| 6 | Refusal and budget stop footers without "reproduces documented material" (decision 47) | on the list | `v2_unknown.html`, `v2_budget_stopped.html` |
| 7 | Dash replacements of PLAN 6.4 (decision 17): "not recorded", "no code", "That is the honest result: nothing below is guessed.", title "Service brief: maker model" | on the list | `v2_unknown.html` (intro, title), `v2_notheating.html` ("not recorded", "no code") |
| 8 | Budget stop box in the refusal slot | on the list (new sections) | `v2_budget_stopped.html` |
| 9 | Upgrade options section after the candidates: name, reason label, summary, badge, citation, "Quoted from the source" line | on the list (new sections) | `v2_upgrade_options.html` |
| 10 | Maintenance due section after the warranty: task, interval with citation, "Last done: per property record ..., on <date>. Due: <date>.", quote | on the list (new sections) | `v2_maintenance_due.html` |
| 11 | Uncited sources pruned and indexes renumbered | on the list | `v2_flo.html` (loses its first source), `v2_unknown.html` (no sources) |
| 12 | happened_before shows "Service record <id>, dated <date>." from the loaded record | new, needs approval (in the Phase 4 contract, not on decision 21's list) | `v2_happened_before.html` |
| 13 | Registry warranty terms line "Warranty terms from property record <id>: ..." (decision 29) | new, needs approval | no golden (no fixture has registry terms); `test_render.py::test_all_model_text_escaped` draws it |
| 14 | `&middot;` in place of the raw middle dot in the header | new, needs approval (bytes only; the page looks the same) | every golden |
| 15 | `&#10003;` in place of the raw check mark on a confirmed code | new, needs approval (bytes only) | `v2_flo.html` |
| 16 | U+2014 and U+2013 in model or source text written as `&#8212;` and `&#8211;` | new, needs approval (bytes only) | `v2_flo.html`, `v2_hvac.html`, `v2_notheating.html`, `v2_unknown.html` |
| 17 | No blank line where v1 printed an empty placeholder line (no matched identity, no code note) | new, needs approval (bytes only) | `v2_notheating.html`, `v2_hvac.html`, `v2_unknown.html` (their published v1 briefs each hold one blank line) |
| 18 | A page whose status is not ok never draws steps, candidates, upgrade options or maintenance, even for an unvalidated brief (backstop; validate already clears them, decision 9) | new, needs approval (invisible for any validated brief) | no golden (validated refusals are already empty); `test_render.py::test_refusal_html_omits_invented_candidate_text` |
| 19 | A budget stop page never shows the all forum warning (its sources were never graded, so forum is a default, not a finding) | new, needs approval | `v2_budget_stopped.html` (its one source carries the default forum tier and no warning) |
| 20 | Extra CSS rules for the new pieces (`.host`, `.retrieved`, `.note`, `.items`, `.quote`) appended after v1's CSS | new, needs approval | every golden |
| 21 | Budget stop sources show the default FORUM badge (refuse.py gives ungraded sources forum; a "not graded" badge would be a further difference) | new, needs a decision (open issue) | `v2_budget_stopped.html` |

Withdrawn: an earlier build moved the candidates table's horizontal scroll
from v1's `div.tablewrap` to the whole section, so the heading and code note
scrolled with the table on a narrow screen. v2 now keeps v1's wrapper, so this
is no longer a difference.

## Regenerating

Only after a reviewed format change:

    .venv/bin/python -m agent.tests.test_render --write-goldens

Then review the diff and record the approval in the Phase 4 decision log entry.
