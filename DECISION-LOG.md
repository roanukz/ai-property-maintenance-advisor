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
