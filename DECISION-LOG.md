# Decision log

Append only. Newest at the bottom. Corrections are appended, never edited in
place.

This log covers the portfolio record. The working application lives outside this
repository; what is captured here is what was decided, what it cost, and what is
still unresolved.

---

## The architecture reversal, before the build

The earlier design pre parsed equipment manuals into a knowledge pack that the
tool would read from. Its own notes concluded that acquiring primary sources at
scale was the open technical problem, which is another way of saying the product
could not answer a single question until a warehouse existed.

**Decided:** search live at the moment of the question instead. The corpus
becomes a byproduct of questions people actually asked rather than a
prerequisite, and every lookup is logged for that purpose.

**Cost:** each brief costs money and takes a minute or two, and quality depends
on what is publicly documented.

**Rejected:** building the corpus first. It converted a one day build into a data
acquisition project with no end date.

---

## The rule that survived the reversal

"Never generate, only retrieve" became **"never ungrounded."** Generating prose
from fetched, cited sources is allowed. Guessing is not. The distinction is what
made a one day build possible without giving up the property that matters.

---

## NO RELIABLE ANSWER is a success state

A maintenance tool that returns a confident, wrong, plausible looking repair
instruction sends someone to open an equipment bay on live electrical or to
order a part that was never the problem. A search engine that returns a bad
result wastes seconds. The asymmetry decided the product.

**Decided:** the refusal is a first class output with its own layout, showing
what was searched, what was found, and why it was not enough.

**Decided, separately and more importantly:** it is enforced in validation code,
not requested in a prompt. Any candidates or suggested steps present on a
refusal response are cleared before rendering, whatever the model returned.
Seventeen offline tests hold that behavior with no API key and no network.

---

## Tier labels are allowed to be unflattering

Sources are labeled manufacturer, dealer, or forum. A dealer blog stays labeled
dealer even when it is the best source found, and a brief whose sources are all
forum content says so at the top.

**Open, surfaced by running it live.** A manufacturer's manual rehosted on a
third party archive currently gets labeled manufacturer, because the document
genuinely is the manufacturer's. A reader scanning badges sees that label next to
an unfamiliar host. The label should describe either the document's origin or
the publisher's identity, and picking one is unresolved.

---

## The acceptance criterion I missed

The spec set a target of a brief in under sixty seconds. Live runs came in at
fifty seven to one hundred seventeen seconds, missing on three of four.

**Decided:** publish the miss rather than reduce the reasoning effort to make the
number. That setting is also what buys the refusal behavior, and a brief that
arrives in fifty seconds and occasionally invents a repair is a worse product
than one that takes ninety and says it does not know.

**Unresolved, honestly:** either the criterion was wrong or the product is too
slow, and the run does not say which. The way to settle it is to run the suite at
reduced reasoning and check whether the fabricated model case still refuses.

---

## Reviewing my own build adversarially

Before shipping, the build was reviewed against its own specification across
three independent lenses, and every finding was then checked by a separate pass
instructed to refute it. Thirty five claims, thirty one confirmed, four refuted
and dropped.

The four that mattered: an intermittent failure that only hit search heavy
briefs, output budgets that would have truncated a brief mid sentence, a
reassembly bug where citations split a response and rejoining it corrupted the
result, and a script injection hole in the shared brief file, which is the
artifact handed to a third party.

**Worth recording:** the four refuted claims matter as much as the thirty one
confirmed. A review that confirms everything it suspects is not reviewing.

---

## How the demo stays free

The published demo makes no API call. Five cases are replayed from
`src/fixtures.js`, captured verbatim from a live run on 18 August 2026.

**Decided** for three reasons: a public demo wired to a paid API is a bill that
scales with strangers; the refusal case is the one worth showing and a visitor
would otherwise have to invent a fake model number to reach it; and a demo that
depends on a third party staying up eventually embarrasses you.

**To regenerate:** run the application's own acceptance suite, then rebuild
`src/fixtures.js` from the lookup log it writes. The briefs under `briefs/` are
copied from the run unedited.

**Cost, stated plainly:** a visitor sees real output but is not exercising the
system. The page says so at the top rather than implying otherwise.

---

## What is genericized in this repository

The property, its owner, and the commercial detail of the underlying business
are removed. No name, no address, no revenue figures, no third party business
relationships.

The equipment, the symptoms, the retrieved documentation, and the generated
briefs are real and unaltered. Genericizing the evidence would have defeated the
point of publishing it.

