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
| `tool.html` | The **recorded v2 demo**. Eight cases replayed from live runs of v2 on 30 September 2026, on the build with the safety step check. It replaces the v1 demo. |

Live: **[the teardown](https://roanukz.github.io/ai-property-maintenance-advisor/)** and **[the demo](https://roanukz.github.io/ai-property-maintenance-advisor/tool.html)**. Both pages
share `src/tokens.css`, so the essay and the thing it describes read as one
product.

## The demo makes no API call

Every result on `tool.html` is verbatim output from a live run of v2 on 30
September 2026 (build `da738a1559ced359`), captured into `src/fixtures.js` and
replayed. Each case names its run ID. The demo was re-recorded on that build,
the one with the safety step check described below, so its briefs carry the
flags after Jev's check, and each case shows which flag the writer set and which
Jev raised (Jev raised one, in case 3). Three reasons:

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
unedited, and the four under `briefs/` are v1's, kept as published. One v2
brief, `briefs/v2/t-bbad96be657444a5.html`, was written against synthetic
property records: its "this has happened before" service record, its install
date and age, and its warranty terms are all synthetic test data, although only
the warranty text says so inside the file, which is kept byte for byte. Each one is
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
remembers. Its source is in `agent/`.

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
- **The same guarantees, still in code.** Citations must point to pages that
  were actually retrieved, tier labels can be lowered but never raised, a panel
  code narrows the answer, a refusal clears every candidate, and a price appears
  only when a cited page states it.
- **A safety check outside the writer.** Jev, a judgment model from TypeSafe,
  reads each step to try first and answers how likely it is to be a safety step
  (electrical, heat or gas). It can raise a safety flag the writing model left
  off, never clear one, and never add a step. If the check fails, the brief says
  so in one line.
- **Spend capped in code.** Every paid call is reserved in a ledger before it is
  made: $0.15 per brief and $5 for the whole build, and every paid command shows
  its planned calls and cost and waits for a typed "proceed".

### Results

Each criterion was set before the build. "Replay" means the whole graph ran on
recorded model replies at no cost: it proves the rules, routing, ledger and
renderer, and says nothing about the model. "Live" means real calls to Claude
Haiku 4.5, Tavily and TypeSafe's Jev. The live evaluation and the demo were
re-recorded on 30 September 2026 (US Eastern time; in UTC, the clock the run
records and the demo's timestamps use, the runs fall early on 1 October) on the
build with the safety check (`da738a1559ced359`), and every live row comes from
that build except the two safety step rows: SC12a's Jev calls trace to a locked
settings file and model `jev-1.13.0`, not to a build, and SC12b's ten live
lookups ran about ten hours earlier on build `9a64835e86a5a6c8`, which has the
same agent graph code under an older fingerprint. Recall is the share of
real safety steps a check flagged; precision is the share of its flags that
were real safety steps.

| Criterion | Result | How it was measured |
| --- | --- | --- |
| The offline suite passes with no key and no network | Pass | 2,649 tests; all 2,649 pass with the network blocked on the build machine (30 September 2026, on the code of build `da738a1559ced359`). A fresh clone runs fewer: the v1 comparison below needs v1's private source, and the tests that replay v2's live recordings need the build machine's untracked `data/` folder. 14 of v1's 17 guardrail behaviors are ported; the other 3 tested parsing a JSON block that structured output removed and are marked obsolete. |
| v1's and v2's validators agree | Pass | 180 payloads through v1's own TypeScript validator and v2's Python port; every accept and reject decision matches. Last run against v1's private source on 30 September 2026, on the code of build `da738a1559ced359`. |
| The five v1 cases give v1's outcome | Pass, replay | One confirmed candidate for a panel code, several for a vague symptom, no invented codes, NO RELIABLE ANSWER for an invented model, a halt on a blurry plate. |
| A refusal never carries candidates | Pass | Unit and property tests. |
| The invented model is refused 3 of 3 times | Pass, live | 3 of 3 refused by the model itself; none forced by validation. Each cost $0.0270 to $0.0451 and took 15.0 to 19.1 s. |
| A blurry plate halts before any search | Pass, replay; the plate reading also live | The halt is shown in replay. Live, the plate reader read the clear plate exactly and marked the blurry plate's model, serial and date unreadable rather than guessing, with no search. |
| A paused run can be finished by another process | Pass, replay | A run stopped at the confirmation is resumed by a new process without repeating the plate reading. |
| Citations and graph evidence are real | Pass, replay | Every citation resolves to a page retrieved in that run or, on the graph route, to a page an earlier run retrieved and the graph recorded; every graph fact carries a verbatim quote from its page, or is dropped. |
| The graph answers what it already knows | Pass, live | The hot tub's first FLO lookup used 3 searches, cost $0.0435 and took 28.7 s. The repeat was answered from the graph with no search for $0.0082 in 4.3 s, about a fifth of the first lookup's cost and a seventh of its time. Its answer was thinner: the one documented code and its meaning, with no steps to try first. |
| A repeat question uses 2 searches or fewer | Pass, live | Repeats used 0, 2 and 2 searches, against 3, 3 and 5 on their first lookups (the two hot tub repeats share one first lookup). Two of the three passes are held to 2 by the top up limit itself: in both the model asked for more searches, and the limit blocked them. The air conditioner's first lookup ($0.0431, 25.8 s) stored 6 documented causes; for a new symptom on it the symptom check could not tell whether any of them fit, so it was routed to a short top up of 2 searches instead of a full search ($0.0207, 15.6 s), and the brief used none of the stored causes. A vague hot tub question found no stored causes, so it topped up with 2 searches ($0.0226, 15.7 s), but the brief still listed only the stored FLO code, unconfirmed, and the limit blocked 1 more search and 1 fetch. |
| Service history is cited | Pass, replay and live | Live, with the synthetic property records attached, the hot tub brief cited the prior synthetic service record under "this has happened before", stated the unit's age as worked out in code from its install date, and quoted the synthetic warranty terms: 0 searches, $0.0067, 5.5 s. |
| Upgrade options are cited or absent | Pass, replay | A discontinued model lists only successors that a fetched page names. |
| The brief is self contained and escaped | Pass | Nothing loads from outside the page, all model text is escaped, and a legacy mode reproduces the four published v1 briefs byte for byte. |
| Spend stays under the caps | Pass, live | All 11 live runs of the re-recorded evaluation stayed under $0.15 (the highest was $0.0451, a refusal), and so did the ten SC12b lookups (the highest was $0.0589). The 11 re-recorded runs cost $0.2536 and 31 Tavily credits. The safety step work of 29 and 30 September cost $0.4878. The whole build, counting every live run on every build, stands at $1.3321 of $5 and 141 of its 300 Tavily credits. |
| Safety steps are flagged, on a fixed labeled set (SC12a) | Measured, live: the choice rule picked Jev, which flagged 30 of 31 held out safety steps | 345 steps, labeled before any scoring: sentences from maker and dealer documentation and steps the tool wrote in earlier briefs. Three model readers, not people, labeled each one blind; they agreed on 338 of 345. The set was split into a tune half (170) and a held out half (175, with 31 safety steps); each check's settings were chosen on the tune half and locked in a file before the held out half was scored, once. Held out, against the 31 safety steps: the word rule as published (breaker, power, heat) flagged 20 (recall 0.645, precision 0.714); the word rule tuned on the tune half (it drops the bare word heat and adds wire, panel, door, disconnect, electrical, jumper and box), 22 (0.710, 0.647); Jev flagging when its answer, a probability from 0 to 1, is 0.51 or higher, 30 (0.968, 0.732); the tuned word rule plus Jev, 30 (0.968, 0.600). The held out half has no steps the tool wrote, so the writing model is measured by SC12b below. The choice rule, written before any result, drops a candidate (tuned word rule, Jev, or the tuned word rule plus Jev) below precision 0.60, then takes the highest recall, then the higher precision: all three stayed, Jev and the tuned word rule plus Jev tied on recall, and Jev had the higher precision, so Jev ships at 0.51. The tuned word list did not hold up: precision 0.955 on its tune half, 0.647 held out. 855 Jev calls, $0.0216. |
| Every safety step in new live briefs is flagged (SC12b) | Pass, live: 14 of 14 | Ten first lookups in cheap mode on 30 September 2026 (build `9a64835e86a5a6c8`), five with the hot tub FLO question (the clear plate photo and "panel shows FLO") and five with the air conditioner's fan question ("outdoor unit runs but the fan does not spin"). Their briefs held 31 steps; the same three model readers called 14 of them safety steps, and all 14 were flagged, with 2 false flags (16 flagged). The writing model, under the revised instruction that spells out which steps count, flagged all 14 by itself with no false flag. Jev flagged the same 14 and added the two false flags, both fan blade steps, so it caught nothing the writer missed. The tuned word rule, which is not shipped, also flagged all 14, with 2 false flags on other steps (14 of 16). There is no clean comparator for the writer before the revised instruction, so no before and after comparison is reported. The sample is small: 14 safety steps from two inputs, mostly breaker steps, so the lower end of recall's 95% interval is 0.78; SC12a's held out half is the stronger evidence for Jev itself. $0.4662 and 47 Tavily credits. |

Latency had no target. Live briefs that researched from scratch took 25.8 and
28.7 s, and refusals 15.0 to 19.1 s. Briefs answered from the graph alone took 4.3
and 5.5 s, and the two short top ups of 2 searches took 15.6 and 15.7 s.

In the re-recorded demo the writing model itself flagged all 5 steps that
switch power at a breaker, and Jev scored each 0.95 or higher. Jev raised one
flag the writer left off, on a step to run the spa for a few minutes with the
filter out to see whether heating resumes (0.87): a step the three readers
labeled not a safety step when it carried a different detail; this version,
whose detail speaks of heating resuming, was not labeled. With Jev alone
shipped, a failed check falls back to the writer's own flags and a notice line,
not to the word rule; whether it should is an open question.

Every test is paired with a planted bug in `agent/tests/mutations.toml` that it
must catch, and all 1,136 are caught (30 September 2026, on the code of build
`da738a1559ced359`). What was decided, what it cost, and what
went wrong on the way is in `DECISION-LOG.md`.

### The recorded v2 demo

`tool.html` replays eight cases, each from one live run on 30 September 2026
(build `da738a1559ced359`) and named by its run ID, and shows the brief file
that run wrote, unedited:

1. A hot tub FLO code, first lookup: the plate read, the owner's confirmation,
   3 searches, and 1 fact saved to memory.
2. The same question again, answered from memory with no search.
3. A vague hot tub symptom, topped up with 2 searches; Jev adds the one flag
   the writer did not set.
4. An air conditioner's first lookup, which saved 6 documented causes.
5. A new question about that air conditioner, topped up with 2 searches.
6. An invented model, refused (3 of 3 live runs).
7. A blurry plate, stopped at the confirmation before any search.
8. A **live run against synthetic property records**: the prior service
   record, the unit's age from its install date, and the warranty terms, with
   no search.

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

A live run needs `ANTHROPIC_API_KEY`, `TAVILY_API_KEY` and, while the safety
check is on, `TYPESAFE_API_KEY` in a `.env` file (see
`.env.example`; `.env` is never committed), `ADVISOR_MODE=cheap` set in the
shell, and `--live`. The first live run on a new checkout also needs
`--new-ledger` to create the spend ledger. Every live command prints what it
plans to spend and waits for "proceed".

## Repository

```
index.html            the teardown essay, v1 and v2
tool.html             the recorded v2 demo
briefs/               four real generated v1 briefs, self contained
briefs/v2/            the v2 briefs the demo shows, one per live run;
                      t-bbad96be657444a5.html cites synthetic property records
demo-assets/          the plate photographs the demos show
src/tokens.css        shared design tokens, dark mode included
src/teardown.css      essay styles
src/demo.css          demo styles
src/demo.js           replay logic, no network
src/fixtures.js       recorded output from the v2 live runs
agent/                v2: the LangGraph agent, its tests and its plan (agent/PLAN.md)
agent/replay/build_demo.py   rebuilds the v2 demo from the run records
agent/tests/fixtures/v1_fixtures.js   v1's recorded output, moved from src/fixtures.js
seed/                 v2: the synthetic property registry
pyproject.toml        v2: dependencies; uv.lock pins exact versions
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
