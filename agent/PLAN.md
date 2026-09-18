# Property Maintenance Advisor v2: Phase 0 plan

Written 17 September 2026 for Roanuk Zaman's review. Before reviewing it, you
said to continue building, so Phase 1 (replay only, $0) proceeds on the
recommendations in the decision table below. Every decision stays open for your
review at the Phase 1 gate, anything built on a recommendation you reject gets
reworked, and no LIVE phase starts without your typed "proceed".

How to read the evidence citations below:

- `PRD:n` is a line in `docs/PRD-v2.md`.
- `v1 lib/x.ts:n` is a line in the private v1 source. Its location is not
  written in this repository on purpose (see risk R14). Locally it is read from
  the environment variable `V1_BRIEFCASE_DIR`.
- Library claims cite the release tag or PyPI version checked on 17 September
  2026. "Unverified" means the claim was not checked against a live source or a
  run, and it should not be relied on until it is.
- Nothing was executed against a paid API during Phase 0. The plan was first
  written from source read at the release tags, because no Python 3.10 or later
  was on this Mac. Late in Phase 0, at your direction ("you'll have to install
  langchain and langgraph"), uv, CPython 3.12.14 and the pinned packages were
  installed into a gitignored `.venv`. The installed versions match section 2.1
  exactly, and section 3.1 records what was then confirmed by running the
  installed code offline.

---

## 1. Summary

v2 rebuilds the v1 service brief tool in Python as a LangGraph `StateGraph`
with one human confirmation pause, a SQLite checkpointer so a paused run can be
resumed by a different process, a LangChain `create_agent` research step that
searches with Tavily under hard call and spend limits, a per property appliance
registry with service history, and a NetworkX knowledge graph that grows only
from validated briefs so a repeat question can skip most searching. Every v1
guarantee stays in plain Python and is tested offline: citations resolve,
NO RELIABLE ANSWER clears candidates and steps, tiers can be lowered by code but
never raised, a panel code narrows the answer, and model text is escaped. The
default mode is `replay`, which uses a custom fake chat model and recorded tool
results and costs $0. Live spend is capped in code at $0.15 per `cheap` run and
$5 for the whole build. The cap is checked before each call is made, so it can
be exceeded by at most one call's input estimate error (a few tenths of a cent
in `cheap`; section 8.9), and every live run record states whether its realized
total stayed at or under the cap.

This plan revision (17 September 2026) applies the findings of an adversarial
review of the first draft. Where a finding changed a design choice rather than
fixing an error, the change is a numbered decision below, not a settled fact.

Claude makes no commits and no pushes. In this plan, "tracked" or "written for
commit" means a file intended to be tracked by git; a commit happens only when
you ask for one (section 12).

### Decisions needed at this gate

Each has a recommendation. Items 1 to 6 block Phase 1. The rest can be
answered at this gate or at the phase named. A recommendation in section 11
(contradictions and risks) takes effect only once its row in this table is
approved; approving the plan as a whole does not adopt a section 11 item that
has no row here.

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Python environment.** Resolved during Phase 0 at your direction. The uv managed CPython 3.12.14 that the planning pass found at 22:43 was installed by Claude in this session: Homebrew started compiling OpenSSL from source for `uv` (no Intel bottle) and was stopped, then `uv` 0.12.16 was installed from its PyPI wheel into the user site (`~/Library/Python/3.9/bin/uv`), then `uv python install 3.12`, then a `.venv` in the repo. | Keep it. Phase 1 adds `pyproject.toml`, `.python-version` and `uv.lock` (tracked when you ask for a commit). Section 2.2. |
| 2 | **SC1b depends on the private v1 source.** The differential test must load v1 `lib/brief-validate.ts`, which is not in this repo and should not be. | Keep v1 private. The test reads `V1_BRIEFCASE_DIR`, and on a fresh clone it skips with a stated reason. Track only the payload JSON, which uses the fabricated Aquarest model and example domains, and only after it passes the privacy diff and your approval like any cassette (section 8.10). SC1 is then reported as "passes; SC1b ran" or "passes; SC1b skipped, v1 source absent". A skipped SC1b does not meet the Phase 2 gate: the gate run must show SC1b ran on your machine against the v1 source, with the payload count and the difference list (section 10). |
| 3 | **Three v1 guardrail tests become obsolete** (tests 11, 12 and 14: fence parsing, no fence parsing, citation split reassembly). Test 13 changes meaning. | Approve at the Phase 2 gate as listed in section 6. |
| 4 | **Schema deltas beyond the two the PRD names.** `budget_stopped` status, source `host`, `retrieved_at`, `origin`, `proposed_tier`, `happened_before.record_id`, and verbatim `evidence` quotes on candidates, upgrade options and maintenance items. | Approve the list in section 7. Each is additive, so v1 payloads validate the same way. |
| 5 | **Tier ceiling rule for third party hosts.** PRD decision 9.2 settles the display but not when a manual on an archive host may keep `manufacturer`. Code cannot verify "a document the maker authored" from the host alone. | Keep the three published tiers. `manufacturer` needs the maker's own domain, or an authorship quote (verbatim in the fetched text and naming the maker) that code verifies. Otherwise the ceiling is `dealer`. Known forum hosts are always `forum`. Host shown next to the badge. Section 8.6. |
| 6 | **Cassettes and the promise in `.gitignore`.** The comment says never publish the request log. v1 lookups hold 248 search results (141 unique URLs) beyond what is already public. | Track only fields that are already public in `src/fixtures.js` and `briefs/` (the briefs, the 18 cited URLs, the model written titles, typed identities, symptoms, the public plate hashes). Uncited search results stay out, and synthetic decoys, labeled synthetic, replace them. You see a privacy diff before anything is written for commit. Section 8.10. |
| 7 | **Observed code rule in code.** v1 enforced it by prompt only. | Code sets `observed_code` from confirmed state. Exactly one matching candidate: keep it, mark it confirmed, drop the others. More than one match: keep matches, none confirmed. Zero matches: validation failure. No observed code: nothing is confirmed. Section 8.5. |
| 8 | **Safety ordering.** Two recorded v1 briefs put a safety step last. | Reorder (stable sort, safety first). Do not reject. Listed as a proposed SC1b and SC2 difference (decision 21). |
| 9 | **What a refusal and a budget stop clear.** v1 clears only candidates and steps. | On NO RELIABLE ANSWER and on `budget_stopped`, clear candidates, try_first, upgrade_options and maintenance_due. Keep warranty and happened_before as v1 did. |
| 10 | **Search cap behavior.** Hitting 5 searches can end the agent (budget stop) or tell the model to stop searching and answer. | Search and fetch caps tell the model to stop using that tool (`exit_behavior="continue"`), and a `before_model` hook ends research once both tool caps are spent. The model call cap is only a loop guard (search cap plus fetch cap plus 2); hitting it ends research with the sources already collected, not a budget stop. Only the ledger's run and build dollar caps, the credit caps and Tavily's plan limit produce `budget_stopped`; the research sub budget (decision 26) ends research, it does not stop the run. This amends the first draft, where the model call cap produced `budget_stopped` and would have stopped normal runs that used their full tool allowance. Section 8.7. |
| 11 | **SC3b wording.** SC3b changes the model, search provider, search cap and pipeline at once, so it cannot settle v1's latency question. | Reword SC3b to "informs, does not settle", record latency and whether the refusal came from the model or was forced by validation, and count `budget_stopped` as a failed run. Section 11, C3. |
| 12 | **Phase 2 gate wording.** "SC1 to SC5 pass" cannot hold: SC3b is LIVE in Phase 6 and five v1 tests need the Phase 4 renderer. | Phase 2 gate: SC1 for behaviors ported so far, SC1b, SC2, SC3a, SC4, SC5. |
| 13 | **The seeded discontinued model (SC9) and the third real model.** The v1 fixtures hold 2 real models, not 3; none of the recorded content covers discontinuation. A hand authored SUPERSEDED_BY edge would break "edges only from validated briefs". | SC9 runs on a synthetic replay fixture kept in `agent/tests/fixtures/`, not in `seed/`. The registry holds a discontinued appliance labeled synthetic whose upgrade options come only from a lookup. You name the third real model at Phase 1 (proposal: a common residential water heater model chosen from public data). Do not register the fabricated Aquarest model, so SC3b stays a clean refusal. |
| 14 | **Haiku 4.5 retirement.** Active today, retirement "not sooner than October 15, 2026" (28 days). | Run Phases 5 and 6 before 15 October 2026. If Haiku 4.5 is retired first, stop. A Sonnet 5 fallback costs about 2.6 times as much (2x price, about 1.3x tokens from the newer tokenizer, plus adaptive thinking billed as output), about $0.22 per research route run, and under the ledger every research route run would end `budget_stopped` (research alone about $0.13, synthesize reservation about $0.106). It needs a new cap you approve, or thinking disabled and lower research caps, before any live run. |
| 15 | **Page text for evidence spans.** v1 recorded no page text, so SC6 and the code grounding check have nothing real to check against offline. | Hand written synthetic page text, labeled synthetic and attached only to example URLs, for Phases 3 and 4. Real text arrives with Phase 6 recordings, kept in gitignored runtime storage; only the verified evidence sentences and a sha256 of each page are tracked. Because page text is not tracked, `seed/graph.json` cannot be fully re verified on a fresh clone; full re verification with page text present is required at the gate that promotes edges into `seed/` (section 8.11). |
| 16 | **Tavily in the ledger.** Tavily is free up to 1,000 credits a month, paid after. | Ledger counts Tavily credits separately at $0, with a cap of 10 credits per run and 150 for the build. The run stops with `budget_stopped` if Tavily reports the plan limit ("Error 432" or "Error 433" inside the error value, section 8.7). At the Phase 5 gate you record the Tavily plan and whether pay as you go is off. If it is on, 432 may never arrive, so credits are priced at $0.008 each and count toward the $5 build cap. |
| 17 | **v1 strings with em dashes.** The prompts, refusal intro, placeholders and page title contain U+2014, which the repo style forbids. | Approve the replacements in section 6.4. Verbatim model text quoted from sources is left untouched. |
| 18 | **`full` mode.** SC11 caps only `cheap`. No phase runs `full` live. | Build `full` and test it in replay only. Proposed per run cap $0.30. No live `full` run without a separate approval. |
| 19 | **Committing `docs/PRD-v2.md`.** It is untracked and contains the private v1 folder path and notes about the API key's credit. | Do not track it as is. Track a redacted copy when you ask for a commit, or keep it local. |
| 20 | **Date style in new log entries.** The repo uses "18 August 2026"; the PRD uses "September 17, 2026". | Use the repo's style, "17 September 2026". |
| 21 | **Every proposed difference from v1, in one place.** The first draft called several differences "approved" before you had approved any. | Approve or reject each, by gate. **Phase 2 (SC1b and SC2):** `budget_stopped` status; safety reordering (decision 8); the observed code rule (decision 7); tier lowering to `dealer` (decision 5, R20); clearing upgrades and maintenance on refusal (decision 9); code built `searched` (decision 4). **Phase 4 (renderer, SC10 goldens):** apostrophe escaping; CSP meta; `data:` favicon; host text next to the badge; per source `retrieved_at` on each source line; the refusal and budget stop footer (decision 47); dash replacements (decision 17); the new sections (budget stop box, upgrade options, maintenance due); pruning and renumbering of uncited sources. The approved difference files (sections 5 and 6.3) are written only after you approve, one row per difference. |
| 22 | **The model facing draft must fit Anthropic's structured output limits.** The docs cap a request at 24 optional parameters and 16 union (`anyOf`) parameters, and return HTTP 400 "Schema is too complex for compilation" past an undisclosed internal limit (platform.claude.com/docs/en/build-with-claude/structured-outputs.md, fetched 17 September 2026). langchain-anthropic 1.7.2 passes the Pydantic schema through anthropic 1.6.0 `transform_schema`, which keeps `anyOf` and copies `required` unchanged, so every Pydantic default counts as optional. A draft built like the lenient parity models counted at least 27 optional and 17 union parameters in review. Replay cannot catch this because `ReplayChatModel` never compiles a grammar. | Keep two model sets. The **parity models** keep v1 defaults and are never sent to the API. **`BriefDraft`** is flat and every field is required: free text that may be absent (`detail`, `why_shown`, `summary`, `evidence`, `authorship_quote`) is a required string where empty means none, converted to `None` by code. An offline test counts optional and union parameters both per `$defs` entry and per use site and asserts at most 0 optional and at most 16 unions (target 12 or fewer). "HTTP 400 on the schema" is a Phase 5 stop condition, and Phase 5 checks schema acceptance with one minimal synthesize call before any research spend (section 9). Section 7.3. |
| 23 | **Validation retry in `cheap` mode.** PRD:147 says a failed validation "sends the run back to research once". At the first draft's numbers a full retry pass never fits under $0.15, so every first failure would have become `budget_stopped`, and "forced after a retry" could never happen live. | In `cheap`, the retry is a reduced pass: search 2, fetch 0, model calls 4 (its own `config.py` row, separate from the graph top up row), plus one synthesize call that sees the errors. The retry is taken when the remaining run budget is at least the retry pass's typical estimate from `config.py` plus the full synthesize reservation (about $0.016 + $0.048 = $0.064), so it fits when first pass spend is at or under about $0.086; a typical first pass ($0.083) fits narrowly, and one with a fetch or at the caps does not (section 9). Per call reservations still hold as the hard cap, so a retry that starts can still end `budget_stopped` partway. Credits: 8 + 2 = 10, the run cap. If you prefer no retry in `cheap`, that is logged as a PRD:147 difference and "forced after a retry" leaves the SC3b wording (C3). Section 8.4. |
| 24 | **What the models see of a page.** Evidence quotes must be verbatim in `raw_content`, but in the first draft no model ever saw `raw_content`, only Tavily's snippet (up to 3 chunks of at most 500 characters joined by " [...] "). Whether chunk text matches `raw_content` under the strict normalization is unverified. | Code cuts verbatim excerpts from `raw_content` (windows around the model, family, observed code, part numbers and symptom terms) under a per source character budget, and gives those excerpts to synthesize, and to the research model for fetch results, as the source text. The synthesis prompt says to quote only from the excerpts. Phase 5 measures how often snippet chunks match `raw_content` (R4). Section 8.7. |
| 25 | **Code grounding when a cited source has no page text.** The first draft passed any code as "unverifiable" whenever no text was recorded, which in a live run would let an invented code through on a PDF cited from snippets (R4). | "Unverifiable" is allowed only in replay, and only for cassettes whose `provenance.derived_from` names a v1 lookup. Everywhere else, recommended: when the cited source has no raw text, the code must appear in its Tavily snippet; if it is in neither, validation fails. Stricter alternative: any non null code on a source with no raw text fails. Section 8.5 rule 4. |
| 26 | **Research sub budget.** At the tool caps, research can cost enough that synthesize's reservation no longer fits, which would end a normal run `budget_stopped` with no draft. | `RESEARCH_BUDGET_USD` 0.09 in `config.py`, so read_plate $0.0033 + research $0.09 + synthesize reservation $0.048 = $0.141 always fits under $0.15. Reaching it ends research with the sources collected so far (like a tool cap) and goes to synthesize; it is not a budget stop. |
| 27 | **Where rules run.** PRD:147 says validate is "the only place rules are enforced", but the PRD diagram (PRD:138) puts upgrade_check after validate. In the first draft upgrade_check filtered entries and computed due dates after validate had already cleared refusals and pruned sources, so graph derived options could reach a NO RELIABLE ANSWER brief or cite unchecked sources. | Every rule function lives in `agent/rules/` and is called only from validate. upgrade_check runs only on `ok` briefs, only collects candidate upgrade options from graph edges (adding their graph sources to the registry), and sends the draft back through validate once (citation, evidence, clearing, prune and renumber). Two named exceptions stay outside validate: read_plate's `unreadable` nulling (PRD:142) and the persist time edge evidence check (PRD:16, PRD:148). Sections 8.3 to 8.5. |
| 28 | **Prices.** PRD:119 says the tool "never estimates prices unless a source gives them", and does not say whether a source stated price may appear. | Recommended: a price may appear only when a cited source states it. A validate rule requires any currency amount in model written text (`summary`, `why_shown`, `detail`, `age_statement`) to appear inside a verified evidence quote from the cited source; otherwise the upgrade or maintenance entry is dropped, and in a v1 field it fails validation. None of the 4 published briefs contains a dollar figure, so goldens are unaffected. |
| 29 | **Purchase date and warranty records in the brief.** G3 (PRD:77) asks for them; v1 rule 8 has the model compute age from the manufacture date only. | Code computes the unit's age from the registry install or purchase date when one exists, falling back to the manufacture date, and cites the registry record by `record_id` as `happened_before` does. Warranty terms appear only as quoted from the registry, with the record ID. `warranty.age_statement` becomes code output, and rule 8 changes to match (section 8.8). |
| 30 | **Prompt changes beyond search tool, fence and dashes.** PRD:26 allows only those. | Approve each row of the section 8.8 table at the Phase 2 gate: rules 2, 3, 5, 6, 8 ("fetched" to "provided"), rule 6 (drops "Do not invent sources"; code builds the trail), rule 8 (decision 29), rule 9 (service records and `record_id`), and the new verbatim quote instruction (decision 24). |
| 31 | **Tracing switch.** PRD:113 wants LangSmith tracing controlled by an environment variable and off by default. The first draft scrubbed tracing variables only in tests. langsmith reads `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING`, `LANGCHAIN_TRACING` in that order and caches the lookup (langsmith main `utils.py`; the 0.12.6 tag was not rechecked). An upload also needs a LangSmith API key, so a stray flag alone does not send data. | `ADVISOR_TRACING=1` is the only opt in. `cli.py`, before any langchain or langsmith import, unsets the four tracing variables and `LANGSMITH_GATEWAY` unless it is set. When opted in, `hide_inputs` keeps the photo and registry fields out. An offline test runs the CLI entry point with `LANGCHAIN_TRACING_V2=true` and a dummy `LANGSMITH_API_KEY` and asserts `tracing_is_enabled()` is False. |
| 32 | **How the SC1b harness loads `brief-validate.ts`.** A plain import makes Node read the `type` field of the nearest `package.json`, which sits in the private v1 folder outside the PRD:22 allowlist. Only that metadata is read and nothing is copied. | Recommended: allow the plain import (the Phase 0 reader already ran the v1 suite this way). Alternative: read the file as text, strip types with `node:module` `stripTypeScriptTypes` (stability unverified), and import it from a `data:` URL, with its own harness test. Section 2.3. |
| 33 | **Does a forced refusal count toward "refuses 3 of 3"?** A forced refusal means the model answered and validation refused it twice. | Recommended: no. SC3b counts only model refusals as passes, and the evaluator reports model refusals, forced refusals and budget stops as separate headline numbers. |
| 34 | **What SC7b reports.** Section 9 plans three repeats, and the FLO repeat asks exactly the question of its first lookup. | Report every repeat with its route, its search count from `search_trail`, and the search count of the same model's v2 first lookup. SC7b passes only if every repeat uses 2 or fewer searches. The FLO repeat is labeled "identical question; graph route caps searches at 0 by design". The Trane repeat symptom is fixed now, different from the first lookup ("AC not cooling upstairs"): proposed "outdoor unit runs but the fan does not spin". N in the resume line is the range across all repeats. |
| 35 | **Reruns.** The $5 cap leaves room for a rerun, with no rule for what is reported. | Every paid run is recorded in the ledger and listed in its phase's decision log entry. A rerun needs a logged code fix that names the defect; the fixed build's runs are then the only reported SC3b or SC7b result, and the superseded runs appear only in the decision log and the ledger, never in the README results table. A rerun of an unchanged build is pooled with the first runs (for example "5 of 6") and never substituted for them. |
| 36 | **v1 live case B1 (vague symptom).** PRD:31 makes the 6 v1 live cases the Phase 5 and 6 suite; B1 appears only as a graph top up repeat. | Your call. Adding a B1 first lookup on the research route costs about $0.08 (worst $0.15, 8 credits) and lets its v1 assertions run on a research route (section 6.5). |
| 37 | **Replay runs never grow the runtime graph.** PRD:110 and PRD:182 grow the graph only from live lookups. | persist writes graph edges only when the mode is `cheap` or `full` and the run has live ledger charges. Replay writes to the `RunContext` graph path: a temp file in tests, `data/replay_graph.json` from the CLI. `seed/graph.json` is built only from edges whose `brief_run_id` matches a live ledger row. |
| 38 | **Tavily client.** langchain-tavily 0.2.18 calls `requests.post` with no HTTP timeout and returns non 200 responses (including 432) as `{"error": ...}` content. A 30 second worker thread timeout abandons a request that can still use a credit, and `concurrent.futures` workers are joined at exit, so a hung request can hang the CLI. | Recommended: keep langchain-tavily (PRD:163 names it). Run it on a daemon `threading.Thread`, count one credit for any abandoned request that may have reached Tavily, and parse the status from the error text. Alternative: call the Tavily REST endpoint directly with an explicit HTTP timeout, which reads the real status code but drops a library the PRD names, so it is a PRD difference. |
| 39 | **Phase 1 schema wording (C2).** PRD:42 says "matching `fixtures.js` exactly", PRD:29 says `types.ts`. | Adopt the section 7.1 wording for the Phase 1 row. |
| 40 | **SC1 wording (C5).** PRD:85 refers to "17 v1 behaviors listed in the decision log and teardown". | "each of the 17 v1 guardrail tests, ported or marked obsolete with approval, enumerated in agent/PLAN.md". |
| 41 | **SC6 and graph sources (C8).** PRD:92 says "retrieved in that run"; the graph route may run 0 searches. | Allow sources loaded from validated graph edges in that run, with origin `graph` and their original `retrieved_at`. This relaxes SC6 as written. |
| 42 | **SC10 wording (C9).** PRD:97 says SC10 "closes" the v1 injection finding; v1 already fixed it. | "keeps the v1 fix for". |
| 43 | **Demo invariant (C10).** PRD:17 says "zero network requests"; the demo loads its own files. | The demo requests nothing from any origin but its own and never calls fetch, XHR, WebSocket or sendBeacon; enforced by `test_published_pages.py` (section 12). |
| 44 | **Wording fixes C11 to C14.** | C11 "logged, but nothing reads them back"; C12 "null in all 4 recorded briefs"; C13 compare a v2 first lookup against v2 repeats of the same model, same mode (decision 34); C14 record `refusal_origin` and count forced refusals separately (decision 33). |
| 45 | **INSTALLED_AT and IS_MODEL are not persisted (R9).** PRD:148 says every edge stores a URL and evidence sentence; these two come from the registry and have none. | Build them at load time from SQLite and keep them out of the persisted graph. |
| 46 | **Graph plus research top up route.** Not one of PRD:144's routes. | Adopt it (section 8.4 rows 2 and 4), with a search cap of 2, fetch cap of 1 and loop guard of 5. |
| 47 | **Refusal and budget stop footer.** The v1 footer says the brief "reproduces documented material", which is untrue on a refusal. | Refusal and budget stop briefs get a footer without that phrase (section 6.3). |
| 48 | **`advisor ledger` command.** Goes beyond PRD:106. | Adopt it (section 8.13). |
| 49 | **`advisor eval` command.** Live SC3b and SC7b runs cannot run inside pytest, which blocks the network and scrubs keys. | Adopt `advisor eval sc3b --live` and `advisor eval sc7b --live` (section 8.13); pytest scores their run records offline. |
| 50 | **Reversal of the published cuts (C17).** The teardown cut multi property support and warranty tracking; G3 and the seed properties bring them back in Phase 1. | Log the reversal with its reason in the Phase 1 decision log entry, when the seed properties and warranty records arrive, not in Phase 7. |
| 51 | **Where generated pages go.** The first draft gave no default for `--html`. | Default outputs to `data/briefs/<run_id>.html` and `data/property/<id>.html` (gitignored). The CLI refuses any `--html` path inside `index.html`, `tool.html`, `src/`, `briefs/` or `demo-assets/`. |
| 52 | **Where the ledger lives.** In the first draft it sat in `data/` beside files a reset might clear, and a cleared ledger would allow another $5. | `data/ledger/ledger.sqlite`, documented as never deleted. The CLI refuses a live run when the ledger file is missing unless `--new-ledger` is passed. Each LIVE phase's decision log entry records cumulative live spend, compared with the console at the gate. |