---

## v2 begins: Phase 0, 17 September 2026

v2 rebuilds the tool as a LangGraph agent in Python, in `agent/` in this
repository, from a PRD approved on 17 September 2026. Phase 0 read v1's rules,
tests and recorded lookups, checked the LangChain and LangGraph names against
current releases, and wrote `agent/PLAN.md`, which maps every success criterion
to a test that can fail. The plan was reviewed adversarially before it was
shown: 65 findings were raised across four lenses, 1 was refuted, and the rest
were applied.

**Worth recording:** Roanuk said to continue building before reviewing the
plan. Phase 1 therefore starts on the plan's recommendations. They are
recommendations, not decisions: each stays open for review at the Phase 1 gate,
and no live phase starts without a typed "proceed".

**Cost:** $0. No paid API call was made.

---

## Tier labels show the document's origin and name the host

A manufacturer's manual rehosted on a third party archive raised the question of
whether the badge describes the document or the publisher. The v2 sign off on
17 September 2026 settled the display, which closes the open question recorded
after the v1 live run.

**Decided:** each source stores two things. The tier badge shows the document's
origin (manufacturer, dealer, or forum), and the host the document was served
from is shown next to it. The model proposes a tier and code can only lower it.

**Proposed, pending review:** when a manual on another host may keep
manufacturer. Known forum and question and answer hosts are always forum, even
when the file is a maker's PDF. Manufacturer is kept only on the maker's own
domain, or when the fetched text holds a quote naming the maker as author that
code finds verbatim. Anything else is capped at dealer.

**Cost:** a manual on an archive host with no authorship line would show
dealer, which understates it. The host shown next to the badge is what lets a
reader judge it. Offline, v1 recordings have no page text, so a manual v1
labeled manufacturer will show dealer in replay. That is a proposed difference
from v1, not yet approved.

---

## Library names checked against current releases

Before any v2 code, the LangChain and LangGraph names in the PRD were checked
against source at the release tags on 17 September 2026, then confirmed against
the installed packages: langchain 1.4.1, langchain-core 1.6.3, langgraph
1.2.11, langgraph-checkpoint 4.2.0, langgraph-checkpoint-sqlite 3.1.1,
langgraph-prebuilt 1.1.0, langchain-anthropic 1.7.2, anthropic 1.6.0, and
langchain-tavily 0.2.18.

**Renamed or deprecated, and what v2 uses instead:** `create_react_agent` is
deprecated in favor of `langchain.agents.create_agent`. The prebuilt
`AgentState` moved to `langchain.agents.AgentState`. `AgentExecutor` now lives
only in the legacy `langchain_classic` package. `NodeInterrupt` is deprecated in
favor of `interrupt()`, and `Interrupt.interrupt_id` is now `Interrupt.id`.
`MemorySaver` is now `InMemorySaver`, with the old name kept as an alias. The
`StateGraph` keywords `config_schema`, `input`, and `output` became
`context_schema`, `input_schema`, and `output_schema`. `checkpoint_during` became
`durability`. Dictionary access on the result of `invoke` is deprecated in favor
of `GraphOutput.interrupts`. On `ModelRequest`, `system_prompt` became
`system_message`. `ChatAnthropic(output_format=...)` became `output_config`, and
`with_structured_output(method="json_mode")` is remapped to `json_schema`.
Calling `.text()` on a message is deprecated in favor of the `.text` property.
The old summarization keywords are deprecated. None of the old names is used.

**Worth recording:** the docs and the source disagree in three places: the
default recursion limit, whether a node timeout error is a TimeoutError, and
whether event streaming v3 is stable. The source was taken as correct, and v2
relies on none of the three.

---

## The built in fake chat models cannot drive the research agent

None of the fake chat models that ship with langchain-core 1.6.3 implements tool
binding, and `create_agent` binds tools whenever the agent has any. This was
read in the source and then confirmed by running the installed package. One of
them also silently starts over when its script runs out, which would hide a
runaway loop.

**Proposed, pending review:** v2 writes its own replay model. It serves
recorded replies with their tool calls and token usage, fails loudly when the
script ends, and records every request so a test can prove a call was never
made, or that the service history reached the prompt. A twelve line version
drove `create_agent` in an offline trial.

**Worth recording:** the replay model never compiles a grammar, so it cannot
catch a response schema that the API would reject. That check is an offline
count against Anthropic's published limits plus one cheap live call at the
start of Phase 5.

