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

---

## Open items settled under Roanuk's delegation, 18 September 2026

Roanuk asked Claude to settle the open items itself. Each is recorded here as
Claude's decision under that delegation, not as Roanuk's own review.

**Decided:**
- v1 tests 11, 12 and 14 are obsolete. They checked that v1 recovered its JSON
  from a fenced block, recovered it with no fence, and reassembled text split by
  citations. v2 receives the brief as structured output from the API, so there
  is no free text to recover. Gate mode now passes with nothing failing.
- The Rheem PRO+E50 M2 RH92 CL is the seed registry's third real model. The
  maker's product page was checked again the same day.
- The privacy review of the replay scripts built from v1's lookups. The names to
  look for were taken from the business and place names visible in the private
  v1 folder's file names, without opening those files, because the PRD forbids
  reading them. The check read 391 copied strings and found no match, and Claude
  read every capitalized word in them by hand. The five scripts and the v1 test
  payloads moved from staging into the tracked test folders, so a fresh clone
  runs those tests too. Their v1 model text keeps its dashes, stored as escapes,
  because the published briefs show the same text unedited.
- The file of API keys Roanuk supplied was moved to the Trash, not deleted. The
  keys stay in the untracked `.env`.
- The design questions the live evaluation raised are answered in the next
  entry.

**Cost:** a list built from file names catches only names that appear in them.
The copied strings were typed test identities, equipment, symptoms, public URLs
and brief text already published, so the remaining risk is low.

---

## Five design changes after the live evaluation, 18 September 2026

Each of the first four changes is held by tests, and each test by a planted
bug it must catch; the fifth changes nothing.

1. **Routing.** A question with no confirmed code is never answered from stored
   codes alone. The graph answers it with no search only when it holds at least
   three verified documented causes for the model or its family and the
   classifier matches the symptom to one of them. Otherwise the graph's facts
   are topped up by a research pass of at most two searches. A vague symptom has
   many causes, and one stored code is not an answer to it.
2. **Documented causes.** The graph gains a fact linking a model to a documented
   cause and its documented action. It is written only from a validated brief,
   and only with a verbatim quote from the cited page that shares at least two
   words with the cause, not counting common words. Lookups that find causes but
   no codes, as air conditioner lookups usually do, otherwise leave nothing for a
   repeat to reuse. This changes the graph schema the PRD specifies.
3. **Whole words.** Page excerpts shown to the model start and end on whole
   words, stored quotes extend to the ends of their words, and the sources block
   given to the writer is cut at a word. Cut text had reached a brief and a
   stored fact.
4. **Live recordings replay offline.** In replay, grounding reads a cited page's
   saved text by its hash. Where no text was saved, a live recording reports
   the check as unverifiable instead of failing it.
5. **The safety step: no prompt change.** A brief can quote only its cited
   pages, so it carries a safety step when a cited page has one. Code puts
   flagged steps first but cannot decide what counts as a safety step without
   trusting the model.

**Known limits:** a quote that names only a component can pass the two word
floor for a cause. A stored cause becomes a source for the brief only when no
code is confirmed.

**Consequence:** these change the routing and what the graph stores, so the
Phase 5 and Phase 6 results measure a build that no longer exists. The next
entry supersedes them.

---

## The live evaluation, rerun on the fixed build, 18 September 2026

Roanuk approved the rerun. It ran the schema check, a fresh first lookup of the
FLO case, the invented model three times, the repeat questions, the two plates,
and one added run with the seeded hot tub's records attached, each batch after
its own preflight.

**Superseded:** the Phase 5 and Phase 6 results above come from build
42a27185d1e87be5, before the design changes. Its run records, graph and registry
moved to `data/superseded/`, which is not tracked; nothing was deleted, and the
ledger still counts their $0.2666 against the cap. The rerun started from an
empty graph. Only the rerun is reported in the README and on the teardown.

**Measured, build 839b1854a0aaf25e, Claude Haiku 4.5 with Tavily:**
- The invented model was refused three times out of three, each time by the
  model. Each run used 5 searches and cost $0.0353 to $0.0490.
- Repeat questions (SC7b) pass on the letter of the bar, 2 searches or fewer,
  but only one of the three shows memory replacing search. The FLO repeat used 0
  searches, $0.0082 and 5.2 seconds, against 4 searches, $0.0587 and 51.3
  seconds for its first lookup; its brief was thinner, with none of the first
  brief's 4 steps to try first. The vague "not heating" question on the same hot
  tub took the graph plus a top up: 2 searches, $0.0234. The model asked for 2
  more searches and 2 more fetches, which the top up limit blocked, and the brief
  still listed only the stored FLO code, unconfirmed. The Trane first lookup
  ("AC not cooling upstairs") used 5 searches and stored 3 documented causes. A
  new symptom on the same unit ("outdoor unit runs but the fan does not spin")
  then took a top up of 2 searches for $0.0217; the classifier matched none of
  the 3 stored causes, the limit blocked 1 more search and 2 fetches, and all 3
  of the brief's causes came from the new searches. The top up limit is what
  held those two repeats to 2, so they test the limit, not the memory.
- The clear plate was read exactly. The blurry plate's model, serial and date
  were marked unreadable instead of guessed.
- With the seeded hot tub's synthetic records attached, the FLO question used 0
  searches, cost $0.0065 and took 4.8 seconds. The brief cited the earlier
  service record and gave the unit's age from its install date.
- Every run stayed under the $0.15 cap. The most expensive cost $0.0632.

**Found:** the FLO first lookup's brief lists "Turn off power at the breaker"
as an ordinary step, with no safety flag. Under the fifth design change this is
the model's call. Whether code should flag steps that cut power is the next
question.

**Cost:** the rerun spent $0.3241 and 29 Tavily credits. The whole build has
spent $0.5907 of its $5 cap and 63 Tavily credits of its 150 credit cap.

---

## The recorded v2 demo replaces v1's, 18 September 2026

Roanuk asked for a recorded v2 demo in place of the v1 demo, since the tool has
been upgraded. The plan had promised to leave the published pages byte for
byte; his request supersedes that promise for the demo and the teardown only.

**Decided:** `tool.html` now replays eight cases from the fixed build's live
runs, each named by its run ID and showing what that run returned, unedited:
the brief it wrote, or for the blurry plate the pause at confirmation. The
cases are the hot tub's first question, the same question from memory, a vague symptom,
the air conditioner's first question and a new symptom, the invented model, the
blurry plate, and the hot tub with its synthetic property records. The page
still calls no API and loads nothing from another site. A script,
`agent/replay/build_demo.py`, builds it from the run records and refuses any run
from a build other than the current one, so the demo cannot quietly show a
replaced build. The run records it reads are not tracked, so a reader of the
repository can check each run ID but cannot rebuild the demo.

**Kept:** v1's four published briefs stay where they were, unchanged, as the
evidence for v1. v1's recorded fixtures, which tests still read, moved to
`agent/tests/fixtures/v1_fixtures.js`.

**Worth recording:** the review before publishing found the first draft blamed
blocked calls on the spending cap. They were blocked by the per run limits on
searches and fetches; every run finished far under its cap. The demo also
called the air conditioner's second answer one "from memory" although the
brief used none of the stored causes. Both were corrected before publishing.

---

## The teardown, rewritten for v2, 18 September 2026

Roanuk asked the teardown to explain why he chose LangGraph and LangChain, with
the benefits and the tradeoffs, why v2 splits the work across two services, and
what v2 lets owners and technicians do.

**Decided:** the page keeps v1's story (Parts 1 to 4) and adds a part on each
question: why LangGraph and LangChain, and what each capability buys an owner or
technician; the two services, with Claude for judgment at the cheapest model
that can do each step and Tavily for search on its free tier; and seven things
v2 lets owners and technicians do, each tied to a demo case. It reports only
the fixed build's live results. The one exception is the build's total spend,
which also counts the replaced build's runs and says so. The front matter
follows the house format, now held by `agent/tests/test_teardown.py`.

**Worth recording:** the review found the first draft gave memory credit for
savings it did not make. Only the hot tub code repeat shows memory replacing
search. The other two repeats were held to 2 searches by the top up limit. The
repeat from memory was also thinner than the first answer. The page now says
all three plainly. The facts strip's second box was changed from that repeat's
cost to the 180 of 180 validator agreement, so two of four headline numbers do
not rest on the same favorable run.

---

## v2, summarized on the fixed build, 18 September 2026

This entry replaces "v2, summarized" above, whose results came from the
replaced build.

**What held:** every v1 guarantee is still enforced in code, and v1's own
validator agrees with v2's on all 180 test briefs. The invented model was
refused three times out of three by the model itself. A repeat of a coded
question was answered from memory with no search for $0.0082, against $0.0587
for the first lookup, though with a thinner brief. A live run with synthetic
property records cited the earlier service record and the unit's age. Every
live run stayed under its $0.15 cap, and the whole build spent $0.59 of its $5.

**What the bar did not show:** the repeat question criterion passes, but two of
its three repeats were held to 2 searches by the top up limit rather than by
anything memory supplied. Whether stored causes save searches on a new symptom
is still untested, because the classifier matched none of them.

**Next:** put one brief in front of a working technician; decide whether code
should flag steps that cut power; tighten the cause rule so a quote naming only
a component cannot pass.

---

## Safety step flagging, Gate 0: facts checked, 29 September 2026

Roanuk's brief for flagging safety steps with Jev, TypeSafe's typed judgment
model, starts with a gate that only reads and checks. Every fact it relies on
was checked against the repo, TypeSafe's live docs, the published SDK and the
local data, and each disputed fact was checked again by a second reader.

**One test call:** with Roanuk's "proceed", one Jev call asked the brief's
safety question about the published breaker step. It answered 0.97, used 384
input tokens, cost $0.000016 and came back as model `jev-1.13.0`. The ledger
could not yet reserve a TypeSafe call, because `reserve()` assumed Anthropic,
so its reserve, charge and release rows were written from a scratch script
under provider `typesafe`.

**Corrected facts:** the published briefs hold 32 steps, 8 flagged, not 34.
The rerun inputs are in the live recordings, not the run records. TypeSafe
paused signups from 22 to 27 September. "jev" is not a documented model name.
Timeouts, connection failures and a missing key raise TypeSafe errors that
`TypeSafeAPIError` does not catch.

**Decided:**
- **53. Where the labeled set comes from.** Pages written on the maker's own
  site cannot supply 30 safety steps for the held out half: they hold about
  111 usable sentences, and a keyword count suggests about 19 safety steps.
  The Sundance Optima 880 manuals are written by the maker but hosted on other
  sites. Roanuk chose the order: maker site pages, then maker written manuals
  on other hosts (a fixed list recorded before any labeling), then dealer
  pages, until the held out half has 30 safety steps or the pages run out.
- **54.** The check pins `jev-1.13.0`. A reply from any other version counts
  as a failed check, because the thresholds belong to the version they were
  set on.
- **55.** The check catches TypeSafe's base error class and any other
  exception, so every failure leaves the word rule and the writer's flag in
  place and shows the notice line.
- **56.** Jev's answers are matched to steps by the step's own text, not its
  position, because the upgrade pass revalidates without a new draft.
- **57.** Each call gets 5 seconds and 1 retry, so a failing check cannot hold
  a brief for half a minute per step.

**Cost:** $0.000016. The build has spent $0.5907 of its $5 cap and 63 of its
150 Tavily credits.

---

## Safety step flagging: the labeled set and the writer instruction, 29 September 2026

The build of the check, the word rule and the evaluation harness came first,
with no paid call. Three reviewers raised 16 problems, all confirmed by a
second reader and fixed; the most serious was that every live SC12b run would
have crashed after its paid calls. The full suite passes and all 1,062 planted
bugs are caught. Before any item was labeled, the first build of the labeled
set showed the brief's sampling could not give two comparable halves, so it
was changed; no reader, score or lock existed yet.

**Decided:**
- **58. Sampling.** At most 25 sentences from any one page (a single hot tub
  manual had filled 138 of the 300 items), pages grouped into families for the
  split so the same manual across years, and runs that answered the same
  question, never straddle it, and halves balanced by item count, families
  taken in the order of a stable hash.