---

## 2. Environment and versions

### 2.1 Packages to pin

All versions are the latest on PyPI as of 17 September 2026 (source:
`https://pypi.org/pypi/<name>/json`). `uv lock` resolves the exact graph and
hashes in Phase 1; any change from this table is reported at the Phase 1 gate.

| Package | Pin | Released | requires_python | Why it is here |
|---|---|---|---|---|
| langchain | 1.4.1 | 2026-09-16 | >=3.10,<4 | `create_agent`, middleware. Pins `langgraph>=1.2.11,<1.3.0`. |
| langchain-core | 1.6.3 | 2026-09-11 | >=3.10,<4 | messages, tools, `ModelError` types (new in 1.6.0) |
| langgraph | 1.2.11 | 2026-08-11 | >=3.10 | `StateGraph`, `interrupt`, `Command` |
| langgraph-checkpoint | 4.2.0 | 2026-08-07 | >=3.10 | serializer, `InMemorySaver` for tests |
| langgraph-checkpoint-sqlite | 3.1.1 | 2026-07-30 | >=3.10 | `SqliteSaver` |
| langgraph-prebuilt | 1.1.0 | 2026-05-12 | >=3.10 | pulled in by langgraph (`<1.2.0`) |
| langchain-anthropic | 1.7.2 | 2026-09-10 | >=3.10,<4 | `ChatAnthropic` |
| anthropic | resolved by uv, expected 1.6.0 | 2026-09-15 | >=3.10 | langchain-anthropic allows `>=0.120.0,<2`; its own lockfile tests 1.1.0 |
| langchain-tavily | 0.2.18 | 2026-04-16 | >=3.10,<4 | Tavily search and extract |
| langsmith | 0.12.6 | 2026-09-16 | >=3.10 | optional tracing, off by default. Releases every few days, so pin exactly. |
| networkx | 3.6.1 | 2025-12-08 | >=3.11 | knowledge graph |
| pydantic | 2.13.5 | 2026-08-28 | >=3.9 | schemas (pins pydantic-core 2.46.5, which has a cp312 macOS x86_64 wheel) |
| pytest | 9.1.1 | 2026-06-19 | >=3.10 | tests |
| pytest-socket | 0.8.1 | 2026-08-19 | >=3.10 | network block in the test process |
| hypothesis | 6.168.0 | 2026-09-08 | >=3.10 | property tests for SC3a, SC6, SC10 |
| uv (tool, not a dependency) | 0.12.16 | 2026-09-18 01:01 UTC | n/a | env, lock, runner |

Not used: `langchain-classic` (legacy `AgentExecutor`), `langchain-tests` and
vcrpy (full HTTP body cassettes would capture prompts and possibly keys; the
replay design in section 8.10 records tool artifacts instead). The install on
17 September 2026 resolved every row above to the pinned version, plus
`anthropic` 1.6.0 and, transitively, `langgraph-sdk` 0.4.4 and `sqlite-vec`
0.1.9 (pulled in by the SQLite checkpointer for its vector store, which v2 does
not use; PRD:117 rules out vector stores).

Compiled dependencies with cp312 or abi3 macOS x86_64 wheels were found for
pydantic-core, jiter, ormsgpack, xxhash, orjson, zstandard, sqlite-vec 0.1.9 and
the aiohttp stack. A full resolution without source builds is unverified until
`uv lock` runs.

### 2.2 Python 3.12 on this Mac (done during Phase 0, at your direction)

What was done, in order: `brew install uv` began building OpenSSL from source,
because Homebrew has no Intel bottle, and was stopped (it left only a refreshed
`ca-certificates`); `/usr/bin/python3 -m pip install --user uv` installed uv
0.12.16 from its PyPI wheel; `uv python install 3.12` installed CPython 3.12.14;
`uv venv --python 3.12 .venv` and `uv pip install` of the section 2.1 packages
built nothing from source. `.venv/`, `__pycache__/`, `.pytest_cache/` and
`.hypothesis/` were added to `.gitignore`. Phase 1 replaces the ad hoc install
with `pyproject.toml`, `.python-version` and a hashed `uv.lock`.

The options the planning pass compared, kept for the record:

Verified locally, read only: macOS 13.7.8, Intel x86_64; `/usr/bin/python3` is
3.9.6; no `python3.10` to `python3.12`, `uv`, `pyenv` or `pipx` on PATH;
Homebrew at `/usr/local/bin/brew`.

| Option | Finding | Verdict |
|---|---|---|
| Homebrew `uv` or `python@3.12` | Homebrew treats Intel macOS as Tier 3 and "has stopped building new bottles for Intel systems" (github.com/Homebrew/brew docs/Support-Tiers.md, reviewed 2026-09-17). Neither formula has an Intel macOS bottle, so both compile from source. | Reject |
| python.org installer | The last 3.12 binary installer is 3.12.10 (April 2025). Later 3.12 releases are source only. Still needs a lock tool. | Reject |
| uv standalone installer, then `uv python install 3.12` | uv 0.12.16 ships an `x86_64-apple-darwin` binary and supports macOS 13 as Tier 1. It installs CPython 3.12.14 (build 20260901), built for macOS 10.15 and later. One tool makes the venv, the hashed cross platform `uv.lock`, and runs tests. | **Recommend** |

A uv managed CPython 3.12.14 already exists at
`~/.local/share/uv/python/cpython-3.12.14-macos-x86_64-none`, created at 22:43
today by something outside Phase 0. If you installed it, Phase 1 uses it and
only adds the `uv` binary. Phase 1 commands, for your approval:

1. Install uv 0.12.16 with the standalone installer from astral.sh, or
   `/usr/bin/python3 -m pip install --user uv==0.12.16`.
2. `uv python install 3.12` (no op if 3.12.14 is present), then write
   `.python-version` with `3.12`.
3. `uv lock`, `uv sync --locked`, `uv run pytest`.

### 2.3 Node for SC1b

Node v24.19.0 is at `~/.local/node/bin/node` and is not on PATH. The v1
guardrail suite loads `.ts` files through Node's built in type stripping with no
flags (on by default since Node 23.6.0, stable in 24.12.0, per
nodejs.org/docs/latest-v24.x/api/typescript.html). A Phase 0 reader ran the v1
guardrail suite under this Node: 17 PASS, exit 0 (observed); no network and no
key (established by reading the imports, not observed). The SC1b test finds Node
through the `NODE_BIN` setting (default `~/.local/node/bin/node`), then PATH, so
SC1b actually runs on this machine, and skips with a stated reason if none is
Node 23.6 or later. In gate mode (`ADVISOR_GATE=2` at the Phase 2 gate) a skip fails the session (section 5).

How the harness loads `brief-validate.ts`: `v1_validate.mjs` imports it by
absolute path from `V1_BRIEFCASE_DIR`. Its only import is a type import, which
type stripping removes. A plain import makes Node's loader read the `type` field
of the nearest `package.json`, which is in the private v1 folder (metadata only,
nothing copied). Decision 32 asks whether that is acceptable; the fallback strips
types from the file text with `node:module` `stripTypeScriptTypes` (stability
unverified) and imports the result from a `data:` URL.

---

## 3. API verification

Checked against source at the release tags and docs.langchain.com on 17
September 2026. Every rename or deprecation row gets a DECISION-LOG entry,
appended in Phase 0 after you approve this plan (section 10.1).

| PRD name | Current name and import | Status | Note |
|---|---|---|---|
| `langchain.agents.create_agent` | `from langchain.agents import create_agent` (langchain 1.4.1, `factory.py:840`) | Current | Binds `recursion_limit=9_999` (`factory.py:1831`), so middleware caps are the only loop guard. `state_schema` must be a TypedDict. |
| (predecessor) `create_react_agent` | replaced by `create_agent` | **Deprecated** (`LangGraphDeprecatedSinceV10`, prebuilt 1.1.0 `chat_agent_executor.py:274`) | `prompt` became `system_prompt`; pre and post model hooks became middleware. Not used. |
| `langgraph.prebuilt.AgentState` | `langchain.agents.AgentState` | **Deprecated** | Used only if custom agent state is needed. |
| `AgentExecutor` | `langchain_classic.agents.AgentExecutor` | **Moved to legacy package** (langchain-classic 1.0.8) | Not used. |
| Human in the loop middleware | `langchain.agents.middleware.HumanInTheLoopMiddleware` | Current | Pauses only on tool calls. **Not suitable for confirm_identity**, which pauses on extracted data. confirm_identity uses `interrupt()` in the parent graph. |
| Model call limit middleware | `ModelCallLimitMiddleware(*, thread_limit, run_limit, exit_behavior="end" or "error")`; `ModelCallLimitExceededError` from `langchain.agents.middleware.model_call_limit` | Current | `"end"` leaves only a plain text message; use `"error"` and catch the typed exception. `run_limit=N` allows exactly N calls. Counters reset every `invoke`. |
| Tool call limit middleware | `ToolCallLimitMiddleware(*, tool_name, thread_limit, run_limit, exit_behavior="continue")`; `ToolCallLimitExceededError` from `...middleware.tool_call_limit` | Current | One instance per tool. Blocked calls get an error `ToolMessage` and no artifact. |
| Custom middleware hooks | `AgentMiddleware`, `before_agent`, `before_model`, `after_model`, `after_agent`, `wrap_model_call`, `wrap_tool_call`, `hook_config`, `ModelRequest`, `ModelResponse`, `ExtendedModelResponse` from `langchain.agents.middleware` | Current | `wrap_model_call` cannot jump (`goto` raises); it can update state through `ExtendedModelResponse`. First in the list is outermost. |
| `ModelRequest(system_prompt=...)` | `system_message` | **Deprecated** (`types.py:107`) | Use `system_message`. |
| Model retry (not in PRD) | `ModelRetryMiddleware` | Current | Default retries any non `ModelError` exception and turns final failure into a normal looking message. Set `retry_on` and `on_failure="error"`. |
| `SummarizationMiddleware` kwargs (not in PRD) | `trigger`, `keep` | **Old kwargs deprecated** (`max_tokens_before_summary`, `messages_to_keep`) | Not used. |
| `langgraph.types.interrupt` | `from langgraph.types import interrupt` (1.2.11 `types.py:851`) | Current | Node reruns from the top on resume; never wrap in try/except; one interrupt per node run. |
| `NodeInterrupt` (not in PRD) | `interrupt()` | **Deprecated** (`errors.py:108`) | Not used. |
| `Interrupt.interrupt_id` (not in PRD) | `Interrupt.id` | **Renamed** (`types.py:575`) | Resume values keyed by `.id` when several are pending. |
| `Command(resume=...)` | `from langgraph.types import Command` (`types.py:799`) | Current | Resume value must not be `None` (raises `EmptyInputError`). Needs a checkpointer and `thread_id`. |
| Result `__interrupt__` | `invoke(..., version="v2")` returns `GraphOutput` with `.value` and `.interrupts`; or `graph.get_state(cfg).interrupts` | Current; **dict access on `GraphOutput` deprecated** (`types.py:382`) | CLI uses `get_state`. |
| `StateGraph` | `from langgraph.graph import StateGraph, START, END` (`state.py:216`) | Current; **kwargs `config_schema`, `input`, `output` deprecated** in favor of `context_schema`, `input_schema`, `output_schema` | TypedDict state, JSON native values only. |
| Conditional edges | `add_conditional_edges(source, path, path_map)` (`state.py:982`) | Current | Always pass a path map. |
| Recursion limit (not in PRD) | config key `recursion_limit`; `GraphRecursionError` | Current. **Docs say default 1000; source says 10007** (`_internal/_config.py:32`). Source wins. | Pass 40 explicitly as a backstop. |
| `checkpoint_during` (not in PRD) | `durability="sync"` | **Deprecated** (`pregel/main.py:2727`) | Use `durability="sync"` on every call. |
| SQLite checkpointer | `from langgraph.checkpoint.sqlite import SqliteSaver` (langgraph-checkpoint-sqlite 3.1.1) | Current | Sync only. Needs `check_same_thread=False` because parallel nodes run on a thread pool. Enables WAL. |
| `MemorySaver` (tests) | `from langgraph.checkpoint.memory import InMemorySaver` | **Renamed**, alias kept (checkpoint 4.2.0 `memory/__init__.py:625`) | Use `InMemorySaver`. |
| Checkpoint serializer (not in PRD) | `JsonPlusSerializer`; env `LANGGRAPH_STRICT_MSGPACK=true` | Current; permissive by default, blocking announced | Set strict mode before any langgraph import. Advisory GHSA-g48c-2wqr-h844 affects 1.0.9 and earlier only. |
| `stream_events(version="v3")` (not in PRD) | same | **Beta** in source, although docs recommend it | Avoid. Use `stream(stream_mode="updates", version="v2")`. |
| `langchain-anthropic` `ChatAnthropic` | `from langchain_anthropic import ChatAnthropic` (1.7.2) | Current | `max_tokens` defaults to the model maximum (64,000 on Haiku), `timeout` defaults to None. Both must be set. |
| `ChatAnthropic(output_format=...)` | `output_config={"format": ...}` | **Deprecated**, removal in 2.0.0 (`chat_models.py:1719`) | Not used directly. |
| `with_structured_output(method="json_mode")` | `method="json_schema"` | **Remapped with a warning** (`chat_models.py:2614`) | Use `json_schema` with `include_raw=True`. |
| Message `.text()` | `.text` property, `.content_blocks` | **Method call deprecated** since core 1.0 | Use the property. |
| `langchain-tavily` | `from langchain_tavily import TavilySearch, TavilyExtract` (0.2.18, source at main commit daa78d8c37, which the repo does not tag; match to the wheel is unverified) | Current | Needs `TAVILY_API_KEY` at construction, no HTTP timeout (`requests.post` in `_utilities.py`), non 200 errors (including 432) raised as `ValueError("Error {status}: ...")` and then returned as `{"error": e}` content rather than raised, so the status is visible only in the error text. Empty results raise `ToolException`, and `TavilySearch` sets `handle_tool_error=True`. `include_usage` is accepted only at instantiation, default False; `TavilyExtract` accepts it too (confirmed in the installed wheel, section 3.1). Wrapped (section 8.7, decision 38). |
| Fake chat models for tests | `langchain_core.language_models.fake_chat_models.*` (core 1.6.3) | Current, **but none implement `bind_tools`**, so none can drive `create_agent` with tools | Write a custom `ReplayChatModel` (section 8.10). `FakeMessagesListChatModel` also wraps around silently when its list runs out. |

### 3.1 Confirmed against the installed packages (offline, $0)

After the install in section 2.2, the names above were introspected in the
installed packages, and two throwaway scripts (kept outside the repo) ran the
riskiest parts of the design with no network and no key.

| Check | Result |
|---|---|
| Signatures of `create_agent`, the three limit middlewares, `AgentMiddleware` hooks, `ToolStrategy`, `ProviderStrategy`, `interrupt`, `Command`, `SqliteSaver`, `ChatAnthropic.with_structured_output` | Match the table above. Two additions not in the PRD: `create_agent` has a `transformers` parameter, and `ChatAnthropic` has `reasoning_effort` and `output_config` fields. |
| Fake models | No class in `fake_chat_models` overrides `bind_tools` or `with_structured_output`; `BaseChatModel.bind_tools` raises `NotImplementedError`. A 12 line scripted `BaseChatModel` whose `bind_tools` returns itself drove `create_agent` with a tool. |
| Cross process resume (SC5) | Process A ran to `interrupt()` and exited; process B resumed the same `thread_id` from the same SQLite file with `Command(resume=...)` and finished. The node before the interrupt ran once, so read_plate is not repeated or rebilled on resume. |
| Pydantic model in checkpointed state | Loads today with the warning "Deserializing unregistered type ... will be blocked in a future version". Confirms section 8.2: JSON native state only, and `LANGGRAPH_STRICT_MSGPACK=true` in tests. |
| `ToolCallLimitMiddleware(exit_behavior="end")` | The over limit call is not executed, and the run ends with plain AI text "'search' tool call limit reached: run limit exceeded (3/2 calls)." and no state flag. Confirms the choice of `"continue"` plus our own `ToolBudgetDone` hook. |
| Custom `before_model` hook with `hook_config(can_jump_to=["end"])` and a `state_schema` extension | Stopped the agent before the next model call and put `budget_stopped: True` in the final state. A pre call spend check works. |
| Tool artifacts | With `response_format="content_and_artifact"`, successful calls keep their artifact; calls blocked by a limit carry none. Sources can be built from artifacts only. |
| Haiku 4.5 profile in langchain-anthropic 1.7.2 | `max_input_tokens` 200,000, `max_output_tokens` 64,000, image inputs, tool calling and structured output all True. |
| `TavilyExtract.include_usage` | Accepted (field present in the 0.2.18 wheel). Resolves an unverified item in the table above. |
| langchain-tavily HTTP timeout | The 0.2.18 wheel calls `requests.post` with no `timeout` argument, as read at main. Decision 38 stands. |
| langsmith 0.12.6 tracing lookup | `tracing_is_enabled` reads `TRACING_V2`, then `TRACING`, through `get_env_var`, as read at main. Decision 31 stands. |

---

## 4. Models and prices for `agent/config.py`

Sources, all fetched 17 September 2026: models overview
(platform.claude.com/docs/en/about-claude/models/overview.md), pricing
(platform.claude.com/docs/en/about-claude/pricing.md), model deprecations,
structured outputs, effort, Haiku 4.5 and Sonnet 5 pages; Tavily api-credits,
search and extract reference, and tavily.com/pricing.

| Constant | Value | Notes |
|---|---|---|
| `HAIKU` | `claude-haiku-4-5-20251001` (alias `claude-haiku-4-5`) | Pin the dated ID. $1 input, $5 output per million tokens. 200K context, 64K max output. Image input and structured outputs supported. **Never send `effort` to Haiku.** Effort is not supported on Haiku 4.5 (what the API does if it is sent is unverified); it uses manual extended thinking only. Status Active; retirement not sooner than 15 October 2026. |
| `SONNET` | `claude-sonnet-5` | $2 input, $10 output (the planned rise to $3 and $15 "will not occur"). 1M context, 128K output. **Returns 400 on a non default `temperature`, `top_p` or `top_k`**, and ChatAnthropic does not strip them for this model. Adaptive thinking on by default and billed as output; manual extended thinking returns 400. New tokenizer, about 30% more tokens for the same text. |
| `OPUS_BASELINE` | `claude-opus-5` | $5 input, $25 output. v1 baseline only (v1 `lib/anthropic.ts:7`). Not called by v2. |
| Cache pricing | read 0.1x base input; 5 minute write 1.25x; 1 hour write 2x | `usage_metadata.input_tokens` already includes cache reads and writes (langchain-anthropic 1.7.2 `chat_models.py:2962`). The ledger subtracts them before pricing. Haiku 4.5 minimum cacheable prompt is 4,096 tokens, so caching is off by default. |
| Anthropic web search | $10 per 1,000 searches | Baseline only; v2 uses Tavily. |
| `MAX_TOKENS` | read_plate 512; route classifier 64; research agent 1,024 per call; synthesize 6,000 (`cheap`), 16,000 (`full`, includes thinking) | Required: the default of 64,000 on Haiku reserves $0.32 of output, more than the whole $0.15 cap. |
| `TIMEOUT_S` | model calls: 30 plus 1 second per 200 `max_tokens` (about 60 for `cheap` synthesize, about 110 for `full` synthesize); Tavily 30 | ChatAnthropic's `None` default most likely means no timeout (inferred). The `full` synthesize timeout must be confirmed, or synthesize streamed, before any live `full` run. |
| `TOOL_PROMPT_TOKENS` | Haiku 4.5: 588; Sonnet 5: 474 | The tool use system prompt Anthropic adds to every tool bearing call (the higher `any` or `tool` figure; `auto` is 496 and 354). Pricing page, fetched 17 September 2026. Added to every tool bearing estimate. |
| `ESTIMATE_MARGIN` | Haiku 4.5: 1.25; Sonnet 5: 1.25 × 1.3 | `count_tokens_approximately` uses 4 characters per token and counts only stringified tool schemas; Sonnet 5's newer tokenizer makes about 30% more tokens. |
| `CLIENT_MAX_RETRIES` | 0 | Retries happen in our code or in `ModelRetryMiddleware` so the ledger sees every attempt. |
| `FULL_SYNTH_EFFORT` | `medium` | Matches the v1 baseline. Never sent to Haiku. |
| Tavily search | `search_depth="basic"` (1 credit), `max_results=5`, `include_raw_content="text"`, `include_usage=True` (reported credits stored in `search_trail`) | `advanced` costs 2 credits and is pinned off in the constructor so the model cannot choose it. No surcharge for raw content is listed; unverified that none exists. |
| Tavily extract | `extract_depth="basic"`, `format="text"`, no `query` | 1 credit per 5 successful URLs; failed URLs free; exact rounding unverified. |
| Tavily free tier | 1,000 credits a month, no card; pay as you go $0.008 per credit | Ledger records credits at $0 unless pay as you go is on, then $0.008 (decision 16). "Error 432" or "Error 433" means the plan limit was hit; stop with `budget_stopped`. The Phase 5 gate records the plan, whether pay as you go is off, and the Anthropic balance. |
| Caps | `RUN_CAP_USD`: cheap 0.15, full 0.30 (proposed); `BUILD_CAP_USD` the lower of 5.00 and the Anthropic balance recorded at the Phase 5 gate; `RUN_CREDIT_CAP` 10; `BUILD_CREDIT_CAP` 150; `RESEARCH_BUDGET_USD` 0.09 (decision 26) | Checked by the ledger before each call; can be exceeded by at most one call's input estimate error (section 8.9). |
| Replay pricing | `REPLAY_PRICES` = Haiku 4.5 rates for every replay model call; `REPLAY_RUN_CAP_USD` 0.15; a cassette may carry an optional `caps` block that overrides it for that test | Replay rows are priced so the ledger logic is exercised, never count toward the build total, and each replay test that expects a retry states its cap or scripted usage (section 8.10). |
| Research limits | main route: search 5, fetch 3, model call loop guard 10; graph top up: search 2, fetch 1, loop guard 5; `cheap` retry pass: search 2, fetch 0, loop guard 4 | Loop guard = search cap + fetch cap + 2. Section 8.7. |