---

## The model facing schema has to fit the structured output limits

Anthropic's structured output allows at most 24 optional parameters and 16
union parameters per request, and every field with a default counts as
optional. A draft schema built with v1's lenient defaults counted at least 27
and 17 in review, which would have failed on the first live synthesis call,
after the plate reading and research had already been paid for.

**Proposed, pending review:** keep two sets of models. The lenient ones keep
v1's defaults for the differential test and are never sent to the API. The one
the model fills is flat, with every field required and empty text standing for
none. A test counts the parameters offline, and Phase 5 confirms the API accepts
the schema with one small call before any research spend.

---

## Tool limits end research; only money stops a run

The first plan draft capped model calls below the tool allowance, so a normal
run that used all its searches and fetches would have ended with a budget stop
that had nothing to do with money. An offline trial also showed that the
library's own "end" behavior for a tool limit leaves only a line of text, with
nothing in the run's state to say why it ended.

**Proposed, pending review:** the search and fetch limits tell the model to
stop using that tool, and research ends once both are spent. The model call
limit is only a guard against a loop, and hitting it keeps the sources already
found. A budget stop comes only from the per run or whole build dollar caps,
the Tavily credit caps, or Tavily reporting its plan limit. A check before each
model call enforces the dollar caps; the same offline trial showed that such a
check can stop the agent and record the stop in its state.

---

## The validation retry becomes a smaller second pass

The PRD sends a failed validation back to research once. At the planned
numbers, a full second research pass never fits under the $0.15 cap, so in
practice there would be no retry at all.

**Proposed, pending review:** in `cheap` mode the retry allows 2 searches and
no fetches, and it runs only when the money left covers its typical cost plus
the synthesis reservation. That holds when the first pass spent about $0.086 or
less. A first pass that fetched pages, or reached its caps, ends as a budget
stop, and that cause is reported separately.

**Rejected alternative, still open to Roanuk:** no retry in `cheap` mode, logged
as a difference from the PRD.

---

## Python 3.12 through uv

This Mac is an Intel machine running macOS 13, and its only Python was 3.9.6.
Every library v2 needs requires 3.10 or later, and the PRD asks for 3.12.

**Decided, at Roanuk's direction during Phase 0:** install uv, a uv managed
CPython 3.12.14, and the pinned packages into a gitignored `.venv` in the repo.
Homebrew has no Intel bottle for uv and began compiling OpenSSL from source, so
that install was stopped and uv 0.12.16 was installed from its PyPI wheel
instead. Nothing was built from source. Phase 1 adds `pyproject.toml` and a
hashed `uv.lock`.

**Worth recording:** the installed versions match the ones the plan pinned from
PyPI, with no drift. The two transitive additions worth naming are
`langgraph-sdk` 0.4.4 and `sqlite-vec` 0.1.9, a vector extension pulled in by
the SQLite checkpointer that v2 does not use.

---

## The refusal test cannot settle the latency question

The v1 log left open whether the sixty second target was wrong or the product
too slow, and named the test that would settle it: the same pipeline at reduced
reasoning. The v2 refusal test changes the model, the search provider, the
search cap, and the pipeline all at once, and three runs is a small sample.

**Proposed:** keep the test, record wall clock time and whether each refusal
came from the model or was forced by validation after a retry, count a budget
stop as a failed run, and publish the result as evidence that informs the
question rather than settles it. A refusal forced by validation would be
reported separately and, as recommended, would not count toward three of three.

---

## Replay proves the rules, not the models

The default mode runs the whole graph on a replay model and recorded tool
results, and costs nothing. It proves that validation, routing, the cost ledger,
and the renderer behave correctly on recorded answers. It says nothing about
whether Haiku or Sonnet would produce those answers. Only the Phase 5 run and
the two live evaluations test model behavior, and on small samples.

**Proposed, pending review:** every replay result in the published results
table says so next to the number. If a live phase is rerun after a logged fix,
only the fixed build's runs are reported there; the earlier runs stay in this
log and in the ledger.

---

## Phase 1: the scaffold, 18 September 2026

Phase 1 built the parts every later phase stands on, in replay only: the
settings file that is the only place a model name or a price may appear, the
brief schemas, the property registry with synthetic seed data, the cost ledger,
the replay model, the network guards, the command line skeleton, and the
builder that turns v1's recorded lookups into replay scripts.