- **59. Balance by appliance, and where the writer is scored.** Halves are
  balanced within each appliance (the first balanced build put 85% air
  conditioner text in the held out half), and two revisions of one Trane
  manual share a family. Every question the writer answered holds a worked
  example, which the brief sends to the tune half, so the held out half has no
  generated steps. The writer's own flags are therefore scored on all of pool
  G; the writer has no tuned setting, so the tune half is fair for it, and the
  report says the writer arm is measured on different items from the others.
  The set: 300 items, 158 tune and 142 held out; air conditioner 85 and 68,
  hot tub 73 and 74.
- **60. Arm A.** The writer instruction's safety sentence is Roanuk's revised
  wording, word for word. This replaces design change 5 ("no prompt change
  for safety steps"). It is measured in SC12b, against the 18 September
  writer.

**The fixed list of maker manuals** (decision 53) was reviewed by Claude
against the stored pages before labeling: 3 Sundance 880 owner's manuals, 2
Trane documents and 3 AquaRest owner's manuals, with 10 off topic or forum
pages left out of pool D. Its hash is pinned in the set and the lock.

**Goldens:** the published word rule raises three flags in the replay goldens,
pending Roanuk's review. v2_flo "Remove the filter cartridge and see whether
the FLO message clears" (heater in the detail; probably a false positive under
the label protocol); v2_hvac "Clean or replace the air filter" (power and
breaker in the detail; moves from 7th to 2nd); v2_notheating "Raise the
temperature setpoint with the filter out and see whether the heater engages"
(moves from 6th to 3rd).

**Cost:** $0.

---

## Safety step flagging, Gates 1 and 2: labels, tuning and the lock, 30 September 2026

**Gate 1.** Three blind readers labeled every item with the protocol and worked
examples only; each reader returned its answers without opening any file. The
first 300 items left the held out half 6 short of 30 in scope positives, so
the stopping rule added the remaining 45 items (the pages then ran out) and the
same readers labeled them before anything was scored. Final set: 345 items,
170 tune and 175 held out; in scope positives 45 tune (electrical 42, heat 3)
and 31 held out (electrical 27, heat 3, gas 1). The readers agreed on 338 of
345 safety answers (0.980), and on 320 with the hazard too (0.928). The labels
come from three model readers, not from people.

**Tuning, tune half only.** 680 Jev calls, $0.0167. Each configuration's
threshold is the highest that still flags every in scope positive.

| Wording | Jev threshold | Jev precision | Word rule plus Jev precision | Flags on "other" steps |
|---|---|---|---|---|
| 1, the brief's starting wording | 0.24 | 0.375 | 0.542 | 23 |
| 2, plus criteria from the protocol's yes and no lists | 0.34 | 0.577 | 0.776 | 16 |
| 3, plus two boundary cases from the protocol (an equipment access door or panel is yes; refrigerant or pressure work and running with the filter out are no) | 0.51 | 0.865 | 0.833 | 0 |
| 4, wording 3 asked as a question | 0.45 | 0.750 | 0.726 | 5 |

Recall is 45 of 45 in every row. Wording 3 is locked.

The word rule, tuned: each word that gained recall with no new false flag was
added, in order of gain, then published words were tested for removal.
Published list: recall 29 of 45, precision 0.853. Add "wire": 32, 0.865. Add
"panel": 35, 0.875. Add "door": 38, 0.884. Add "disconnect": 39, 0.886. Add
"electrical": 40, 0.889. Add "jumper": 41, 0.891. Add "box": 42, 0.894. Plurals
of the added words: no change. Drop "heat" (it caught only a thermostat step
and two cover and siting steps): 42, 0.955. No other published word could go
without losing a positive. Tuned list: recall 42 of 45, precision 0.955.

**Decided:**
- **61.** The production question is wording 3 and the shipped word list is
  the tuned list, both as locked below. A test checking the layer mechanics
  now pins the published list it was written against.

**Gate 2, the lock** (`data/eval/sc12a/lock.json`, written before any held out
number existed):

```json
{
  "criteria": {
    "false": "Ordinary use of the controls (pressing a button, changing a mode or setpoint for normal use), checking water level or water chemistry, cleaning or swapping a filter, or a step whose only hazard is mechanical, chemical, pressure, water or a fall, unless the step or its detail brings in one of the yes cases. Refrigerant, nitrogen, vacuum or other pressure work, and running the equipment with the filter out, are no unless the step or its detail brings in one of the yes cases.",
    "true": "The step, or doing it wrong, involves electricity, gas or overheating: it switches power or gas off, on or reset (a circuit breaker, a disconnect, a GFCI, unplugging the unit, a power switch used to cut power for service, or a gas valve); it has the reader open, touch or work near something that can be electrically live, hot or carrying gas (an equipment panel or cabinet, wiring, a heater or heating element, a burner, hot water or a hot surface); it checks one of those hazards before contact, such as reading an indicator light before touching anything; or it runs or tests a heater or burner as a diagnostic. Removing or opening an access door, panel, cover or control box on the equipment counts as working near something that can be live."
  },
  "locked_at": "2026-09-30T15:56:57+00:00",
  "maker_documents": "dd513bf70a30b54eb88c5fe0fc2a77b5ecaca42333945aa739c047443abc8f78",
  "model": "jev-1.13.0",
  "question_hash": "34fb2f3b18541662",
  "sha256": {
    "criteria": "3b51fe2098ef198b3b7d7d9be6f5b82e02690456be8a15a7c654a7039b5fcd69",
    "item_ids": "686e00ee6934cd777f4b749163eea4c9c98d77cf7b27f84f2b011fd059001576",
    "labels": "5c34a7c92a89b2729c23e790ea37bed7019581ebbca0796011c3b88fe45b71a1",
    "maker_documents": "502c0dc318b2af55dcd734dbf5c4fa0e00e3badf90779e37cae5e4283b0efe2d",
    "model": "3b144d5e6bb7bf4cafa48404341aafa9ada977d2f3425ee65a4a0e800d2cc00c",
    "split": "9884207337762e9d4c9d94d2b6f84e731d7c8cbe2a699449efa1538f7c097b78",
    "thresholds": "1405a875b43315e3f565a3c8707682d3e1b9ea80e11b910616e05becdc2c16e1",
    "word_list_published": "03cb1ffc13b4e5ba9b997f170e7bc4323502139bd91245d62ac4b5ecf0550dba",
    "word_list_tuned": "95b49beef0eb765f4ee570e2661ce19bd070481d00374cc25ffe325eb3b462a8",
    "wording": "25a5448c3af6a71bf6204f419c65c1aca7a77d50b8ab64aaee2e17b2e8e19cb4"
  },
  "thresholds": {
    "jev": {
      "rule": "full_recall",
      "threshold": 0.51
    },
    "word+jev": {
      "rule": "full_recall",
      "threshold": 0.51
    }
  },
  "word_list_published": [
    "breaker",
    "breakers",
    "power",
    "powered",
    "powering",
    "heat",
    "heater",
    "heaters",
    "heating",
    "heated"
  ],
  "word_list_tuned": [
    "breaker",
    "breakers",
    "power",
    "powered",
    "powering",
    "heater",
    "heaters",
    "heating",
    "heated",
    "wire",
    "wires",
    "panel",
    "panels",
    "door",
    "doors",
    "disconnect",
    "disconnects",
    "electrical",
    "jumper",
    "box"
  ],
  "wording": "Doing this step, or doing it wrong, involves electricity, gas or overheating: it switches power or gas off, on or reset; it has the reader open, touch or work near something that can be electrically live, hot or carrying gas; it checks such a hazard before contact; or it runs or tests a heater as a diagnostic."
}
```

**Cost:** $0.0167 for the tune half. The build has spent $0.6074 of its $5
cap and 63 of its 150 Tavily credits.

---

## Safety step flagging: the held out half and what ships, 30 September 2026

The held out half was asked once, under the lock (175 Jev calls, $0.0049), and
scored once. It holds 31 in scope positives. Wilson 95% intervals in brackets.

| Arm | Recall | Precision |
|---|---|---|
| Word rule, as published | 20 of 31, 0.645 [0.469, 0.789] | 0.714 [0.529, 0.847] |
| Word rule, tuned | 22 of 31, 0.710 [0.534, 0.839] | 0.647 [0.479, 0.785] |
| Jev, wording 3 at 0.51 | 30 of 31, 0.968 [0.838, 0.994] | 0.732 [0.581, 0.843] |
| Word rule plus Jev | 30 of 31, 0.968 [0.838, 0.994] | 0.600 [0.462, 0.724] |
| Writer's own flags, on all of pool G (decision 59) | 10 of 14, 0.714 [0.454, 0.883] | 0.909 [0.623, 0.984] |

No held out item carries a writer flag, so each candidate equals its layer
alone. Flags on steps whose only hazard is "other": 4 for Jev and for word
rule plus Jev, 0 for either word rule.

**Decided:**
- **62. What ships: Jev.** The choice rule kept all three candidates (each at
  precision 0.60 or more), Jev and word rule plus Jev tied on recall, and Jev
  has the higher precision. `SAFETY_LAYERS = ("jev",)`, `JEV_THRESHOLD = 0.51`,
  `JEV_SAFETY_ENABLED = True`. The published breaker step scores 0.98 under the
  locked wording, so the published miss is flagged.

**Worth recording:**
- The tuned word list did not hold up. On the tune half it scored precision
  0.955; held out it scored 0.647, below the published list's 0.714, for 2
  more positives. It was tuned on 45 positives, and words such as "door" and
  "box" found harmless steps in pages it had not seen.
- With Jev alone shipped, the word rule is not a production layer. When a Jev
  call fails, the brief keeps the writer's flags and shows the notice line
  telling the reader to treat any step at a breaker, a panel, a heater or a
  gas valve as a safety step; no word rule backs it up. The brief's choice rule
  allows this; whether a failed check should fall back to the word rule is a
  question for Roanuk.
- Jev's highest answers are reliable on this set: every held out item it
  scored 0.8 or more (23) is a positive. Between 0.5 and 0.8 it is mixed.
- The replay goldens go back to the writer's own flags: the replay cassettes
  hold no Jev answers, so under the shipped configuration nothing is raised.
  The three word rule flags recorded on 29 September no longer apply.

**Cost:** $0.0049. SC12a in total: 855 Jev calls, $0.0216. The build has spent
$0.6123 of its $5 cap and 63 of its 150 Tavily credits.

---

## Safety step flagging, Gate 3: the credit cap, 30 September 2026

At Gate 3 the 87 Tavily credits left covered the 80 planned for SC12b's first
passes but not the ceiling of 100 if every run reached its per run cap.

**Decided:**
- **63.** Roanuk raised the build credit cap from 150 to 300, the alternative
  his brief offered under decision 1. It costs nothing on Tavily's free tier
  of 1,000 credits a month. The dollar caps are unchanged. The test that checks
  Gate 3's credit plan now pins the old cap it was written for.

---

## Safety step flagging, SC12b: ten live first lookups, 30 September 2026

Ten first lookups ran on the new build in cheap mode, after the lock and with
arm A's revised instruction in: five with the hot tub FLO input of run
t-267045726dd04118 and five with the air conditioner input of run
t-a6cc7b63e63b41d0. Before each run that model's facts left the graph, and the
graph file was restored afterward; its hash was the same before and after the
batch. Every run finished ok, every Jev call was answered, and no brief showed
the notice line.

**Measured:** the runs wrote 31 steps (23 distinct). The same three blind
readers labeled the 20 not already labeled in SC12a: 14 of the 31 steps are in
scope safety steps.

| Layer on the 31 steps | Recall | Precision |
|---|---|---|
| Final flags, as the briefs show them | 14 of 14 | 14 of 16 |
| Writer, revised instruction (arm A) | 14 of 14 | 14 of 14 |
| Jev at 0.51, the shipped layer | 14 of 14 | 14 of 16 |
| Word rule, tuned (not shipped) | 14 of 14 | 14 of 16 |

The writer on 18 September, before arm A, flagged 3 of the 5 safety steps in
its v2 briefs; search results drift between runs, so that comparison is loose.