Parameters never sent: `temperature`, `top_p`, `top_k` (any mode), `effort` and
`thinking` to Haiku. A test scans `agent/**/*.py`, excluding `agent/config.py`
and `agent/tests/`, for model IDs and dollar figures and fails if any are found.
Tests import prices and model IDs from `config.py` rather than hard coding them.
(The first draft scanned all of `agent/`, which would have failed on this plan,
the cassettes and the ledger fixtures.)

---

## 5. Traceability matrix

Modes: **offline** means unit tests with no model at all; **replay** means the
whole graph on `ReplayChatModel` and recorded tool results; **LIVE** means real
paid calls, run only after you type "proceed" for that phase. Every offline and
replay test runs with the network blocked (section 8.14).

LIVE runs are never pytest tests. pytest blocks sockets and deletes the
Anthropic and Tavily keys at import (section 8.14), so a live call cannot run
inside it. Live evaluations are CLI commands (`advisor eval sc3b --live`,
`advisor eval sc7b --live`, code in `agent/live/`, outside the pytest test
paths). They require `ADVISOR_MODE=cheap`, `--live` and a typed "proceed", print
the planned calls and estimated cost from `config.py` first (PRD:13), and write
JSON run records to `data/eval/`. pytest keeps only the offline evaluators that
score those records.

Every row names the mutation or injected bad input that must turn it red. The
mutations are tracked data, not prose: `agent/tests/mutations.toml` lists, for
each one, the file, the search text, the replacement text and the node IDs
expected to fail. The gate script applies each in a temporary copy, runs the
listed tests, and reports that they went red. A test that stays green under its
mutation is reported as broken.

Gate mode: with `ADVISOR_GATE` set to the gate's phase number (for example
`ADVISOR_GATE=2` at the Phase 2 gate), a `conftest.py` hook fails the session if
any SC1b case, or any `v1_inventory.json` node ID with a phase at or below that
number, is skipped, xfailed, deselected or missing. (As built in Phase 1; the
first draft said `ADVISOR_GATE=1` at every gate, which at the Phase 2 gate would
have enforced no phase 2 rows.) A skipped test cannot satisfy a gate.

Latency is measured per node and per run and reported at the Phase 5 and 6
gates. There is no latency target, and no test asserts one.

Test files live in `agent/tests/`. Child processes in any test are started
through one helper, `tests/helpers.py::spawn_offline_child`, which puts the
Python guard on `PYTHONPATH` or the Node guard on `--import`, and scrubs the
environment.

