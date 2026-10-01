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
