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
| `index.html` | The **product teardown**, the essay documenting the job, the evidence, five decisions, the acceptance run including the criterion it missed, and what I cut. This is the front door. |
| `tool.html` | The **recorded demo**. Five cases replayed from a real run. |

Live: **[the teardown](https://roanukz.github.io/ai-property-maintenance-advisor/)** and **[the demo](https://roanukz.github.io/ai-property-maintenance-advisor/tool.html)**. Both pages
share `src/tokens.css`, so the essay and the thing it describes read as one
product.

## The demo makes no API call

Every result on `tool.html` is verbatim output from a live run on 18 August
2026, captured into `src/fixtures.js` and replayed. Three reasons:

1. **It costs nothing to visit and nothing to host.** The working build calls a
   paid API and runs up to eight web searches per brief. A public demo wired to
   that is a bill that scales with strangers.
2. **The case that matters is always reachable.** A visitor on a live tool would
   have to invent a fake model number to see the refusal behavior. Here it is a
   button.
3. **It is deterministic.** A portfolio demo that depends on a third party
   staying up is a demo that eventually embarrasses you.

The briefs under `briefs/` are the real generated artifacts, unedited, each one
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
  leaves behind the codes, parts and successor models it verified, such as
  "this model shows this code", each stored with the exact words from the page
  that say so. A brief that verifies none of these leaves nothing behind. A
  repeat question checks the graph before it searches.
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
Haiku 4.5 and Tavily on 18 September 2026.

| Criterion | Result | How it was measured |
| --- | --- | --- |
| The offline suite passes with no key and no network | Pass | 1,872 tests pass with the network blocked on the build machine. A fresh clone runs fewer: the v1 comparison below needs v1's private source, and the five v1 replay cases wait on a privacy review of their recordings. 14 of v1's 17 guardrail behaviors are ported; the other 3 tested parsing a JSON block that structured output removed and await approval as obsolete. |
| v1's and v2's validators agree | Pass | 180 payloads through v1's own TypeScript validator and v2's Python port; every accept and reject decision matches. Run against v1's private source on 18 September 2026. |
| The five v1 cases give v1's outcome | Pass, replay | One confirmed candidate for a panel code, several for a vague symptom, no invented codes, NO RELIABLE ANSWER for an invented model, a halt on a blurry plate. |
| A refusal never carries candidates | Pass | Unit and property tests. |
| The invented model is refused 3 of 3 times | Pass, live | 3 of 3 refused by the model itself; none forced by validation. |
| A blurry plate halts before any search | Pass, replay; the plate reading also live | The halt is shown in replay. Live, the plate reader alone ran on the blurry photo and marked the model, serial and date unreadable rather than guessing. |
| A paused run can be finished by another process | Pass, replay and live | The first live run was resumed in a new process. |
| Citations and graph evidence are real | Pass, replay | Every citation resolves to a page retrieved in that run or, on the graph route, to a page an earlier run retrieved and the graph recorded; every graph fact carries a verbatim quote from its page, or is dropped. |
| The graph answers what it already knows | Pass, replay and live | A live repeat of the FLO question used no search. Its answer was thinner than the first lookup's: no steps to try first, and a documented action cut off mid word. Its graph fact came from the first live run's stored data, saved by rerunning that step after a logged fix, with no new paid call. |
| A repeat question uses 2 searches or fewer | **Miss, live** | Repeats on already researched units used 0, 0 and 5 searches. The vague hot tub repeat's zero searches came with a narrower answer: one cause, from the one stored code. The five came from an air conditioner whose first lookup found causes but no error codes, and the graph as specified stores codes, parts and successor models, not causes. |
| Service history is cited | Pass, replay | A matching prior record fills "this has happened before", cited to that record. |
| Upgrade options are cited or absent | Pass, replay | A discontinued model lists only successors that a fetched page names. |
| The brief is self contained and escaped | Pass | Nothing loads from outside the page, all model text is escaped, and a legacy mode reproduces the four published v1 briefs byte for byte. |
| Spend stays under the caps | Pass, live | Every live run stayed under $0.15 (the highest was $0.045); the whole build spent $0.27 of $5. |

Latency had no target. Live briefs that searched took 20 to 34 seconds, and
briefs answered from the graph took 5 to 9 seconds.

Every test is paired with a planted bug that it must catch, and the 804 planted
bugs in `agent/tests/mutations.toml` are all caught. What was decided, what it
cost, and what went wrong on the way is in `DECISION-LOG.md`.

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
index.html            the teardown essay (v1)
tool.html             the recorded demo (v1)
briefs/               four real generated v1 briefs, self contained
demo-assets/          the plate photographs used in the recorded run
src/tokens.css        shared design tokens, dark mode included
src/teardown.css      essay styles
src/demo.css          demo styles
src/demo.js           replay logic, no network
src/fixtures.js       recorded output from the v1 live run
agent/                v2: the LangGraph agent, its tests and its plan (agent/PLAN.md)
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