**Measured:** 473 test cases pass with the network blocked. The suite carries
256 planted bugs, each paired with the tests it must break, and all 256 were
caught in a separate full run. A planted bug counts as caught only if every test
named for it fails; a test file that merely stops importing does not count.

**Cost:** $0. No paid API call was made.

**Worth recording:** the build was reviewed adversarially before this entry.
Twenty eight findings, none refuted. The most useful: replay scripts restarted
from the top each time a step ran, so a retry would have been served the first
answer again; the replay search stand ins skipped the code that turns Tavily's
plan limit into a budget stop; and gate mode read "1" as a switch when it is a
phase number, so the Phase 2 gate would have enforced nothing. All three were
fixed and are now held by tests.

---

## Two v1 cuts come back in v2

The v1 teardown cut multi property support and warranty deadline tracking as
the business rather than the test. v2's second and third goals need them back:
a repeat question is cheaper only if the tool remembers the equipment, and a
brief can say this has happened before only if a service history exists.

**Decided:** v2 carries per property appliance records, service history,
purchase and install dates, and a maintenance schedule taken only from cited
manufacturer intervals. Accounts, the cleaner capture flow, and branding stay
cut. The v1 page is left as published, because it describes what v1 tested.

**Cost:** the seed registry is synthetic, so none of this is tested against a
real property until someone runs it on one.

---

## The seed registry and its third real model

The PRD asked for the three real models from the v1 fixtures. The fixtures hold
two: the Sundance Spas Optima 880 and the Trane XR16 4TTR6036. The third model
in them, the Aquarest ZX-9000 Pro, is the invented one the refusal test uses,
and registering it would give that test a record to lean on.

**Proposed, pending Roanuk's choice:** the Rheem Professional Classic Plus with
LeakSense, model PRO+E50 M2 RH92 CL, a 50 gallon electric water heater. Claude
checked on 18 September 2026 that Rheem's own product page lists that model.
The remaining seed rows are two synthetic properties, a discontinued appliance
with an invented maker and model for the upgrade test, and two more invented
appliances. Every row is labeled synthetic.

---

## Replay scripts wait for a privacy review

The replay scripts are built from v1's recorded lookups by copying named fields
only. The builder found nothing private: every recorded run used typed test
identities and empty owner notes. The check still reads INCOMPLETE, because the
names it must look for (the property, its town, and the people) are known only
to Roanuk and the list is empty.

**Decided:** the scripts and the v1 test payloads stay in the untracked staging
folder until Roanuk fills in that list, reruns the check, and approves. Phase 2
tests read them from staging until then.

**Open:** 27 staged strings carry an em or en dash. All are v1 model output that
the published briefs already show unedited.

---

## Phase 2: the graph runs end to end on replay, 18 September 2026

Phase 2 connected everything: the v1 validator ported to Python, the v2 rules on
top of it, the research agent with its limits, every node, and the graph with
the confirmation pause and a saved checkpoint. A question now goes in, pauses
for the owner to confirm the unit, resumes in a different process, and comes
out as a brief or a refusal.

**Measured:** 1,183 test cases pass offline. The differential test fed 180
payloads through v1's own TypeScript validator and v2's Python port, and every
accept or reject decision, reason and cleaned up brief matched. Replaying the
five recorded v1 cases gave the v1 outcome each time: one confirmed candidate
for the FLO code, six unconfirmed candidates for the vague symptom, no invented
codes for the air conditioner, NO RELIABLE ANSWER for the invented model, and a
halt at confirmation for the blurry plate. 451 planted bugs were all caught.

**Cost:** $0. No paid API call was made.

**Worth recording:** review found that a code guessed from the symptom text was
being treated as if the owner had confirmed it, so a short run of capital
letters after "shows" or "error" in an ordinary sentence could narrow the answer
to a code that was never on the panel and end in a refusal. The run now pauses so the owner confirms the
code, and the confirmed code, or its absence, is stated to the research and
synthesis steps. It also found that one fetched address with no "https://" in
front would have made every draft fail, because v1's check covered sources the
brief never cited. Such addresses are now never registered.

**Open, for Roanuk at this gate:** marking v1 tests 11, 12 and 14 obsolete (they
tested parsing a fenced JSON block, which structured output removes); the
differences from v1 listed in the plan, section 10.3; and the prompt wording
changes. The Phase 2 gate check stays red until the three obsolete tests are
approved.

---

## Phase 3: the tool starts to remember, 18 September 2026

