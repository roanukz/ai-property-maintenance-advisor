# AI Property Maintenance Advisor

### [Read the product teardown](https://roanukz.github.io/ai-property-maintenance-advisor/) &nbsp;·&nbsp; [See the recorded demo](https://roanukz.github.io/ai-property-maintenance-advisor/tool.html)

**Photograph a data plate, get a cited service brief for that exact model.**

When a short term rental owner lives ninety miles from the property and the hot
tub stops working, the guest can only describe the problem and read out an
error code. A technician gets called, inspects the panel, looks up the code,
identifies the model, and diagnoses the fault. Often that first visit is spent
working out which part is needed, and the part is ordered or fetched on a second
one. Aberdeen Group research, widely cited across field service, puts about 51
percent of repeat visits down to a missing or wrong part.

None of that information is hard to find. The code is on the panel, the model is
on the data plate, and the manufacturer publishes what the code means. It is all
available and it is never in the same place at the same time as the person who
needs it. So this reads the data plate from a photo, searches the manufacturer
documentation live, and uses generative AI to write a brief for that exact
model, where every claim is cited back to the document it came from.

The interesting part is what it does when the documentation does not support an
answer. A maintenance tool that invents a plausible repair is worse than no
tool, because the cost of a confident wrong answer lands on someone standing in
front of live equipment. So NO RELIABLE ANSWER is a first class output, enforced
in validation code rather than requested in a prompt.

## Two pages, one URL

| Path | What it is |
| --- | --- |
| `index.html` | The **product teardown**, the essay documenting the job, the evidence, what v1 proved, why v2 is built on LangGraph and LangChain, how it splits the work between Claude and Tavily, what v2 lets owners and technicians do, how it was tested, and what I cut. This is the front door. |
| `tool.html` | The **recorded v2 demo**. Eight cases replayed from live runs of v2 on 14 October 2026, on Claude Haiku 5.5 (build `d24ed7c475dcd813`). Four of the eight end with no reliable answer, and the page says so. It replaces the v1 demo. |

