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
| `tool.html` | The **recorded v2 demo**. Eight cases replayed from live runs of v2 on 18 September 2026. It replaces the v1 demo. |

Live: **[the teardown](https://roanukz.github.io/ai-property-maintenance-advisor/)** and **[the demo](https://roanukz.github.io/ai-property-maintenance-advisor/tool.html)**. Both pages
share `src/tokens.css`, so the essay and the thing it describes read as one
product.

## The demo makes no API call

Every result on `tool.html` is verbatim output from a live run of v2 on 18
September 2026 (build `839b1854a0aaf25e`), captured into `src/fixtures.js` and
replayed. Each case names its run ID. Three reasons:

1. **It costs nothing to visit and nothing to host.** The working build calls
   two paid APIs, Claude and Tavily, on every question it has not seen before.
   A public demo wired to that is a bill that scales with strangers.
2. **The case that matters is always reachable.** A visitor on a live tool would
   have to invent a fake model number to see the refusal behavior. Here it is a
   button.
3. **It is deterministic.** A portfolio demo that depends on a third party
   staying up is a demo that eventually embarrasses you.

The briefs under `briefs/v2/` are the real generated artifacts of those runs,
unedited, and the four under `briefs/` are v1's, kept as published. One v2
brief, `briefs/v2/t-550f902f22f846d5.html`, was written against synthetic
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
- **Spend capped in code.** Every paid call is reserved in a ledger before it is
  made: $0.15 per brief and $5 for the whole build, and every paid command shows
  its planned calls and cost and waits for a typed "proceed".

### Results

Each criterion was set before the build. "Replay" means the whole graph ran on
recorded model replies at no cost: it proves the rules, routing, ledger and
renderer, and says nothing about the model. "Live" means real calls to Claude
Haiku 4.5 and Tavily on 18 September 2026, all on one build
(`839b1854a0aaf25e`).

| Criterion | Result | How it was measured |
| --- | --- | --- |
| The offline suite passes with no key and no network | Pass | 2,245 tests; all 2,245 pass with the network blocked on the build machine. A fresh clone runs fewer: the v1 comparison below needs v1's private source, and the tests that replay v2's live recordings need the build machine's untracked `data/` folder. 14 of v1's 17 guardrail behaviors are ported; the other 3 tested parsing a JSON block that structured output removed and are marked obsolete. |
| v1's and v2's validators agree | Pass | 180 payloads through v1's own TypeScript validator and v2's Python port; every accept and reject decision matches. Run against v1's private source on 18 September 2026. |
| The five v1 cases give v1's outcome | Pass, replay | One confirmed candidate for a panel code, several for a vague symptom, no invented codes, NO RELIABLE ANSWER for an invented model, a halt on a blurry plate. |
| A refusal never carries candidates | Pass | Unit and property tests. |
| The invented model is refused 3 of 3 times | Pass, live | 3 of 3 refused by the model itself; none forced by validation. Each cost $0.0353 to $0.0490 and took 17.0 to 35.0 s. |
| A blurry plate halts before any search | Pass, replay; the plate reading also live | The halt is shown in replay. Live, the plate reader read the clear plate exactly and marked the blurry plate's model, serial and date unreadable rather than guessing, with no search. |
| A paused run can be finished by another process | Pass, replay | A run stopped at the confirmation is resumed by a new process without repeating the plate reading. |
| Citations and graph evidence are real | Pass, replay | Every citation resolves to a page retrieved in that run or, on the graph route, to a page an earlier run retrieved and the graph recorded; every graph fact carries a verbatim quote from its page, or is dropped. |
| The graph answers what it already knows | Pass, live | The hot tub's first FLO lookup used 4 searches, cost $0.0587 and took 51.3 s. The repeat was answered from the graph with no search for $0.0082 in 5.2 s. Its answer was thinner: the one documented code and its meaning, with no steps to try first. |
| A repeat question uses 2 searches or fewer | Pass, live | Repeats used 0, 2 and 2 searches, against 4, 4 and 5 on their first lookups. Two of the three passes are held to 2 by the top up limit itself: in both the model asked for more searches, and the limit blocked them. The air conditioner's first lookup ($0.0632, 21.4 s) stored 3 documented causes; a new symptom on it matched none of them, so it was routed to a short top up of 2 searches instead of a full search ($0.0217, 15.5 s), and the brief used none of the stored causes. A vague hot tub question found no stored causes, so it topped up with 2 searches ($0.0234, 12.6 s), but the brief still listed only the stored FLO code, unconfirmed, because the limit blocked 2 more searches and 2 fetches. |
| Service history is cited | Pass, replay and live | Live, with the synthetic property records attached, the hot tub brief cited the prior service record under "this has happened before", stated the unit's age as worked out in code from its install date, and quoted the synthetic warranty terms: 0 searches, $0.0065, 4.8 s. |
| Upgrade options are cited or absent | Pass, replay | A discontinued model lists only successors that a fetched page names. |
| The brief is self contained and escaped | Pass | Nothing loads from outside the page, all model text is escaped, and a legacy mode reproduces the four published v1 briefs byte for byte. |
| Spend stays under the caps | Pass, live | All 11 live runs stayed under $0.15 (the highest was $0.0632). The rerun that produced these results cost about $0.32; the whole build spent $0.59 of $5, including runs on an earlier build that was replaced. |

Latency had no target. Live briefs that researched from scratch took 21.4 and
51.3 s, and refusals 17.0 to 35.0 s. Briefs answered from the graph alone took 4.8
and 5.2 s, and the two short top ups of 2 searches took 12.6 and 15.5 s.

One finding about the model: on the hot tub's first lookup, a step that says to
turn off power at the breaker was not flagged as a safety step. The teardown
puts safety flagging first in what comes next.

Every test is paired with a planted bug in `agent/tests/mutations.toml` that it
must catch, and all 918 are caught. What was decided, what it cost, and what
went wrong on the way is in `DECISION-LOG.md`.

### The recorded v2 demo

`tool.html` replays eight cases, each from one live run on 18 September 2026 and
named by its run ID, and shows the brief file that run wrote, unedited:

1. A hot tub FLO code, first lookup: the plate read, the owner's confirmation,
   4 searches, and 1 fact saved to memory.
2. The same question again, answered from memory with no search.
3. A vague hot tub symptom, topped up with 2 searches.
4. An air conditioner's first lookup, which saved 3 documented causes.
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

A live run needs `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` in a `.env` file (see
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
                      t-550f902f22f846d5.html cites synthetic property records
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