Phase 3 added the knowledge graph that v1 promised and never built. A validated
brief now leaves behind edges such as "this model shows this code", each
carrying the exact words from the page that say so, the page's address, and
when it was read. The router checks that graph first, adds the appliance's
service history when there is one, and searches only when the graph has nothing
verified.

**Measured, in replay:** a first question about the FLO code on the seeded hot
tub searched once and saved one verified edge. The same question asked again
took the graph route: no searches and no research calls, at under half the
replay cost. 1,378 test cases pass offline, and 560 planted bugs are all caught.
These are replay numbers; they prove the routing, not what a live repeat saves.
That is measured in Phase 6.

**Cost:** $0. No paid API call was made.

**Decided:** an edge is kept only if its sentence appears word for word in the
fetched page, allowing nothing but whitespace differences, and names what the
edge claims. An edge that fails is dropped and the drop is logged. It is never
kept with a warning. A code offered in a candidate must appear as a whole word
in the page it cites; a quote the model supplies does not count on its own,
because review showed a quote can contain the code while the page never prints
it as a separate code.

**Worth recording:** review found that a brief with no codes reported its code
check as "verified", although nothing had been checked. It now reports "not
applicable", and "not checked" when an earlier rule already rejected the draft.
A result label that claims a check that never ran is the failure this project
exists to prevent.

**Open:** on a fresh copy of the repository, a code answered from the graph
cannot be checked, because the page text stays out of the repository. Phase 6
decides between checking against the saved sentence and requiring the page.

---

## Phase 4: upgrades, maintenance, and the rendered brief, 18 September 2026

Phase 4 finished the replay build. A brief can now list replacement models when
a unit is discontinued, and a maintenance schedule, and the renderer and the
static property page are complete.

**Decided:** a replacement model is shown only when a fetched page names it
word for word; with no such page the list is empty, not marked unverified. A
maintenance interval is shown only when it comes from the maker's own page, and
the due date is computed from the property's own maintenance log, never taken
from the model. A price appears only when the cited page states that exact
amount; review caught "$12" being accepted because the page said "$129", and
the check now requires the whole amount.

**Measured:** the renderer reproduces all four published v1 briefs byte for
byte in its legacy mode, which is the proof that nothing in the format drifted
by accident. The v2 format's differences from v1 are listed one by one for
approval. 1,601 test cases pass offline, and 14 of v1's 17 guardrail behaviors
are held by passing tests; the other 3 await approval as obsolete.

**Cost:** $0. No paid API call was made.

**Worth recording:** the Phase 1 to 4 build ran entirely on replay, with a fake
model reading recorded answers. That proves the rules, the routing, the ledger
and the renderer. It proves nothing about how Haiku behaves, which is what the
live phases are for.

---

## The live path, built without spending, 18 September 2026

Before any paid call, the commands that make paid calls were built and tested
against fake Anthropic and Tavily clients. They run the same graph, rules and
ledger as the replay build, so the live runs test the models and nothing else
changes.

**Decided:** every live command first prints what it plans to spend and waits
for the word "proceed"; there is no flag that skips the question. Keys are read
only by live commands and are scrubbed from everything the tool prints or
writes, including error messages from a failed call. Sonnet 5 synthesis stays
off until it gets its own approval.

**Worth recording:** the review before this entry found that an empty ledger
file would have been treated as a fresh one with a new $5 budget, and that a key
echoed in an API error could have reached the run log. Both are fixed and held
by tests. A test that failed about one run in three turned out to be the privacy
check reading five digits inside a random run ID as a ZIP code; it would have
blocked real recordings at random, and the check now ignores digits inside
longer words.

**Cost:** $0. No request reached Anthropic or Tavily.

---

## Phase 5: the first live run, 18 September 2026

Roanuk typed "proceed" with the Tavily free plan (pay as you go off) and $5 on
the Anthropic key. Two paid steps ran: a one call check that the API accepts the
brief's schema, then the FLO case on the clear plate photo, paused for the
owner to confirm the unit and resumed in a separate process.

**Measured:** the schema was accepted ($0.0036). The run read the plate
correctly, found one confirmed candidate for the FLO code with a cited dealer
source, and cost $0.0403 against an estimate of $0.083 and a cap of $0.15. It
took about 24 seconds. v1's version of this case took about 71 seconds (inferred
from the gaps between its log timestamps) on a different model and pipeline, so
the two numbers are not a like for like comparison. All 57 snippet pieces from
the 19 search results whose page text was saved appeared word for word in that
text; the other 6 results had no saved page text to check against.