**Worth recording:** every in scope step was flagged, so there is no miss to
publish. On these runs the revised instruction alone caught all 14; Jev added
no catch and raised two steps the readers did not call safety steps ("Check
for a broken fan blade in the outdoor unit" at 0.72 and "Spin the fan blade by
hand to see if it rotates freely" at 0.58, both mechanical). The sample is
small and repetitive: 14 positives from two inputs, mostly breaker steps, so
the lower bound of recall's 95% interval is 0.78. SC12a's held out half, where
Jev caught 30 of 31, is the stronger evidence for the code layer; SC12b shows
the instruction change doing most of the work on familiar questions.

**Cost:** $0.4662 and 47 Tavily credits for the ten runs, against a plan of
about $0.65. This work in total: $0.4878, under its $1.50 stop line. The build
has spent $1.0785 of its $5 cap and 110 of its 300 Tavily credits. The ten
recordings passed the privacy scan and stay untracked until Roanuk approves
the privacy diff.

---

## Safety step flagging: what is published, and a correction, 30 September 2026

The README, the teardown (Part 9's safety finding, with the held out table,
and Part 10's first item, now done) and the plan (section 5 rows and section
10.11) report SC12a and SC12b. A fact check and a house style review raised
15 problems, each checked by a second reader, and all were fixed.

**Correction to the three entries above.** Pool G, the tool's own earlier
steps, pools v1's briefs (Claude Opus 5, 18 August), the replaced v2 build of
18 September and the fixed v2 build, as the brief defined it ("every live run,
both builds"). Using those steps as text to score Jev and the word rule is
sound. But the writer arm scored on them (10 of 14, decision 59) and the "18
September writer" comparator for SC12b (3 of 5) partly measure the replaced
build, which is never a result. Both are withdrawn and appear on no published
page; the SC12b scorer now counts the fixed build's briefs only, which gives
1 of 2, too few to report. The writer's own measure on this work is SC12b:
under the revised instruction it flagged all 14 safety steps.

**Decided:**
- **64.** The writer arm and the 18 September comparator are not reported,
  for the reason above. What the page reports for the writer is SC12b alone.

**Worth recording:** the SC12b runs carry build `9a64835e86a5a6c8`. Since
then only the evaluation scorer and the committed labels under
`agent/safety_eval/` changed; the agent's runtime files did not. The build
fingerprint counts both, so the tree's fingerprint differs from the runs'.

**Final checks on this tree:** 2,584 tests pass, with the network blocked and
no keys, in normal and gate mode; all 1,105 planted bugs are caught.

**Cost:** $0. Nothing is committed; the SC12b recordings stay untracked until
Roanuk approves the privacy diff.

---

## The demo re-recorded on the build with the safety check, 30 September 2026

Roanuk asked for the recorded demo to be re-recorded on the new build, and
typed "proceed" after a preflight of every batch. The demo's cases are the same
runs as the refusal, repeat question and plate tests, so those were run again
too, and the published numbers for them move to this build.

**Decided:**
- **65. The build fingerprint covers the agent's runtime code only.** It now
  leaves out the demo tooling and the evaluation harness under
  `agent/safety_eval/`, whose committed labels change whenever labels are
  imported. Before this, run records and the demo builder computed the build
  differently and could never match.
- **66. A repeat is compared only with a first lookup from its own build,**
  never with an SC12a, SC12b or plates run.
- **67. The 18 September build's results leave the published pages.** Its run
  records, evaluation records, recordings, briefs, lookup logs, graph and
  registry (89 files) moved to
  `data/superseded/2026-09-30-build-839b1854a0aaf25e/`; nothing was deleted,
  and the ledger still counts its spend. Its published briefs under
  `briefs/v2/` are replaced by the new ones. Its numbers stay here, in the
  entries above, as history. Build 839b1854a0aaf25e had no known defect; it is
  replaced because the demo now shows the build that ships.

**Measured, build da738a1559ced359, Claude Haiku 4.5 with Tavily and Jev:**
- The invented model was refused three times out of three, each time by the
  model, with 5 searches each, for $0.0311, $0.0451 and $0.0270.
- Repeat questions: the FLO repeat used 0 searches and $0.0082 in 4.3 seconds,
  against 3 searches, $0.0435 and 28.7 seconds for its first lookup. The vague
  "not heating" question took the graph plus a top up of 2 searches for
  $0.0226. The Trane first lookup used 5 searches, $0.0431 and 25.8 seconds;
  the new symptom on the same unit took a top up of 2 searches for $0.0207.
- The clear plate was read exactly; the blurry plate's model, serial and date
  were marked unreadable.
- With the seeded hot tub's synthetic records attached, the FLO question used
  0 searches, $0.0067 and 5.5 seconds, and the brief cited the earlier service
  record and the unit's age.
- The safety check ran on every brief that had steps. The writer, under the
  revised instruction, flagged the breaker steps itself. Jev raised one flag
  the writer left off, on "Run the spa for a few minutes without the filter
  installed" (0.87). The three readers labeled the same step, with a different
  detail, not a safety step in SC12a; this one was not labeled. The demo shows
  it as Jev's flag and says nothing more.

**Cost:** $0.2536 and 31 Tavily credits. The build has spent $1.3321 of its $5
cap and 141 of its 300 Tavily credits.

---

## The re-recorded demo and pages, published, 30 September 2026

The demo is rebuilt from the re-recorded runs (build da738a1559ced359) and now
shows the safety check on every case that has steps to try first: how many
steps Jev answered and, for each flagged step, whether the writer or Jev set
the flag, with Jev's answer. It never says which steps are safety steps. The
teardown and README carry this build's numbers. A fact check, a house style
review and a demo check raised 18 problems, each checked by a second reader,
and all were fixed.

**Worth recording:**
- Under decision 67 the teardown no longer describes the unflagged breaker
  step that started this work, since it came from the replaced build; it keeps
  the general reason a check in code was needed. The history is in the entries
  above.
- The runs were made in the evening of 30 September, US Eastern time, which is
  early on 1 October in UTC, the clock the run records use. The demo gives both.
- SC12a's labeled set is fixed by its items file and its lock. Its pool G was
  built partly from `briefs/v2/`, whose briefs are now the new build's;
  re-deriving pool G would give a different set, so it is not re-derived.

**Final checks on this tree:** 2,649 tests pass, with the network blocked and
no keys, in normal and gate mode; all 1,136 planted bugs are caught.

**Cost:** $0.

---

## A word rule backs up a failed Jev call, 1 October 2026

With Jev alone shipped (decision 62), a failed Jev call left a step with only
the writer's flag and the notice line. Roanuk chose to add a backup.

**Decided:**
- **68. The tuned word rule backs up any step whose Jev call failed.** It
  applies only to steps whose signal carries an error, in a check whose status
  is partial or failed; never to a step Jev answered, and never when the check
  ran cleanly, was skipped, was turned off or had no recorded answers in
  replay. It only raises a flag, and the notice line still shows. The run
  record names the layer `word_fallback`. The switch is
  `config.SAFETY_WORD_FALLBACK`.

**Why the tuned list:** held out, it caught 22 of 31 safety steps at
precision 0.647, against 20 of 31 at 0.714 for the published list. The
published list was not one of the choice rule's candidates, so this pick was
made on 1 October, after the held out scores were known, following the rule's
order: both lists have precision of at least 0.60, and the tuned list has the
higher recall. Either is weaker than Jev (30 of 31 at 0.732) on these items.

**Worth recording:** no live run has used the build with the backup, and no
recorded run has had a failed Jev call, so the backup is shown working only by
the offline tests. The recorded demo replays build da738a1559ced359, from
before the backup; the backup changes only what happens when a check fails,
which none of the demo's checks did. The demo builder now names the layer if a
future recording carries it. The replay goldens and the legacy render did not
change.

**Cost:** $0.

---

## A second PRD: human labels, warranty status, a replacement sweep and a product recall check, 13 October 2026

Roanuk's second PRD adds a human check on the safety step labels (SC12c),
warranty status computed in code (SC13), a "plan a replacement" sweep (SC14),
a product recall check against the U.S. Consumer Product Safety Commission
(CPSC, SC15), and the teardown and README updates for them. A read only pass
checked it against the repo, the data and the outside services first, and a
second reader checked every conflict it raised; none was refuted.

**What held:** the 175 held out items and the three AI readers' labels are
complete and local; every new label can be scored from the recorded Jev
answers at no cost; files under `agent/safety_eval/` stay outside the build
fingerprint; CPSC's recall service answered with no key; Anthropic lists
Claude Haiku 4.5 as active, retirement not sooner than 15 October 2026, with
at least 60 days' notice promised.

**What conflicted, and the decisions:**
- **69. Cheap mode moves to Claude Haiku 5.5** (`claude-haiku-5-5`),
  amending decision 14. The code refuses every Haiku 4.5 live run from 15
  October, which would block the sweep's lookups and any re-record. Roanuk
  chose the newer model over moving the date. Every published live number
  is re-measured on it in one live batch, after the code for this PRD is
  built and replay tested.
- **70. Roanuk labels with what the AI readers had.** The PRD named both the
  lock's criteria and "the same criteria the AI readers had"; they are
  different texts, because the lock holds Jev's tuned wording, which added
  two boundary cases chosen on the tune half. He labels all 175 held out
  items with the label protocol and its six worked examples, verbatim, and
  the same five hazard choices. The labeling page shows third party page
  sentences, so it stays local and is never committed; only item ids and his
  answers are. A local server saves each answer as he goes. The blind answers
  are sealed by hash before scoring, and any later change is kept separately.
- **71. CPSC's 2018 programmer's guide governs.** CPSC's API page still links
  the 2017 guide, which lists a product model parameter; the 2018 guide, the
  newest, drops it, as the PRD describes. Only the 2018 guide's documented
  parameters are sent, never a model parameter.
- **72. The sweep's lookups get their own research mode,** asking only about
  discontinuation and successor models. The existing research looks for
  error codes and troubleshooting, and the air conditioner would answer from
  its stored causes without searching at all.
- The PRD's own picks stand for its other questions: one synthetic appliance
  with active coverage is added, since every seeded term has expired; serial
  ranges in product recalls are not parsed; the demo re-record is decided
  after the product recall check is built.

**Worth recording:** about 70 offline tests read the real date, and the
Haiku date gate would have failed them from 15 October. The tests now run on
a fixed date; tests that need the real clock set it on purpose.

**Cost:** $0. One free, keyless request reached CPSC to confirm the service.

---

## The label check and warranty status, built on replay, 13 October 2026

Phase 0 (the label check, SC12c) and Phase A (warranty status in code, SC13)
are built and pass the full suite offline, in normal and gate mode. Six
reviewers read the build and a second reader checked each finding; nine of
ten were fixed and one was declined (decision 75). Build `1f79e25e8155f393`
(it was `b5b6504d37823e60`; Phase 0 alone left it unchanged, decision 65).

**Decided:**
- **73. One run date.** Each run takes one UTC date when it starts and keeps
  it, even when it resumes on a later day. The model's "today", the age
  statement, every coverage status and the brief footer all read it. The
  property page takes its date once per page. "(UTC)" is printed on the
  coverage line and the property page only, so no other recorded text
  changes. Run record and source timestamps still use the clock, because they
  are timestamps, not dates the reader acts on.
- **74. Code computes every warranty end date and status.** The end date is
  the start (the appliance's install or purchase date) plus the term in
  months, clamped to the end of the month, so a 29 February start plus 12
  months ends on 28 February. A row is "not started" before its start date,
  "active" while the run date is before its end date, then "ended". A start
  date held only as a year or a month gives "end date unknown", never
  "ended". The model is not shown the coverage rows, only the terms text it
  already saw.
- **75. What the brief says.** Active coverage is listed with each
  component, its end date and its record, then "Check with the maker before
  paying for a repair; this brief does not say which coverage, if any,
  applies to it." With rows but none active, it says none is active as of the
  run date and gives the latest end date. With no rows it says "No warranty on
  file.", never "out of warranty". With terms text but no rows it says the
  terms are not broken into components, so nothing is computed from them.
  The check sentence goes only with coverage that is or may be active
  (active, not started, end date unknown, or terms only), never after "No
  warranty on file." or when every row has ended; a reviewer asked whether it
  should always print, and it was declined because telling the owner to
  check coverage that has ended invites a wasted call. Every synthetic row is
  labeled "synthetic".
- **76. The model may not write a date in warranty text.** Rule 13 rejects
  any date in the warranty cautions or verify items, with a retry: numeric
  dates in any order, month names with a day or year, words such as "until"
  or "expires" followed by a date, and any standalone year from 1900 to
  2099. The last is a trade-off: a model number such as "2000 series" fails
  too, and the retry asks for the text without it. Prices and model numbers
  joined to letters or a dollar sign pass, and so does "May" alone. Unlike a
  price, a date quoted from a source is not allowed, because code holds the
  dates.
- **77. One synthetic appliance with active coverage.** A water softener
  (Synthetic Water Co. WS-400, on the second property) was added, bought
  20 April 2026 and installed 4 May 2026, with three rows: valve 60 months and
  tank 120 months from install, labor 12 months from purchase. The Optima
  880's terms text maps to three rows (shell 60, equipment 24 and labor 12
  months from install), all ended by October 2026.
- **78. The blind labels are protected by the code, not by care.** Once the
  labels are sealed, the page's answers are refused and the server will not
  start; sealing is refused once any score exists; the scorer reports
  whether the answer log still matches the seal. A revision's reason may
  quote a step, so the reason stays in the local data folder and the
  committed file holds only the item id, the time and the two labels. A lost
  order file can be rebuilt from the original seed, and a new order is never
  drawn over saved answers.

**Worth recording:** the local registry predates the coverage table. Read
against a fresh seed, it differs only by the softener and the nine lookups
recorded on 1 October, and reloading the seed keeps lookups, so it is
reloaded before the next live run. Every recorded brief now ends its warranty
section with "No warranty on file.", because none has a registry appliance;
this is rendered difference 22, which joins the list awaiting approval.

**Cost:** $0.

---

## The sweep, the product recall check and Haiku 5.5, built on replay, 13 October 2026

Phases B (the replacement sweep, SC14) and C (the product recall check,
SC15) and the move to Claude Haiku 5.5 (decision 69) are built and pass
offline. Two rounds of three builders each were reviewed, and every finding
was checked by a second reader: 16 of 17 findings survived in the first round
and 12 of 12 in the second, and all were fixed. Build `ad11e6e15a55d30a`.

**Decided:**
- **79. Haiku 5.5 settings.** Every Haiku step calls `claude-haiku-5-5`.
  Effort is low for the plate read and the classifier and medium for research
  and the cheap brief; thinking is left adaptive, and no thinking budget,
  sampling setting or fallback is ever sent. Output budgets rose so thinking
  has room (plate 2,048, classifier 1,024, research 4,096, cheap brief 16,000
  tokens); a cheap run's worst case still reserves about $0.114, under its
  $0.15 cap. Calls are priced on two rate cards chosen by prompt length,
  counting cache reads and writes. Anthropic's pages, checked on 13 October,
  give the prices, a tool overhead of 406 tokens, image input for all current
  models, and retirement not sooner than 7 October 2027. Replay stays on the
  recorded model's prices and budgets, so the goldens, the published demo and
  every recorded number are unchanged.
- **80. A declined call ends the run honestly.** When Haiku 5.5's safety
  classifiers decline a call, it is charged, never retried on another model,
  recorded as `model_declined` with its category, and the run ends on a code
  written brief. Recordings can now hold Haiku 5.5 replies, thinking included,
  and a declined call, so a live run can be replayed.
- **81. The first live step checks the model.** One small call per Haiku step
  at its exact settings, including a plate photo, must pass and must be
  answered by `claude-haiku-5-5`, or the batch stops. A refused photo turns
  plate reading off rather than switching models.
- **82. The sweep reads only verified facts.** Offline, it shows each
  appliance's age from its install date and any successor whose verified
  quote names it; with nothing found it says "No discontinuation documented
  in what this tool has verified." A discontinued part whose quote names no
  successor is shown with "No successor is named in this evidence." The
  lifecycle lookup is its own small path through research, one structured
  extraction, the validation rules and the graph save. It refuses invented
  makers and makers without a reviewed domain before any call, and it saves a
  lifecycle fact, from a lookup or a brief, only when its quote names the
  unit's full model.
- **83. One $0.15 cap for the whole sweep** (Roanuk's choice). A lookup starts
  only while what is left covers its worst case ($0.033 in cheap mode); a
  unit left out saves nothing.
- **84. The product recall check.** Only the 2018 guide's parameters are
  sent, and only the maker's name: Manufacturer, Importer and Distributor are
  asked separately and merged by recall number. A recall counts only when it
  prints the model exactly, in its model field, product name, description or
  title; the printing may join the model's parts but never split them. A
  series printing ("4TTR6036*") is shown with its own sentence, and a match
  found only in the text says so. A match lists the recall number, title,
  hazard, remedy and link, then "Check whether your serial number is in the
  affected range." It never says the unit is affected; with no match it says
  no recall names this exact model, with the date checked; a failed request
  says the check did not run. Invented makers are never queried. Responses are
  stored with their date and never overwritten.
- **85. Appliances carry a model number** (Roanuk's choice). The Trane is
  checked as "4TTR6036" as well as "XR16 4TTR6036", each exactly; the product
  line alone never matches. An older registry gains the field without losing
  a row, and live steps that read the registry refuse one that differs from
  the seed.
- **86. A date that is not on the calendar is not a date,** so no age or end
  date is computed from it, and the brief says the date could not be read.
- **87. The stricter test gate now covers this PRD:** phase 6 requires 370
  named tests.
- **88. Phase 0 is deferred** (Roanuk's choice). He has no time to label, so
  the human label check is not done; the labeling page and scorer stay built.
  The pages keep saying the safety result was scored against three model
  readers. Filling in the labels for him was declined: it would be a fourth
  model reader recorded under his name.

**Measured:** 3,422 tests pass offline, in normal mode and both gate phases;
all 1,515 planted bugs are caught.

**Worth recording:** the live batch that re-measures every published live
number on Haiku 5.5 is planned from the code's own estimates at about $0.20
typical and about 90 search credits, with the run caps holding the worst
case near $2.41 against the $3.67 left. It waits for Roanuk's "proceed". It
needs the earlier runs filed under `data/superseded/` and the registry
reloaded from the seed first.

**Cost:** $0.

---

## The live batch on Claude Haiku 5.5, 13 and 14 October 2026

The live batch re-measured every published live number in cheap mode on
Claude Haiku 5.5 (decision 69), on build `d24ed7c475dcd813` unless noted. The
SC12b runs were made on the evening of 13 October, US Eastern, which is early
on 14 October in UTC, the clock the run records use; the demo's cases were
made on the morning of 14 October.

**Decided:**
- **89. Research effort is high, amending decision 79.** The first SC12b pass
  at medium effort (build `ad11e6e15a55d30a`) ended with no reliable answer
  on 4 of 10 first lookups; three of the four fetched one page or none, so the
  model stopped researching early. Before the rerun a bar was set: at least 9
  of 10 must answer. At high effort 7 of 10 answered, so the bar was not met.
  The refusals moved to the writing step: Haiku 5.5 declined when its only
  sources were the maker's general pages or a dealer table cut off before the
  action.
- **90. Haiku 5.5 ships at 7 of 10, and prompt rule 6 stays as written**
  (both Roanuk's choices). Rule 6 asks for documentation of this exact model
  or its documented family and forbids general knowledge of the equipment
  category. Haiku 4.5's Trane answers cited Trane's maker wide troubleshooting
  pages (such as "AC Fan Not Working?"), which the rule does not allow: both
  Trane briefs on build `da738a1559ced359` and all five of its SC12b air
  conditioner runs (build `9a64835e86a5a6c8`). So its 10 of 10 includes
  answers the rule does not allow. Haiku 5.5 is not consistent either: its
  three answered air conditioner runs cite the same pages, and two refused on
  similar sources. No code check enforces rule 6; only the model applies it.
  The pages report 7 of 10 as measured.
- **91. The earlier runs are filed under `data/superseded/`, by moves only.**
  Before the batch, build `da738a1559ced359`'s run, evaluation, recording,
  brief and lookup files, the SC12b runs of build `9a64835e86a5a6c8` and the
  live graph (182 files) moved to
  `data/superseded/2026-10-13-build-da738a1559ced359/`. Before the rerun,
  build `ad11e6e15a55d30a`'s capabilities check and medium effort SC12b runs
  (93 files) moved to `data/superseded/2026-10-13-build-ad11e6e15a55d30a/`; a
  changed setting's results are not a finding, so they appear here and never
  on the pages. Nothing was deleted, each folder's MOVED.txt lists every
  move, and the ledger still counts the spend. The registry stayed in place
  and was reloaded from the seed. The product recall check is kept from build
  `ad11e6e15a55d30a`: it sends only the maker's name to CPSC and reads no
  model or effort setting, so the change could not alter it.
- **92. The demo is re-recorded from these runs and shown as measured,
  refusals included** (Roanuk's choice). Its eight cases finished on 14
  October between 06:37 and 06:56 Eastern. Four of them end with no reliable
  answer (the vague hot tub question, both air conditioner questions and the
  invented model), and the demo says so. The demo builder, now version 5,
  accepts a refusal on the vague question and both air conditioner
  questions, and fails the build if a refusal claims an answer, lists steps or
  saves anything to memory. Case 8 shows the warranty coverage line. The new
  demo passes check_demo in a preview with 0 problems. (Written before the
  install: installing it was to wait, with the commit and the publish, for
  Roanuk's "proceed". It has since been installed into the working tree; see
  "Installed" below.)

**Measured** (every number the pages may use, from the file named):
- Capabilities check passed, all 4 calls answered by `claude-haiku-5-5`
  (`data/eval/capabilities/capabilities.json`); the schema check was
  accepted.
- SC12b, 10 first lookups (`data/eval/sc12b-t-*.json`; `advisor eval sc12b
  --score`): 7 of 10 answered, 4 of 5 on the hot tub and 3 of 5 on the air
  conditioner. Of 18 steps, 11 are in scope safety steps. The final flags
  caught 11 of 11 at precision 11 of 14; Jev 10 of 11 at 10 of 11; the writer
  10 of 11 at 10 of 13; the tuned word rule, not shipped, 11 of 11 at 11 of
  14. The ten runs cost $0.0750 and 35 credits, a median of $0.0076 and 41.3
  seconds a run. Haiku 4.5's ten SC12b runs (build `9a64835e86a5a6c8`) all
  answered, for $0.4662 and 47 credits, a median of $0.0476 and 24.9 seconds
  (archived run records).
- Repeat questions (SC7b, `data/eval/sc7b-t-*.json`): MISS, repeats used 0 to
  4 searches against a limit of 2. FLO first lookup t-88d15d19cba943ec: ok, 2
  searches, 4 credits, $0.0075, 40.1 seconds. FLO repeat t-d644c199cbd74532:
  ok from memory, 0 searches, $0.0015, 9.8 seconds by its run record (9.9 by
  its evaluation record; the demo and pages use 9.8), with no steps to try
  first. Vague repeat t-d077f82722f24d5f: no reliable answer, 2 searches,
  $0.0040. Trane first lookup t-5cb8a33347ab4425: no reliable answer, 4
  searches, $0.0086, 37.6 seconds. Trane repeat t-22e572e8a78146c1: no
  reliable answer, a full research pass of 4 searches because the first
  lookup saved nothing, $0.0089. On Haiku 4.5 (build `da738a1559ced359`) the
  same check passed with 0 to 2 searches; its FLO first lookup cost $0.0435
  in 28.7 seconds and its repeat $0.0082 in 4.3 seconds.
- Invented model (SC3b, `data/eval/sc3b-t-*.json`): 3 of 3 refused by the
  model, PASS, for $0.0045, $0.0051 and $0.0068, taking 25.6, 21.6 and 27.2
  seconds by their run records (21.6 to 27.2 s on the pages).
- Plates (`data/eval/plates-t-*.json`): the clear plate read exactly and the
  blurry one marked unreadable, PASS, for $0.0005 and $0.0006, taking 4.0 and
  3.7 seconds by their evaluation records (these runs pause at the
  confirmation and have no run record).
- Claude Haiku 5.5 prices, as the pages quote them (`agent/config.py`
  `PRICES_PER_MTOK`, `LONG_PROMPT_ABOVE_TOKENS` and
  `LONG_PROMPT_PRICES_PER_MTOK`; decision 79): $0.10 per million input tokens
  and $0.50 per million output tokens, and $0.50 and $2.50 for a call whose
  prompt is longer than 100,000 tokens, checked on Anthropic's pricing page on
  13 October 2026. Its retirement date, no sooner than 7 October 2027, is
  `HAIKU_RETIREMENT_EARLIEST` in `agent/config.py`, checked on Anthropic's
  model deprecations page the same day (decision 79).
- Warranty status (SC13): the one synthetic water softener's rows are active
  on 14 October 2026, labor to 2027-04-20, the control valve to 2031-05-04
  and the resin tank to 2036-05-04 (`data/property/prop-b.html`, written by
  the sweep run on 14 October); every other seeded term has ended.
- Records run t-044cd20f9678498e: ok from the records and the graph, 0
  searches, $0.0013, 7.7 seconds; it confirmed the FLO meaning with no steps
  to try first, and its coverage line reads "No warranty coverage on file is
  active as of 2026-10-14 (UTC)", over three synthetic rows, all ended.
- Replacement sweep (`data/runs/lifecycle-*.json`): no successor verified for
  any of the three real units; a Trane candidate was dropped as
  `unit_not_in_span`; $0.0044 and 8 credits.
- Product recall check (`data/runs/recalls-b37be1f2a56b49fd.json`, build
  `ad11e6e15a55d30a`): 9 free requests; 12 distinct recalls returned for
  Trane, 6 for Rheem and 1 for Sundance Spas; none names any of the three
  models exactly.
- Tests: on 14 October 2026, after the last page edit, all 3,451 offline
  tests pass, with the network blocked, in normal mode and in gate mode
  (`ADVISOR_GATE=6`), and a full run on the same code caught all 1,537
  planted bugs.

**Worth recording:**
- Written before the install: the installed demo check
  (`test_check_demo_passes_on_the_installed_site`) failed until the new demo
  was installed, because the published site still named runs now filed under
  `data/superseded/`. It passes now.
- The graph keeps only a code's meaning, not its steps. After the batch it
  holds one edge, FLO on the Optima 880 from a dealer page, and 0 documented
  causes. The FLO repeat listed none of the first brief's 4 steps, and the
  records run listed none either, where Haiku 4.5's records run listed 4.
- The vague repeat's short research pass allows one fetch. The second fetch
  it asked for, an 880 Series owner's manual, was blocked by that limit, and
  its refusal names those manuals' troubleshooting sections as what was
  missing.
- `advisor graph stats` counts 4 dropped edges, all from SC12b runs whose
  graph writes were rolled back afterward; the drop log is not rolled back
  with the graph.
- Another maker's page and a video page were cited under the dealer tier.
- The four items above are runtime behavior, and runtime code stays frozen on
  this build (decision 65), so none was fixed.
- Two invented model recordings fail the privacy scan, because the research
  notes quote a maker's contact details; they are not promoted, and the demo
  reads only their run ids and statuses.
- The new demo selection's note said its runs were made on 13 and 14
  October; every demo case is from 14 October. It was corrected at install.

**Installed** (14 October 2026, into the working tree only; nothing is
committed or published, which waits for Roanuk's "proceed" after he sees the
diff):
- The re-recorded demo is installed: `tool.html`, `src/demo.js` and
  `src/fixtures.js` are replaced from the builder's output, and the seven 30
  September briefs in `briefs/v2/` are replaced by the seven new runs' briefs.
  `agent/replay/demo_selection.json`'s note now says the runs were made on 14
  October 2026.
- Four planted bugs for the new demo drawing code were added
  (`installed_demo_js_route_lead_not_drawn`,
  `installed_demo_js_compare_note_not_drawn`,
  `installed_demo_js_outcome_row_not_drawn`,
  `installed_demo_js_coverage_not_drawn`), and two were changed to fit the
  new data (`installed_demo_safety_dropped`,
  `demo_selection_recorded_on_moved`).
- After review, the demo's note on its refusals no longer calls them simply a
  success: it says that declining is the tool's success state when its
  sources do not support an answer, and that cases 3, 4 and 5, which ask
  about real units, also count against the answer bars in the teardown's
  Part 8. The builder computes those cases from the selection; a test checks
  the sentence and planted bug `build_demo_refusal_note_cost_dropped` checks
  the test. The teardown's Part 7 says the same.
- check_demo finds 0 problems on the repo root, and
  `test_check_demo_passes_on_the_installed_site` passes. `build_info.build_id()`
  is still `d24ed7c475dcd813`: the builder and its checker sit outside the
  fingerprint.
- Still waiting on Roanuk: promoting the seven recording candidates into
  `agent/tests/cassettes/` and the privacy diff addendum that would allow it.
  The two invented model recordings the privacy scan blocked are never
  promoted. The demo builds and passes its tests without them.

**Cost:** $0.1953 and 101 Tavily credits for the batch, including $0.0652
and 30 credits for the filed medium effort runs; the product recall check was
free. The build has spent $1.5275 of its $5 cap and 242 of its 300 Tavily
credits.

---

## Two follow-ups after the publish, 14 October 2026

PRD 2 was published at 253d357. Two small items held back while the build was
frozen for the demo were done afterward, offline and at no cost. A review and
a second reader checked them; five minor findings were fixed.

**Decided:**
- **93. `advisor graph stats` reads the graph that matches the mode.** It
  printed a block for both the runtime and the replay graph every time, so in
  cheap mode the missing replay graph read as if the live graph were missing.
  It now prints one block: the runtime graph in cheap or full mode, the replay
  graph in replay or when no mode is set. This changes runtime code, so the
  build moves from `d24ed7c475dcd813` to `179b4295c98a3143`. Nothing it
  changes runs in a brief. The published demo and every PRD 2 result stay on
  `d24ed7c475dcd813`, as the demo stayed on its build after decision 68.
- **94. The first live product recall check is kept as the offline
  fixture,** as the PRD asked. The three stored checks from 13 October
  (22:21 US Eastern) are copied byte for byte into
  `agent/tests/fixtures/cpsc/`, with each response body's hash in that
  folder's README, and tests read them through the check's own code: no
  product recall names any of the three models exactly. The Sundance Spas
  response holds one recall that lists an "880" series among others, a near
  miss the exact match correctly leaves out.
- The demo's design note (`agent/replay/DEMO_V2_DESIGN.md`) now describes the
  14 October demo, and says a rebuild on a later build is refused unless it is
  marked as a stand-in.

**Measured:** 3,470 tests pass offline, in normal and gate mode, and all
1,543 planted bugs are caught.

**Cost:** $0.

---

## The human label check, and a Claude judge registered before its run, 15 October 2026

**The human label check (SC12c, Phase 0).** Roanuk labeled all 175 held out
items blind on the labeling page, with the readers' protocol and worked
examples, and the answers were sealed before scoring (sha256 `70725f59...`;
26 answers changed before sealing, the latest kept). Scored by
`python -m agent.safety_eval.sc12c score`, read only on every SC12a file:
- Against the three model readers' majority: 162 of 175 agree (92.6%),
  Cohen's kappa 0.728. Roanuk called 26 steps safety steps; the readers, 31.
  Eleven of his yes answers named only "other" as the hazard, which the
  protocol counts as no, exactly as the readers' votes are counted.
- Against his labels: Jev (wording 3 at 0.51) caught 22 of 26, recall 0.846
  [0.665, 0.938], precision 0.537 [0.387, 0.679]; the published word list
  18 of 26, recall 0.692 [0.500, 0.835], precision 0.643; the tuned word list
  18 of 26, precision 0.529. Against the readers the same arms caught 30, 20
  and 22 of 31, at precision 0.732, 0.714 and 0.647.
- What it changes: Jev still catches the most safety steps by a person's
  reading, but with 26 positives its recall interval overlaps both word
  lists', and about half of its flags are steps he would not call safety
  steps. By the readers' labels its lead was clear only over the published
  list: its recall interval [0.838, 0.994] already overlapped the tuned
  list's [0.534, 0.839].
- Five items were checked by hand against the raw votes, the sealed labels
  and Jev's recorded scores; all five match the scorer.
- After seeing the 13 disagreements he revised three (two now match the
  readers). Revisions are recorded separately; every score above uses the
  sealed blind labels.

**Decided:**
- **95. A Claude judge arm, registered before it runs.** On the same 175 held
  out items, Claude Haiku 5.5 is asked the exact question Jev is asked
  (wording 3 and its criteria, question hash 34fb2f3b18541662, built by the
  harness's own code), one call per step, with the cheap mode classifier's
  settings: effort low, max_tokens 1024, a single true or false field, no
  thinking setting, sampling parameters or fallbacks. A flag is a true answer;
  there is no threshold and no tuning. A decline counts as not flagged and is
  reported separately. One run only (settings hash `658f072743accb13`, pinned
  in code); one retry per item on a lost connection only; a stopped run
  resumes under its own ledger run and cap. It is scored against Roanuk's
  sealed labels and against the readers' majority, beside Jev and both word
  lists. The reference labels from the readers came from Claude model
  readers, which may favor Claude; the human labels do not. Planned: 175
  calls, about $0.02 typical, at most $0.15 by the run cap. Roanuk approved
  the spend on 15 October.

**Measured, the Claude judge (run claude-judge-152437a0a5ed, completed, 175 of
175 answered, no declines):**
- Against Roanuk's sealed labels: Claude caught 19 of 26, recall 0.731
  [0.539, 0.863], precision 0.576 [0.408, 0.728]; Jev 22 of 26, 0.846
  [0.665, 0.938], precision 0.537 [0.387, 0.679]. Every safety step Jev
  missed, Claude missed too.
- Against the readers' majority: Claude 28 of 31, recall 0.903 [0.751,
  0.967], precision 0.848 [0.691, 0.933]; Jev 30 of 31, 0.968, precision
  0.732. Claude's precision is well above Jev's here and close to it against
  Roanuk (0.576 against 0.537), the pattern that would appear if labels made
  by Claude readers favored Claude, though it does not show a bias; Jev's
  precision also fell by his labels, from 0.732 to 0.537.
- Claude and Jev agree on 163 of 175 steps (93.1%), kappa 0.795.
- Cost per step, from the ledger: Claude $0.000104 ($0.0182 for the run),
  Jev $0.000028 (the SC12a held out pass), so Claude cost about 3.7 times as
  much. Claude took a median 1.08 seconds per step (90th percentile 1.67).
- Scores: `agent/safety_eval/claude_judge_scores.json`, by
  `python -m agent.safety_eval.claude_judge score`, read only on `data/`. It
  holds the scores against the readers and, under `against_roanuk`, against
  Roanuk's sealed blind labels, loaded through the SC12c seal check (sha256
  `70725f59...`). The score is refused if the labels changed after the seal,
  if their ids are not the held out 175, or if Jev and both word lists do not
  reproduce `sc12c_scores.json`; they do, Jev at 22 of 26. The 3 revised
  labels are not scored.
- 42 planted bugs were added on 15 October: 41 for the Claude judge and its
  scorer (7 of them for the scoring against Roanuk's labels) and 1 for the
  teardown's explanation of kappa where it first appears. Each was confirmed
  caught with `mutate.py --only`, not in a full run.
- On 15 October 2026 the full offline suite, 3,542 tests, passed with the
  network blocked by pytest-socket, in normal mode and in gate mode (phase 6,
  370 required node ids), and a full mutation run caught all 1,585 planted
  bugs; check_demo finds 0 problems on the repo root.
  `build_info.build_id()` is still `179b4295c98a3143`: the scorer, its tests
  and the pages sit outside the fingerprint.

**Cost:** $0.0182 for the Claude judge; $0 for the human check. The build has
spent $1.5457 of its $5 cap.

## A paired test on the same steps, added after the results, 16 October 2026

**Decided:**
- **96. A paired test on the same steps, added after the results.** The
  teardown rested "no check is shown to beat another" on overlapping Wilson
  intervals, which judge each check by its own total. A paired test is
  stronger on the same 175 steps: it compares two checks step by step,
  counting only the steps where they disagree. The exact two sided McNemar
  test (binomial on the disagreements at one half, the smaller tail doubled
  and capped at 1) is now in `stats.mcnemar_exact`, and `claude_judge score`
  runs it for every pair of Jev, Claude and the two word lists, against
  Roanuk's sealed labels and the readers' majority, for missed safety steps
  and for false flags: 24 tests. It was chosen after the recall and precision
  results were known, not registered before any run, and no correction is
  made for the number of tests; the printout, the scores file and the pages
  all say so. Recorded answers only: no call, nothing under `data/` changed.

**Measured (source: `agent/safety_eval/claude_judge_scores.json`, under
`paired`, which also holds the ids behind every split):**
- Against Roanuk's sealed labels (26 safety steps, 149 others), Jev against
  Claude: safety steps only one caught 3 and 0, p 0.250 (1/4); false flags
  only one raised 7 and 2, p 0.180 (23/128). This matches the figures worked
  out by hand before the build. Jev against each word list: catches 4 and 0,
  p 0.125; false flags against the published list 17 and 8, p 0.108, the
  smallest p of the 12 tests by Roanuk's labels, and against the tuned list
  12 and 9, p 0.664.
- Against the readers' majority (31 safety steps, 144 others), Jev against
  Claude: catches 3 and 1, p 0.625 (5/8); false flags 7 and 1, p 0.070
  (9/128). Jev against the published list: catches 11 and 1, p 0.006; against
  the tuned list 8 and 0, p 0.008. Claude against the published list: 9 and
  1, p 0.021.
- What it changes: by Roanuk's labels nothing is separated, as before, now on
  a paired test rather than intervals alone; Jev's widest lead is 4 to 0 over
  each word list on catches, and the published list's is 8 to Jev's 17 on
  false flags. By the readers' labels Jev's leads over both word lists are
  the most lopsided splits, including the tuned list, whose recall interval
  overlapped Jev's; with 24 uncorrected tests chosen after the results, they
  are reported as a reason to look again, not proof. The pages sort splits
  against the common 0.05 bar, stated once; a Bonferroni correction for 24
  tests (0.05 divided by 24, about 0.0021) leaves none under it, the
  smallest being 0.006 (13/2048).
- The teardown's safety part, Part 10, Appendix A and Appendix B (plain
  definitions of a paired test and a p value) and the two README rows were
  updated; the TL;DR and Summary were not, since nothing in them is now
  contradicted. index.html was re-hashed in the published manifest.
- 14 planted bugs were added, 1,599 in all: 9 for the test and scorer, 3
  for the teardown's explanation of a paired test and a p value where each
  first appears and in Appendix B, and 2 for the paired section giving both
  sides of each of Jev's splits by Roanuk's labels and the correction for 24
  comparisons. Each was first confirmed caught with `mutate.py --only`, and
  a full mutation run on 16 October caught all 1,599; the offline suite,
  3,562 tests, passes with the network blocked in normal mode and in gate
  mode (phases 5 and 6). `build_info.build_id()` is still
  `179b4295c98a3143`.

**Cost:** $0. The build has spent $1.5457 of its $5 cap.

---

## The advisor as an MCP server: built offline, run once live, then called by Claude Code in replay, 16 October 2026

The advisor now also runs as a local MCP server. MCP, the Model Context
Protocol, is an open standard that lets an AI assistant call outside tools.
`agent/mcp_server.py` serves four tools over stdio (standard input and output,
a direct connection between two programs on one computer): `get_service_brief`,
`resume_brief`, `property_status` and `check_product_recalls`. Every guarantee
stays in the advisor's own code. The server was built and tested offline, in
replay and on the live path's fake clients. Then a test program drove the
official SDK's client against it, first in replay at no cost and then for one
live brief that Roanuk approved in advance, with no person in the run. Later
the same day, Claude Code called the tools in a free replay session. Claude
Desktop has not been tried, and no assistant has yet run a live, paid brief.
Roanuk took the recommended option at each choice and said to publish when done
(decision 104). A review, confirmed by a second reader, then found that the
section and the Summary implied an assistant had used the server before one
had; the pages, `README.md` and `docs/mcp.md` were rewritten around what is
shown (Pages, below).

**Decided** (97 to 102 as proposed, each taken by Roanuk on 16 October; 103 and
104 added then):
- **97. The official MCP Python SDK, pinned at 2.2.0.** It is the newest release
  at least two weeks old: 2.3.0 was uploaded on 2 October, under two weeks
  before. v2 is the SDK's stable line (v1.x now takes only critical bug fixes
  and security patches), and one v2 server answers both protocol eras:
  2026-07-28 connections, on which a tool returns its question in place of
  asking mid call, and every handshake era app up to 2025-11-25. `mcp==2.2.0`
  sits beside the other exact pins in `pyproject.toml` and is locked in
  `uv.lock`. It was installed into `.venv` with pip (bootstrapped with the
  standard library's ensurepip, since the uv made environment had none), from
  hashes exported from the lock. The lock gains 17 packages, the SDK and 16
  dependencies (pywin32 on Windows only). cryptography is held at 48.0.1, the
  newest with an Intel macOS wheel (49 and later ship arm64 only, and building
  it from source with Rust was ruled out on this Mac), and four are held at
  their newest release at least two weeks old: opentelemetry-api 1.45.0,
  pycparser 3.0, rpds-py 2026.6.3 and starlette 1.7.0.
- **98. One module inside the build fingerprint.** The server is
  `agent/mcp_server.py`, and `build_info` covers it. It decides whether a live
  run may spend (the preflight and the confirmation) and what reaches intake, as
  `agent/cli.py` does, and decision 35 asks that a published number trace to
  the code that produced it. The trade off: every edit to the adapter is a new
  build. The build moves from `179b4295c98a3143` to `17427277685ed433`; the
  published demo and every PRD 2 result stay on `d24ed7c475dcd813`, as they did
  after decision 93. The alternative, listing it in `build_info.EXCLUDED`,
  keeps `179b4295c98a3143` but lets a run started through MCP carry a build id
  that does not cover the code that confirmed its spend.
- **99. The same graph through the same pieces, with `agent/cli.py`
  unchanged.** The tools call `intake_input`, `run_context`, the SQLite
  checkpointer and `run_until_pause_or_end` as `cmd_ask` and `cmd_resume` do,
  and a live run is wrapped in `LiveSession`: the key, ledger, budget, Haiku
  date and registry drift refusals, the keys in the environment for the run
  only, the capture, the scrub and the SC11 record. One step differs: the typed
  "proceed" comes from the confirmation, never from stdin, which carries the
  protocol. `cli.py` is not edited, because 99 planted bugs anchor on its text;
  the orchestration lines are repeated, and parity tests hold them equal. The
  server never creates a ledger, and a live mode needs both `ADVISOR_MODE` and
  the server's own `--live`.
- **100. How the two questions are asked.** The identity confirmation pauses
  where `advisor ask` pauses, and every paid or outbound call first shows the
  command's own preflight. An app that declared form elicitation is asked by
  elicitation: a request mid call on a handshake era connection, a returned
  question on a 2026-07-28 connection. Otherwise a pause returns the thread id,
  the identity read and what is missing, finished with `resume_brief`, and a
  preflight returns a confirmation id, answered on a second call with
  `confirm="proceed"` and that id. A confirmation is used once, bound to the
  tool, its arguments and the exact preflight lines, and kept for ten minutes in
  the server process only; a preflight that changed before it was used is shown
  again. A live resume shows its own preflight. A live run segment refuses to
  start without a confirmed session, whatever code calls it.
- **101. What a brief returns.** Structured content with the status, every
  step to try first with its cited source, every safety flag word for word with
  the layer that raised it, the notice line when the check failed, the
  candidates with their sources, the warranty coverage line, the cost from the
  ledger and the run id, and the brief exactly as the run recorded it; the same
  in plain text; and the location of the HTML brief on the owner's computer, as
  a path and a file URI (the file to send the technician), readable as the
  resource `advisor://briefs/<run id>`.
- **102. `check_product_recalls` mirrors `advisor property recalls --live`.**
  It reads no `ADVISOR_MODE` and opens no ledger, so it also works on a replay
  server, but it always shows `product_recalls.plan_lines` first and sends
  nothing until the confirmation. `property_status` is offline and writes no
  file: the property page is a resource rendered when it is read.
- **103. Gate phase 7 requires every MCP test.** `gate_requirements.json`
  gains phase 7: the 20 test functions of `test_mcp_server.py` and
  `test_mcp_gate.py`. A run under `ADVISOR_GATE=7`, which also enforces phases
  2 to 6, fails when any of them is not collected, is deselected, skips or is
  marked to fail; with the earlier phases it requires 390 node ids, against
  370 at phase 6, which is unchanged. `test_mcp_gate.py` fails when a test
  function in those files is missing from phase 7, when one is required before
  phase 7, or when phase 7 names a test that does not exist. No phase lists the
  page tests (`test_teardown.py`, `test_published_pages.py`, `test_style.py`,
  `test_build_demo.py`), so the page test added below is in no phase either.
- **104. The options Roanuk took.** On 16 October Roanuk said to publish when
  done, and the builder's recommended option was taken at each choice: the
  server inside the build fingerprint (decision 98), not listed in
  `build_info.EXCLUDED`; both confirmation paths kept, elicitation and the
  confirmation id (decision 100), with the tools never set to always allow, so
  that in an app without forms the app's own tool approval prompt stays the
  person's check; `check_product_recalls` mirroring
  `advisor property recalls --live` (decision 102); the first real connection
  made in replay, then one live brief, approved at about $0.01; and gate phase
  7 (decision 103).

**Measured, offline:**
- 29 test cases in `agent/tests/test_mcp_server.py`, with the network blocked.
  26 of them go through the SDK's own client: 25 in process, and 1 that starts
  the server over real stdio in a guarded child process. The other 3 call the
  server's code directly (the store of pending confirmations, the build
  fingerprint and the entry point's mode check). They run on protocol version
  2026-07-28, and on the earlier 2025-11-25 (the client's `mode="legacy"`) in
  the 4 cases on how the server asks the person. They show parity (the same
  case through `advisor ask` and `advisor resume` and through the tools writes
  the same run record); in offline tests against fake paid services, a live
  call refusing every missing, wrong, reused, foreign or changed confirmation,
  with nothing sent and no ledger row; every citation and safety flag a brief
  holds reaching the response, on nine recorded cases; and a real stdio round
  trip. A form (elicitation) has been answered only in these tests, and the
  2025-11-25 version has run only here.
- 57 planted bugs were added: 46 for the server and its gate (44 on
  `agent/mcp_server.py`, 1 on `build_info` and 1 on the gate file) and 11 for
  the page test below, 1,656 in all. Each was first confirmed caught with
  `mutate.py --only`, and a fresh full mutation run on 16 October 2026, on the
  code of build `17427277685ed433`, caught all 1,656; none survived.
- The offline suite, 3,650 tests, passes with the network blocked and
  `V1_BRIEFCASE_DIR` set, in normal mode and under `ADVISOR_GATE=5`, `6` and
  `7` (phase 6: 370 required node ids, 4 SC1b cases; phase 7: 390 required
  node ids). Phase 6 is unchanged: `test_round2_sweep_gate.py` requires PRD 2's
  test files only, and phase 7 lists the MCP tests (decision 103).
  `build_info.build_id()` is `17427277685ed433`.

**Measured, the first real connection, in replay (free):** a test program,
`mcp_evidence.py`, drove the official SDK's client, which launched the server
over stdio with the recorded FLO case, on protocol version 2026-07-28. All four
tools were listed; the replayed brief paused (2.56 s) and resumed (0.49 s), and
came back with 2 steps to try first, both cited, and 1 safety flag;
`property_status` returned the property's 3 appliances (0.07 s). Only the replay
ledger was written, and no money was spent. Source:
`data/runs/t-bc31dd881bff44c4.client/mcp_evidence_replay.log`, kept with the
script, the only record of this connection's calls. The log ends with the test
script's own crash on its final summary line (it read the protocol version after
the client had closed), after every call had printed; the script was fixed
before the live part. The log names no run ID; the replayed brief is most likely
`data/runs/t-dc8de46ef5f0488c.json`, the one replay run on this build before the
live run, written at 13:41:19 UTC.

**Measured, live** (run `t-bc31dd881bff44c4` on build `17427277685ed433`, 16
October 2026, finished 13:42 UTC; sources: `data/runs/t-bc31dd881bff44c4.json`
and `advisor ledger --run t-bc31dd881bff44c4` for the cost, route and
processing time, and `data/runs/t-bc31dd881bff44c4.client/`, the script and
`mcp_evidence_live.log`, the only source of the per call times and both
preflights):
- Who connected: the same test program, through the official SDK's client over
  stdio on protocol version 2026-07-28, the way Claude Desktop and Claude Code
  connect to a local server. The client declared no forms, so the server used
  confirmation ids.
- No person was in the run. The test program played the owner, under Roanuk's
  approval given in advance at about $0.01: it answered "proceed" only to a
  preflight whose typical estimate was at most $0.02 and whose worst case was
  at most the $0.15 run cap, and it confirmed the unit the plate shows
  (Sundance Spas, Optima 880, serial 100915742, 06/2014) and the code FLO.
- The sequence: (1) `get_service_brief` with the hot tub's clear plate photo
  and "panel shows FLO" returned `confirmation_required` with the preflight,
  typical $0.0109, worst case $0.15, the run cap, in 0.35 s, and nothing was
  sent. (2) After "proceed" it read the plate exactly (SUNDANCE SPAS, OPTIMA
  880, serial 100915742, 06/2014) and paused for the owner to confirm the unit
  and the code FLO, in 5.37 s. The code came from the owner's words ("panel
  shows FLO"), not from the plate. (3) `resume_brief` returned its own
  preflight, typical $0.0105, worst case $0.1497, noting the $0.0003 the plate
  reading had spent, in 0.20 s. (4) After "proceed" it finished with status ok
  in 10.03 s.
- The brief: answered from memory, from facts an earlier lookup verified
  (route graph, 0 searches, 0 Tavily credits), with one confirmed candidate,
  the FLO meaning "Flow Switch Error: Water flow is restricted or the switch is
  stuck.", cited to a dealer page. It had no steps to try first, so the safety
  check was skipped and no Jev call was made.
- Totals: $0.0014 by the ledger ($0.0003 for the plate reading and $0.0011
  for the writing), inside the $0.15 cap (SC11 pass). The four tool calls took
  15.95 s (0.35 + 5.37 + 0.20 + 10.03); the whole session, from starting the
  server to closing it, 17.82 s. The run record's processing time is 12.97 s,
  12.96 s of it in the two model calls (3.25 s reading the plate, 9.70 s
  writing).
- Comparator: the same question answered from memory through the command line
  on 14 October (the FLO repeat, `t-d644c199cbd74532`, build
  `d24ed7c475dcd813`) cost $0.0015 and took 9.8 s. Both runs sent the same
  input (2,862 and 5,317 input tokens); the writer's output was 1,080 tokens
  against 1,274, which accounts for the cost difference, and the server makes
  no paid call of its own. The extra time is in the two model calls (3.25 s
  against 2.02 s for the plate, 9.70 s against 7.74 s for the writing).
- What it does not show: the run made no search and no Jev call, so a
  research brief has not yet run live through the server, and it had no steps
  and no safety flags.

**Measured, Claude Code in replay (free; 16 October 2026, 14:55 UTC, about 73
minutes after the live run):**
- Setup: Claude Code 2.1.299, running Claude Opus 5.5, was given the server
  for one session only, started with `--cassette agent/tests/cassettes/flo.json`,
  so it answered from recorded model replies and spent no money. The session
  used a one session config, `--mcp-config` with `--strict-mcp-config`, so
  nothing was added to Claude Code's settings. It was non-interactive (`-p`),
  and the only tools allowed were the three advisor tools it needed
  (`get_service_brief`, `resume_brief` and `property_status`); each call shows
  as accepted by a config rule, so no approval prompt was shown.
- The prompt was written as an owner would write it: a guest says the hot tub's
  panel shows FLO; the plate photo path; "if it asks me to confirm the unit, I
  confirm what the plate shows: Sundance Spas, Optima 880, serial 100915742,
  made 06/2014, and the code FLO"; then check property prop-a; report what to
  try first with each step's source, any safety flags word for word, the cost
  and the run ID. The kept stream holds the session's events, not the prompt.
- What happened, from `stream.jsonl`: (1) the session connected to the server
  ("status": "connected") and discovered its four tools as
  `mcp__property-advisor__*`. (2) On its own, Claude called `get_service_brief`
  (the photo path, the symptom, code FLO, property prop-a) and
  `property_status` (prop-a). (3) The brief paused for the unit. The server's
  form question (elicitation) was canceled automatically, because the session
  was non-interactive, so the run stayed paused ("The identity question was
  canceled"). The stream does not record which protocol version the session
  used. (4) Claude then called `resume_brief` with the confirmed unit taken
  from the owner's request: the fallback path for apps without forms, shown
  working with a real assistant. (5) The brief came back with status ok: 2
  steps to try first, both cited to dealer pages, and 1 safety flag set by the
  brief's writer. Jev's check shows `not_recorded`, because this older
  recording holds no Jev answers. (6) Claude's answer reported both steps with
  their sources; the safety flag word for word, and that the writer raised it;
  that Jev's check was not recorded; the cost labeled as the replay ledger,
  `real_money` false; the run ID `t-7ecdeb193c584be8`; and the property's
  warranty, product recall and replacement status. It also added its own
  reading. It noted that the plate's serial differs from the synthetic registry
  record, and it called two lines in the brief contradictory and said which to
  trust. That contradiction is a replay artifact: the recorded model text
  predates the unit details given in this session. It is exactly the retelling
  the tool does not control.
- The session took 34.5 s and 5 turns. It cost the project $0: replay, with
  no row in the spending ledger (the replay ledger priced the recorded case at
  $0.0828, labeled as no money spent). Claude Code's own session reported
  $0.2456 of usage on Roanuk's Claude plan, outside the project's ledger; it is
  not a project cost.
- Evidence, untracked and kept out of the public repository:
  `data/evidence/claude_code_replay_20261016/` holds `stream.jsonl` (every
  event), `mcp_config.json`, an empty `stderr.txt`, and two earlier attempts
  that stopped at "Not logged in", before the command line copy of Claude Code
  was signed in. The run record is `data/runs/t-7ecdeb193c584be8.json` and the
  brief `data/briefs/t-7ecdeb193c584be8.html`.

**Pages** (16 October 2026, in the working tree only; nothing is committed or
published):
- `index.html`: Feature 9 in Part 7, "The advisor inside an AI assistant
  (MCP)", sits inside the element with `id="mcp"`, so
  `https://roanukz.github.io/ai-property-maintenance-advisor/#mcp` lands on it.
  A reader arriving there from a resume can follow it without the rest of the
  page: it opens with one sentence on what the advisor does, explains MCP and
  says what has been shown so far; it explains standard input and output, the
  CPSC product recall check, Jev, the build fingerprint, the preflight, replay
  and "from memory" where each first appears. Then come what the build keeps,
  the tests and the replay connection, the live brief with the test program as
  the owner, Claude Code in replay, a warning box with the limits, and where
  each number comes from, with each evidence folder named by its path inside
  the repository and marked as kept out of the public repo. It is a feature,
  not a new numbered part: a new part would renumber Parts 8 to 11, and the
  recorded demo, which is rebuilt only from a frozen build, names "Part 8 of
  the teardown". Appendix B defines MCP and preflight.
- The Summary's method gains a sentence on how this evidence was gathered:
  replay first, then the one approved live brief, run by a test program
  through the official client, then Claude Code in replay. It is kept free of
  the word MCP, so that the first use stays in the results, where the page test
  expects it. The results sentence says what Claude Code did in replay, that
  the live brief cost $0.0014, about the same as the $0.0015 from the command
  line, and that Claude Desktop is untried and no assistant has run a live,
  paid brief. The cautions name the live MCP brief's build. The TL;DR is
  unchanged: it is already at 100 words, and the server is a new way in, not a
  new finding.
- The whole build spend reads $1.5471 in Parts 6 and 8. Part 8's line on the
  server says its refusals were shown in offline tests against fake paid
  services. The counts read 3,650 tests and 1,656 planted bugs on the code of
  build `17427277685ed433` in the Summary, Part 8 and Appendix A. Appendix A
  records the replay first order, the test program as the owner and the
  Claude Code session, its limits line says what has and has not run, and the
  list of builds lost a stray "and". `index.html` was re-hashed in the
  published manifest.
- `README.md`: the bullet defines MCP, preflight and SDK and says what has been
  shown; the results row for the MCP server reads "Shown, with no bar set
  before the build" and gives the replay parity, the live brief and the Claude
  Code session, with the limits and the evidence folders; the results intro
  says the MCP row had no bar; the suite row (3,650 tests, gate phases 5, 6
  and 7), the planted bug count (1,656) and the whole build spend ($1.5471)
  are updated; the live rows that come from other builds name the MCP brief's;
  the section on using the advisor from an assistant states the same limits;
  and the repository list names `agent/mcp_server.py` and `docs/mcp.md`.
- `docs/mcp.md` defines MCP at the start and gives a dated status paragraph:
  Claude Code connected and called the tools in replay; one live brief ran
  through the official client, driven by a test program; Claude Desktop is
  untried; form support per app is unverified (Claude Code's non-interactive
  session canceled the form); an assistant may leave out citations or safety
  flags it was given. It now cites decisions 97 to 104 (decision 99 for the
  shared code), says "With neither" for replay mode, says the recall check
  keeps only recalls that print the exact model, explains elicitation forms,
  says a new checkout creates the ledger once with a live `advisor` command
  and `--new-ledger`, says the server cannot tell whether the owner or the
  assistant supplied the confirmed fields or typed "proceed", and says the
  app's approval prompt is the owner's only check, to keep on and never set to
  always allow, with how each app asks unverified. It uses
  `/path/to/ai-property-maintenance-advisor` in place of this Mac's path.
  `test_style.py` scans only `agent/` and `seed/`; adding `docs/mcp.md` would
  add a planted bug and change the measured counts, so it waits for the next
  round.
- A page test, `test_teardown.py::test_mcp_section_names_its_run_build_and_limits`:
  exactly one element carries `id="mcp"`; MCP is explained where the page
  first names it and inside the section; the section names run
  `t-bc31dd881bff44c4` and build `17427277685ed433` and states five limits (no
  assistant has yet run a live, paid brief, and Claude Desktop is untried;
  form support unverified, and without forms the unit's confirmation and the
  "proceed" pass through the assistant; never set the tools to always allow;
  the assistant's retelling outside the tool's control; safety flags shown on
  the recorded cases, not on the live run); and Appendix B defines MCP. 11
  planted bugs pair with it: `teardown_mcp_anchor_renamed`,
  `teardown_mcp_first_use_unexplained`, `teardown_mcp_section_unexplained`,
  `teardown_mcp_run_dropped`, `teardown_mcp_build_dropped`,
  `teardown_mcp_limit_apps_dropped`, `teardown_mcp_limit_forms_dropped`,
  `teardown_mcp_limit_always_allow_dropped`,
  `teardown_mcp_limit_retelling_dropped`,
  `teardown_mcp_limit_live_flags_dropped` and `teardown_terms_drop_mcp`. When
  the section was rewritten, the test's expected limits changed and the search
  texts of eight of them moved with the text; each of the 11 was confirmed
  caught again with `mutate.py --only`. No test function or planted bug was
  added or removed, so the counts above stand.
- `test_mcp_server.py`'s fingerprint test now cites decision 98.

**Worth recording:**
- A line printed to stdout before serving reaches the stdio wire, and the SDK's
  client only logs it. The stdio test therefore records any line that is not
  the protocol and requires none.
- The paid call sites already redact keys from their errors
  (`agent/redaction.py`); the server's redaction of a failed live run's message
  is a backstop behind them.
- The SDK creates OpenTelemetry spans, but only the OpenTelemetry API is
  installed, with nothing to collect or export them.
- Still open, and stated on the pages: no assistant has yet run a live, paid
  brief, and Claude Desktop has not been tried; `docs/mcp.md` shows how to
  connect each. Which apps show MCP forms to the person is unverified: Claude
  Code's non-interactive session canceled the form it was sent. Without forms,
  the owner's confirmation of the unit passes through the assistant, just as
  the "proceed" does: the server cannot tell who supplied either, and the
  assistant could send either without asking, even a model number the plate
  did not show. In the Claude Code session the assistant did send the unit
  details, taken from the owner's request. So the app's own tool approval
  prompt is the owner's only check, and how each app shows it is unverified;
  the Claude Code session allowed its three tools in advance, so none was
  shown. The assistant retells a brief in its own words, outside the tool's
  control: the structured response carries every citation and safety flag word
  for word, but the assistant may leave some out, or add its own reading, as
  Claude Code did. The live brief came from memory, so it had no steps to try
  first and no safety flags; that safety flags reach the response is shown on
  the recorded cases, not on the live run, and a research brief has not yet
  run live through the server.
- The `advisor-mcp` script appears after the next `uv sync`;
  `python -m agent.mcp_server` works now.

**Next steps, held for the next build:** any edit to `agent/mcp_server.py`
changes the build id that the live run and every result above name, so two
small changes found in review wait for the next build:
- The brief's text summary says "1 documented candidates"; one candidate
  should read in the singular.
- The server's instructions tell the assistant to pass "proceed" only after
  the person has typed it, but say nothing about the unit's confirmed fields.
  They should also tell it never to fill in a field itself: only to pass on
  what the owner confirmed.

**Cost:** $0.0014 for the live MCP brief, with 0 Tavily credits; the replay
connection, the Claude Code replay session and every test spent nothing from
the project's ledger. The build has spent $1.5471 of its $5 cap and 242 of its
300 Tavily credits.

---

## Two fixes to what the MCP server says, 16 October 2026

**Decided:**
- **105. The server's words.** Result messages now count in the singular at 1
  ("1 step", "1 flagged as a safety step", "1 documented candidate", "1
  appliance"); the live log of 16 October had printed "1 documented
  candidates". A paused brief's next step, and the server's standing
  instructions to the assistant, now say to show the owner what was read, ask
  them to confirm or correct it, and never fill in or change a field itself,
  because the server cannot tell who supplied the fields `resume_brief`
  receives. An assistant can still ignore the instruction, so the app's own
  approval prompt before each tool call stays the owner's check, as the
  teardown says.
- These are edits to runtime code, so the build moves from
  `17427277685ed433` to `841df66ad9669ef8`. The published MCP evidence (the live brief
  `t-bc31dd881bff44c4` and the Claude Code replay session
  `t-7ecdeb193c584be8`) stays on `17427277685ed433`, and the second PRD's
  results on `d24ed7c475dcd813`, as the demo stayed on its build after
  decisions 68 and 93.

**Measured:** 2 new tests, each in gate phase 7, and 4 planted bugs, each
confirmed caught with `mutate.py --only`.

**Cost:** $0.

---

## Claude Desktop runs one live brief, 16 October 2026

Claude Desktop ran the advisor's MCP server for one live, paid brief: the first
live brief through an assistant, and the first MCP brief with a person reading
the preflight and typing "proceed". Roanuk chose the trial and approved the
money in advance ("proceed, use the money"), then read the preflight and typed
"proceed" himself in the Desktop chat. The brief ended with no reliable answer,
so no live brief through an assistant has yet carried steps to try first or
safety flags.

**Decided:**
- **106. The Claude Desktop trial, reported as it ran.** The refusal stands
  under the existing rule (prompt rule 6: documentation for this exact model
  or its documented family, never general knowledge of the equipment
  category), and nothing in the code changes: no file under `agent/` outside
  `agent/tests/` was edited, and `build_info.build_id()` is still
  `841df66ad9669ef8`. Claude Desktop goes back to replay after the trial. The
  pages report the run, its refusal and what Desktop added outside the tool,
  with the limits the trial leaves open.

**Measured, live** (run `t-7f23f74a338140bd` on build `841df66ad9669ef8`, 16
October 2026, finished 16:21:37 UTC; sources, all untracked and kept out of the
public repository: `data/runs/t-7f23f74a338140bd.json` and
`data/evidence/claude_desktop_live_20261016/ledger_t-7f23f74a338140bd.txt`, the
ledger's readout of the run, for the cost, route, searches and processing time;
`data/evidence/claude_desktop_live_20261016/mcp-server-property-advisor.log`,
Claude Desktop's own log of the server's messages, and
`mcp.log.property-advisor-lines`, the same lines from Desktop's MCP log, for
each call's times; `desktop_transcript.txt`, the Desktop chat as Roanuk pasted
it; `desktop_mcp_entry_live.json`, the server entry Desktop ran, with the
repository path replaced by `<repo>`; and the brief's page,
`data/briefs/t-7f23f74a338140bd.html`):
- Who connected: Claude Desktop 2.42304.0 on macOS, which started the server
  from its own settings file with live spending allowed in cheap mode
  (`--live`, `ADVISOR_MODE=cheap`), on build `841df66ad9669ef8`, which
  includes decision 105.
- The request, in Roanuk's words: "The water heater at my rental isn't making
  hot water. Use the property-advisor tools to get me a service brief for
  appliance appl-waterheater at property prop-b." There was no plate photo:
  the unit came from the property's synthetic registry record (a Rheem PRO+E50
  M2 RH92 CL electric water heater), so there was no plate reading and no
  pause to confirm the unit, and decision 105's instruction (never fill in a
  field) was not put to Desktop.
- (1) `get_service_brief` returned the preflight and sent nothing, in 0.41 s
  in Desktop's log (16:20:24.046 to 16:20:24.456 UTC): typical $0.0105 and
  worst case $0.15 against the $0.15 cap; model calls typical 7, at most 17;
  Tavily credits typical 5, at most 10; Jev calls 8 planned for one draft, up
  to 16 for two; the whole build at $1.5471 of its $5.
- The server sent no form: it took the path for apps without forms. Desktop
  showed the preflight as text in the chat and asked Roanuk to type proceed,
  and its log shows no request from the server to the app, only results. So
  "proceed" passed through the assistant, the fallback way:
  `confirmation_required`, then a call with `confirm="proceed"` and the
  `confirmation_id`. Roanuk typed it himself; the next call reached the server
  25.7 s after the preflight left it, a gap that includes the assistant
  writing its message and his reading and typing. Desktop's log does not
  record which protocol version it used.
- (2) The call with "proceed" took 47.50 s in Desktop's log (16:20:50.161 to
  16:21:37.656 UTC). The run record gives 43.45 s of processing, 26.06 s of it
  in research and 17.30 s in writing the brief. Route: history and research
  (the graph had nothing verified for this unit or its family; the registry
  held 1 record for the appliance). 5 web searches, 3 page fetches, 7 Tavily
  credits. $0.0061 by the ledger (0.0060734), against the preflight's typical
  $0.0105, inside the $0.15 cap (SC11 pass).
- The brief: `no_reliable_answer`, refused by the model. Its reason, from the
  run record: the only manuals found belong to sibling models with different
  suffixes (RH95 EC1), the retailer and marketing pages describe no
  procedure, and matching the symptom would need general knowledge about
  electric water heaters, which the brief does not use. Jev's check was
  skipped, because there were no steps to check. No steps, no safety flags.
- (3) Claude Desktop then called `property_status` for prop-b on its own (0.09
  s in its log, 16:21:57.247 to 16:21:57.335 UTC), offline and free, and
  searched the web itself (its chat shows "searched the web").
- Claude Desktop's answer reported the refusal and its reason correctly, the
  cost ($0.0061 and 7 credits, 51 of 300 left) and the location of the brief's
  page, and said the property's record is synthetic. Then it added its own
  content, outside the tool: warranty terms of closely related models from the
  maker's product pages, with an inference that parts are probably still
  covered; troubleshooting steps it labeled as general electric water heater
  knowledge, "not from the advisor"; and its own pick of what to do. Before
  the run it had also added a safety caution of its own. It suggested logging
  the sibling suffix refusal in the decision log, which this entry does. So
  the tool refused to fill the gap from general knowledge, and the assistant
  filled it, labeled as its own: the retelling limit the pages already state,
  now seen in a live run.
- After the run, the whole build has spent $1.5532 of its $5 and 249 of its
  300 Tavily credits. Claude Desktop's own usage on Roanuk's Claude plan was
  not measured and is outside the project's ledger.
- Afterward Desktop's settings were switched back to replay (`--cassette
  agent/tests/cassettes/flo.json`). Claude Desktop rewrote its settings file
  from its own copy once, which put live mode back, so the switch was made
  again; it takes effect when Desktop next starts.

**Pages** (16 October 2026, in the working tree only; nothing is committed or
published):
- `index.html`, the MCP section (`#mcp`): the opening now says what has run so
  far (Claude Code in free replay; one live brief through the official client,
  driven by a test program; one live brief in Claude Desktop, with Roanuk
  reading the preflight and typing "proceed", which ended with no reliable
  answer). A new paragraph pair, "One live brief in Claude Desktop, with me as
  the owner.", follows the Claude Code paragraph and opens with one sentence
  on decision 105's two fixes. The test program's paragraph no longer says a
  research brief has not run live through the server, and the paragraph on
  the offline tests no longer says the 2025-11-25 version has run only in
  them, since Desktop's log does not record which version it used. The
  warning box now says that Desktop has run one live brief but no live brief
  through an assistant has carried steps or safety flags; that no form has
  reached a person outside the tests and the server sent Desktop none; that
  the pause to confirm the unit has not been put to an assistant since
  decision 105; that the app's approval prompt and how each app shows it are
  unverified, Desktop included; and that the retelling is outside the tool's
  control, with Desktop's labeled general advice as the live example. "Where
  each number comes from" names the Desktop evidence by its paths inside the
  repository and says the Desktop preflight's figures come from the chat.
  The server's counts read 31 test cases, 27 through the official client (26
  in process, 1 over real standard input and output), and 50 planted bugs for
  the server and its gate setting.
- The Summary's method adds the Desktop brief, still free of the word MCP; the
  results replace "Claude Desktop is untried" with the Desktop brief, its
  refusal and its labeled general advice; the cautions trace the Desktop brief
  to build `841df66ad9669ef8` and give 3,659 tests on that build. The TL;DR is
  unchanged.
- Part 6 and Part 8 give the whole build spend as $1.5532 and 249 of 300
  Tavily credits, counting the two MCP briefs; Part 8 and Appendix A give
  3,659 tests and 1,663 planted bugs on the code of build
  `841df66ad9669ef8`; Appendix A traces the Desktop brief to that build, adds
  it to the replay first order and rewrites its limits line; Appendix B's
  build entry names it. `index.html` was re-hashed in the published manifest.
- `README.md`: the MCP bullet, the results intro, the suite row (3,659 tests;
  phase 7 lists 22 test functions, for 392 required node ids), the spend row,
  the MCP row (decisions 97 to 106, the Desktop run, the limits and the
  evidence), the planted bug count (1,663) and the section on using the
  advisor from an assistant.
- `docs/mcp.md`: the status paragraph adds the Desktop brief and its limits;
  "Adding it to Claude Desktop" no longer says "Not yet tried", and says that
  the server sent Desktop no form for the preflight and that Desktop may
  rewrite its settings file while it runs, so the file is edited with Desktop
  closed or edited again after quitting.
- `test_teardown.py::test_mcp_section_names_its_run_build_and_limits` now also
  requires the Desktop run `t-7f23f74a338140bd` and build `841df66ad9669ef8`,
  and its limits match the new warning box, including the pause not yet put
  to an assistant. The search texts of `teardown_mcp_limit_apps_dropped` and
  `teardown_mcp_limit_live_flags_dropped` moved with the text. No test
  function was added or removed. Three planted bugs were added, one for each
  new check: `teardown_mcp_desktop_run_dropped`,
  `teardown_mcp_desktop_build_dropped` and `teardown_mcp_limit_pause_dropped`,
  each confirmed caught with `mutate.py --only`; they add 3 manifest test
  cases, so the suite has 3,659 tests and the full run 1,663 planted bugs.

**Cost:** $0.0061 for the live brief, with 7 Tavily credits; Claude Desktop's
own usage on Roanuk's Claude plan was not measured and is not a project cost.
The build has spent $1.5532 of its $5 cap and 249 of its 300 Tavily credits.

**Next steps:**
- A plate photo through Claude Desktop, to put the pause to confirm the unit,
  and decision 105's instruction never to fill in a field, to an assistant.
- A live brief with steps to try first and safety flags through an assistant,
  to show the flags reaching the response, and the retelling, live.
- Whether any app shows the server's forms (elicitation) to the person; the
  server sent Claude Desktop none, so this trial did not test it.
- Whether a sibling model's manual (the same line with a different suffix,
  here RH95 EC1 for an RH92 CL) can ever count for a unit. That is a rule
  change for Roanuk to decide, not something this trial decides.