| Criterion | Test file :: function | Mode | Phase | Fixtures | Asserts | Goes red when |
|---|---|---|---|---|---|---|
| SC1 (no network) | `test_offline_guard.py::test_socket_blocked_in_process` | offline | 1 | none | opening a TCP socket raises `SocketBlockedError` | `--disable-socket` removed from `pytest` config |
| SC1 (no network in children) | `test_offline_guard.py::test_socket_blocked_in_child_python`, `::test_network_blocked_in_child_node`, `::test_sc5_and_sc1b_children_use_spawn_helper` | offline | 1 | `tests/netguard/sitecustomize.py`, `tests/harness/netguard.mjs` | a child started by `spawn_offline_child` prints the guard's "guard loaded" marker, and its `socket.create_connection` or `fetch` fails with the guard's own error text (not just a non zero exit); the SC5 and SC1b tests start their children only through that helper | guard file removed from the child's `PYTHONPATH` or `--import` list; SC5 or SC1b launches a child with `subprocess` directly |
| SC1 (no keys) | `test_offline_guard.py::test_no_keys_or_tracing_in_env` | offline | 1 | none | `ANTHROPIC_*`, `TAVILY_API_KEY`, `LANGSMITH_*`, `LANGCHAIN_*` absent; `langsmith.utils.tracing_is_enabled()` is False | conftest stops scrubbing the env, or a key is set in the shell and the scrub is removed |
| SC1 (no live tests collected) | `test_offline_guard.py::test_default_collection_contains_no_live_tests` | offline | 1 | none | the default collection contains no test under `agent/live/` and no test that imports `agent.live` | `agent/live` added to `testpaths`, or a live evaluation written as a pytest test |
| SC1 (gate mode) | `test_offline_guard.py::test_gate_mode_fails_on_skipped_required_test` (pytester) | offline | 1 | a synthetic inventory with one skipped and one xfailed node | in gate mode the session fails and names both nodes | the conftest hook removed, or it ignores xfail |
| SC1 (replay never builds live clients) | `test_models.py::test_replay_mode_never_constructs_live_clients` | offline | 1 | none | model factory in replay returns `ReplayChatModel`; tool factory returns stubs; `ChatAnthropic`, `TavilySearch`, `TavilyExtract` constructors patched to raise are never hit | factory falls through to `ChatAnthropic` for any node |
| SC1 (17 v1 behaviors) | `test_v1_inventory.py::test_every_v1_guardrail_is_ported_or_approved_obsolete` | offline | 2 (rows with phase 2), 4 (all) | `tests/v1_inventory.json` (17 rows from section 6.1, each with a `phase` field) | every row whose phase is at or before the current phase maps to collected node IDs that ran and passed (in gate mode, not skipped or xfailed), or is marked obsolete with an approval date | a mapped test is deleted, renamed or skipped; an obsolete row lacks approval |
| SC1b (parity layer only) | `test_sc1b_differential.py::test_v1_and_v2_decisions_match` (parametrized per payload) | offline (needs Node and the private v1 source; skips with reason otherwise, and a skip fails the Phase 2 gate) | 2 | `tests/fixtures/sc1b_payloads.json` (13 v1 guardrail payloads, 3 from the parse tests, about 25 new edge payloads from section 6.2; passes the privacy diff before it is tracked, with a `provenance` field naming its source file relative to `V1_BRIEFCASE_DIR`), `tests/fixtures/sc1b_approved_differences.json` (written only after you approve, decision 21) | This compares v1 with `agent/rules/v1_parity.py` only; the model facing schema layer is covered by the next row. For each payload: same accept or reject decision; same reason code (a v1 message to reason code table in the harness maps v1's free text errors, with `v1_type_error` for native TypeErrors, and v2 must give `v1_type_error` for the null source element and truthy primitive payloads); and the normalized brief, defined as v1's output against v2's `model_dump(exclude_unset=True)`, so a key v1 leaves absent must stay absent (for example `title`, `detail`, `why_shown`, `happened_before.summary`, and omitted top level fields). Every JSON path is compared except rows in the approved differences file, each `{payload_id, json_path, v1_value, v2_value, approved_on}`. An accept or reject decision is never exempted without its own explicit decision row | v2 validator patched to accept a `source_index` of `true`, or to reject an unknown `who` instead of normalizing it; parity model dumps defaults for absent keys; an approved difference row widened to a whole payload |
| SC1b (harness can fail) | `test_sc1b_differential.py::test_harness_reports_a_planted_divergence` | offline | 2 | v1 guardrail test 5 payload (ok with empty sources, a v1 reject) plus a stub v2 validator that accepts everything | the harness reports a decision mismatch on that payload | comparison code compares only one side |
| SC1b (schema layer) | `test_schemas.py::test_draft_fields_share_parity_semantics` (parametrized over the SC1b payloads restricted to the fields `BriefDraft` shares with v1: `try_first`, `candidates`, `warranty`, `warranty_caution`, `happened_before`, `no_reliable_answer` minus `searched`) | offline | 2 | a small adapter in `tests/helpers.py` that splits a payload into a draft plus a source registry; reused by the ported v1 tests that feed v1 shaped briefs into v2 validate | `BriefDraft.model_validate` followed by the validate node reaches the same accept or reject decision and reason code as `v1_parity` alone, apart from an explicit per payload delta list in the test. Under `json_schema` structured output the model's reply is already constrained to `BriefDraft`, so this guards drift in the schema layer rather than production inputs | `Candidate.who` in `BriefDraft` becomes a bare `Literal` with no before validator |
| Structured output limits (decision 22) | `test_schemas.py::test_brief_draft_within_structured_output_limits` | offline | 1 | `BriefDraft` run through anthropic `transform_schema` | optional parameters (not in `required`) counted per `$defs` entry and per use site is 0; union (`anyOf` or type array) parameters counted both ways is at most 16 | a field given a default, or a new nullable field pushing unions past 16 |
| SC2 FLO | `test_sc2_replay.py::test_flo_gives_one_confirmed_candidate` | replay | 2 | cassette `flo.json`, with decoy results interleaved between cited sources in the registry; variant `flo_three_candidates.json` (synthetic, labeled; every candidate scripted `confirmed: false`) | status ok, not `budget_stopped`; exactly 1 candidate; its normalized code is `FLO`; `confirmed` true, set by code (the variant proves it, since the script says false); `observed_code` is `FLO` set by code from confirmed state; each try_first step, candidate and warranty caution, matched by text to its recorded v1 counterpart, cites the same URL the recorded v1 brief cited for that item | narrowing rule disabled (variant keeps 3); code keeps the model's `confirmed` value (variant ends with none confirmed); index remap in the cassette loader off by one in either direction (lands on a decoy or the wrong real source) |
| SC2 vague symptom | `test_sc2_replay.py::test_vague_symptom_gives_multiple_candidates` | replay | 2 | `notheating.json`, decoys interleaved | status ok, not `budget_stopped`; at least 2 candidates; none confirmed; safety steps first (recorded order had one last); each item's cited URL equals the URL the recorded v1 brief cited for it | safety ordering changed to reject instead of reorder (run falls to NO RELIABLE ANSWER); index remap off by one in either direction |
| SC2 HVAC | `test_sc2_replay.py::test_hvac_grounded_with_no_invented_codes` | replay | 2 (recorded), 3 (grounding variant) | `hvac.json`; variant `hvac_invented_code.json` with synthetic page text and a `caps` block (or scripted usage) stated so the retry is affordable | recorded: status ok, every candidate code null, every index resolves, and the grounding check reports "unverifiable" because the cassette's `provenance.derived_from` names a v1 lookup. The recorded half is weak by nature (v1 recorded no codes); the grounding tests below carry the rule. Variant: a code absent from the cited source text fails validation twice and ends NO RELIABLE ANSWER (not `budget_stopped`), and the invented code appears nowhere in the final brief or HTML | code grounding check disabled |
| Code grounding (decision 25) | `test_grounding.py::test_code_on_textless_source_fails_outside_v1_cassettes` and `::test_v1_cassette_exemption_is_reported_as_unverifiable` | offline and replay | 3 | a source with `raw_content` null and a snippet lacking the code, once in a non v1 replay cassette and once in live mode with stubbed tools; a v1 derived cassette | outside v1 derived replay cassettes, a non null code whose cited source has no raw text and is not in the snippet fails validation; in a v1 derived cassette the check reports "unverifiable" and passes, and the SC2 results say so | the exemption keyed on "no text recorded" instead of provenance and mode |
| SC2 fabricated model | `test_sc2_replay.py::test_fabricated_model_gives_no_reliable_answer` | replay | 2 | `unknown.json`, whose synthetic search queries deliberately differ from v1's recorded `searched` strings | status `no_reliable_answer`; candidates and try_first empty; `searched` equals the queries in the code built `search_trail`, not v1's recorded list and not source titles; `refusal_origin` is `model` | validate fills `no_reliable_answer.searched` with `[]` or with source titles instead of the `search_trail` queries |
| SC2 blurry plate | `test_sc2_replay.py::test_blurry_plate_halts_at_confirmation` | replay | 2 | `blurry.json` (recorded E2 extraction) | run is paused at `confirm_identity`; interrupt payload shows model null and `unreadable`; zero research, synthesize or search calls | confirm_identity proceeds when model is null |
| SC2 (no budget stop) | `test_sc2_replay.py::test_no_sc2_case_ends_budget_stopped` | replay | 2 | all 5 cassettes, with per call usage split scaled so each pass matches the section 9 estimate | under `REPLAY_PRICES` and `REPLAY_RUN_CAP_USD`, no SC2 case ends `budget_stopped` | cassette split changed so one pass exceeds the estimate, or replay priced at Opus rates |
| Observed code rule (decision 7) | `test_observed_code.py::test_narrowing_rule` (parametrized) and `test_graph_retry.py::test_zero_match_twice_ends_forced_refusal` | offline and replay | 2 | scripted drafts | one match scripted `confirmed: false` ends with only that candidate, confirmed true; two matches keep both, none confirmed; zero matches is a validation failure, and twice in a run ends in a forced refusal; no observed code with a candidate scripted `confirmed: true` ends with none confirmed; normalization inputs (case, surrounding punctuation, NFC, free text mode names) match only when they normalize equal | code keeps the model's `confirmed` value; zero matches passes |
| SC3a | `test_validate.py::test_refusal_clears_candidates_and_steps` (v1 test 2 payload) and `::test_refusal_clearing_property` (Hypothesis: arbitrary candidates, steps, upgrades, maintenance under refusal status) | offline | 2 | payloads only | after `validate`, candidates, try_first, upgrade_options and maintenance_due are empty for every generated input | the clearing line is removed |
| SC3a (forced refusal) | `test_graph_retry.py::test_second_validation_failure_forces_refusal_without_candidates` | replay | 2 | synthetic script: two drafts that fail validation and carry candidates; scripted usage and a `caps` block stated so the reduced retry pass is affordable | one retry through the reduced research pass, then `no_reliable_answer` with `refusal_origin="forced"`, empty candidates and steps, full search trail | retry counter not incremented (loop never ends, recursion limit hit) or refusal node skips clearing |
| Retry affordability (decision 23) | `test_graph_retry.py::test_retry_taken_when_affordable`; the unaffordable branch is `test_graph_budget.py::test_budget_stop_is_never_no_reliable_answer` run with a first pass at the caps | replay | 2 | first pass spend at the typical estimate with one validation failure; first pass spend at the caps | typical: the retry runs with search 2, fetch 0 and 4 model calls; at caps: the retry is skipped and the run ends `budget_stopped` | affordability computed as the sum of worst case per call reservations (retry never taken), or the retry uses the main route caps |
| SC3b | `advisor eval sc3b --live` (`agent/live/eval_sc3b.py`) | LIVE | 6 | none; writes three run records and three new cassettes | 3 runs in `cheap`; each should end `no_reliable_answer` with `refusal_origin` `model`; records latency, searches, cost, `refusal_origin`, whether a `budget_stopped` came from an unaffordable retry, and whether the trail reached the real maker's documentation | any run returns ok, `budget_stopped`, or a forced refusal (decision 33; if you count forced refusals, only ok and `budget_stopped`). Published either way |
| SC3b (evaluator) | `test_live_eval.py::test_sc3b_evaluator_scores_each_outcome` and `::test_sc3b_evaluator_pools_unchanged_build_reruns` | offline | 5 | synthetic run records: ok, `budget_stopped`, `budget_stopped` from an unaffordable retry, forced refusal, model refusal; a rerun set from an unchanged build | ok and `budget_stopped` score as failures; a forced refusal scores as decision 33 says; model refusals, forced refusals and budget stops are reported as separate headline numbers; reruns of an unchanged build are pooled ("5 of 6"), never substituted | evaluator treats `budget_stopped` or a forced refusal as a model refusal; evaluator keeps only the latest run |
| SC4 | `test_sc4_blurry.py::test_blurry_plate_zero_searches_and_zero_spend_after_extraction` | replay | 2 | `blurry.json`; variant `blurry_guessed_model.json` (synthetic: model text present but confidence `unreadable`) | code nulls every `unreadable` field; run pauses; resuming without a model re-prompts and stays paused; ledger has no entry after the read_plate entry; search stub call count 0 | code nulling removed (variant proceeds to research); confirm_identity accepts an empty model |
| SC4 (no repeat vision call) | `test_sc4_blurry.py::test_resume_does_not_rerun_read_plate` | replay | 2 | `blurry.json` | read_plate script consumed exactly once across pause and resume | read_plate merged into confirm_identity |
| SC5 | `test_sc5_resume.py::test_resume_in_a_new_process` | replay (two child processes via `spawn_offline_child`) | 2 | `flo.json`, temp SQLite files; `ADVISOR_CASSETTE`, checkpoint and data paths set for both children | process 1 (`advisor ask`) exits at the interrupt and prints a thread ID; process 2, a different PID, runs `advisor resume <id> --model ...` with the same cassette and reaches status ok; read_plate call log shows 1 call total | CLI uses `InMemorySaver`; process 2 uses a different checkpoint path |
| SC5 (serializable state) | `test_state.py::test_state_json_round_trips_after_every_node` | replay | 2 | `flo.json` | `json.dumps` then `json.loads` of state after every node equals the state | a node writes a Pydantic model or message object into state. (A "non importable type" mutation was dropped: under strict msgpack a blocked type comes back as a plain dict with a warning, not an error, per langgraph `serde/jsonplus.py`, so it would not turn SC5 red) |
| SC6 (citation half) | `test_citations.py::test_every_index_resolves_to_a_source_registered_this_run` and `::test_prune_and_renumber_property` (Hypothesis) | offline | 2 (unit), 3 (property) | synthetic source registries | an index outside the run's source registry fails validation; after pruning uncited sources, every cited field points to the same URL as before renumbering | renumbering off by one |
| SC6 (registry built from tools) | `test_research_sources.py::test_registry_is_exactly_successful_tool_artifacts` | replay | 2 | research script with two successful searches, one search blocked by `ToolCallLimitMiddleware` (error `ToolMessage`, no artifact), one fetch raising `ToolException`, and a final `AIMessage` naming an extra URL | the source registry equals the successful artifact URLs, in order, and nothing else | sources built from model output, or from error `ToolMessage`s |
| SC6 (graph edge half) | `test_evidence.py::test_edge_kept_only_with_verbatim_span` and `::test_evidence_span_property` (Hypothesis) | offline | 3 | synthetic page texts (labeled synthetic) | any 20 to 400 character substring of the fetched raw text (after the allowed normalization, section 8.11) that contains the edge target is accepted; a span with one non whitespace character changed, its case flipped, containing `[...]`, found only in a search snippet, outside the length bounds, or missing the edge target is rejected; a rejected edge is absent from the graph, not stored with a flag | matching made case insensitive or fuzzy; rejected edges stored with `verified=False` |
| SC6 (excerpts) | `test_evidence.py::test_quote_from_excerpt_verifies_when_snippet_formatting_differs` | replay | 3 | a source whose snippet chunks and `raw_content` differ in whitespace and markdown | a quote taken from the code cut excerpt verifies against `raw_content`; a quote copied from the snippet with its markdown fails | excerpts cut from the snippet instead of `raw_content` |
| SC6 (whole graph) | `test_graph_integrity.py::test_every_edge_reverifies` and `test_persist.py::test_persist_writes_only_verified_edges` | replay and offline | 3 (runtime), 6 (seed) | the replay persisted graph; `seed/graph.json`; a persist replay with one passing and one failing edge | every persisted edge re passes the full span check where `data/pages/<sha256>.txt` exists; otherwise `evidence_sha256`, span length and target in span are checked and missing page text is reported as a counted limitation; persist writes the passing edge and drops and logs the failing one. The gate that promotes edges into `seed/` requires full re verification with page text present | persist path writes edges without calling the span check |
| SC7a | `test_router.py::test_route_decision_table` (parametrized over section 8.4 table) and `test_sc7a_graph_route.py::test_graph_route_makes_no_search_calls` | offline and replay | 3 | `tests/fixtures/graphs/optima_flo.json` (synthetic graph fixture, labeled) | route `graph` with reason logged; research model and search stub called 0 times; brief cites graph sources with their original `retrieved_at` | router ignores graph coverage; an edge without evidence counted as coverage |
| SC7b | `advisor eval sc7b --live` (`agent/live/eval_sc7b.py`) | LIVE | 6 | graph built from the Phase 5 and 6 first lookups | every repeat is reported with its route, its search count and the same model's v2 first lookup count (decision 34); SC7b passes only if every repeat uses 2 or fewer searches; the numbers are published either way | n/a (live measurement) |
| SC7b (evaluator) | `test_live_eval.py::test_sc7b_counts_searches_from_trail_not_model` | offline | 5 | synthetic run records: one with 2 searches and 1 fetch, one with a search blocked by the cap | the search count is the number of search tool calls in `search_trail` that reached Tavily; blocked calls are excluded and listed; fetches and credits are reported separately (the 2 search, 1 fetch record scores 2) | count taken from model text or from ledger credits |
| SC8 | `test_sc8_history.py::test_happened_before_cites_prior_record` | replay | 3 | seeded synthetic appliance with one synthetic service record; cassette `optima_history.json` | the synthesize request recorded in `ReplayChatModel.received` contains the seeded record's ID, date and symptom; `happened_before.matches` true; `record_id` equals the seeded record and belongs to this appliance; the rendered brief shows the record date | `history_hits` omitted from the synthesize prompt; record ID check removed, with variants citing another appliance's record and a nonexistent record (both must end with `happened_before` null) |
| SC8 (no history) | `test_sc8_history.py::test_no_history_means_null` | replay | 3 | appliance with no records; script fills `happened_before` anyway | `happened_before` is null | model value passed through |
| G3 registry dates (decision 29) | `test_sc8_history.py::test_age_from_registry_date` | replay | 3 | the seeded appliance with an install date and warranty terms; script supplies its own age statement | `warranty.age_statement` is computed by code from the registry install or purchase date and cites the registry `record_id`; warranty terms appear only as quoted from the registry | model supplied age passes through |
| SC9 (cited half) | `test_sc9_upgrades.py::test_upgrade_options_only_cited_successors` and `::test_graph_superseded_edge_becomes_cited_option` | replay | 4 | `tests/fixtures/discontinued.json` (synthetic model, synthetic page text, example URLs, all labeled); a graph fixture with a verified SUPERSEDED_BY edge | of two model proposed successors, only the one whose index resolves and whose evidence quote is verbatim in that source survives; a graph derived successor becomes a cited option whose source is registered, verified and renumbered by validate | entries with a bad index or unverified quote kept; graph options added after pruning |
| SC9 (no citation half) | `test_sc9_upgrades.py::test_upgrade_options_empty_without_citation` and `::test_upgrade_check_adds_nothing_to_refusal_or_budget_stop` | replay | 4 | same fixture, all proposals uncited; the graph fixture with a refusal and with a budget stop | `upgrade_options == []` and the section is not rendered; on NO RELIABLE ANSWER and `budget_stopped`, upgrade_check adds nothing | uncited entries rendered as "unverified" instead of dropped; upgrade_check runs on a refusal |
| Maintenance (PRD:111) | `test_maintenance.py::test_only_manufacturer_tier_intervals_kept` | replay | 4 | synthetic sources of each tier with interval text; a `maintenance_log` row; a script that supplies its own `due_date` | intervals cited to dealer or forum sources, and interval text outside the verified evidence, are dropped; `due_date` is computed by code from `maintenance_log`, and a model supplied value is ignored | the tier check removed |
| Tier ceiling (PRD:16, decision 5) | `test_tiers.py::test_host_ceiling_table` (parametrized over section 8.6) | offline | 2 | host and authorship quote cases | final tier is the lower of the proposal and the ceiling for every row; a forum host is always `forum`; an unverified authorship quote gives `dealer` | the ceiling returns the proposed tier |
| Prices (decision 28) | `test_prices.py::test_currency_amount_needs_verified_quote` | offline | 4 | drafts with a dollar amount inside and outside the verified evidence quote | an upgrade or maintenance entry with an unquoted amount is dropped; an unquoted amount in a v1 field fails validation | the currency rule removed |
| SC10 | `test_render.py::test_brief_loads_nothing_external` | offline | 4 | the 4 recorded briefs plus one synthetic fixture per conditional section: `budget_stopped`, `happened_before`, upgrade options, maintenance due, the all forum warning | an allowlist over the parsed tree: only a fixed set of elements; the only `link` is `rel="icon"` with `href="data:,"`; `meta` limited to charset, viewport, robots and the CSP (`default-src 'none'; style-src 'unsafe-inline'`); no `on*` attributes; no meta refresh and no `base`; the only URL bearing attribute is `a[href]` matching `^https?://` (protocol relative `//` rejected); no `url(`, `@import`, `image-set(` or `@font-face` in style elements or style attributes | a stylesheet link, web font, inline event handler or `style="background:url(...)"` added to the template |
| SC10 (escaping) | `test_render.py::test_all_model_text_escaped` (Hypothesis over every string field, found by walking the Pydantic models, with the v1 guardrail test 10 payload as an explicit example) and `::test_non_web_url_renders_as_text` | offline | 4 | hostile strings (`<script>`, `<img onerror>`, `"`, `'`) injected into every model string field and into untrusted non model fields: page titles, evidence quotes, host, symptom, identity, and service record fields | no injected `<`, `"` or `'` survives unescaped, in text or in attribute context; `javascript:` URLs never become links | escaping removed from any one field, or attribute values escaped without `quote=True` |
| SC10 (v1 format) | `test_render.py::test_legacy_render_is_byte_identical` and `::test_v2_render_matches_reviewed_golden` | offline | 4 | `briefs/*.html` read only; tracked v2 goldens that you approve once at the Phase 4 gate | legacy: rendering each recorded brief with `legacy=True` and no validate equals the published file byte for byte; v2: rendering through validate equals the reviewed v2 golden | section order changed; any unapproved change to v2 output |
| Property page (beyond SC10, PRD:112) | `test_render.py::test_property_page_self_contained_escaped_and_labeled_synthetic` | offline | 4 | the seed registry | the same allowlist and escaping checks as SC10; the page states that its data is synthetic (PRD:109) | the synthetic label removed, or an external load added |
| Output paths (decision 51) | `test_cli.py::test_html_path_refused_inside_published_paths` | offline | 4 | `--html` values inside `briefs/`, `src/`, `demo-assets/`, `index.html`, `tool.html` | the CLI refuses each, and the defaults land under `data/` | the path check removed |
| SC11 (per run) | `test_ledger.py::test_call_refused_before_it_is_made` and `test_graph_budget.py::test_budget_stop_is_never_no_reliable_answer` | offline and replay | 1 (ledger), 2 (graph) | synthetic usage scripts | when a reservation would push the run over its cap, the model is not called (`ReplayChatModel.received` unchanged), status is `budget_stopped`, and `status != "no_reliable_answer"`; the retry is skipped with `budget_stopped` when it cannot be afforded | check moved after the call; budget stop routed to the refusal node |
| SC11 (overshoot) | `test_ledger.py::test_actual_over_reservation_is_recorded_and_blocks_next_call` and `::test_timed_out_call_is_charged_at_reservation` | offline | 1 | scripted usage at twice the estimate; a call that times out after it was sent | the full actual charge is recorded and logged as above its reservation; the next call is refused if it no longer fits; the overshoot is no larger than that one call's excess; a timed out call is charged at its full reservation, marked "estimated" | the charge capped at the reservation; the reservation released on timeout |
| SC11 (live post condition) | every run record written by the Phase 5 and 6 commands | LIVE | 5, 6 | the ledger | `ledger.run_total(run_id) <= RUN_CAP_USD`, reported per run as SC11 pass or miss | n/a (live measurement) |
| SC11 (build total) | `test_ledger.py::test_live_run_refused_when_build_total_would_pass_cap` and `::test_live_run_refused_when_ledger_missing` | offline | 1 | temp ledger preloaded with $4.90 of live spend; a missing ledger file | a live run cannot start when remaining build budget is below the per run cap; replay entries never count toward the build total; with no ledger file a live run is refused unless `--new-ledger` is passed | build total summed over replay rows, or checked only at the end of a run; a missing ledger silently recreated |
| SC11 (accounting) | `test_ledger.py::test_charge_splits_cache_tokens` | offline | 1 | synthetic `usage_metadata` with cache reads and writes | charge equals the hand computed cost from `config.py` prices | cache tokens priced at the base input rate |
| Research limits (decisions 10, 16, 26) | `test_research_limits.py::test_sixth_search_is_blocked_and_run_continues`, `::test_research_full_allowance_is_not_budget_stopped`, `::test_loop_guard_keeps_collected_sources`, `::test_research_budget_ends_research_not_run`, `::test_credit_cap_and_http_432_stop_the_run` | replay | 2 | scripts: 7 search calls; 5 searches plus 3 fetches; a model that keeps calling after both caps (loop guard); research usage reaching $0.09; a credit total reaching 10 and a stub returning "Error 432" | search stub called 5 times and status not `budget_stopped`; a full allowance run reaches synthesize; at the loop guard research ends with sources built from the collector and the run is not `budget_stopped` and never `no_reliable_answer` directly; at the research budget, research ends and synthesize runs; credit cap and 432 end `budget_stopped` | model call cap mapped to `budget_stopped`; loop guard set below search plus fetch; 432 treated as an ordinary tool error |
| Tool failures | `test_research_tools.py::test_tool_failure_shapes_end_cleanly` (parametrized) | replay | 2 | stub returns: worker timeout, `{"error": "Error 500: ..."}`, `{"error": "Error 432: ..."}`, empty results, a string return | each non budget failure becomes an error `ToolMessage` with no artifact and the run reaches a clean terminal status; 432 ends `budget_stopped` | a wrapper built with `handle_tool_error=False`, or a failure shape not converted to `ToolException` |
| Parallel branches | `test_graph_fanout.py::test_three_branch_fanout_single_superstep` and `::test_latency_keeps_every_node_entry` | replay | 3 | route with history, graph and research; the ledger charging from the research branch and the lookup log written from the tool thread | no `INVALID_CONCURRENT_GRAPH_UPDATE`; no sqlite `ProgrammingError`; `latency` holds an entry for every node that ran, in parallel and in sequence | `latency` without its merge reducer; a sqlite connection shared across threads |
| Tracing off (PRD:113, decision 31) | `test_cli_tracing.py::test_cli_scrubs_tracing_unless_opted_in` | offline | 1 | `LANGCHAIN_TRACING_V2=true` and a dummy `LANGSMITH_API_KEY` in the child env | after the CLI entry point runs, `tracing_is_enabled()` is False; with `ADVISOR_TRACING=1` it is True and the photo and registry fields are hidden | the scrub moved after the first langsmith import |
| Replay graph isolation (decision 37) | `test_persist.py::test_cli_replay_leaves_runtime_graph_unchanged` | replay | 3 | a temp copy of `data/graph.json` | a CLI replay run leaves it byte identical and writes edges only to the replay graph path | persist ignores the mode |
| Key scan (PRD:20) | `test_privacy.py::test_no_key_patterns_in_tracked_files` | offline | 1 | every tracked file under `agent/` and `seed/`, except this plan and the scan's own pattern list | no `sk-ant-`, `lsv2_`, Tavily key prefix (likely `tvly-`, unverified), `Authorization` or `x-api-key` header, or long high entropy token | a fixture containing a fake key pattern |
| Latency (reported, no target) | `test_ledger.py::test_latency_recorded_per_node` | replay | 2 | any cassette | every node has a recorded duration | timing wrapper removed |

---

## 6. v1 port table

### 6.1 The 17 guardrail tests

Source: v1 `scripts/guardrail-tests.mjs` (line of each test in brackets).
13 of them pass a payload through `validateBrief`; 4 exercise only
`parseBriefJson`.

| # | v1 test | Verdict | v2 test | Reason |
|---|---|---|---|---|
| 1 | NRA renders no candidates and no try first steps [63] | Port | `test_validate.py::test_nra_with_empty_lists_is_accepted` (Phase 2) and `test_render.py::test_nra_renders_banner_without_candidates_or_steps` (Phase 4; also asserts no upgrade or maintenance section) | Core refusal behavior. The v1 payload already has empty lists, so the clearing itself is tested by SC3a (test 2 and the property test) |
| 2 | Misbehaving model's candidates stripped under NRA [87] | Port | `test_validate.py::test_refusal_clears_candidates_and_steps` (Phase 2) and `test_render.py::test_refusal_html_omits_invented_candidate_text` (Phase 4) | This is SC3a |
| 3 | NRA with malformed `searched` rejected [125] | Port with changes | `test_schemas.py::test_nra_with_non_list_searched_is_rejected` | Schema rejects it before rules run; assert the error location. In a live run the reject leads to retry then forced refusal. |
| 4 | NRA with no explanation block rejected [140] | Port | `test_validate.py::test_nra_without_explanation_block_is_rejected` | |
| 5 | ok with empty bibliography rejected [152] | Port | `test_validate.py::test_ok_with_empty_sources_is_rejected` | Graph route sources count as sources |
| 6 | Candidate citing a nonexistent source rejected [167] | Port | `test_citations.py::test_candidate_index_out_of_range_is_rejected` | SC6 |
| 7 | Try first step citing a nonexistent source rejected [192] | Port | `test_citations.py::test_step_index_out_of_range_is_rejected` | SC6 |
| 8 | Invalid tier label rejected [207] | Port with changes | `test_schemas.py::test_invalid_tier_label_is_rejected` plus `test_tiers.py` (lower never raise) | Literal type in the model facing schema; new lowering rules |
| 9 | `javascript:` source URL rejected before render [226] | Port with changes | `test_sources.py::test_non_http_retrieved_url_is_not_registered` and `test_render.py::test_non_web_url_renders_as_text` | In v2 code builds sources from retrieval, so the bad URL is injected as a retrieved result |
| 10 | HTML in model output escaped [241] | Port | `test_render.py::test_all_model_text_escaped` (the v1 payload is an explicit Hypothesis example) | SC10. v2 also escapes `'`. |
| 11 | JSON recovered from a fenced block [279] | **Obsolete, needs your OK** | none | Synthesis uses structured output; there is no fence |
| 12 | JSON recovered with no fence [284] | **Obsolete, needs your OK** | none | Same |
| 13 | Response with no JSON fails loudly [289] | Port with changes | `test_graph_retry.py::test_unparseable_synthesis_counts_as_validation_failure` | Keeps the intent: output that fails the schema goes to retry then forced refusal, never an empty ok brief |
| 14 | Citation split text reassembles [297] | **Obsolete, needs your OK** | none | Pinned a fix for Anthropic server search splitting text at citation blocks. Tavily and structured output produce no citation blocks. |
| 15 | Confirmed observed code candidate renders as confirmed [312] | Port | `test_render.py::test_observed_code_confirmed_candidate_renders_confirmed` (Phase 4), plus `test_observed_code.py::test_narrowing_rule` and `test_graph_retry.py::test_zero_match_twice_ends_forced_refusal` (Phase 2) | |
| 16 | Warranty cautions keep their citation [341] | Port | `test_render.py::test_warranty_caution_keeps_citation` | |
| 17 | Bare string warranty caution normalized [363] | Port with changes | `test_schemas.py::test_bare_string_caution_becomes_uncited` | Needs a before validator and defaults for the fields this payload omits |

Totals: 9 port, 5 port with changes, 3 obsolete. The published line
"Seventeen offline tests hold that behavior" (DECISION-LOG.md, NO RELIABLE
ANSWER entry) is loose: 4 of the 17 cover the refusal. This table is the first
public list of the 17, and v2 log entries will say "17 guardrail tests, 4 of
which cover the refusal".

### 6.2 Validator checks to Python functions

v1 `lib/brief-validate.ts` mutates the brief in place and throws on the first
failure. v2 splits it into `agent/rules/v1_parity.py` (exact port, the layer
SC1b compares) and the v2 rules in section 8.5. SC1b covers the parity layer
only; the model facing `BriefDraft` layer is compared with v1 by
`test_schemas.py::test_draft_fields_share_parity_semantics` (section 5). SC1b
compares decisions and reason codes, never error text: the harness holds a table
from each v1 error message to a reason code, and v2 raises the same codes.

| v1 check (brief-validate.ts line) | Behavior | Python function |
|---|---|---|
| status in {ok, no_reliable_answer} (39 to 41) | throw | `check_status` (v2 adds `budget_stopped`, a proposed difference, decision 21) |
| sources is an array (42 to 44) | throw | `check_sources_array` |
| each source url is text, title is text or missing, url matches `^https?://` (case insensitive scheme), tier in the three values (45 to 53) | throw; a null source element throws | `check_source` |
| try_first and candidates not arrays become `[]` (58 to 59) | normalize | `default_lists` |
| NRA: block present; `searched` string list with no default; `found` defaults `[]`; `why_insufficient` text (61 to 66) | throw | `check_nra_block` |
| NRA: clear try_first and candidates, return early (70 to 72) | normalize | `clear_on_refusal` (v2 also clears upgrades and maintenance) |
| ok: sources non empty (75 to 77) | throw | `check_ok_has_sources` |
| ok: `no_reliable_answer` set to null even if filled (78) | normalize | `null_nra_on_ok` (kept for parity; see risk R11) |
| step `step` text, `detail` text or missing, `safety_flag` becomes `=== true`, index in range (80 to 85) | throw or coerce | `check_step` |
| candidate meaning, action text; `why_shown` text or missing; `who` not in {anyone, technician} becomes technician; `confirmed` becomes `=== true`; index in range; `code` never checked (88 to 95) | throw or coerce | `check_candidate` |
| warranty_caution text; bad index becomes null (97 to 101) | throw or null | `check_warranty_caution` |
| warranty age statement text; cautions not array becomes `[]`; bare string caution becomes `{text, source_index: null}`; entry text; bad index null; `verify` string list defaulting `[]` (104 to 112) | throw, coerce or null | `check_warranty` |
| happened_before `matches` becomes `=== true`; summary text or missing (115 to 116) | coerce or throw | `check_happened_before` |

Parity traps the port must handle, each with an SC1b edge payload:

- An index of `true` is rejected by v1 (`Number.isInteger(true)` is false);
  Python treats `True` as an int, so bools are excluded explicitly.
- JSON `2.0` is the integer 2 in JavaScript and accepted; Python parses a float,
  so integral floats are accepted. The string `"0"` is rejected; Pydantic lax
  mode would coerce it, so these fields use strict checks.
- `safety_flag`, `confirmed`, `matches` use `v is True`, not Pydantic coercion
  (lax mode would turn `"true"` into True, the unsafe direction for
  `confirmed`).
- `who` is normalized by a before validator, not a Literal that rejects.
- `code`, `matched_identity`, `observed_code` accept any JSON type in the parity
  layer.
- Extra keys are ignored (`extra="ignore"`).
- A truthy primitive in an object slot (for example `happened_before: "yes"`)
  throws in v1 under ES module strict mode (inferred, not run); SC1b confirms.
- The harness parses JSON strictly (Python's default accepts `NaN`; JavaScript
  does not).
- Any exception counts as a v1 rejection. A native TypeError maps to the reason
  code `v1_type_error`, and v2 must give that code for the same payloads.
- Nothing is trimmed and comparisons are case sensitive except the URL scheme.

Checks v1 never tested (1, 2, 3a, 3b, 4, 7, 8, 11 to 14, 16 to 20, 22 to 25, 27,
29 to 31 in the reader's numbering) each get at least one SC1b edge payload.

v1 `parseBriefJson` (9 to 18) is obsolete under structured output.

### 6.3 Renderer items

From v1 `lib/brief-html.ts`. A Phase 0 scratch port reproduced all 4 briefs
byte for byte. The v2 renderer must do the same in `legacy=True` mode, with no
validate, before any v2 change (Phase 4 target), which makes the published
briefs golden files for `test_legacy_render_is_byte_identical`. Rendering
through validate is compared instead against v2 goldens you review once at the
Phase 4 gate, because validate changes sources (pruning, renumbering, tier
lowering, safety order); in the recorded FLO brief, for example, `sources[0]` is
never cited and would be pruned.

| Item | v1 | v2 |
|---|---|---|
| Escaping | `esc` (7 to 13): `&`, `<`, `>`, `"`; not `'` | `html.escape(s, quote=True)` (also `'`). Proposed difference (decision 21): bytes change wherever an apostrophe appears. |
| URL allowlist | `^https?://` case insensitive, in validator (20, 48 to 50) and `safeUrl` (19 to 22) | Same regex in both places; failing URL renders as plain text |
| Unescaped index label | `[${i + 1}]` (36) | Computed from a validated int only |
| Section order | header; NRA box; warranty caution; all forum warning; happened before (only if `matches`); try this first; documented candidates; warranty; sources; footer | Same order. New sections: `budget_stopped` box in the NRA slot; upgrade options after candidates; maintenance due after warranty. Safety steps now sorted first. |
| Tier badge | origin tier only (15 to 17) | origin tier badge with host text next to it (decision 9.2) |
| Footer (151) | "This brief reproduces documented material found at the listed sources on {date}. It is not a diagnosis. Confirm any code on the unit's own display before ordering parts." | Keep the words. Each source line shows its own retrieval date so the footer stays true on the graph route. Refusal and budget stop briefs get a footer without "reproduces documented material" (decision 47, needs your OK). |
| External loads | none; inline CSS; system fonts; `noindex` | Same, plus CSP meta and `<link rel="icon" href="data:,">` so browsers do not request `/favicon.ico` |

### 6.4 Wording changes forced by the no dash rule (need your OK)

| v1 location | v1 text | v2 text |
|---|---|---|
| brief-html.ts:44 (serial, date placeholders) | U+2014 | "not recorded" |
| brief-html.ts:110 (null code) | U+2014 | "no code" |
| brief-html.ts:53 (refusal intro) | "That is the honest result" followed by an em dash | "That is the honest result: nothing below is guessed." |
| brief-html.ts:160 (title) | "Service brief" em dash maker model | "Service brief: {maker} {model}" |
| prompts.ts:9, 32 (twice), 36 | em dashes inside rules | replaced by a semicolon or colon (section 8.8) |

v1 guardrail test names contain 4 em dashes; ported names are reworded.

### 6.5 v1 live cases (PRD:31)

Source: v1 `scripts/run-tests.mjs`. PRD:31 makes these 6 cases the Phase 5 and 6
live suite. Each v1 check is carried over, either asserted in the run record or
reported, with the v2 differences that apply.

| v1 case (line) | v2 run | v1 checks carried over | v2 differences |
|---|---|---|---|
| E1 clear plate, Optima 880 (14 to 24) | Phase 5 FLO run on `plate-clear.jpg`, and the Phase 6 plate run | asserted: model matches "Optima 880", manufacturer matches "Sundance", serial equals the printed value | read_plate moves from Opus 5 to Haiku 4.5, so a miss is a model finding, reported, not a code failure |
| E2 blurry plate (26 to 39) | Phase 6 plate run on `plate-blurry.jpg` | asserted: model, serial and manufacture date are null and `unreadable` | code also nulls any field the model marks `unreadable` (PRD:142), so the check reads the raw model output as well as the final extraction |
| B1 Optima 880, "not heating" (45 to 68) | Phase 6 vague symptom repeat (graph top up); a first lookup on the research route only if you approve it (decision 36) | asserted: status ok; a FLO family candidate; some candidate cites a manufacturer or dealer source. Reported: try_first mentions a filter check and a water level check | tier lowering (decision 5) may turn a manufacturer source into dealer, which still passes; search cap 2 on the top up route |
| B2 Optima 880, "panel shows FLO" (71 to 90) | Phase 5 FLO run | asserted: status ok; exactly 1 confirmed candidate; `observed_code` set | `observed_code` and `confirmed` are set by code (decision 7); a `budget_stopped` is reported as a miss with its cause |
| B3 Trane XR16 4TTR6036, "AC not cooling upstairs" (93 to 117) | Phase 6 Trane first lookup | asserted: if ok, sources non empty and every candidate's index resolves; if refused, the explanation block is present | `budget_stopped` is a third outcome, reported separately; search cap 5 instead of 8 |
| B4 fabricated Aquarest ZX-9000 Pro, "not heating" (120 to 138) | Phase 6 SC3b, 3 runs | asserted: status `no_reliable_answer`; no candidates; `searched` non empty | `searched` comes from the code built trail; forced refusals and budget stops are counted separately (decision 33) |

---

## 7. Schema

### 7.1 Authority

**`types.ts` is authoritative for field names and value types; the v1 validator
is authoritative for which fields may be missing; the 4 recorded briefs in
`src/fixtures.js` are the conformance corpus.** The inner brief objects in
`fixtures.js` match `types.ts` exactly, so there is no conflict there. The
fixture wrapper (`identity`, `symptom`, `share`, `usage`, plus demo only
`label`, `plate`, `stops_here`) does not match `BriefResult` (`share_path`,
`generated_at`, `all_forum`, `usage`), so PRD:42's "matching fixtures.js
exactly" cannot apply to the wrapper. Proposed wording for the Phase 1 row:
"Brief and Extraction match types.ts field for field and accept every recorded
brief in fixtures.js unchanged."

Every field in `types.ts` is declared required, but v1 accepts payloads that
omit many of them (guardrail test 17 omits four). Declaring them required in
Pydantic would fail SC1b, so the parity models use v1's defaults. These lenient
parity models are never sent to the API. The model facing `BriefDraft` is a
separate, flat model in which every field is required, because Anthropic's
structured output counts every defaulted field as optional and caps optional
and union parameters (decision 22).

### 7.2 Field by field (`agent/schemas.py`)

| types.ts (line) | Field | Pydantic | Default when missing |
|---|---|---|---|
| `Confidence` (3) | | `Literal["high","low","unreadable"]` | |
| `Extraction` (5 to 16) | manufacturer, model, serial, manufacture_date | `str \| None` | required (strict JSON schema at v1 anthropic.ts:56 to 77) |
| | confidence | object of four `Confidence` | required |
| `Identity` (18 to 23) | four fields | `str` (empty string means unknown) | required at intake |
| `SourceTier` (25) | | `Literal["manufacturer","dealer","forum"]` | |
| `BriefSource` (27 to 31) | title | `str \| None` | `None` |
| | url | `str` matching `^https?://` | required |
| | tier | `SourceTier` | required |
| `TryFirstStep` (33 to 38) | step | `str` | required |
| | detail | `str \| None` | `None` |
| | safety_flag | `bool` via `v is True` | `False` |
| | source_index | strict int, bools excluded, integral floats accepted | required |
| `Candidate` (40 to 48) | code | `Any` in parity layer; `str \| None` in the model facing draft | `None` |
| | documented_meaning, documented_action | `str` | required |
| | who | before validator to `"anyone"` or `"technician"` | `"technician"` |
| | why_shown | `str \| None` | `None` |
| | source_index | strict int | required |
| | confirmed | `bool` via `v is True` | `False` |
| `Brief` (50 to 69) | status | `Literal["ok","no_reliable_answer"]` plus v2 `"budget_stopped"` | required |
| | matched_identity, observed_code | `Any` in parity, `str \| None` in v2 | `None` |
| | warranty_caution | `{text: str, source_index: int \| None} \| None` | `None` |
| | happened_before | `{matches: bool, summary: str \| None} \| None` | `None` |
| | try_first, candidates | lists | `[]` (also when present but not a list) |
| | warranty | `{age_statement: str, cautions: [...], verify: list[str]} \| None`; bare string caution coerced | `None` |
| | no_reliable_answer | `{searched: list[str], found: list[str] = [], why_insufficient: str} \| None` | `None` |
| | sources | `list[BriefSource]` | required |
| `BriefResult` (71 to 81) | wrapper | `RunResult` in v2 (below) | |

### 7.3 Deltas

| Delta | Shape | Reason | Status |
|---|---|---|---|
| `upgrade_options` | `list[UpgradeOption]`, default `[]`. Each: `successor_manufacturer: str \| None`, `successor_model: str`, `reason: Literal["discontinued","parts_unavailable"]`, `summary: str`, `source_index: int`, `evidence: str` | G4, SC9 | **Approved by the PRD** (PRD:146). `evidence` field needs approval. |
| `maintenance_due` | `list[MaintenanceItem]`, default `[]`. Each: `task`, `interval` (verbatim), `source_index` (must be manufacturer tier after lowering), `evidence: str`, `last_done_record_id: str \| None`, `due_date: str \| None` (computed by code from the registry) | G3 | **Approved by the PRD** (PRD:146). `evidence`, `last_done_record_id`, `due_date` need approval. |
| Host next to tier | `BriefSource.host: str`, from the URL by code, never the model | PRD decision 9.2 | **Approved by the PRD** (PRD:173) |
| `budget_stopped` status | widen `Brief.status`; a budget stopped brief carries header, sources retrieved so far, the search trail, and the stop reason; candidates, steps, upgrades and maintenance cleared | PRD:145 | **Needs approval** (not in types.ts; v1 rejects it at brief-validate.ts:39 to 41). The alternative, a wrapper field, keeps strict parity but hides the status from the brief file. Recommend widening. |
| `BriefSource.retrieved_at` | ISO date | graph route cites sources fetched in earlier runs; keeps the footer true | Needs approval |
| `BriefSource.origin` | `Literal["search","extract","graph"]` | SC6 says "retrieved in that run"; graph sources are loaded in that run from validated edges | Needs approval |
| `BriefSource.proposed_tier` | the model's proposal, kept beside the final tier | shows when code lowered a tier | Needs approval |
| `happened_before.record_id` | `str \| None` (in the draft, a required string, empty meaning none) | SC8 citation to a service record. Records are not added to `sources` because v1 rejects non web URLs and renders sources as links. | Needs approval |
| `Candidate.evidence` | `str \| None`, verbatim quote from the cited source | code grounding check (HVAC "no invented codes") and graph edge evidence | Needs approval |
| `no_reliable_answer.searched` | filled by code from the real search trail | PRD:147 "full search trail" | Needs approval (v1 let the model fill it) |
| `refusal_origin` on the result | `Literal["model","forced"]` | forced refusals are not documentation findings; SC3b reports them separately | Needs approval |
| `warranty.age_statement` source | computed by code from the registry install or purchase date, else the manufacture date, with the registry `record_id` | G3; decision 29 | Needs approval |
| `BriefDraft` (model facing) | the brief minus `sources`, `observed_code`, `searched`; plus `source_tiers: list[{source_index, tier, authorship_quote}]`. Every field required, no defaults. Free text that may be absent (`detail`, `why_shown`, `summary`, `evidence`, `authorship_quote`) is a required string where empty means none; code converts empty to `None`. Nullable (`anyOf`) only where absence carries meaning (for example `matched_identity`, `warranty`, `no_reliable_answer`, `happened_before`, candidate `code`, caution `source_index`), at most 16 in total and 12 or fewer targeted. Enum values are lowercase, with a before validator lowercasing the model's reply | code builds sources; the model points by index; decision 22 | Internal type, no types.ts counterpart; checked by `test_brief_draft_within_structured_output_limits` |
| `RunResult` wrapper | `brief`, `thread_id`, `mode`, `route`, `route_reason`, `generated_at`, `all_forum`, `cost_usd`, `tavily_credits`, `searches`, `fetches`, `latency_s`, `usage` | replaces `BriefResult`; `usage.web_searches` meant Anthropic searches and no longer applies | Wrapper, not the brief |
| Identity null vs empty string | intake accepts `""` (v1) and converts to `None` inside v2 state | v2 extraction uses null | Internal |

---

## 8. Architecture as it will be built

### 8.1 Package layout

`pyproject.toml`, `uv.lock` and `.python-version` sit at the repo root so the
package imports as `agent` and the PRD's `agent/config.py` path holds. No
tracked folder is named `data`, because the `.gitignore` pattern `data/`
matches at every depth.

```
agent/
  config.py            model IDs, prices, caps, max_tokens, Tavily settings
  cli.py               the advisor command
  schemas.py           Pydantic models (section 7)
  state.py             AdvisorState TypedDict, RunContext dataclass
  graph.py             build_graph(checkpointer)
  models.py            model and tool factories (replay or live)
  ledger.py            cost ledger (section 8.9)
  registry.py          SQLite registry access; registry_schema.sql
  kg.py                NetworkX knowledge graph load, query, add, save
  prompts.py           ported v1 rules (section 8.8)
  nodes/               intake, read_plate, confirm_identity, route, history,
                       graph_lookup, research, gather, synthesize, validate,
                       refuse, upgrade_check, render, persist
  rules/               v1_parity.py, tiers.py, observed_code.py, safety.py,
                       citations.py, evidence.py, grounding.py
  research/            agent.py (create_agent), tools.py (search, fetch,
                       excerpts), middleware.py (SpendCap, ToolBudgetDone)
  live/                eval_sc3b.py, eval_sc7b.py (LIVE evaluations run by
                       `advisor eval`; outside the pytest test paths)
  render/              brief_html.py, property_page.py
  replay/              replay_model.py, tool_stubs.py, cassettes.py,
                       build_cassettes.py, privacy_diff.py
  tests/               test files from section 5; conftest.py; helpers.py
                       (spawn_offline_child, payload adapter); mutations.toml;
                       netguard/sitecustomize.py; harness/v1_validate.mjs,
                       harness/netguard.mjs; cassettes/*.json;
                       fixtures/ (payloads, graphs, synthetic page texts)
seed/
  registry_seed.json   synthetic properties, appliances, records
  graph.json           empty until Phase 6; then built only from edges whose
                       brief_run_id matches a live ledger row, after full
                       re verification with page text present
data/ (gitignored, runtime only)
  registry.sqlite, checkpoints.sqlite, graph.json (live runs only),
  replay_graph.json (CLI replay runs), pages/<sha256>.txt, lookups/<id>.json,
  briefs/<run_id>.html, property/<id>.html, eval/<run_id>.json
  ledger/ledger.sqlite (never deleted; see decision 52)
```

### 8.2 State

`AdvisorState` is a TypedDict holding only JSON native values (str, int, float,
bool, None, lists, string keyed dicts), timestamps as UTC ISO strings, and no
LangChain message objects. Pydantic models are used only at node boundaries.
`LANGGRAPH_STRICT_MSGPACK=true` is set in the CLI entry point and in
`conftest.py` before any langgraph import, and a test asserts `json.dumps`
succeeds on state after every node.

| Field | Type | Written by |
|---|---|---|
| run_id, mode | str | intake |
| property_id, appliance_id | str or None | intake |
| symptom | str | intake |
| photo | `{path, sha256}` or None (never base64) | intake |
| extraction | dict or None | read_plate |
| identity, identity_confirmed, observed_code, confirm_prompt | dict, bool, str or None, str or None | confirm_identity |
| route, route_reason | list[str], str | route |
| sources | append only list (`operator.add` reducer) of `{source_id, url, host, title, retrieved_at, origin, text_sha256, snippet}` | research, graph_lookup |
| search_trail | append only list of `{query, n_results, credits, at, status}` | research |
| graph_hits, history_hits | list[dict] | graph_lookup, history |
| draft | dict or None | synthesize |
| validation_errors | list[str], replaced each pass | validate |
| validation_failures, research_attempts | int | validate, research |
| status | `running`, `ok`, `no_reliable_answer`, `budget_stopped` | several |
| refusal_origin, stop_reason | str or None | validate, refuse, research |
| brief | dict or None | validate, refuse |
| cost_usd, tavily_credits | float, int (mirrors of the ledger, which is the truth), each with a sum reducer so every writer returns only its delta | every paid node |
| latency | `Annotated[dict[str, float], merge_dicts]`, so each node adds its own entry | every node |
| html_path | str or None | render |

Parallel writes: history, graph_lookup and research run in one superstep, and
LangGraph raises `INVALID_CONCURRENT_GRAPH_UPDATE` when two branches write a
key that has no reducer. So every key written inside the fan out group either
has a reducer (`sources`, `search_trail`, `latency`, `cost_usd`,
`tavily_credits`) or is written by one branch only: `status` and `stop_reason`
are written only by research within the group.

`RunContext` (passed per call, not checkpointed, so `advisor resume` passes it
again): mode; ledger, registry, graph and pages paths (paths, never open
connections, section 8.9); cassette (replay only; the CLI reads it from
`ADVISOR_CASSETTE` or `--cassette`); run caps; and a per run tool artifact
collector that the tool wrappers write to, so sources can be built even if the
research agent raises.

Append only `sources` keeps indexes stable across the retry. Renumbering happens
once, after validation passes, during pruning.

### 8.3 Nodes

| Node | Model or code | Inputs | Outputs |
|---|---|---|---|
| intake | code | CLI args | validated `Intake` model (typed, so v1's `.trim is not a function` errors cannot recur), appliance from registry, candidate observed code from the symptom (a token of 2 to 6 capital letters or digits after "shows", "displays", "code", "error", or in quotes) |
| read_plate | model (Haiku, vision), skipped when an identity is typed | photo | extraction; code sets every `unreadable` field to null |
| confirm_identity | code, `interrupt()` | extraction or typed identity, candidate observed code | one interrupt per node run; the owner confirms or edits identity and observed code; if model is still null, sets `confirm_prompt` and loops back to itself through a conditional edge |
| route | code first, Haiku classifier only when rules cannot decide | identity, observed code, graph, history | route list and reason |
| history | code (SQL) | appliance ID | service records, purchase date, warranty terms |
| graph_lookup | code (NetworkX) | model, family, code | validated edges and their sources (origin `graph`, original `retrieved_at`) |
| research | model (create_agent on Haiku) | identity, symptom, prior validation errors | sources and search trail built by code from the successful tool artifacts in the `RunContext` collector; `budget_stopped` only on the run or build dollar cap, the credit caps or Tavily's plan limit |
| gather | code | status | routes to synthesize, or to render when `budget_stopped` |
| synthesize | model (Haiku in `cheap`, Sonnet 5 in `full`), `with_structured_output(BriefDraft, method="json_schema", include_raw=True)` | numbered sources, each given as its title, host and code cut verbatim excerpts of `raw_content` (snippet only when no raw text exists), truncated in code to at most 11,200 tokens in total; history records with their IDs, dates and symptoms; registry dates and warranty terms; identity; symptom; prior errors | draft (raw message kept for the ledger); schema failure counts as a validation failure |
| validate | code | draft, sources, state | brief or errors (section 8.5) |
| refuse | code | trail, errors | NO RELIABLE ANSWER with the code built search trail and `refusal_origin="forced"` |
| upgrade_check | code, runs only on `ok` briefs | draft, graph edges | collects candidate upgrade options from SUPERSEDED_BY and PART_DISCONTINUED edges, registers their graph sources in `sources`, and sends the draft back through validate once (decision 27). It enforces nothing itself |
| render | code | brief | self contained HTML, written by default to `data/briefs/<run_id>.html` (decision 51) |
| persist | code | everything | upserts keyed by thread ID (idempotent): lookup log, registry service record of the lookup, and graph edges from the validated brief after the edge evidence check (section 8.11). Edges go to `data/graph.json` only in `cheap` or `full` with live ledger charges; replay writes to the `RunContext` graph path (decision 37) |

Parallel branches (history, graph_lookup, research) are each a single node with
a plain edge to `gather`, so `gather` and `synthesize` run once per attempt; a
test asserts that.

### 8.4 Routing and retry decision tables

Route (first matching row wins; `history` is added whenever the appliance has
records):

| # | Condition | Route | Search cap |
|---|---|---|---|
| 1 | observed code, and the graph has the model (or its family) with a HAS_CODE edge for that code carrying verified evidence | graph | 0 |
| 2 | observed code, graph has the model but not that code | graph plus research top up | 2 |
| 3 | no observed code, graph has the model with verified edges; Haiku classifier says the symptom matches documented causes in the graph | graph | 0 |
| 4 | no observed code, graph has the model; classifier says it does not match, or is unsure | graph plus research top up | 2 |
| 5 | graph has nothing verified for the model or family | research | 5 (`cheap`, `full`) |

After validate:

| Condition | Next |
|---|---|
| status `budget_stopped` | render |
| no errors, status `no_reliable_answer` | render (upgrade_check never runs on a refusal) |
| no errors, status `ok`, upgrade pass not yet done | upgrade_check, which returns to validate once |
| no errors, status `ok`, upgrade pass done | render |
| errors, `validation_failures == 1`, and remaining run budget is at least the retry pass's typical estimate plus the full synthesize reservation (decision 23; about $0.064 in `cheap`) | research with the retry pass caps (`cheap`: search 2, fetch 0, loop guard 4; `full`: the main route caps), with the errors passed to synthesize |
| errors, `validation_failures == 1`, retry not affordable | render as `budget_stopped` (stop reason "retry not affordable", reported separately in SC3b) |
| errors, `validation_failures >= 2` | refuse |

`recursion_limit` 40 on every call as a backstop, never as the loop stop.

### 8.5 Validate: the v2 rules on top of the parity layer

Validate is the only node that enforces rules (decision 27). Every rule function
lives in `agent/rules/` and is called only from here. The two named exceptions
are read_plate's `unreadable` nulling (PRD:142) and the persist time edge
evidence check (PRD:16, PRD:148). Validate runs once per synthesize attempt, and
once more on an `ok` brief after upgrade_check adds graph derived options.

Order: the draft is converted to the parity shape (empty strings to `None`),
then the parity layer (section 6.2), then:

1. **Citations.** Every `source_index` resolves into this run's source registry.
   Sources are never taken from model output.
2. **Tiers.** Final tier = the lower of the model's proposed tier and the host
   ceiling (section 8.6). A source the model gave no tier gets `forum`.
3. **Observed code.** Decision 7. Code normalization for matching: Unicode NFC,
   uppercase, strip surrounding whitespace and punctuation. Free text codes such
   as a mode description are matched only if they normalize equal.
4. **Code grounding.** A non null candidate code must appear verbatim (section
   8.11 normalization) in the cited source's fetched text or in its `evidence`
   quote verified against that text. If the cited source has no raw text, the
   code must appear in its Tavily snippet (decision 25, recommended option);
   otherwise validation fails. The one exemption: in replay, for a cassette
   whose `provenance.derived_from` names a v1 lookup, the check reports
   "unverifiable" and does not fail, and that limit is stated in the SC2
   results. The exemption is keyed on provenance and mode, never on "no text
   was recorded".
5. **Safety ordering.** Stable sort, safety first.
6. **Refusal and budget stop clearing.** Decision 9.
7. **happened_before.** `record_id` must name a record of this appliance loaded
   in this run, else `happened_before` is set to null (same treatment as v1 gives
   a bad caution index).
8. **Registry dates.** `warranty.age_statement` is computed by code from the
   registry install or purchase date when one exists, else the manufacture date,
   and cites the registry `record_id`; warranty terms are quoted from the
   registry only (decision 29).
9. **Upgrades and maintenance.** Entry dropped unless index resolves and the
   `evidence` quote is verified; maintenance also needs final tier
   `manufacturer` and the interval text inside the quote. `due_date` is computed
   here by code from `maintenance_log`; a model supplied value is ignored.
   Upgrade options proposed by the model and those added by upgrade_check from
   graph edges go through the same checks.
10. **Prices.** A currency amount in model written text (`summary`, `why_shown`,
    `detail`, `age_statement`) must appear inside a verified evidence quote from
    the cited source; otherwise the upgrade or maintenance entry is dropped, and
    in a v1 field validation fails (decision 28).
11. **Prune and renumber.** Uncited sources are removed and every index is
    renumbered. This runs last, after graph derived options are in.
12. **ok with a filled refusal block** keeps v1 behavior (block nulled). See R11.

### 8.6 Tier ceiling by host

| Host class | Ceiling |
|---|---|
| Known forum or Q&A host (list in `config.py`: reddit.com, quora.com, justanswer.com, stackexchange hosts, hosts starting with `forum.` or `forums.`, and others added with your review) | forum, always, even for a maker's PDF |
| The maker's own domain (map in `config.py`, for example the Sundance and Trane domains) | manufacturer |
| Any other host, with an `authorship_quote` verified verbatim in the fetched text and containing the maker's name | manufacturer |
| Any other host | dealer |

The host is always stored and shown next to the badge. Offline, v1 derived
cassettes have no page text, so a manual archive source that v1 labeled
manufacturer will show `dealer` in replay. That is an expected difference,
proposed for your approval in decision 21.

### 8.7 Research agent and middleware

`create_agent(model, tools=[search, fetch], system_prompt=RESEARCH_PROMPT,
middleware=[...], checkpointer=False)` called inside the `research` node, never
added as a graph node, so the transcript stays out of parent checkpoints and
code builds sources before anything reaches parent state. No `response_format`
(the auto strategy picks a different path for fake models than for Claude).

Tools are our own `@tool(response_format="content_and_artifact",
handle_tool_error=True)` wrappers. langchain-core 1.6.3 defaults
`handle_tool_error` to False and re raises, and the `ToolNode` that
`create_agent` builds re raises everything except argument validation errors,
so without this any Tavily failure would crash the run with a traceback.

- `search(query)`: calls Tavily search with the pinned settings on a daemon
  `threading.Thread` with a 30 second timeout (decision 38), and returns compact
  numbered text for the model (title, URL, snippet, at most about 430 tokens per
  result) plus an artifact `{query, results: [{url, title, content,
  raw_content}], credits}`. Raw text goes to `data/pages/<sha256>.txt`.
- `fetch(url)`: Tavily extract, basic, text, no query. The model sees verbatim
  excerpts cut by code from `raw_content`, at most 1,500 characters including
  title and URL, never the whole page.
- Excerpts (decision 24): code cuts windows of `raw_content` around the model,
  family, observed code, part numbers and symptom terms, under a per source
  character budget. The same excerpts are the source text synthesize sees, so
  every quote the model makes can be verified against `raw_content`.
- Failure shapes: the worker `TimeoutError`, `{"error": ...}` content, a string
  return from the inner tool, and empty results are converted to
  `ToolException`, so the model gets an error `ToolMessage` with no artifact.
  "Error 432" or "Error 433" inside the error value (Tavily's plan limit; the
  status is visible only in the text, decision 38), and a per run or build
  credit cap, raise the ledger's `BudgetExceeded` instead, which ends the run
  `budget_stopped`. A request abandoned by the timeout is counted as one credit,
  because it may still reach Tavily.

Sources are built only from successful tool artifacts. Each wrapper writes its
artifact to the per run collector on `RunContext` and its raw result to the
lookup log as it arrives, at the same point, so the trail and the sources
survive even if `agent.invoke` raises.

Middleware list (first is outermost):

| Middleware | main route (replay, cheap, full) | graph top up | `cheap` retry pass |
|---|---|---|---|
| `ModelRetryMiddleware(max_retries=1, retry_on=(ModelRateLimitError, ModelAPIError, ModelConnectionError, ModelTimeoutError), on_failure="error")` | yes | yes | yes |
| `ToolBudgetDone` (custom `before_model` hook with `can_jump_to=["end"]`): ends the agent once both tool caps are spent, since the final text is unused | yes | yes | yes |
| `ModelCallLimitMiddleware(run_limit=N, exit_behavior="error")`, a loop guard only, N = search cap + fetch cap + 2 | 10 | 5 | 4 |
| `ToolCallLimitMiddleware(tool_name="search", run_limit=N, exit_behavior="continue")` | 5 | 2 | 2 |
| `ToolCallLimitMiddleware(tool_name="fetch", run_limit=N, exit_behavior="continue")` | 3 | 1 | 0 |
| `SpendCap` (custom `wrap_model_call`, innermost so every retry passes through it) | yes | yes | yes |

The research node catches `ModelCallLimitExceededError` and ends research with
the sources in the collector; it is not a budget stop. It catches the ledger's
`BudgetExceeded` and sets `budget_stopped` only for the run or build dollar cap,
the credit caps, and Tavily's plan limit. When research spend reaches
`RESEARCH_BUDGET_USD` (decision 26), SpendCap ends research the same way as a
tool cap and the run goes on to synthesize. Agent counters reset on each
`invoke`, so the retry pass gets its own caps; the per run dollar and credit
caps live in the ledger, which spans the whole run. read_plate, the classifier
and synthesize call models directly and go through the same `ledger.reserve`
and `ledger.charge` functions.

### 8.8 Prompts

Ported close to word for word from v1 `lib/prompts.ts`. PRD:26 allows changes
only where v1 refers to its search tool or its JSON fence; the dash edits are in
section 6.4. Extraction rules E1 to E5 are unchanged except the em dash in rule 3
becomes a semicolon. Brief rules 4 and 7 are unchanged. The output format
section is cut (structured output supplies the shape); its inline field notes
move into `Field(description=...)`. JSON rule J5 ("do not pad") is cut because
code prunes. Prompts ask; `validate` enforces.

Changes beyond the search tool, the fence and dashes, each for your approval at
the Phase 2 gate (decision 30):

| v1 location | v1 text (abridged) | v2 text | Why |
|---|---|---|---|
| prompts.ts:18 (rule 1) | "Search the live web for documentation matching this exact manufacturer and model..." | research prompt: "Use the search and fetch tools to find documentation matching this exact manufacturer and model..."; synthesis prompt: "Use only the numbered sources provided below." | search tool (allowed by PRD:26) |
| prompts.ts:20 (rule 2) | "a citation to a source you actually fetched in this session, identified by its index in your "sources" array" | "a citation to one of the numbered sources provided below, identified by its number"; tier becomes a proposal in `source_tiers` | code builds sources |
| prompts.ts:26, 30 (rules 3, 5) | "fetched sources", "a fetched source" | "provided sources", "a provided source" | code builds sources |
| prompts.ts:32 (rule 6) | "fill "no_reliable_answer" with exactly what you searched for, what you found, and why it is not enough... Do not invent sources." | "fill "no_reliable_answer" with what you found and why it is not enough; the search trail is added by code." "Do not invent sources" is dropped | code builds the trail and the sources |
| prompts.ts:36 (rule 8) | "state install-date math only... the age of the unit from its manufacture date (if known) to today... citing a fetched source" | "Do not compute the unit's age; it is supplied from the property records or the manufacture date. If this equipment category commonly voids warranty for commercial or short term rental use, say so as a caution, citing a provided source..." | decision 29 |
| prompts.ts:38 (rule 9) | "If prior service notes were provided, compare them to the current symptom... one-line summary" | "If service records for this appliance are listed below, compare them to the current symptom. Fill "happened_before" with whether one matches, a one line summary, and that record's record_id. If no records are listed, set "happened_before" to null." | registry replaces pasted notes |
| new (synthesis) | none | "Every evidence, authorship or interval quote must be copied exactly from the source excerpts shown; do not reword, join or shorten across a gap." | decision 24 |
| prompts.ts:84 to 91 (user prompt) | "Prior service notes pasted by the owner"; "Search first, then answer in the required JSON format." | service records block with record IDs; research: "Search first."; synthesis: "Answer using the provided schema." | registry; fence removed |

### 8.9 Spend cap and ledger

`data/ledger/ledger.sqlite` (decision 52: its own folder, documented as never
deleted, so resetting the graph or checkpoints cannot reset recorded spend),
table `entries(id, ts, run_id, thread_id, mode, node, provider, model, kind,
input_tokens, cache_read, cache_write, output_tokens, tavily_credits, usd,
estimated, note)` where `kind` is `reserve`, `charge`, `release`, or `stop`.

Connections: the ledger, the registry and the lookup log each open a short lived
sqlite connection per operation, with WAL and a busy timeout. Parallel branches
run on LangGraph's thread pool and the Tavily wrapper writes from its own
thread, and a connection opened with the default `check_same_thread=True` raises
`ProgrammingError` on another thread. `RunContext` carries paths, never
connections.

Before every paid call: reservation = estimated input tokens (via
`count_tokens_approximately`, plus `TOOL_PROMPT_TOKENS` on any tool bearing
call, times `ESTIMATE_MARGIN` for the model, images at a conservative 1,600
tokens each) times the input price, plus `max_tokens` times the output price.
The call is made only if run spent plus open reservations plus this reservation
is at or under the run cap, and build spent plus this reservation is at or under
the build cap. After the call: charge actual usage (cache tokens split out) in
full, even when it is above the reservation, log any charge above its
reservation, and release the reservation. Because the output side is exact
(`max_tokens`) and only the input side is estimated, a run can exceed its cap
by at most one call's input estimate error: a few tenths of a cent in `cheap`
and under a cent in `full`. Every live run record states
`ledger.run_total(run_id) <= RUN_CAP_USD` as SC11 pass or miss. In live modes,
Anthropic's free token counting endpoint may be used for the estimate instead
(it is also an estimate and has its own rate limits); replay keeps the
approximate count.

A failed billed call (structured output error carrying `.ai_message`) is still
charged. A call that times out or loses its connection after the request was
sent is charged at its full reservation, marked `estimated`, because whether
Anthropic bills an abandoned request is unverified; `estimated` rows are
reconciled against the console at each LIVE gate. Charges are written
immediately, not through graph state, because commands from a retried attempt
are dropped.

Replay rows are written with `mode=replay`, priced at `REPLAY_PRICES` (Haiku
4.5 rates) against `REPLAY_RUN_CAP_USD` or a cassette's own `caps` block, and
never count toward the build total. `advisor` refuses to start a live run when
the remaining build budget is below the per run cap, and when the ledger file
is missing unless `--new-ledger` is passed. At each LIVE gate you also check
the Anthropic console, which is the real record, and the cumulative live spend
goes into that phase's decision log entry.

### 8.10 Replay harness, fake model, cassettes, privacy diff

**`ReplayChatModel(BaseChatModel)`**: implements `_generate` only (no `_stream`,
so tool calls and usage are never dropped on a streaming path), overrides
`bind_tools` (accepts an empty list), serves scripted `AIMessage`s with
`tool_calls`, `usage_metadata` and a fresh ID each time (because
`add_messages` overwrites a repeated ID silently), raises when the script runs
out instead of wrapping around, records every request in `received`, and
exposes `model` and `max_tokens` the way `ChatAnthropic` does so the ledger
reads it identically. For `method="json_schema"` it mirrors ChatAnthropic's
parsing path. It never compiles a grammar, so it cannot catch a schema the API
would reject (decision 22). Tool stubs return recorded artifacts, write to the
`RunContext` collector as the real wrappers do, and count calls.

**Cassette** (`agent/tests/cassettes/<case>.json`, `.json` never `.log`):

```
{
  "cassette_version": 1,
  "case": "flo",
  "provenance": {
    "derived_from": "v1 lookup 549892815cb6",
    "copied_fields": ["input.identity", "input.symptom", "brief"],
    "public_already": "src/fixtures.js case flo; briefs/549892815cb6.html",
    "synthetic_fields": ["research.script", "tool_results.*.query", "decoys"],
    "built_by": "agent/replay/build_cassettes.py",
    "reviewed": null
  },
  "input": {"identity": {...}, "symptom": "...", "plate_sha256": null},
  "read_plate": {"extraction": {...}},
  "resume": {"identity": {...}, "observed_code": "FLO"},
  "research": {"script": [...], "tool_results": [...]},
  "synthesize": [{"attempt": 1, "draft": {...}, "usage": {...}}],
  "page_texts": {},
  "caps": null,
  "expect": {"status": "ok", "candidates": 1}
}
```

Provenance of each part:

| Part | Source | Label |
|---|---|---|
| identity, symptom | typed v1 inputs, blank serial and date kept (the recorded warranty text depends on them) | derived, already public |
| read_plate extraction (FLO, vague, blurry) | v1 extract logs; plate by sha256 of `demo-assets/*.jpg` (byte identical) | derived, already public |
| brief content | v1 recorded brief, converted to `BriefDraft` with indexes remapped by URL | derived, already public |
| cited sources (18 unique URLs) and titles | v1 brief sources (model written titles, as published) | derived, already public |
| uncited search results | not copied; replaced by decoy results on `example.com` style URLs, interleaved between cited sources in the registry so an index shifted either way lands on a decoy or the wrong real source | synthetic |
| search queries and per call grouping | not recorded by v1; the synthetic queries deliberately differ from v1's recorded `searched` strings, so SC2 can tell the code built trail from v1's list | synthetic |
| per call token usage | v1 logged only per brief totals; split across scripted calls and scaled so each pass matches the section 9 estimate, so no SC2 case ends `budget_stopped` under replay pricing | synthetic, scaled from a derived total |
| `caps` | optional per cassette override of `REPLAY_RUN_CAP_USD`; any cassette whose test expects a retry states either this or its scripted usage | synthetic |
| page text | not recorded by v1 | synthetic, only on example URLs, never attached to a real URL |

**Privacy diff.** `build_cassettes.py` reads v1 lookups from
`V1_BRIEFCASE_DIR`, copies by allowlist only (never a whole object), and writes
to a staging folder. `privacy_diff.py` prints every copied string with its
source JSON path, the result of a scan against a gitignored local denylist plus
regexes for emails, phone numbers, street addresses and 5 digit numbers outside
URLs, plus key patterns (`sk-ant-`, `lsv2_`, a Tavily key prefix, likely
`tvly-` but unverified, `Authorization` and `x-api-key` headers, and long high
entropy tokens), strips tracking parameters from URLs, rejects any image with
an EXIF segment, and shows the `git diff`. Nothing leaves staging until you
approve, and approval lets files be written into the working tree only; a
commit happens only when you ask. The SC1b payload file
(`tests/fixtures/sc1b_payloads.json`, taken from the private v1
`scripts/guardrail-tests.mjs`) goes through the same diff and approval and
carries a `provenance` field naming its source file relative to
`V1_BRIEFCASE_DIR`. The Phase 7 README says that v1 test payloads are published,
not v1 source.
Never copied: `prior_notes`, photo base64, `share_path`, error lookups, file
names, local paths, request headers, `user_location`, and any key.

### 8.11 Knowledge graph and the evidence span

Persisted graph (`data/graph.json`, written only by live runs, decision 37;
tracked snapshot in `seed/graph.json`) is document derived only:

| Node | Key | Attributes |
|---|---|---|
| Model | `model:{maker}:{model_norm}` | maker, model, display name |
| ModelFamily | `family:{maker}:{family}` | |
| ErrorCode | `code:{maker}:{family or model}:{code_norm}` | code as printed |
| Part | `part:{maker}:{part_no}` | |
| SuccessorModel | `model:...` (same key space as Model) | |
| Source | `source:{sha256(url)[:12]}` | url, host, title, retrieved_at, text_sha256, final tier |

Edges IN_FAMILY, HAS_CODE, CODE_POINTS_TO_PART, SUPERSEDED_BY,
PART_DISCONTINUED each store `source_url`, `retrieved_at`, `evidence` (the
verbatim sentence), `evidence_sha256`, `brief_run_id`, `validated_at`. Property
and Appliance nodes and INSTALLED_AT and IS_MODEL edges are built at load time
from SQLite and never persisted, because their source is the registry, not a
document, and they have no evidence sentence (see R9).

**Verbatim evidence span, exactly.** A span is accepted only if all hold:

1. It is taken from `raw_content` returned by search with
   `include_raw_content`, or by extract called without `query`; never from the
   `content` snippet field. (The first draft's wording could be read to exclude
   every search result, because every search has a query.)
2. After one normalization, applied identically to the span and the page text,
   the span is a contiguous substring of the page text. The normalization is:
   Unicode NFC, then every run of whitespace characters (space, tab, newline,
   carriage return, no break space U+00A0) becomes one space, then leading and
   trailing spaces are removed. **Nothing else**: no case folding, no quote or
   apostrophe normalization, no dash or hyphen changes, no ligature expansion,
   no removal of soft hyphens, no punctuation changes.
3. It does not contain `[...]` (Tavily's snippet joiner).
4. It is 20 to 400 characters, sentence length, which also keeps quoted
   documentation short.
5. It contains the edge's target as printed: the code for HAS_CODE, the part
   number for CODE_POINTS_TO_PART and PART_DISCONTINUED, the successor model for
   SUPERSEDED_BY, the family name for IN_FAMILY. The source end of the edge is
   established by the document having been retrieved for that model in a
   validated brief.

An edge failing any condition is dropped and the drop is logged. It is never
stored with a lower confidence.

Promotion into `seed/`: only edges whose `brief_run_id` matches a live ledger
row are eligible, and every one must re pass the full span check against its
page text in `data/pages/` at that gate. Because page text is not tracked, a
fresh clone can check only `evidence_sha256`, span length and target in span;
`test_every_edge_reverifies` reports missing page text as a counted limitation.

### 8.12 Registry and synthetic seed data

Tables: `properties(id, label, synthetic)`, `appliances(id, property_id,
category, manufacturer, model, serial, purchase_date, install_date,
warranty_terms, warranty_source, status, synthetic)`, `service_records(id,
appliance_id, date, symptom, observed_code, work_done, performed_by,
synthetic)`, `maintenance_log(id, appliance_id, task, done_on, record_id)`,
`lookups(run_id, thread_id, appliance_id, status, route, cost_usd, created_at)`.

Seed (`seed/registry_seed.json`, every row `synthetic: true`, loaded into
`data/registry.sqlite` at runtime):

- 2 properties with generic labels ("Synthetic property A", "Synthetic
  property B"). They do not mirror the property described on the published
  teardown.
- 6 appliances: Sundance Spas Optima 880 and Trane XR16 4TTR6036 (real models,
  synthetic serials and dates); a third real model you name at Phase 1; one
  discontinued model (synthetic, labeled, used by SC9); two more common
  residential appliances with synthetic records.
- One synthetic prior service record on the Optima 880 for SC8. SC2 replays run
  with no appliance attached, as v1 did, so this record cannot change SC2.

### 8.13 CLI

| Command | Does |
|---|---|
| `advisor ask --symptom "..." [--photo PATH \| --identity JSON] [--property ID --appliance ID] [--code X] [--cassette PATH] [--html PATH] [--live]` | starts a thread, streams node names, stops at the interrupt and prints the thread ID and the extracted fields |
| `advisor resume THREAD_ID [--manufacturer ... --model ... --serial ... --date ... --code ...] [--cassette PATH] [--html PATH] [--live]` | resumes with `Command(resume=...)`; stays paused if model is still missing |
| `advisor property show ID [--html PATH]` | appliances, history, maintenance due; writes the static property page, labeled synthetic, by default to `data/property/<id>.html` |
| `advisor graph stats` | node and edge counts by type, edges dropped for failed evidence |
| `advisor ledger` (proposed addition, decision 48) | run and build spend, credits, remaining budget, `estimated` rows |
| `advisor eval sc3b --live` and `advisor eval sc7b --live` (proposed addition, decision 49) | the LIVE evaluations; each requires `ADVISOR_MODE=cheap`, `--live` and a typed "proceed", prints planned calls and estimated cost first, and writes one JSON run record per run to `data/eval/` |

Mode comes from `ADVISOR_MODE` (`replay` default, `cheap`, `full`); anything but
replay also needs `--live`, and then prints planned calls and estimated cost
before starting.

Cassette: `ADVISOR_CASSETTE` or `--cassette` selects the replay script. It is
accepted only in replay, rejected with `--live`, and read by both `ask` and
`resume`, so a resumed run in a new process replays the same script.

Output paths: `--html` defaults to `data/briefs/<run_id>.html` (briefs) and
`data/property/<id>.html` (property page). Any path inside `index.html`,
`tool.html`, `src/`, `briefs/` or `demo-assets/` is refused (decision 51).

Tracing: `ADVISOR_TRACING=1` is the only opt in (decision 31). `cli.py`, before
any langchain or langsmith import, unsets `LANGSMITH_TRACING_V2`,
`LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING`, `LANGCHAIN_TRACING` and
`LANGSMITH_GATEWAY` unless it is set, because langsmith caches its environment
lookups. When opted in, `hide_inputs` keeps the photo and registry fields out of
traces.

### 8.14 How tests block the network

- In process: pytest-socket `--disable-socket --allow-unix-socket` in
  `pyproject.toml` (it patches `socket.socket` and `getaddrinfo` in the pytest
  process only).
- Environment: `conftest.py` deletes `ANTHROPIC_*`, `TAVILY_API_KEY`,
  `LANGSMITH_*`, `LANGCHAIN_*` (including `LANGSMITH_GATEWAY`, which reroutes
  model traffic) at import, calls `langsmith.configure(enabled=False)`, and sets
  `LANGGRAPH_STRICT_MSGPACK=true`. For this reason no LIVE evaluation is a
  pytest test; they are CLI commands (section 5), and `agent/live/` is outside
  `testpaths`.
- Every child process is started by `tests/helpers.py::spawn_offline_child`,
  which scrubs the environment the same way and applies the right guard.
- Python children (SC5): `PYTHONPATH` puts `tests/netguard/sitecustomize.py`
  first; as built in Phase 1 it patches `socket.socket.connect`, `connect_ex`,
  `sendto` and `sendmsg` (Unix sockets allowed), `socket.create_connection`,
  `getaddrinfo` and the `gethostby*` lookups to raise, and prints a "guard
  loaded" marker.
- Node child (SC1b): launched with absolute paths, as `node --import
  <file URI of agent/tests/harness/netguard.mjs> <absolute path of
  agent/tests/harness/v1_validate.mjs>`, the URI built with
  `Path(...).resolve().as_uri()`. A bare relative path such as
  `tests/harness/netguard.mjs` is read as a package name and fails with
  `ERR_MODULE_NOT_FOUND` (reproduced on Node v24.19.0 in review), and pytest
  runs from the repo root. The preload prints a "guard loaded" marker and makes
  `net.connect`, `net.createConnection`, `net.Socket.prototype.connect`,
  `tls.connect`, every `dns` and `dns.promises` lookup and resolver method,
  `dgram.createSocket`, `http2.connect`, `http.request`, `http.get`,
  `https.request`, `https.get` and `globalThis.fetch` throw, each naming its
  entry point in the error. Whether Node 24's permission
  model can also deny network is unverified, so it is not relied on.
- Each guard has its own test that tries to connect and must fail with the
  guard's own error text, after the marker (section 5).

---

## 9. Cost plan

Prices from section 4. All figures are estimates until Phase 5 measures one.

**A. From v1 measured tokens.** v1 brief calls used 41,708 to 90,838 input and
2,698 to 6,936 output tokens (v1 lookup logs; the PRD's "42K to 91K" checks
out). At Haiku prices. The last column is not a measured bill: v1 never computed
cost, cache tokens were not logged, and whether a fallback model served any call
is unverified.

| v1 case | Arithmetic | Haiku cost | v1 computed from logged usage at Opus 5 list prices with 7 to 8 searches (estimate) |
|---|---|---|---|
| Fabricated | 41,708 × $1/M + 2,698 × $5/M = $0.0417 + $0.0135 | $0.055 | $0.346 |
| HVAC | 54,552 × $1/M + 5,293 × $5/M = $0.0546 + $0.0265 | $0.081 | $0.485 |
| FLO | 64,749 × $1/M + 3,476 × $5/M = $0.0647 + $0.0174 | $0.082 | $0.491 |
| Vague | 90,838 × $1/M + 6,936 × $5/M = $0.0908 + $0.0347 | $0.126 | $0.708 |

This reproduces the PRD's $0.05 to $0.13 (PRD:157) and $0.35 to $0.71
(PRD:159). Haiku's older tokenizer produces about 1/1.3 as many tokens for the
same text, so the real figure could be lower. It excludes read_plate, the
classifier and any retry, and v1's input volume came from Anthropic's search
content, which does not predict Tavily's.

**B. Bottom up for the v2 design, `cheap`, one pass.** These are estimates in
two columns. "Typical" assumes 5 searches, no fetch and a final answer turn, as
v1's runs mostly used their full search cap. "At caps" assumes 5 searches and 3
fetches, after which `ToolBudgetDone` ends research (8 model calls). Other
assumptions: research system and task text 1,500 tokens plus Haiku's tool use
system prompt (about 500 tokens), so each research call starts at 2,000; each
search adds 5 results of at most about 430 tokens (a 1,500 character snippet
plus title and URL), about 2,150 tokens; each fetch adds at most 1,500
characters, about 400 tokens; 250 output tokens per research call. Synthesize
sees at most 11,200 tokens of source excerpts (truncated in code, 28 results ×
400) plus 3,000 of prompt and writes 3,000; typical assumes 10,000 of excerpts.

| Step | Typical | At caps |
|---|---|---|
| read_plate (2,798 × $1/M + 95 × $5/M, v1 measured tokens) | $0.0033 | $0.0033 |
| classifier, only when rules cannot decide (800 × $1/M + 20 × $5/M) | $0.0009 | $0.0009 |
| research input | 6 calls: 6 × 2,000 + 2,150 × (0 + 1 + 2 + 3 + 4 + 5) = 44,250 × $1/M = $0.0443 | 8 calls: 2,000 + 4,150 + 6,300 + 8,450 + 10,600 + 12,750 + 13,150 + 13,550 = 70,950 × $1/M = $0.0710 |
| research output | 6 × 250 = 1,500 × $5/M = $0.0075 | 8 × 250 = 2,000 × $5/M = $0.0100 |
| synthesize | 13,000 × $1/M + 3,000 × $5/M = $0.0280 | 14,200 × $1/M + 3,000 × $5/M = $0.0292 |
| **Total, research route** (read_plate, research, synthesize) | **$0.083** | **$0.113** |
| Typical with 1 fetch (7 calls, 57,400 input, 1,750 output) | $0.098 | |
| Graph route (0 searches): read_plate + synthesize | $0.031 | $0.033 |
| Graph plus top up (2 searches, fetch cap 1; 3 calls either way, 12,450 input, 750 output = $0.016) | $0.048 | $0.049 |
| `cheap` retry pass (search 2, fetch 0, loop guard 4; 3 calls = $0.016) plus synthesize | +$0.044 | +$0.045 |

If every research call used its full 1,024 output tokens at caps, research
alone would reach about $0.112. `RESEARCH_BUDGET_USD` ($0.09, decision 26) ends
research before that, so read_plate $0.0033 + research $0.09 + the synthesize
reservation always fit under $0.15. The synthesize reservation is (11,200 +
3,000) × 1.25 × $1/M + 6,000 × $5/M = $0.048.

Retry affordability (decision 23): the retry needs its typical estimate ($0.016)
plus the synthesize reservation ($0.048), $0.064 in all, so it is taken only
when first pass spend is at or under about $0.086. A typical first pass
($0.083) fits, narrowly; a first pass with a fetch ($0.098) or at the caps
($0.113) does not, and ends `budget_stopped` with stop reason "retry not
affordable". That makes `budget_stopped` a realistic outcome on hard cases, and
it is reported, never hidden as a refusal. The first draft's figure of $0.079
omitted fetches and the tool use system prompt.

**`full` mode** (Sonnet 5 synthesis only): 13,000 × 1.3 = 16,900 input × $2/M =
$0.034; output 3,900 plus up to about 4,000 thinking = 7,900 × $10/M = $0.079;
plus research and read_plate $0.055; total about $0.17, inside the PRD's $0.10
to $0.25. Not run live in this build (decision 18).

**Tavily credits per run**: first pass up to 5 searches plus up to 3 fetch calls
(at most 1 credit each at basic) = at most 8; a `cheap` retry adds 2, reaching
the 10 per run cap. Graph top up: at most 3.

**Planned live calls, for approval at each phase's gate:**

| Phase | Runs | Estimate | Worst case at caps | Tavily credits | Credits with retry, at `RUN_CREDIT_CAP` |
|---|---|---|---|---|---|
| 5 | 1 minimal synthesize call to confirm the API accepts the `BriefDraft` schema (decision 22), then 1 `cheap` run: FLO on `plate-clear.jpg` (v1 cases E1 and B2; exercises read_plate on Haiku, research, synthesize, validate, persist) | $0.01 + $0.083 = $0.09 | $0.02 + $0.15 = $0.17 | at most 8 | 10 |
| 6, SC3b | 3 `cheap` runs, fabricated model, typed identity (v1 case B4); $0.083 less read_plate | 3 × $0.08 = $0.24 | $0.45 | at most 24 | 30 |
| 6, SC7b | FLO repeat (graph route, first lookup is the Phase 5 run); vague symptom repeat (top up, v1 case B1); Trane XR16 first lookup (v1 case B3, typed identity); Trane repeat (top up, symptom per decision 34) | $0.03 + $0.05 + $0.08 + $0.04 = $0.20 | $0.60 | at most 14 (0 + 3 + 8 + 3; 17 if the FLO repeat misses the graph route) | 40 |
| 6, plates | read_plate live on E1 and E2 plates (covers the untested Haiku vision path, including whether it marks the blurry model unreadable) | 2 × $0.0033 = $0.007 | $0.01 | 0 | 0 |
| **Build total planned** | 11 paid runs or calls | **about $0.54** | **$1.23** | at most 46 of 1,000 | 80 |
| Optional, decision 36 | B1 first lookup on the research route, typed identity | +$0.08 | +$0.15 | +8 | +10 |

The PRD's "roughly $1 to $2" for Phase 6 (PRD:161) is conservative against
this. The Trane first lookup and the Phase 5 FLO run also serve as over refusal
controls for SC3b: real models should get ok briefs.

**Reruns and what is reported (decision 35).** The $5 build cap leaves room for
one approved rerun of a failed phase. Every paid run is recorded in the ledger
and listed in that phase's decision log entry. A rerun is allowed only after a
logged code fix that names the defect; the fixed build's runs are then the only
reported SC3b or SC7b result, and the superseded runs appear only in the
decision log and the ledger, never in the README results table. A rerun of an
unchanged build is pooled with the first runs (for example "5 of 6") and never
substituted for them.

---

## 10. Phases 1 to 7

| Phase | Deliverables | Tests that must pass | Gate criteria | Reported at the gate | Decision log entry |
|---|---|---|---|---|---|
| 1 (replay) | uv env and lock; `config.py`; `schemas.py` (parity models and the flat `BriefDraft`); registry and synthetic seed; ledger; `ReplayChatModel` and tool stubs; cassette builder and privacy diff; network guards; spawn helper; CLI tracing scrub; `mutations.toml`; `.gitignore` additions (see section 12) | SC1 guard, gate mode and collection tests; schemas accept all 4 recorded briefs; `BriefDraft` within structured output limits; SC11 ledger tests including overshoot, timeout and missing ledger; no inline model IDs or prices; key scan; tracing scrub | tests green; $0 spent; you approve the privacy diff (cassettes and SC1b payloads) before anything is written for commit | resolved versions vs section 2; privacy diff; anything that did not behave as documented | the Phase 1 entry, including the reversal of the published cuts (decision 50), since the seed properties and warranty records arrive here |
| 2 (replay) | graph skeleton with every node on fakes; interrupt and SQLite checkpointer; v1 parity validator and v2 rules except evidence; refuse; minimal render; SC1b harness; research tools, middleware and collector | SC1 (inventory rows with phase 2), SC1b (must have run, not skipped), SC1b harness and schema layer, SC2 (5 cases, recorded variants, no budget stop), observed code rule, SC3a, retry affordability, SC4, SC5, SC11 graph tests, tier ceiling, research limits, tool failures | Offline and replay criteria only. SC3b is LIVE in Phase 6 and cannot be part of this gate. A skipped SC1b does not meet this gate: the run, under `ADVISOR_GATE=2`, must show SC1b ran on your machine against the v1 source, with the payload count and the difference list. You approve the 3 obsolete tests, the Phase 2 proposed differences (decision 21) and the prompt changes (decision 30). | mutation results; SC1b differences found; latency of replay runs (for plumbing only) | the Phase 2 entry |
| 3 (replay) | NetworkX graph, evidence check and excerpts, router and classifier, graph route, history route, top up route, persist | SC6 (all rows), SC7a, SC8 (all rows), code grounding, SC2 HVAC grounding variant, parallel branches, replay graph isolation | green; $0 | edges dropped by the evidence check in fixtures; route decisions table | the Phase 3 entry |
| 4 (replay) | upgrade and maintenance sections, full renderer, property page | SC9, SC10 (all rows), maintenance, prices, property page, output paths, all 17 v1 behaviors (SC1 complete) | green; $0; legacy render byte identical; you approve the v2 goldens and the Phase 4 proposed differences (decision 21) | rendered samples; difference list | the Phase 4 entry |
| 5 (LIVE) | the schema acceptance call, then one `cheap` run as planned in section 9 | the schema call is accepted (HTTP 400 on the schema is a stop condition); the run completes or stops cleanly; ledger matches the console; SC11 post condition | you type "proceed" before; stop after the run; Tavily plan, pay as you go status and Anthropic balance recorded | actual cost vs estimate, tokens, credits, latency, route, `estimated` ledger rows, snippet versus `raw_content` match rate (R4), anything unexpected; request for the Phase 6 budget | measured cost, tokens, credits and latency against the estimate, and cumulative live spend |
| 6 (LIVE) | SC3b and SC7b runs through `advisor eval`, plate runs; new cassettes recorded from tool artifacts; privacy diff | SC3b, SC7b (results logged whatever they show); SC11 post condition per run | you type "proceed" before; results published either way; reruns only under decision 35 | per run status, refusal origin, searches, cost, latency; build total | measured results for SC3b (model refusals, forced refusals, budget stops as separate numbers), SC7b (every repeat), the v1 live case checks (section 6.5), cost and cumulative live spend, and any rerun with its logged defect |
| 7 (replay) | README section, results table; README line on source location; superseding log entry for "The working application lives outside this repository" | whole offline suite | you review | final spend, criteria table with pass or miss | the v2 summary entry and the superseding entry |

Every gate report also includes what was built, test results, money spent so
far (for the phase and cumulative), open issues, and any contradictions with
the PRD (PRD:50), in addition to the items in the column above.

### 10.1 Decision log entries

PRD:168 asks for a decision log entry at the end of every phase, and PRD:14 and
PRD:173 ask for Phase 0 entries. Entries are appended to `DECISION-LOG.md` in its
existing style (`---` separators, `## Title` headings, plain prose, bold labels
such as **Decided:**, **Proposed:**, **Open:**), newest at the bottom, never
editing an old entry. Claude writes them only as appends and makes no commit.

| When | Entry |
|---|---|
| Phase 0, after you approve this plan | tier labels store origin and host (PRD decision 9.2), with the tier ceiling you choose (decision 5); one entry covering every deprecated or renamed row in section 3; the replay model decision; the Python environment decision; the SC3b wording; what replay proves; and each other decision approved at this gate |
| Phases 1 to 4 | one entry per phase: what was decided or reversed, and any measured result (Phase 1 also carries the reversal of the published cuts, decision 50) |
| Phases 5 and 6 | measured cost, tokens, credits, latency, cumulative live spend, the SC3b and SC7b outcomes whatever they show, and any rerun with its defect |
| Phase 7 | the v2 summary and the entry superseding "The working application lives outside this repository" |


### 10.2 Phase 1 as built (18 September 2026)

Phase 1 is built, in replay only, at $0. 473 test cases (174 test functions)
pass with the network blocked, and all 256 tracked mutations in
`agent/tests/mutations.toml` turn their listed tests red. Where the build
differs from this plan:

| Item | Plan | As built | Why |
|---|---|---|---|
| Tavily safe wrappers | Phase 2 (section 8.7) | Built now in `agent/research/tools.py`: thread timeout, failure mapping, Error 432 and 433 to a budget stop, ledger credits, collector writes. Still to come in Phase 2: page text files, the lookup log, and the decision 24 excerpt windows | Review found that replay stubs used as the final tools would bypass the wrappers, so replay could never test 432, credit caps or empty results. The replay stubs are now inner Tavily fakes and `make_stub_tools` returns the real wrappers around them |
| `test_research_tools.py::test_tool_failure_shapes_end_cleanly` | Phase 2, whole graph | Exists now at the wrapper and `create_agent` level | Phase 2 extends it to the graph rather than adding a second test |
| Cassette tool result shapes | section 8.10 example | Two more failure shapes: a bare string return and a scripted timeout | Needed to script the "Tool failures" row of section 5 |
| Credits on failure | not stated | A timed out request is charged `TAVILY_TIMEOUT_CREDITS` (1); a string return or empty results is charged the reserved credits; an error response releases the hold | An error response comes back without credits being spent |
| `--new-ledger` | Phase 2 | Built now in the CLI | Review finding P7 |
| Gate mode | `ADVISOR_GATE=1` at every gate | `ADVISOR_GATE=<phase>` | See section 5; at the Phase 1 gate there are no phase 1 inventory rows and no SC1b tests yet, so gate mode enforces nothing until Phase 2 |
| `happened_before` given as a JSON array | not stated | Kept verbatim, as v1 does; validate and the renderer must treat a list as no match | Parity with v1 for SC1b |
| Privacy scan | string patterns | Also scans dict keys, integers of 5 or more digits, and bare phone numbers | Review finding |
| Warranty `record_id` (decision 29) | Phase 1 schema | Deferred to the Phase 2 schema pass | Adding it before decision 29 is approved would be an unapproved schema delta; the test that needs it is Phase 3 |
| Third real model (decision 13) | you name it | Proposed: Rheem Professional Classic Plus with LeakSense, model PRO+E50 M2 RH92 CL, 50 gallon electric, seed appliance `appl-waterheater`, marked `pending_owner_choice`. Checked on 18 September 2026 on the maker's product page | Needs your confirmation |
| LangSmith pytest plugin | not stated | Disabled with `-p no:langsmith_plugin` in `pyproject.toml` | The plugin loads automatically and can upload results when a key is present |

Privacy diff status: the builder staged 5 cassettes and the SC1b payload file
under `data/staging/` (gitignored), with a report at
`data/staging/privacy_diff.md`: 391 copied strings, 0 blocking hits. It reads
INCOMPLETE because the local denylist is empty. Only you know the private names
to put in it (property name and town, owner, guest and cleaner names, street),
so nothing moves out of staging until you fill `data/staging/denylist.txt`,
rerun `python -m agent.replay.privacy_diff`, and approve. 27 staged strings
contain an em or en dash. All are v1 model output that `briefs/` already
publishes unedited, so they stay as recorded unless you say otherwise; decision
17 names only text quoted from sources, so this needs your call.


### 10.3 Phase 2 as built (18 September 2026)

Phase 2 is built, in replay only, at $0: the v1 parity validator and the v2
rules (except evidence spans, code grounding and prices, which are Phase 3 and
4 hooks that pass for now), the refuse node, the research agent and its
middleware, every node, the graph with the confirmation interrupt and the
SQLite checkpointer, a minimal renderer, and `advisor ask` and `advisor resume`.

Measured, all offline:

- 1,183 test cases pass; 181 more are the SC1b cases, which skip without
  `V1_BRIEFCASE_DIR` and run when it is set.
- Gate mode (`ADVISOR_GATE=2`, v1 source present): 1,363 pass and 1 fails. The
  failure is `test_v1_inventory.py`, and it stays red until you approve the 3
  obsolete v1 tests (11, 12, 14) by filling in `approved_obsolete_on`.
- SC1b: 180 payloads (16 from v1's guardrail tests, 164 authored edge cases;
  86 accept, 94 reject) through v1's TypeScript validator and v2's parity layer.
  The decisions, reason codes and normalized briefs agree on all 180, so no
  approved differences file exists.
- SC2 on the staged v1 cassettes, at replay prices: FLO ok with 1 confirmed
  candidate; vague symptom ok with 6 candidates, none confirmed; HVAC ok with
  every code null; fabricated model NO RELIABLE ANSWER from the model; blurry
  plate paused at confirmation with the model null after one read_plate call.
  No case ended `budget_stopped`. The HVAC "grounding reports unverifiable"
  clause waits for Phase 3's grounding check.
- SC5 by hand: `advisor ask` paused in one process and `advisor resume`
  finished in another, reaching `ok` and writing a self contained brief.
- 451 tracked mutations, all caught.

Where the build differs from this plan (each for your review):

| Item | Plan | As built | Why |
|---|---|---|---|
| Retry decision | inside the after validate table | a separate `retry_gate` node after validate | keeps validate a pure rules node |
| Budget stops | gather routes to render | budget stops pass through validate, which builds the budget stop brief, then render | every budget stop gets the same brief shape and clearing |
| persist | a node before END | runs when a thread reaches END, writing `runs/<run_id>.json`; marked `TODO(phase3)`: Phase 3 wires it as a node and removes the runner call | Phase 2 had no graph edges to persist |
| Lookup log | `lookups/<id>.json` | `lookups/<run_id>.jsonl`, one line per tool call | appended as calls arrive, so it survives a crash |
| Research agent call | not stated | runs with `durability="exit"` | otherwise it inherits the parent's `durability="sync"` and langgraph 1.2.11 waits on a checkpoint write that never starts |
| Source and trail keys | section 8.2 | sources also carry `excerpts`; trail entries carry `tool` | synthesize and SC7b need them |
| `run_rules` | draft, sources and state | also `identity`, `search_trail`, `page_texts`, `today` | code built searched, age from dates, authorship quotes |
| Typed identity | confirm_identity pauses only for a photo | also pauses when the symptom text suggests a code, so the owner confirms the code | review found a guessed code was treated as confirmed (decision 7 says confirmed state) |
| Confirmed code in prompts | not stated | both user prompts state the confirmed code, or that none was confirmed | review found research and synthesis never saw a code the owner corrected |
| Non web URLs | rejected by v1's source check | never registered as a source; an uncited non web source is dropped before the v1 check | one stray fetch URL with no scheme would otherwise reject every draft |
| Gate requirements | inventory and SC1b | also `agent/tests/gate_requirements.json`: SC2, SC3a, SC4, SC5 and SC11 tests must run at the gate, and a gate that selects no tests fails | review found a gate run on a subset of files could pass green |
| Inventory rows 1, 2, 9, 15 | whole rows phase 4 | per node phases, so their Phase 2 halves are enforced now | review finding |
| Latency keys | one entry per node | a node that runs again adds `node_2`, `node_3` | a retry no longer overwrites the first pass's timing |

Proposed differences from v1 for your approval at this gate (decision 21):
the confirmation pause on a typed identity whose symptom suggests a code; the
confirmed code line in both user prompts; the prompt wording listed in the
`agent/prompts.py` docstring (decision 30); an ok brief that cites no source
after pruning is rejected; non web URLs never registered; tier ceilings compare
maker names without punctuation and corporate suffixes and treat a trailing dot
on a host as the same host; plus the Phase 2 rows already listed in decision 21.

Unverified: whether the LangSmith tracer honors the hidden input and output
settings. In replay, the scripted token usage is far larger than the reservation
estimates (the replay prompts are short, but the scripts carry v1's token
counts), so a replay run can pass its cap by more than one call's estimate
error. This is a replay artifact; in a live run the estimate is made from the
real prompt. The post run cap check is asserted only in the live phases.


### 10.4 Phase 3 as built (18 September 2026)

Phase 3 is built, in replay only, at $0: the NetworkX knowledge graph, the
verbatim evidence span check, code grounding, the router and its classifier, the
history and graph lookup branches running in parallel, the graph plus research
top up route, persist as a node that writes only verified edges, and `advisor
graph stats`.

Measured, all offline:

- 1,378 test cases pass (plus the 181 SC1b cases when the v1 source is set).
  Gate mode 3 with the v1 source: 1,558 pass and 1 fails, the same obsolete
  test approval as Phase 2. It enforces 57 required tests.
- 560 tracked mutations, all caught.
- By hand in replay: a first FLO lookup on the seeded hot tub took route row 5
  (history and research), used 1 search, cost $0.024 at replay prices, and
  wrote one verified HAS_CODE edge to the replay graph. The repeat took route
  row 1 (history and graph): 0 searches, 0 research calls, $0.011, grounding
  verified, and the graph source kept the first run's retrieval time. The
  runtime graph file was never created.
- A damaged saved edge is dropped and logged at load, and the run falls back to
  research instead of failing.

Where the build differs from this plan (each for your review):

| Item | Plan | As built | Why |
|---|---|---|---|
| Code grounding (8.5 rule 4) | code in the raw text, or in an evidence quote verified against it | the code must stand as a whole token in the cited page's raw text, or in its snippet when there is no raw text; an evidence quote alone never grounds a code | review showed the quote path could only add false passes: a verified quote can hold a code that is not a separate token on the page |
| HAS_CODE edges | the five span conditions | also require the code to stand as a whole token where the quote sits in the page | so "E1" is never recorded from a page that prints "E10" |
| Span length (8.11 condition 4) | 20 to 400 characters | measured after normalization, and pinned by a test | the rule was ambiguous |
| ErrorCode key | family or model | family when the graph already holds a verified IN_FAMILY edge for the model, else model | sibling models in a family share the code |
| Graph load | not stated | lenient by default: a failing saved edge is dropped and logged to `<graph>.drops.jsonl`; strict only in the integrity tests | one damaged edge would otherwise fail every later run |
| "Graph has the model" | not stated | counts only document edges out of the model, not a model known only as someone's successor | route row 5 applies when nothing is documented for that model |
| Sources reducer | plain append | append, deduplicated by URL | the three parallel branches can register the same page |
| Edge page hash | not listed | each edge stores the hash of the page it was verified against | the promotion check re verifies against the right page |
| Decision 37 hardening | replay never writes the runtime graph | replay also never writes under `seed/`, and a live run never writes a graph it did not read | review finding |
| Persisted edge kinds | five kinds | only HAS_CODE until Phase 4 adds SUPERSEDED_BY and the part edges | Phase 3 scope |
| Grounding status | not stated | `verified` only when a code was checked and found; `not_applicable` when the brief carries no code; `not_checked` when the observed code rule already rejected the draft; `unverifiable` for v1 derived replays | a brief with no codes must not report a check that never ran |

New prompt text for your approval under decision 30: the route classifier's
system prompt and user prompt, and the registry block given to synthesize.

Open, for Phase 6: a graph source has no snippet, so grounding on the graph
route needs the page text in `data/pages`. On a fresh clone with only
`seed/graph.json`, a graph route code would fail grounding until the choice is
made between treating the edge's verified evidence as the grounding text and
requiring the page text.


### 10.5 Phase 4 as built (18 September 2026)

Phase 4 is built, in replay only, at $0: upgrade options, the maintenance
schedule, the price rule, the remaining graph edge kinds, the full renderer, the
static property page, and `advisor property show`. Every replay phase is now
built. `config.BUILD_PHASE` is 4, so a plain test run enforces all 17 v1
inventory rows.

Measured, all offline:

- 1,601 test cases pass (plus the 181 SC1b cases with the v1 source set). Gate
  mode 4 with the v1 source: 1,781 pass and 1 fails, the same obsolete test
  approval. It enforces 74 required tests.
- SC1: 14 of the 17 v1 guardrail behaviors map to tests that run and pass; the
  other 3 (tests 11, 12, 14) await your approval as obsolete.
- SC10: the legacy renderer reproduces all four published `briefs/*.html` byte
  for byte. The v2 renderer's output loads nothing from outside the page under
  an allowlist test, and escapes every model and untrusted field in text and
  attribute context.
- 663 tracked mutations, all caught.

Where the build differs from this plan (each for your review):

| Item | Plan | As built | Why |
|---|---|---|---|
| Maintenance due date | code computes it from `maintenance_log` | when the model gives no valid log ID, code uses the newest log row of this appliance whose task matches (case, articles and one word ending ignored) | nothing shows the model the log IDs, so otherwise no live run would ever get a due date |
| `MaintenanceItem.last_done_on` | not listed | a new field, written by code only | the rendered line shows when the task was last done |
| Price rule scope (decision 28) | `summary`, `why_shown`, `detail`, `age_statement` | also `matched_identity`, the warranty caution and verify lists, `happened_before.summary`, and a refusal's found and why lines; an amount must stand whole in the quote (so "$12" is not taken from "$129") | every model written field the page prints; PRD:119 says the tool never estimates prices |
| Successor and part names | substring of the quote | must stand as whole tokens in the quote, in validate and at persist | "SX-20" must not be taken from "SX-200" |
| Tier proposals from upgrade_check | none (decision 27) | only for the graph sources upgrade_check itself registered, or uncited graph sources | otherwise a maker's discontinuation notice found through the graph would show the default forum badge |
| Rendered differences from v1 | the decision 21 Phase 4 list | 21 differences in `agent/tests/fixtures/goldens/README.md`: 11 on the decision 21 list, 10 new (the service record line, the registry warranty terms line, character references for the middle dot, check mark and dashes, no blank placeholder line, a status backstop, no all forum warning on a budget stop, extra CSS rules, and the default forum badge on budget stop sources) | each needs your approval with the goldens |

For your review at this gate: the 9 v2 goldens in
`agent/tests/fixtures/goldens/`, with the difference table in its README.

Open: the prompts do not yet ask the model for upgrade options, maintenance
intervals or prices, so live runs will produce them only if the model offers
them unasked; asking would be a prompt change under decision 30. Budget stop
sources show the default forum badge because no tier was graded; a "not graded"
badge would need a decision.


### 10.6 The live path, built before Phase 5 (18 September 2026)

Built with fake clients only; no request reached Anthropic or Tavily. The live
commands reuse the replay graph, nodes and ledger; only the model and search
clients change.

- Keys come from the gitignored `.env` or the environment, only for live
  commands, and are redacted from everything printed or written. A planted fake
  key never appeared in any output or file in the tests, including failed runs.
- Every live command prints a preflight (planned calls, typical and worst case
  cost computed from `config.py`, the run cap, build spend and credits so far)
  and continues only when the reply is exactly `proceed`. There is no flag that
  skips it.
- Refusals before anything is spent or created: replay mode, no `--live`, a
  missing key, a missing or empty ledger without `--new-ledger`, `--new-ledger`
  on a resume, build budget below one run, a Haiku mode on or after 15 October
  2026 until the model list is rechecked, and any `full` mode run until
  `config.FULL_LIVE_APPROVED` records decision 18's separate approval.
- `advisor check-schema --live` is the decision 22 schema check (one call);
  `advisor eval sc3b|sc7b|plates --live` are the Phase 6 batches; `advisor
  ledger --run <thread>` reports tokens, cost against the estimate, and the R4
  snippet match rate offline. Each finished live run writes a recording
  candidate and its copy log for the privacy diff.
- 1,856 test cases pass offline, gate mode 5 fails only on the obsolete test
  approval, and 795 tracked mutations are all caught.

Keys: on 18 September 2026 you supplied the keys; they are in `.env` (mode 600,
gitignored). `APIKEYS.rtf` in the repo root is listed in `.git/info/exclude` so
it cannot be committed by accident; deleting it is recommended.


### 10.7 Phase 5 results (LIVE, 18 September 2026)

Roanuk typed "proceed" on 18 September 2026 and recorded the account state:
Tavily free plan with pay as you go off, $5 on the Anthropic key (so the build
cap stays $5).

| Step | Result | Cost (estimate) |
|---|---|---|
| Schema check (decision 22) | Accepted; the reply parsed as `BriefDraft` | $0.0036 (typical $0.0028, worst $0.0046) |
| read_plate on `plate-clear.jpg` | Sundance Spas, Optima 880, serial 100915742, 06/2014, all high confidence; FLO proposed as the code | $0.0029 |
| Rest of the FLO run (resumed in a new process) | `ok`; route row 5 (graph empty); 5 searches, 3 fetches, 1 search blocked at the cap; 1 confirmed FLO candidate, 3 try first steps, 1 cited source (`thecoverguy.com`, dealer); grounding verified | $0.0374 |
| **Run total** | SC11 pass: $0.0403 against the $0.15 cap | typical $0.083, worst $0.15 |

Tokens: read_plate 2,539 in and 73 out; research 21,377 and 723; synthesize
10,672 and 350; no cache tokens. Tavily: 5 credits (1 per search; the three
single page extracts were billed 0). Latency: about 24 seconds in total
(read_plate 4.1 s, research 13.7 s, synthesize 6.2 s); no target, reported
only. v1 checks: E1 pass, B2 pass. R4: 57 of 57 snippet chunks were found in
their page's `raw_content` (100%). The ledger has no estimated rows; build
spend is $0.0440 of $5.

Found by the run and fixed at $0 before Phase 6, each held by a new test and
mutation:

1. **No graph edge was saved.** The model's evidence quote for FLO was
   verbatim from the page but left out the code, which the page prints in the
   table cell just before it. Code now widens such a quote to start at the code
   when it is printed on the same line within 40 characters (the result is still
   a contiguous verbatim span of the page). The Phase 5 run's edge was then
   written by rerunning the persist step on the stored run with no new call;
   the run's own record is left as the live build wrote it.
2. **The lookup log misreported credits** for searches running in parallel (it
   logged the change in the run total). The ledger was right; the log now
   records each call's own charge.
3. **The recording did not load as a cassette**, because the model's sixth
   search was blocked at the cap and three parallel searches finished out of
   order. The recorder now notes calls blocked at the cap and restores call
   order, and the replay stubs match results by query or URL.

Open, for your decision: a live recording leaves out real page text (decision
15), so replaying it offline cannot re-verify code grounding, and the replay
ends in a forced refusal. Proposed: in replay, read page text from the local
`data/pages` folder by hash when it is present, and otherwise report grounding
as "unverifiable" for recordings, as for v1 derived cassettes.

A model finding, not a code failure: unlike v1's FLO brief, the live Haiku
brief carries no safety flagged step (v1's first step was to confirm water is
moving before testing the heater).


### 10.8 Phase 6 results (LIVE, 18 September 2026)

Roanuk typed "proceed" for Phase 6 on 18 September 2026. Three batches ran in
`cheap` mode, each after its own preflight. Phase 6 spent $0.2226 against a
typical estimate of about $0.45; the build total is $0.2666 of the $5 cap, with
34 of 1,000 free Tavily credits used. Every live run passed SC11; the most
expensive cost $0.0454.

| Batch | Result | Detail |
|---|---|---|
| SC3b (v1 case B4, fabricated Aquarest model, 3 runs) | **PASS: 3 of 3 refused by the model** | 0 forced refusals, 0 budget stops; 5 searches each; $0.0353, $0.0444, $0.0372; 33.9 s, 23.5 s, 20.9 s; the search trail reached the real maker's documentation each time; v1 B4 checks pass |
| SC7b (4 runs) | **MISS: repeats used 0, 0 and 5 searches** | FLO repeat: graph route, 0 searches, $0.0083, 5.3 s (first lookup used 5). Vague symptom repeat: graph route (row 3, classifier match), 0 searches, $0.0098, 8.6 s. Trane XR16 first lookup: research, 5 searches, $0.0364, 20.2 s. Trane repeat: research (row 5), 5 searches, $0.0454, 23.0 s. v1 checks B1, B2, B3 pass |
| Plates (v1 cases E1, E2) | **PASS** | E1 read the model and serial exactly ($0.0029, 1.8 s); E2 marked model, serial and date unreadable instead of guessing ($0.0028, 1.2 s) |

Why SC7b missed: the Trane first lookup documented six causes for "AC not
cooling" and no error codes. The PRD's graph schema (PRD:148) has edges for
codes, parts and successors, but none for a documented symptom and its cause,
so a lookup that finds no codes leaves nothing for a repeat to reuse, and the
router correctly sends the repeat back to full research. The coded repeats
used 0 searches. This is a limit of the schema as specified, not a defect in
the code, and it is reported as a miss.

Found by the data:

1. **The graph route narrows a vague question.** The vague symptom repeat ("not
   heating") was answered from the one FLO edge the graph held: 1 candidate,
   unconfirmed. No live first lookup of that question ran (the optional B1
   first lookup, decision 36, was not run); v1's recorded brief for it listed
   6. The
   classifier matched the symptom to a documented cause, which route row 3
   allows, and the answer was cheap but narrower. Proposed for review: take the
   graph plus top up route (row 4) whenever no code is confirmed, and keep row 3
   only when the graph holds several verified causes.
2. **Symptom level knowledge is not stored.** Proposed for review, as a PRD
   change: a documented cause edge (model to cause, with the same verbatim
   evidence rule) so code-less lookups can be reused.
3. **Excerpts can end mid word.** The FLO repeat's documented action ends
   "deactivated and filte", copied exactly from an excerpt cut mid word, and the
   stored FLO edge carries the same cut text (it is verbatim page text, so the
   span rule passes). Proposed: cut excerpt windows at word boundaries and
   extend a stored span to the end of its word.

---

## 11. Risks, unknowns, and contradictions with the PRD

### Contradictions with the PRD

| # | PRD says | Evidence | Recommendation |
|---|---|---|---|
| C1 | Phase 2 gate "SC1 to SC5 pass" (PRD:43) | SC3b is LIVE in Phase 6 (PRD:89); 5 v1 tests need the Phase 4 renderer | Decision 12 |
| C2 | Pydantic matches `types.ts` (PRD:29) vs `fixtures.js` exactly (PRD:42) | inner briefs agree; wrappers differ | Section 7.1 wording (decision 39) |
| C3 | SC3b "settles the open latency question" (PRD:89) | it changes model, search provider, search cap (8 to 5) and pipeline at once, measures no latency, and 3 of 3 is consistent with a true refusal rate as low as about 37% (one sided 95%) | Reword: "The fabricated model case refuses in 3 of 3 live `cheap` runs. Each run records wall clock time, whether the refusal came from the model or was forced after a retry (a retry fits only when first pass spend is at or under about $0.086, decision 23; if you choose no retry in `cheap`, this clause is dropped), and whether the trail reached the real maker's documentation. `budget_stopped` counts as a failed run, and a forced refusal counts as decision 33 says. This informs, but does not settle, the open latency question in v1's decision log." Decision 11 |
| C4 | "Two optional additions" (PRD:146) | at least 8 more deltas (section 7.3) | Decision 4 |
| C5 | SC1 "17 v1 behaviors listed in the decision log and teardown" (PRD:85) | only the count is published; 3 may be obsolete (PRD:30) | "each of the 17 v1 guardrail tests, ported or marked obsolete with approval, enumerated in agent/PLAN.md" (decision 40) |
| C6 | "The 3 real models from the v1 fixtures" (PRD:109) | 2 real models plus the fabricated Aquarest ZX-9000 Pro | Decision 13 |
| C7 | Seeded discontinued model (SC9) vs "edges come only from validated briefs" (PRD:148, PRD:182) | a hand made SUPERSEDED_BY edge would break the published corpus promise | Decision 13 |
| C8 | SC6 "retrieved in that run" (PRD:92) | the graph route may run 0 searches; SC8 cites records | Allow sources loaded from validated graph edges in that run, with origin `graph` and original `retrieved_at` (decision 41) |
| C9 | SC10 "closes the v1 script injection finding" (PRD:97) | v1 already escapes all model text and allowlists URLs (v1 brief-html.ts:7 to 22; guardrail tests 9 and 10 pass today) | "keeps the v1 fix for" (decision 42) |
| C10 | Public demo "must keep making zero network requests" (PRD:17) | the demo loads its own CSS, scripts, images and briefs from its own origin; what the pages promise is "no API call" | Invariant: the demo requests nothing from any origin but its own and never calls fetch, XHR, WebSocket or sendBeacon. Enforced by a checksum test in section 12 (decision 43). |
| C11 | "Nothing stores that corpus" (PRD:62) | lookups are logged (DECISION-LOG.md, architecture reversal entry) | "logged, but nothing reads them back" (decision 44) |
| C12 | `happened_before` "empty in all 5 recorded cases" (PRD:63) | 4 cases have a brief; blurry has none | "null in all 4 recorded briefs" (decision 44) |
| C13 | SC7b and the resume line "8 searches to N" (PRD:191) | v1 hit its cap of 8 in 3 of 4 runs and `cheap` caps at 5 anyway | Compare a v2 first lookup against v2 repeats of the same model, same mode, and report every repeat (decisions 34 and 44) |
| C14 | Forced refusal after two validation failures becomes NO RELIABLE ANSWER (PRD:147) while budget exhaustion "is not a finding about the documentation" (PRD:145) | a validation failure is not a documentation finding either | Record `refusal_origin`; count forced refusals separately (decisions 33 and 44) |
| C15 | Tier precedence (PRD:147 vs PRD:173) | a maker PDF on a forum host is undefined | Host rule wins (can only lower); decision 5 |
| C16 | Section 9 numbering runs 1, 2, 3, 5, 4 (PRD:170 to 176) | Markdown renders it renumbered | Cite 9.2 by content; fix the source if the PRD is ever committed |
| C17 | Published cuts reversed | the teardown cut "Multi property" and "Warranty deadline tracking and prevention scheduling"; G3 and 2 seed properties bring them back | Log the reversal with its reason in the Phase 1 entry, when the seed properties and warranty records arrive (decision 50) |
| C18 | The v1 published next step was a technician test, not more build | README and teardown | Phase 7 README says plainly that v2 does not answer that question |
| C19 | The v1 live cases E1 and E2 test plate reading, and PRD:31 already makes them part of the Phase 5 and 6 suite; read_plate moves from Opus 5 to Haiku | SC4 is replay only | Not a gap in the PRD: the two plate calls in Phase 6 and the Phase 5 plate run meet PRD:31 (section 6.5) |

### Risks and unknowns

| # | Risk or unknown | Recommendation |
|---|---|---|
| R1 | **What replay can and cannot prove.** Replay proves that the graph, the router, the validators, the ledger and the renderer do the right thing with recorded or scripted model outputs. It proves nothing about how Haiku or Sonnet behave: whether they refuse the fabricated model, cite honestly, or pick a good route. SC2 replays v1 Opus 5 outputs through v2 code, so a pass means v2's rules accept v1's good answers, not that v2's models would produce them. Only SC3b, SC7b and the Phase 5 run test model behavior, on small samples. | State this in the README results table next to every replay result. |
| R2 | Haiku 4.5 retirement not sooner than 15 October 2026 | Decision 14 |
| R3 | No page text in v1 recordings, so SC6 and code grounding are tested on synthetic text until Phase 6 | Decision 15; SC2 results say which checks were "unverifiable" on v1 cassettes |
| R4 | Tavily text fidelity (search raw content vs extract, and whether snippet chunks match `raw_content`), PDF handling, and whether raw content costs extra credits are unverified | Phase 5 measures the snippet to `raw_content` match rate; models quote from code cut excerpts of `raw_content` (decision 24); if PDFs fail, the manual is cited from search snippets, no graph edge is made from it, and any code cited to it must appear in the snippet (decision 25) |
| R5 | Built in fake chat models cannot drive `create_agent` with tools | Custom `ReplayChatModel` (section 8.10), with its own tests |
| R6 | `ChatAnthropic` defaults to the model's maximum output and no timeout | Explicit `max_tokens`, `timeout`, `max_retries=0` in `config.py` |
| R7 | `ModelRetryMiddleware` defaults would retry a budget exception and disguise a final failure | `retry_on` and `on_failure="error"` set explicitly |
| R8 | Checkpoint serializer silently rebuilds a Pydantic model without validation if the schema changed | JSON native state only; strict msgpack on; JSON round trip test |
| R9 | PRD says every edge stores a URL and evidence sentence, but INSTALLED_AT and IS_MODEL come from the registry | Keep them out of the persisted graph; build them at load time from SQLite (decision 45) |
| R10 | SC1b skipped on any machine without the private source | Decision 2; the gate report states whether SC1b ran, and under `ADVISOR_GATE=2` a skip fails the Phase 2 gate |
| R11 | v1 keeps an answer when the model sets ok and also fills the refusal block, and accepts an ok brief with no candidates and no steps | Keep both for parity now; you may choose to make either a validation failure (a listed SC1b difference) |
| R12 | Observed code parsing from free text could miss or invent a code | The owner confirms the code at the interrupt; `--code` overrides |
| R13 | `budget_stopped` could become common on hard cases because a retry fits only after a cheap first pass | Decision 23; report its rate in Phase 6, with "retry not affordable" counted separately; do not raise the cap without your approval |
| R14 | `docs/PRD-v2.md` and any committed file could publish the private v1 path | Decision 19; this plan names the v1 folder only as `V1_BRIEFCASE_DIR` |
| R15 | A Python 3.12.14 install appeared during Phase 0 from an unknown process | Decision 1 |
| R16 | The anthropic SDK 1.x uses `httpx2`; HTTP mocking libraries built for `httpx` may not intercept it (inferred) | Not needed: replay never constructs live clients, and a test proves it |
| R17 | SQLite writer locking if two CLI commands touch the same thread at once | One command per thread at a time; the CLI takes a lock file per thread |
| R18 | LangSmith tracing would send photos and property data to a hosted service | Off by default; the CLI scrubs tracing variables unless `ADVISOR_TRACING=1` (decision 31); if you turn it on, `hide_inputs` keeps the photo and registry fields out |
| R19 | The docs and source disagree on the recursion limit default, `NodeTimeoutError`'s base class, and `stream_events` v3 stability | Source wins; none of the three is relied on |
| R20 | Tier lowering will show a v1 manufacturer source as dealer offline | Decision 5; listed as a proposed difference (decision 21) |
| R21 | The model facing schema could be rejected by the API (HTTP 400) after read_plate and research have been paid for | Decision 22: flat all required `BriefDraft`, an offline limits test, and a cheap schema acceptance call first in Phase 5 |
| R22 | A Tavily failure (timeout, error content, empty results) would crash the run, because tool errors re raise by default | `handle_tool_error=True` and conversion to `ToolException` (section 8.7), with a replay test per failure shape |
| R23 | Replay tests that expect a retry could silently depend on an unstated cap | `REPLAY_RUN_CAP_USD` and per cassette `caps` (section 4); each such test states its cap or scripted usage |

---

## 12. What will not change

- `index.html`, `tool.html`, `src/`, `briefs/` and `demo-assets/` stay byte for
  byte as they are. Phase 1 adds `test_published_pages.py::test_published_files_unchanged`,
  which compares sha256 hashes of every file in those paths against a tracked
  manifest, and `::test_demo_makes_no_off_origin_or_script_requests`, which
  parses `tool.html`, `src/demo.js` and the briefs for any absolute URL load or
  any use of fetch, XHR, WebSocket or sendBeacon. Mutation: editing one byte of
  any published file turns the first test red.
- The demo keeps its zero API call promise and keeps loading nothing from any
  origin but its own. `src/fixtures.js` is never regenerated.
- `DECISION-LOG.md` stays append only; the old entry saying the application
  lives outside the repository is superseded by a new entry, not edited.
- The v1 private folder is read only and never copied into this repo beyond the
  allowlisted, already public fields in section 8.10.
- `.gitignore`: the working tree already has uncommitted lines for `.venv/`,
  `__pycache__/`, `.pytest_cache/` and `.hypothesis/`, added in Phase 0 with the
  install (section 2.2). Phase 1 proposes adding `*.py[cod]`, `.mypy_cache/`, `.ruff_cache/`,
  `*.egg-info/`, `build/`, `dist/`, `.coverage`, `htmlcov/`, `*.sqlite`,
  `*.sqlite3`, `*.db`, `*-wal`, `*-shm`, `*-journal`, and `.env.*` with
  `!.env.example`, and updating the comment about the request log to match
  decision 6. `uv.lock` and `.python-version` are tracked (written for commit
  when you ask).
- Claude never runs `git commit` or `git push` (PRD:20). In this plan,
  "tracked" or "written for commit" means intended to be tracked. Approving a
  privacy diff lets the files be written into the working tree; a commit happens
  only when you explicitly ask. `.env`, `data/` and API keys are never tracked.
