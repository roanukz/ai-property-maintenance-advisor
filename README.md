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

## Repository

```
index.html            the teardown essay
tool.html             the recorded demo
briefs/               four real generated briefs, self contained
demo-assets/          the plate photographs used in the recorded run
src/tokens.css        shared design tokens, dark mode included
src/teardown.css      essay styles
src/demo.css          demo styles
src/demo.js           replay logic, no network
src/fixtures.js       recorded output from the live run
DECISION-LOG.md       what was decided and why
```

The application source is not in this repository. What is here is the product
record and the evidence.

## Status

This is a validation instrument, built to answer one question: does a cited
brief change what a technician brings to the job. No technician has used it
yet, and answering that is the next step rather than a footnote.

## License

MIT, see `LICENSE`. The retrieved documentation quoted inside the generated
briefs belongs to its publishers and is cited to them.