**Cost:** $0.0440 of live spend so far, of the $5 build cap. 5 of 1,000 free
Tavily credits.

**Worth recording:** the run succeeded but saved nothing to the knowledge
graph. The model quoted the meaning of FLO word for word from a table and left
out the code in the cell beside it, and the evidence rule drops a quote that
does not name what it claims. Code now widens such a quote to begin at the code
when the page prints it just before the quote on the same line; everything
stored is still the page's own text. The run's edge was then written by
rerunning that step on the stored run, with no new paid call. Without this, the
repeat question in Phase 6 would have measured the defect instead of the tool.
Two smaller defects in logging and recording were fixed in code for later
runs; the Phase 5 lookup log keeps its misreported credit counts, and the
ledger's 5 credits is the correct figure.

**Model finding, not a code failure:** v1's brief for this case opened with a
safety step, confirming water is moving before testing the heater. The live
Haiku brief has no safety flagged step.

---

## Phase 6: the live evaluation, 18 September 2026

Roanuk typed "proceed" again. Three batches ran on Haiku 4.5 with Tavily, each
after printing its planned calls and cost. Phase 6 spent $0.22, about half the
estimate; the whole build has spent $0.27 of its $5 cap.

**Measured:** the invented model was refused three times out of three, each
time by the model itself and with nothing forced by validation. The plate
reader read the clear plate exactly and marked the blurry one unreadable rather
than guessing. Every live run stayed under its $0.15 cap; the most expensive
cost $0.045. Full research runs took 20 to 34 seconds, and answers from the
knowledge graph took 5 to 9 seconds. There was no latency target, so these are
reported, not scored.

**Missed:** a repeat question was supposed to need two searches or fewer. Two
repeats about the hot tub needed none, answered from the knowledge graph. The
repeat about the air conditioner needed five, the same as its first lookup,
because that first lookup documented causes but no error codes, and the graph
the PRD specifies stores codes, parts and successor models but not causes. The
miss is published as a miss.

**Worth recording:** the cheap answer is not always the complete one. The vague
"not heating" repeat was answered from the single FLO entry in the graph and
listed one possible cause. No live first lookup of that question was run; v1's
recorded answer to the same question on the same unit listed six. The routing
rule allowed it; whether it should is a decision for the next round. The FLO
repeat's graph answer was also thinner than its first lookup: no steps to try
first, and a documented action cut off mid word. The page excerpts the model is
shown can end mid word, the model copies them exactly as told, and that cut
text reached both the brief and the stored graph fact. The FLO repeat's graph answer rests on the edge written from the Phase 5
run after that run's evidence fix, recorded in the Phase 5 entry.

**Cost:** $0.2226 in this phase, 29 Tavily credits.

---

## The application source is now in this repository

The opening of this log says the working application lives outside this
repository. That remains true of v1, whose source stays private. It is no longer
true of v2: its source, tests and plan are in `agent/`, and the README says so.

---

## v2, summarized, 18 September 2026

v2 set out to fix three limits of v1: it forgot everything, it knew nothing
about the property, and it had no plan for aging equipment. It was built as a
LangGraph agent in seven gated phases: the first four at no cost on recorded
replies, the fifth and sixth live on Claude Haiku 4.5 and Tavily, and the
seventh this write up.

**What held:** every v1 guarantee is still enforced in code, and v1's own
validator agrees with v2's port on 180 test payloads. The invented model was
refused three times out of three by the model itself. Every live run stayed
under its $0.15 cap, and the whole build spent $0.27 of its $5 budget. A repeat
of a coded question was answered from the knowledge graph with no search, using
the fact saved from the first live run after a logged fix; that answer was
thinner than the first lookup's.

**What missed:** a repeat question about an air conditioner needed five
searches, because its first lookup found causes but no codes and the graph as
specified has nowhere to keep causes. The criterion asked for two or fewer, so
it is a miss.

**What the data raised for the next round:** the knowledge graph needs a place
for documented causes, not only codes; the graph route should not answer a
vague symptom from a single stored code; the page excerpts shown to the model
should not end mid word; and the live Haiku brief for the FLO code, unlike
v1's, carried no safety step. None of these was changed after the
live runs, because a change would need new runs to measure.

**Still waiting on Roanuk:** approval of three v1 tests as obsolete, the privacy
review of the replay recordings, the third real model in the seed registry, and
the design decisions listed in the plan.