Live: **[the teardown](https://roanukz.github.io/ai-property-maintenance-advisor/)** and **[the demo](https://roanukz.github.io/ai-property-maintenance-advisor/tool.html)**. Both pages
share `src/tokens.css`, so the essay and the thing it describes read as one
product.

## The demo makes no API call

Every result on `tool.html` is verbatim output from a live run of v2 on 14
October 2026 (build `d24ed7c475dcd813`, on Claude Haiku 5.5), captured into
`src/fixtures.js` and replayed. Each case names its run ID. The demo was
re-recorded on that build, which carries the safety step check, warranty status
and the replacement sweep described below, so its briefs carry the flags after
Jev's check, and each case shows which flag the writer set and which Jev
raised. Only case 1 has steps to try first: the writer flagged 3 of its 4, and
Jev agreed with all three and raised none of its own. It is replayed rather than
live for three reasons:

1. **It costs nothing to visit and nothing to host.** The working build calls
   Claude and Tavily on every question it has not seen before, and TypeSafe's
   Jev on every step to try first that it writes, all billed by use.
   A public demo wired to that is a bill that scales with strangers.
2. **The case that matters is always reachable.** A visitor on a live tool would
   have to invent a fake model number to see the refusal behavior. Here it is a
   button.
3. **It is deterministic.** A portfolio demo that depends on a third party
   staying up is a demo that eventually embarrasses you.

The briefs under `briefs/v2/` are the real generated artifacts of those runs,
unedited, and the four under `briefs/` are v1's, kept as published. The
blurry plate case stops before a brief, so there are seven. One v2 brief,
`briefs/v2/t-044cd20f9678498e.html`, was written against synthetic property
records: its "this has happened before" service record, its install date and
age, its warranty terms and its warranty coverage rows are all synthetic test
data, although inside the file only the warranty text and the coverage line
say so, and the file is kept byte for byte. Each one is
self contained with no external dependencies. That is not a demo convenience: it
is the product requirement, since a technician has to open one with no app and
no login.

## The share card

`og-image.svg` is the source; `og-image.png` is what the meta tags point at.
Edit the SVG, then re-render at 2x:

```bash
node -e 'const s=require("sharp"),f=require("fs");s(f.readFileSync("og-image.svg"),{density:288}).resize(2400,1254).png({compressionLevel:9}).toFile("og-image.png")'
```

The layout is centered on purpose. LinkedIn's profile Featured section ignores
the 1.91:1 ratio and center crops to roughly a square, so every element sits
inside a 540px wide center column and survives that crop. Same rule as
`agent-answer` and `save-the-dates`.

## v2: the agent

v1 answered each question from scratch: every brief paid for seven or eight
live searches, even for a model it had researched the week before, and it knew
nothing about the property. v2 rebuilds it in Python as a LangGraph agent that
remembers. Its source is in `agent/`. In cheap mode, the lower cost model
setting every live number below was measured on, each Claude step calls Claude
Haiku 5.5. It moved from Claude Haiku 4.5, which the code stops using on 15
October 2026.

- **A graph of how the steps connect.** Read the plate, pause for the owner to
  confirm the unit, route, research or reuse, write the brief, check it, and
  save what was learned. The pause survives a restart: a run stopped at the
  confirmation can be finished by a different process.
- **A knowledge graph that grows from real questions.** A validated brief
  leaves behind the codes, parts, successor models and documented causes it
  verified, such as "this model shows this code", each stored with the exact
  words from the page that say so. A brief that verifies none of these leaves
  nothing behind. A repeat question checks the graph before it searches.
- **Property records.** Appliances, service history, install and warranty
  dates, and a maintenance schedule taken only from the maker's own intervals.
  The seed records are synthetic.
- **Warranty status in code.** Coverage is stored as rows (component, term,
  and whether it starts at install or purchase), and code works out each
  row's end date and whether it is active on the run's date. The model never
  writes a warranty date, and a validation rule rejects one. The brief lists
  active coverage and tells the owner to check with the maker before paying
  for a repair; code never decides which coverage applies to a repair. With no
  rows, it says "No warranty on file.", never "out of warranty". Shown on a
  live run on build `d24ed7c475dcd813`, with synthetic rows.
- **A "plan a replacement" sweep.** `advisor property sweep` lists each unit's
  age and any successor model or discontinued part the graph has verified,
  with the quote that names it. Where the graph has nothing, it says "No
  discontinuation documented in what this tool has verified.", never "not
  discontinued". An optional lookup fills in units with no such facts, through
  the same validation and graph rules. Shown live on build
  `d24ed7c475dcd813`.
- **A product recall check.** `advisor property recalls` asks the U.S.
  Consumer Product Safety Commission (CPSC), whose recall service is free and
  needs no key, for each maker's product recalls, and counts a product recall
  only when it prints the unit's model exactly. A match lists the product
  recall and says to check whether the unit's serial number is in the affected
  range; it never says the unit itself is affected, and with no match it says
  no product recall names this exact model, with the date checked. Shown live on build
  `ad11e6e15a55d30a`.
- **The same guarantees, still in code.** Citations must point to pages that
  were actually retrieved, tier labels can be lowered but never raised, a panel
  code narrows the answer, a refusal clears every candidate, and a price appears
  only when a cited page states it.
- **A safety check outside the writer.** Jev, a judgment model from TypeSafe,
  reads each step to try first and answers how likely it is to be a safety step
  (electrical, heat or gas). It can raise a safety flag the writing model left
  off, never clear one, and never add a step. If a Jev call fails, a fixed list of safety words (the tuned word rule,
  described below) can still flag that step, and the brief says in one line
  that the check did not run.
- **Spend capped in code.** Every paid call is reserved in a ledger before it is
  made: $0.15 per brief, $0.15 for a whole replacement sweep and $5 for the
  whole build, and every paid command shows its planned calls and cost and
  waits for a typed "proceed".
- **An MCP server for AI assistants.** MCP, the Model Context Protocol, is an
  open standard that lets an AI assistant call outside tools. Through four
  tools, an MCP app can ask the advisor for a brief, a property's status or a
  product recall check, with every rule above still in the advisor's own code.
  Every paid or outbound call first shows its preflight (the planned calls and,
  for a paid call, their typical and worst case cost against the cap) and goes
  ahead only on "proceed". So far Claude Code has called the tools in a free
  replay session (recorded model replies, no money spent); one live, paid
  brief has run through the client of the official MCP SDK for Python (SDK:
  software development kit, the library for building MCP servers and
  clients), driven by a test program; and one live, paid brief has run in
  Claude Desktop, with me reading the preflight and typing "proceed". That
  brief ended with no reliable answer, so no live brief through an assistant
  has yet carried steps to try first or safety flags. See
  [Use it from Claude Desktop or Claude Code](#use-it-from-claude-desktop-or-claude-code-mcp).

### Results

Each criterion was set before the build, except the MCP server's, which had no
bar, and each carries the success criterion ID that `DECISION-LOG.md` uses
(SC12b and so on). "Replay" means the whole graph ran on recorded model
replies at no cost: it proves the rules,
routing, ledger and renderer, and says nothing about the model. "Live" means
real calls to Claude Haiku 5.5 in cheap mode, Tavily, TypeSafe's Jev and, for
the product recall check, CPSC's free recall service. A build is named by a
fingerprint of the runtime code, so any change to that code makes a new build,
and every row names the build it was measured on. The live evaluation and the
demo were re-measured on 13 and 14 October 2026 (US Eastern time; in UTC, the
clock the run records and the demo's timestamps use, the SC12b runs of the
evening of 13 October fall early on 14 October) on build `d24ed7c475dcd813`.
The batch opened with a check that every Claude step is answered by Claude
Haiku 5.5 at its exact settings, and it passed. Four live rows come from elsewhere:
SC12a's Jev calls trace to a locked settings file and model `jev-1.13.0`, not
to a build; the Claude judge ran once, on 15 October 2026 on build
`179b4295c98a3143`, with settings fixed in code before it ran (settings
fingerprint `658f072743accb13`); the product recall check ran once on build
`ad11e6e15a55d30a`, the build just before a research setting was raised (it
sends only the maker's name and reads no model setting, so that change could
not alter it); and the MCP server's two live briefs ran on 16 October 2026,
the first on build `17427277685ed433`, which adds only the server to
`179b4295c98a3143`, and the Claude Desktop brief on build `841df66ad9669ef8`,
which changes only what the server says (decision 105).
Where a row compares with Claude Haiku 4.5, it names that build too. Recall is
the share of real safety steps a check flagged; precision is
the share of its flags that were real safety steps.

| Criterion | Result | How it was measured | Build |
| --- | --- | --- | --- |
| The offline suite passes with no key and no network | Pass | 3,696 tests, run with the network blocked on the build machine on 17 October 2026: all 3,696 pass, in normal mode and in gate mode at phases 5, 6 and 7 (`ADVISOR_GATE`; a gate run fails if any test its phase requires is missing or skipped, and phase 7 adds all 22 of the MCP server's test functions and the 1 test that checks phase 7's list, for 393 required node ids). A fresh clone runs fewer: the v1 comparison below needs v1's private source, and the tests that replay v2's live recordings need the build machine's untracked `data/` folder. 14 of v1's 17 guardrail behaviors are ported; the other 3 tested parsing a JSON block that structured output removed and are marked obsolete. | The code of `841df66ad9669ef8` |
| v1's and v2's validators agree | Pass | 180 payloads through v1's own TypeScript validator and v2's Python port; every accept and reject decision matches. Last run against v1's private source on 14 October 2026. | The code of `d24ed7c475dcd813` |
| The five v1 cases give v1's outcome | Pass, replay | One confirmed candidate for a panel code, several for a vague symptom, no invented codes, NO RELIABLE ANSWER for an invented model, a halt on a blurry plate. | The code of `d24ed7c475dcd813` |
| A refusal never carries candidates | Pass | Unit and property tests. | The code of `d24ed7c475dcd813` |
| The invented model is refused 3 of 3 times (SC3b) | Pass, live | 3 of 3 refused by the model itself; none forced by validation. Each cost $0.0045 to $0.0068 and took 21.6 to 27.2 s. On Claude Haiku 4.5 (build `da738a1559ced359`) its three refusals cost $0.0270 to $0.0451 each. | `d24ed7c475dcd813` |
| A blurry plate halts before any search | Pass, replay; the plate reading also live | The halt is shown in replay. Live, the plate reader read the clear plate exactly and marked the blurry plate unreadable rather than guessing, with no search, for $0.0005 and $0.0006. | `d24ed7c475dcd813` |
| A paused run can be finished by another process | Pass, replay | A run stopped at the confirmation is resumed by a new process without repeating the plate reading. | The code of `d24ed7c475dcd813` |
| Citations and graph evidence are real | Pass, replay | Every citation resolves to a page retrieved in that run or, on the graph route, to a page an earlier run retrieved and the graph recorded; every graph fact carries a verbatim quote from its page, or is dropped. | The code of `d24ed7c475dcd813` |
| The graph answers what it already knows | Pass, live | The hot tub's first FLO lookup used 2 searches, cost $0.0075, took 40.1 s and saved 1 fact. The repeat was answered from the graph with no search for $0.0015 in 9.8 s, about a fifth of the first lookup's cost and a quarter of its time. Its answer was thinner: the one stored code and its meaning, with none of the first brief's 4 steps to try first, because the graph keeps a code's meaning, not its steps. On Claude Haiku 4.5 (build `da738a1559ced359`) the first lookup cost $0.0435 in 28.7 s and the repeat $0.0082 in 4.3 s. | `d24ed7c475dcd813` |
| A repeat question uses 2 searches or fewer (SC7b) | Miss, live | Repeats used 0, 2 and 4 searches. The FLO repeat used 0. A vague hot tub question found the stored FLO fact but not enough to answer, so it ran a short top up of 2 searches and 1 fetched page, and ended with no reliable answer ($0.0040, 26.5 s); the top up allows one fetch, and the second it asked for, an owner's manual for the hot tub's series, was blocked, which is what its refusal names as missing. The air conditioner's first lookup ended with no reliable answer and saved nothing (4 searches, $0.0086, 37.6 s), so its new symptom question had nothing to start from and ran a full search of 4 ($0.0089, 43.5 s), also ending with no reliable answer. That 4 is the miss: the limit of 2 applies only when the graph holds something to start from. On Claude Haiku 4.5 (build `da738a1559ced359`) the same check passed with 0, 2 and 2. | `d24ed7c475dcd813` |
| Service history is cited | Pass, replay and live | Live, with the synthetic property records attached, the hot tub brief cited the prior synthetic service record under "this has happened before", stated the unit's age as worked out in code from its install date, quoted the synthetic warranty terms and printed the warranty coverage line that code writes: 0 searches, $0.0013, 7.7 s. It confirmed the FLO meaning but listed no steps to try first, where Claude Haiku 4.5's records run (build `da738a1559ced359`) listed 4. | `d24ed7c475dcd813` |
| Upgrade options are cited or absent | Pass, replay | A discontinued model lists only successors that a fetched page names. | The code of `d24ed7c475dcd813` |
| Warranty status is computed in code (SC13) | Pass, replay and live | Code works out each coverage row's end date as its start plus its term in months, held to the last day of a short month, so a 29 February start plus 12 months ends on 28 February, and whether the row is active on the run's date. Tests that try many generated dates check the date math, a validation test rejects any warranty date the model writes, a page with no rows says "No warranty on file.", never "out of warranty", and every row is labeled synthetic. Live, the records run printed "No warranty coverage on file is active as of 2026-10-14 (UTC)" over the hot tub's three synthetic rows (labor ended 10 June 2022, equipment 10 June 2023, shell 10 June 2026). One synthetic water softener was added because every seeded term had ended; its property page, written on 14 October, shows labor active to 20 April 2027, the control valve to 4 May 2031 and the resin tank to 4 May 2036. | `d24ed7c475dcd813` |
| The replacement sweep shows only a cited successor (SC14) | Pass, replay; live: no successor verified for any of the 3 real units | Offline, the sweep reads only the graph's verified facts, with the network blocked and $0 spent. Replay tests cover a unit with a verified successor, a unit with nothing documented, and an invented model (refused, nothing saved), and no successor appears without a quote that names it. Live, the optional lookup ran for the three real units under one $0.15 cap for the whole sweep: none found a successor whose quote names the unit's model, one candidate for the air conditioner was dropped because its quote did not name this unit, and nothing was saved. The four synthetic units were refused before any call. $0.0044 and 8 Tavily credits. | `d24ed7c475dcd813` |
| The product recall check names only exact model matches (SC15) | Pass, replay; live: no product recall names any of the 3 models | It asks CPSC by maker name only, using only parameter names from CPSC's 2018 guide to its recall service (a test checks every request), then matches each product recall's text against the unit's model exactly, in code; a near match is never shown as a match. Live, 9 requests, three per real maker (as manufacturer, importer and distributor), returned 12 distinct product recalls for Trane, the air conditioner's maker, 6 for Rheem, the water heater's, and 1 for Sundance Spas, the hot tub's. None names any of the three models exactly, so each unit shows that no product recall names its exact model, with the date checked. Invented makers are never queried. Free, so no ledger charge; each response is stored with its date. | `ad11e6e15a55d30a` |
| Spend stays under the caps | Pass, live | Every question run on this build stayed under its $0.15 cap (the highest was $0.0098, an SC12b first lookup), and the sweep's three lookups stayed under the sweep's one $0.15 cap. The 26 ledger entries on this build, 21 question runs (the two plate readings included), the sweep's 3 lookups, the model check and a check that the model accepts the brief's structure, cost $0.1301 and 71 Tavily credits. The whole build, counting every live run on every build (the Claude judge of 15 October and the two MCP briefs of 16 October included), stands at $1.5532 of $5 and 249 of its 300 Tavily credits. | `d24ed7c475dcd813` |
| Safety steps are flagged, on a fixed labeled set (SC12a) | Measured, live: the choice rule picked Jev, which flagged 30 of 31 held out safety steps | 345 steps, labeled before any scoring: sentences from maker and dealer documentation and steps the tool wrote in earlier briefs. Three model readers, not people, labeled each one blind; they agreed on 338 of 345. The set was split into a tune half (170) and a held out half (175, with 31 safety steps); each check's settings were chosen on the tune half and locked in a file before the held out half was scored, once. Held out, against the 31 safety steps: the word rule as published (breaker, power, heat) flagged 20 (recall 0.645, precision 0.714); the word rule tuned on the tune half (it drops the bare word heat and adds wire, panel, door, disconnect, electrical, jumper and box), 22 (0.710, 0.647); Jev flagging when its answer, a probability from 0 to 1, is 0.51 or higher, 30 (0.968, 0.732); the tuned word rule plus Jev, 30 (0.968, 0.600). The held out half has no steps the tool wrote, so the writing model is measured by SC12b below. The choice rule, written before any result, drops a candidate (tuned word rule, Jev, or the tuned word rule plus Jev) below precision 0.60, then takes the highest recall, then the higher precision: all three stayed, Jev and the tuned word rule plus Jev tied on recall, and Jev had the higher precision, so Jev ships at 0.51. The tuned word list did not hold up: precision 0.955 on its tune half, 0.647 held out. 855 Jev calls, $0.0216. | None: a locked settings file and model `jev-1.13.0` |
| Ten new first lookups answer, at least 9 of 10, a bar set before the runs (SC12b) | Miss, live: 7 of 10 | Ten first lookups in cheap mode on the evening of 13 October 2026, five with the hot tub FLO question (the clear plate photo and "panel shows FLO") and five with the air conditioner's fan question ("outdoor unit runs but the fan does not spin"). 7 of 10 answered: 4 of 5 on the hot tub and 3 of 5 on the air conditioner; in the other 3 the model declined to write a brief from what it found. On Claude Haiku 4.5 (build `9a64835e86a5a6c8`) the same ten lookups all answered, for $0.4662 and 47 Tavily credits. That 10 of 10 is not a clean comparison: prompt rule 6 tells the writer to use documentation for this exact model or its documented family and never general knowledge of the equipment category, and Haiku 4.5's air conditioner answers cited Trane's general troubleshooting pages, which the rule does not allow. Haiku 5.5 is not consistent either: its three answered air conditioner lookups cite the same kind of pages, and two of its refusals declined on similar sources. No code check enforces rule 6; only the model applies it. The ten runs cost $0.0750 and 35 Tavily credits. | `d24ed7c475dcd813` |
| Every safety step in new live briefs is flagged (SC12b) | Pass, live: 11 of 11 | The seven briefs from those ten first lookups held 18 steps; the same three model readers called 11 of them safety steps, 10 of them on the hot tub. As the briefs show them, all 11 were flagged, with 3 false flags (11 of 14). The writer flagged 10 of 11 by itself, with 3 false flags (10 of 13); Jev flagged 10 of 11 with 1 false flag (10 of 11), and every Jev call answered, so Jev added the one the writer missed. The tuned word rule, which ships only as the backup for a failed Jev call, flagged 11 of 11 with 3 false flags (11 of 14). The sample is small: the lower end of recall's 95% interval, the range the true rate plausibly falls in given so few steps (a Wilson interval), is 0.741. On Claude Haiku 4.5 (build `9a64835e86a5a6c8`) 14 of 14 safety steps in its ten briefs were flagged. | `d24ed7c475dcd813` |
| A person checks the safety step labels (SC12c) | Measured: I agree with the model readers on 162 of 175 held out steps, and by my labels Jev caught 22 of 26 safety steps, a lead over the word lists inside overlapping intervals | The safety rows above are scored against three model readers' labels. On 15 October 2026 I labeled all 175 held out steps myself on the labeling page, one at a time and blind, with the readers' protocol and worked examples, and my answers were sealed with a fingerprint of the file (it changes if one byte does) before anything was scored. Under the protocol, a yes that names only "other" as the hazard counts as no, as it does for the readers. Against the majority of the three readers, which are Claude models, I agree on 162 of 175 (92.6%), Cohen's kappa 0.728 (Cohen's kappa is agreement corrected for chance: 0 is what chance alone would give, 1 is full agreement); I called 26 steps safety steps, the readers 31. Against my 26, with 95% Wilson intervals: Jev at 0.51 caught 22 (recall 0.846, interval 0.665 to 0.938) at precision 0.537 (0.387 to 0.679); the published word list 18 (recall 0.692, interval 0.500 to 0.835) at precision 0.643; the tuned word list 18 (recall 0.692) at precision 0.529. Jev still catches the most safety steps by a person's reading, but with 26 safety steps its lead over the word lists sits inside overlapping 95% intervals, and about half of its flags are steps I would not call safety steps. After seeing the 13 steps where we disagreed I revised 3 labels (2 now match the readers); the revisions are recorded separately, and every number here uses the sealed blind labels. One person labeled the steps, me, and I am not a technician, so a technician might draw the line elsewhere; 26 safety steps is a small sample. A paired test added on 16 October, after these results were known (it compares two checks step by step on the same steps, counting only where they disagree; the p value is the chance of a split at least this lopsided if the two were equally good), finds Jev caught 4 of my safety steps that each word list missed and neither list caught one Jev missed (p 0.125), so by my labels Jev's lead is still not shown; on false flags the published list leads, 8 to Jev's 17 (p 0.108), also not shown; by the readers' labels Jev's leads over the published list (11 to 1, p 0.006) and the tuned list (8 to 0, p 0.008) are the most lopsided of 24 comparisons, reported with no correction for their number. $0. | None: labeled and scored on 15 October 2026 against SC12a's recorded answers; the page and scorer sit outside the build fingerprint |
| A Claude judge asked Jev's question, set before its run (decision 95) | Measured, live: by my labels Claude caught 19 of 26 safety steps, Jev 22 | Registered before it ran: on the same 175 held out steps, Claude Haiku 5.5 was asked Jev's exact question, one call per step, at low effort; a true answer is a flag, with no threshold and no tuning. All 175 answered, none declined, for $0.0182. Against my labels: recall 0.731 (19 of 26, interval 0.539 to 0.863), precision 0.576 (0.408 to 0.728); Jev's lead over Claude, 22 to 19, also sits inside overlapping intervals. Every safety step by my labels that Jev missed, Claude missed as well. Against the readers' majority: 28 of 31 (recall 0.903) at precision 0.848, against Jev's 30 of 31 at 0.732; the readers are Claude models, which may favor Claude, and Claude's precision falls from 0.848 against them to 0.576 against my labels. Claude and Jev agree on 163 of 175 steps (93.1%), Cohen's kappa 0.795. From the ledger, Claude cost $0.000104 a step against Jev's $0.000028, about 3.7 times as much, and took a median 1.08 s a step (90th percentile 1.67 s). A paired test on the same steps, chosen after these results were known and four of the 24 comparisons reported with no correction for their number, does not show a difference: by my labels Jev caught 3 safety steps Claude missed and Claude none Jev missed (p 0.250), and of the 149 other steps Jev raised 7 false flags Claude did not and Claude 2 Jev did not (p 0.180); by the readers' labels the splits are 3 to 1 on catches (p 0.625) and 7 to 1 on false flags (p 0.070). Nothing in the shipped check changed. | `179b4295c98a3143`: settings fingerprint `658f072743accb13`, run `claude-judge-152437a0a5ed`, 15 October 2026 |
| The MCP server gives the same brief and spends only after its preflight and "proceed" (decisions 97 to 106) | Shown, with no bar set before the build: replay parity with the command line, one live brief through the official SDK's client, Claude Code calling the tools in a free replay session, and one live brief in Claude Desktop, which ended with no reliable answer | Offline, 32 test cases in `agent/tests/test_mcp_server.py` run with the network blocked, 28 of them through the SDK's own client, 27 in process and 1 over real stdio (standard input and output, a direct connection between two programs on one computer), on protocol version 2026-07-28 and, in the 4 cases on how the server asks the person, the earlier 2025-11-25. They show the same run record through `advisor ask` and `advisor resume` as through the tools (replay parity with the command line); in offline tests against fake paid services, a live call refusing every missing, wrong, reused, foreign or changed confirmation, with nothing sent and no ledger row; and every citation and safety flag reaching the response on nine recorded cases. A form (elicitation, in which the server asks the app to show the person a form) has been answered only in these tests. A confirmation is used once, bound to the tool, its exact arguments and the exact preflight lines, and expires after ten minutes. All 53 planted bugs for the server and its gate are caught. On 16 October 2026 a test program drove the SDK's client over stdio, first in replay at no cost on the recorded FLO case (all four tools listed; the replayed brief paused, resumed and came back with 2 steps to try first, both cited, and 1 safety flag; `property_status` returned its 3 appliances), then for one live brief on protocol version 2026-07-28 without forms. No person was in the live run: the test program played the owner under my approval given in advance at about $0.01, sending "proceed" only to a preflight with a typical cost of at most $0.02 and a worst case of at most the $0.15 cap, and confirming the unit the plate shows. `get_service_brief` with the clear plate photo and "panel shows FLO" returned `confirmation_required` with the preflight (typical $0.0109, worst case $0.15, the run cap) in 0.35 s, and nothing was sent. After "proceed" it read the plate exactly (Sundance Spas, Optima 880, serial 100915742, 06/2014) and paused for the owner to confirm the unit and the code FLO, which came from the owner's words, not the plate, in 5.37 s. `resume_brief` returned its own preflight (typical $0.0105, worst case $0.1497) in 0.20 s, and after "proceed" it finished with status ok in 10.03 s. The four tool calls took 15.95 s; the whole session, from starting the server to closing it, 17.82 s. The brief was answered from memory, from facts an earlier lookup verified (the graph route, 0 searches, 0 Tavily credits, no Jev call), with one confirmed candidate, the FLO meaning "Flow Switch Error: Water flow is restricted or the switch is stuck.", cited to a dealer page. It cost $0.0014 by the ledger, inside the $0.15 cap (SC11 pass), about the same as the $0.0015 the same question cost from memory through the command line on 14 October (`t-d644c199cbd74532`); the difference is the length of what the model wrote in the brief writing call (1,080 output tokens against 1,274, with the same input), not the server's. Its run record gives 12.97 s of processing, against 9.8 s on 14 October, 12.96 s of it in the two model calls. Later that day Claude Code 2.1.299, running Claude Opus 5.5, was given the server for one session only, in replay: non-interactive, with nothing added to its settings and only the three tools it needed allowed in advance. It found the four tools and on its own called `get_service_brief` and `property_status`; the server's form question was canceled automatically, so the brief stayed paused, and Claude sent the confirmed unit itself, taken from the owner's request, through `resume_brief`, the path for apps without forms. The brief came back ok with 2 steps to try first, both cited to dealer pages, and 1 safety flag set by the writer; Jev's check shows as not recorded, because the older recording holds no Jev answers. Claude's answer gave both steps with their sources, the safety flag word for word and that the writer raised it, that Jev's check was not recorded, the cost labeled as the replay ledger with `real_money` false, the run ID, and the property's warranty, product recall and replacement status, and it added its own reading, calling two lines in the brief contradictory: a replay artifact, since the recorded model text predates the unit details given in this session. 34.5 s and 5 turns, $0 to the project. Then decision 105 made two fixes to what the server says, which moved the build to `841df66ad9669ef8`: result messages count in the singular at 1, and a paused brief and the server's standing instructions tell the assistant to show the owner what was read, ask them to confirm or correct it, and never fill in or change a field itself. On that build, Claude Desktop 2.42304.0 on macOS ran the server from its own settings file with live spending allowed in cheap mode (`--live`, `ADVISOR_MODE=cheap`), for run `t-7f23f74a338140bd`. I chose the trial and approved the money in advance, then read the preflight and typed "proceed" myself in the Desktop chat. My request: "The water heater at my rental isn't making hot water. Use the property-advisor tools to get me a service brief for appliance appl-waterheater at property prop-b." There was no plate photo: the unit came from the property's synthetic registry record (a Rheem PRO+E50 M2 RH92 CL electric water heater), so there was no plate reading and no pause to confirm the unit, and decision 105's instruction was not put to Desktop. `get_service_brief` returned its preflight and sent nothing in 0.41 s by Desktop's log (16:20:24.046 to 16:20:24.456 UTC): typical $0.0105 and worst case $0.15 against the $0.15 cap; model calls typical 7, at most 17; Tavily credits typical 5, at most 10; Jev calls 8 planned for one draft, up to 16 for two; the whole build at $1.5471 of its $5. The server sent no form: it took the fallback path for apps without forms (`confirmation_required`, then a call with `confirm="proceed"` and the `confirmation_id`), and Desktop's log shows only results from the server, no request to the app. Desktop showed the preflight as text in the chat and asked me to type proceed, so "proceed" passed through the assistant. Desktop's log does not record which protocol version it used, and this trial does not show whether Desktop can show forms. The next call reached the server 25.7 s after the preflight left it, a gap that includes the assistant writing its message and my reading and typing. That call took 47.50 s in Desktop's log (16:20:50.161 to 16:21:37.656 UTC); the run record gives 43.45 s of processing, 26.06 s of it in research and 17.30 s writing the brief. Route: history and research (the graph had nothing verified for this unit or its family; the registry held 1 record for the appliance), with 5 web searches, 3 page fetches and 7 Tavily credits. It cost $0.0061 by the ledger (0.0060734), against the preflight's typical $0.0105, inside the $0.15 cap (SC11 pass). The brief ended with no reliable answer, refused by the model: the only manuals found belong to sibling models with different suffixes (RH95 EC1), the retailer and marketing pages describe no procedure, and matching the symptom would need general knowledge about electric water heaters, which the brief does not use. Jev's check was skipped, because there were no steps to check; no steps, no safety flags. Desktop then called `property_status` for prop-b on its own (0.09 s, offline and free) and searched the web itself. Its answer reported the refusal and its reason correctly, the cost ($0.0061 and 7 credits, 51 of 300 left) and the location of the brief's page, and said the property's record is synthetic. Then it added its own content, outside the tool: warranty terms of closely related models from the maker's product pages, with an inference that parts are probably still covered; troubleshooting steps it labeled as general electric water heater knowledge, "not from the advisor"; and its own pick of what to do. Before the run it had also added a safety caution of its own. The tool refused to fill the gap from general knowledge, and the assistant filled it, labeled as its own: the retelling limit below, seen in a live run. Claude Desktop's own usage on my Claude plan was not measured and is outside the project's ledger. After the trial, Desktop was set back to replay. Limits: no live brief through an assistant has yet carried steps to try first or safety flags. No form has yet reached a person outside the tests: Claude Code's non-interactive session canceled the one it was sent, and the server sent Claude Desktop none. Which apps show MCP forms to the person is unverified, and without forms both the owner's confirmation of the unit and the "proceed" pass through the assistant, which could send either without asking, even a model number the plate did not show; the pause to confirm the unit has not been put to an assistant since decision 105. So the app's own approval prompt is the owner's only check: keep it on, and never set these tools to always allow; how each app shows that prompt is unverified. The assistant's retelling is outside the tool's control: Claude Code added its own reading, and Claude Desktop filled a refusal's gap with general advice. Neither live brief had steps or safety flags, so safety flags reaching the response is shown on recorded cases only. Evidence, kept out of the public repo: the test program's script and logs in `data/runs/t-bc31dd881bff44c4.client/`, the only source of the per call times, both preflights and the replay connection (the replay log ends with the test script's own crash on its final summary line, after every call had printed; the script was fixed before the live part); the Claude Code session in `data/evidence/claude_code_replay_20261016/`; and the Claude Desktop trial in `data/evidence/claude_desktop_live_20261016/`: the chat as I pasted it, Desktop's own log of the server's messages (the source of each call's times), the same lines from its MCP log, the server entry it ran and the ledger's readout of the run, beside the run record `data/runs/t-7f23f74a338140bd.json` and the brief's page `data/briefs/t-7f23f74a338140bd.html`. | `17427277685ed433`: live run `t-bc31dd881bff44c4` and Claude Code's replay run `t-7ecdeb193c584be8`; `841df66ad9669ef8`: Claude Desktop's live run `t-7f23f74a338140bd`; all 16 October 2026 |

Latency had no target. Claude Haiku 5.5 is slower than Haiku 4.5 in every
set of timed runs, while costing much less. The ten SC12b first lookups
took a median of 41.3 s (27.1 to 45.5) at a median $0.0076, against 24.9 s
(20.5 to 45.0) and $0.0476 for Haiku 4.5's ten on build `9a64835e86a5a6c8`:
about a sixth of the cost per run. The FLO first lookup took 40.1 s and its
repeat from the graph 9.8 s, the run record's processing time, as the demo
shows; on Haiku 4.5 they took 28.7 and 4.3 s. Refusals of the invented model
took 21.6 to 27.2 s, the records run 7.7 s, and the two plate readings 4.0
and 3.7 s.

In the re-recorded demo, only case 1 has steps to try first. The writing model
flagged 3 of its 4 steps, and Jev agreed with all three (0.98, 0.77 and 0.96)
and raised no flag of its own. Cases 2 and 8 answered from memory with no
steps, and cases 3 to 6 ended with no reliable answer, so the safety check had
nothing to read.

With Jev alone shipped, the tuned word rule backs up any step whose Jev call
fails: it can add a flag to that step, never remove one, and the brief still
shows the notice line. Held out, by the model readers' labels, the tuned word
rule caught 22 of 31 safety steps (recall 0.710) at precision 0.647, against 30
of 31 (0.968) at 0.732 for Jev and 20 of 31 at 0.714 for the published list. By
my own blind labels (SC12c), the tuned list caught no more than the published
one, 18 of 26 each, and flagged more steps that were not safety steps:
precision 0.529, against 0.643. The published list was not
one of the choice rule's candidates; the tuned list was picked on 1 October,
after the held out scores were known, in the rule's order: both have precision
0.60 or higher, and the tuned list has the higher recall. Every live run of 13
and 14 October was on a build with the backup, but every Jev call answered, so
the backup never ran live; it is shown working only by the offline tests.

Every test is paired with a planted bug in `agent/tests/mutations.toml` that it
must catch. A full run on 17 October 2026, on the code of build
`841df66ad9669ef8`, caught all 1,692. What was decided, what it cost, and what went wrong on the way is
in `DECISION-LOG.md`.

### The recorded v2 demo

`tool.html` replays eight cases, each from one live run on 14 October 2026
on Claude Haiku 5.5 (build `d24ed7c475dcd813`) and named by its run ID, and
shows the brief file that run wrote, unedited. Four of them end with no
reliable answer, as measured, and the page says so:

1. A hot tub FLO code, first lookup: the plate read, the owner's confirmation,
   2 searches, and 1 fact saved to memory; Jev agrees with all three of the
   writer's safety flags.
2. The same question again, answered from memory with no search, but with
   none of the first brief's 4 steps to try first.
3. A vague hot tub symptom: a short top up of 2 searches that ends with no
   reliable answer, after the limit blocked the owner's manual its refusal
   names as missing.
4. An air conditioner's first lookup: 4 searches, no reliable answer, and
   nothing saved to memory.
5. A new question about that air conditioner: with nothing saved to start
   from, a full search of 4, again with no reliable answer.
6. An invented model, refused (3 of 3 live runs).
7. A blurry plate, stopped at the confirmation before any search.
8. A **live run against synthetic property records**: the prior service
   record, the unit's age from its install date, the warranty terms and the
   coverage line code writes from them, with no search.

Every number on the page comes from the run named next to it; nothing is
estimated. The page makes no network call. `agent/replay/build_demo.py`
rebuilds it from the run records on the build machine (`data/` is not
tracked), refuses any run from another build, and
`agent/replay/check_demo.py` checks the result.

### Run it

Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

```bash
uv run pytest
```

A replay run costs nothing. It pauses at the confirmation step and prints the
command that resumes it; in replay, add the same `--cassette` flag to that
command:

```bash
uv run advisor ask --symptom "panel shows FLO" --appliance appl-optima880 --cassette agent/tests/cassettes/synthetic/optima_history.json
```

The replacement sweep runs offline by default: it reads only what the graph
has verified, spends nothing, and writes each property's page.

```bash
uv run advisor property sweep prop-a
```

`--all` sweeps every property. `--research` first looks up discontinuation and
successor models for units with no such facts, in replay with one `--cassette`
per unit, or live with `--live`, under one $0.15 cap for the whole sweep.

The product recall check prints each appliance's latest stored check, offline:

```bash
uv run advisor property recalls prop-a
```

`--live` asks CPSC's recall service now. It is free and needs no key, and it
still prints the planned requests and waits for "proceed".

A live run needs `ANTHROPIC_API_KEY`, `TAVILY_API_KEY` and, while the safety
check is on, `TYPESAFE_API_KEY` in a `.env` file (see
`.env.example`; `.env` is never committed), `ADVISOR_MODE=cheap` set in the
shell, and `--live`. The first live run on a new checkout also needs
`--new-ledger` to create the spend ledger. Every live command prints what it
plans to spend and waits for "proceed".

### Use it from Claude Desktop or Claude Code (MCP)

MCP, the Model Context Protocol, is an open standard that lets an AI assistant
call outside tools. `agent/mcp_server.py` serves the advisor to an MCP app, such
as Claude Desktop or Claude Code, as four tools: `get_service_brief` and
`resume_brief` (the same pipeline as `advisor ask` and `advisor resume`),
`property_status` (a property's warranty coverage, its "plan a replacement"
rows and its stored product recall checks; free and offline) and
`check_product_recalls` (the live CPSC check; free, but it asks first). A
finished brief reaches the assistant with every step to try first and its
source, every safety flag word for word, the documented candidates, the
warranty coverage line, the cost, the run ID and the location of the brief's
page on your computer, the file to send a technician.

#### Connect your own copy

Each person runs their own copy of the server, on their own computer; the
server itself has nothing to sign up for. You need Python 3.12 and
[uv](https://docs.astral.sh/uv/), as in "Run it" above, and Claude Code or
Claude Desktop, signed in to your own account. The assistant's part of each
conversation counts against that account's own plan or usage, as any chat
does, never against mine; the server's replay spends nothing.

1. Clone the repository and install it. `uv sync` installs the project and its
   pinned dependencies in the clone's own `.venv` folder, with the server's
   entry point, the command that starts it, at `.venv/bin/advisor-mcp`.

   ```bash
   git clone https://github.com/roanukz/ai-property-maintenance-advisor.git
   cd ai-property-maintenance-advisor
   uv sync
   ```

2. **Claude Code.** (Steps 2 and 3 are alternatives: do the one for your
   app.) From the cloned folder, add the server, then check that it connects.
   `$PWD` is the shell's name for the current folder, so the command saves
   full paths, which the app needs because it starts the server without a
   shell. `--cassette` names the recorded case (a cassette: recorded model
   replies) that the server replays.

   ```bash
   claude mcp add property-advisor -- "$PWD/.venv/bin/advisor-mcp" --cassette "$PWD/agent/tests/cassettes/flo.json"
   claude mcp list
   ```

   `claude mcp list` should show `property-advisor` as connected.
   `claude mcp add` saves the server in Claude Code's local scope, for this
   project only: a `claude` session started in the cloned folder, or a folder
   inside it, has the property-advisor tools, and one started anywhere else
   does not. To use it from every folder, put `--scope user` after
   `claude mcp add`. I checked on 17 October 2026 that this form, run from the
   clone, lists the server as connected in `claude mcp list` from another
   folder.

3. **Claude Desktop (macOS).** Quit Desktop completely and open
   `~/Library/Application Support/Claude/claude_desktop_config.json`. If the
   file does not exist, create it with the block below as its whole content.
   If it does, add only the `"property-advisor"` entry inside its existing
   `"mcpServers"` object (adding that object if there is none), and keep the
   rest of the file. Either way, replace
   `/path/to/ai-property-maintenance-advisor` with the full path of your clone
   (run `pwd` in it to see it):

   ```json
   {
     "mcpServers": {
       "property-advisor": {
         "command": "/path/to/ai-property-maintenance-advisor/.venv/bin/advisor-mcp",
         "args": ["--cassette", "/path/to/ai-property-maintenance-advisor/agent/tests/cassettes/flo.json"]
       }
     }
   }
   ```

   Then open Desktop again. Desktop may rewrite this file from its own copy
   while it is open, which can undo an edit made then, so edit it with Desktop
   closed, and check it again after the next restart. If the property-advisor
   tools do not appear in Desktop, the server's log is
   `~/Library/Logs/Claude/mcp-server-property-advisor.log`.

4. **Ask a first question the replay answers.** In Claude Code, start
   `claude` from the cloned folder (or from any folder, if you added the
   server with `--scope user`); in Claude Desktop, open a new chat. Replace
   `/path/to/ai-property-maintenance-advisor` with your clone's full path
   (run `pwd` in the clone to see it), and ask, for example: "My hot tub's
   panel shows FLO. The data plate photo is at
   /path/to/ai-property-maintenance-advisor/demo-assets/plate-clear.jpg. Use
   the property-advisor tools to get me a service brief." When the brief
   pauses for you to confirm the unit, compare what was read with the photo (a
   Sundance Spas Optima 880) and confirm it yourself. Replay answers only that
   one recorded case. A question needs a plate photo path, an appliance ID
   from the synthetic property registry such as `appl-optima880`, or the
   unit's maker and model typed from its plate; with none of them, the server
   refuses. With any of them, a replayed brief carries the hot tub's FLO steps
   and sources, even when its heading and property record lines name another
   unit. Asked about `appl-xr16`, the air conditioner at prop-a, it returned
   "Brief for Trane XR16 4TTR6036" with the hot tub's steps. So read every
   replayed brief as the hot tub's, whatever unit it names. `property_status`
   answers for both synthetic properties, prop-a and prop-b, offline.

What I checked on 16 October 2026, on a fresh clone with no keys and no `.env`:
started from `advisor-mcp` with `--cassette`, the server connected over
standard input and output, a direct connection between two programs on one
computer, with its four tools; the replayed FLO brief paused for the unit, then
came back with status ok, 2 steps to try first, both cited, 1 flagged as a
safety step, 1 documented candidate, and its cost labeled as the replay ledger
with `real_money` false; and `property_status` for prop-a listed its 3
appliances. Claude Code 2.1.299, run with a throwaway settings folder, added
the server with the `claude mcp add` command above, and `claude mcp list`
showed it connected. Not yet tried: Windows and Linux (only macOS has been
tried); Claude Desktop started from the `advisor-mcp` entry above (it has run
the same code, started as `.venv/bin/python -m agent.mcp_server`); and `/mcp`
inside a Claude Code session.

#### Who pays, and where the keys live

- **Nothing is hosted.** The server talks to the app only over standard input
  and output. It opens no network port and has no address anyone else can
  reach, so no one else can connect to my copy, and I cannot connect to
  yours: everyone who connects runs their own copy.
- **Replay needs no keys and spends nothing.** Replay, the default, answers
  from recorded model replies and labels its cost as the replay ledger, with
  `real_money` false. Its cost line still counts the calls the recording
  holds, priced at the recorded model's prices, for example "0.0828 USD on
  the replay ledger (no money was spent), 5 Tavily credits" for the FLO
  brief: those figures describe the recording, and a replay sends no
  request, so it spends no money and no Tavily credit on anyone's account.
  Started with neither `--cassette` nor `--live`, the server refuses a
  brief, since replay needs a cassette, and `property_status` still works
  offline.
- **A live brief is paid for by whoever runs that copy.** The server reads keys
  only on the computer running it, from that computer's environment or the
  copy's own `.env`, so a live brief's cost lands on that person's own
  Anthropic, Tavily and TypeSafe accounts. No copy can use my keys: they stay
  on my computer and are not in the repository.
- **The repository holds no keys.** On 16 October 2026 every file in the
  repository's history, on every branch and the published pages included, and
  every file in the working copy that git tracks or would commit was searched
  for my three key values, and none appeared; a scan for key shaped strings
  found none either. A test, `agent/tests/test_no_keys.py`, now fails the
  suite if a key shaped string is in any file git would commit, and checks
  that `.gitignore` keeps `.env` and `.env.local` out.
- **The product recall check is free and keyless**, in any mode, but it sends
  requests to CPSC's public recall service, so it asks first.
- **Keep keys in your copy's `.env`, never in an app's settings file.** Copy
  `.env.example` to `.env` in the clone and fill it in; git ignores `.env`, and
  it must never be committed. An `env` block in `claude_desktop_config.json`,
  or a key passed to `claude mcp add` with `-e`, is saved as plain text in the
  app's own settings, outside this repository's `.gitignore`, and with
  `--scope project`, in a `.mcp.json` file inside the clone that git would
  commit; put only `ADVISOR_MODE` there.
- **A live entry answers whatever reaches the assistant.** While an app's
  entry for the server has `--live`, text in a web page, a file, an email or
  another tool's reply can ask the assistant to start a brief and send
  `confirm="proceed"` itself. In an app without forms the app's approval
  prompt is the only check, and any mode or rule that approves tool calls
  without asking you, such as Claude Code's bypass permissions mode, its auto
  mode or an always allow rule, removes it for these tools too. Add a live
  entry only for a live run you are watching, in a session that asks before
  each tool call, then switch back to replay and restart the app.

A live brief needs all of these, on your own accounts: your own
`ANTHROPIC_API_KEY`, `TAVILY_API_KEY` and `TYPESAFE_API_KEY` in the clone's
`.env`; a spend ledger, created once with a live `advisor` command and
`--new-ledger` (see "Run it"); and the server started with `--live` in place of
the cassette, with `ADVISOR_MODE=cheap` in its environment. Without the keys, a
live server refuses a brief before anything is sent and names each missing
key; given `--live` without `ADVISOR_MODE`, it refuses to start. The server
never creates a ledger. [docs/mcp.md](docs/mcp.md) gives the live settings for
both apps.

Every rule stays in the advisor's code: the tools run the same graph as the
command line, under the same ledger and the same caps, $0.15 per brief and $5
for the whole build, kept in each copy's own ledger. A paid or outbound call
first returns the command's own preflight, the planned calls and their cost,
and goes ahead only on exactly `proceed`, typed into a form in an app that
shows MCP forms (elicitation, in which the server asks the app to show the
person a form), or sent back as `confirm="proceed"` with the preflight's
`confirmation_id`, which works once, for the same tool, arguments and preflight
lines, within ten minutes. In an app without forms, both the owner's
confirmation of the unit and that `proceed` pass through the assistant: the
server cannot tell who supplied them, and the assistant could send either
without asking, even a model number the plate did not show. So the app's own
approval prompt is the owner's only check: keep it on, and never set these
tools to always allow. How each app shows that prompt has not been verified.

What has been shown, on 16 October 2026 (the MCP row in the results above):
Claude Code called the tools in a free replay session, and when its
non-interactive session canceled the server's form, it sent the unit details
itself, taken from the owner's request; one live brief ran through the
official SDK's client, with a test program playing the owner; and one live
brief ran in Claude Desktop 2.42304.0, where I read the preflight and typed
"proceed" myself. The server sent Desktop no form, so my "proceed" reached the
server through the assistant. That brief ended with no reliable answer, so no
live brief through an assistant has yet carried steps to try first or safety
flags, and with no plate photo, the pause to confirm the unit has not been put
to an assistant since decision 105. Desktop reported the refusal correctly,
then filled the gap with general advice it labeled as its own. The evidence is
kept out of the public repo; [docs/mcp.md](docs/mcp.md) says where, and gives
the full status and the settings for both apps, in replay or live.

## Repository

```
index.html            the teardown essay, v1 and v2
tool.html             the recorded v2 demo
briefs/               four real generated v1 briefs, self contained
briefs/v2/            the v2 briefs the demo shows, one per live run;
                      t-044cd20f9678498e.html cites synthetic property records
demo-assets/          the plate photographs the demos show
src/tokens.css        shared design tokens, dark mode included
src/teardown.css      essay styles
src/demo.css          demo styles
src/demo.js           replay logic, no network
src/fixtures.js       recorded output from the v2 live runs
agent/                v2: the LangGraph agent, its tests and its plan (agent/PLAN.md)
agent/mcp_server.py   v2: the advisor as a local MCP server for AI assistants
agent/replay/build_demo.py   rebuilds the v2 demo from the run records
agent/tests/fixtures/v1_fixtures.js   v1's recorded output, moved from src/fixtures.js
seed/                 v2: the synthetic property registry
pyproject.toml        v2: dependencies; uv.lock pins exact versions
docs/mcp.md           v2: using the advisor from Claude Desktop or Claude Code
DECISION-LOG.md       what was decided and why
```

v1's application source is still private. v2's source is in `agent/`.

## Status

This is a validation instrument, built to answer one question: does a cited
brief change what a technician brings to the job. No technician has used it
yet, and answering that is the next step rather than a footnote.

## License

MIT, see `LICENSE`. The retrieved documentation quoted inside the generated
briefs belongs to its publishers and is cited to them.
