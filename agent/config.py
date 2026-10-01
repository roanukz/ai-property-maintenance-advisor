"""Every model ID, price, cap, and limit v2 uses.

This is the only file in agent/ (outside agent/tests/) allowed to hold a model
ID or a dollar figure; test_config.py enforces that. Values were verified on
17 September 2026 against the Anthropic models overview and pricing pages, the
Tavily credits and pricing pages, and the installed packages (PLAN.md sections
3.1 and 4). Anything marked "proposed" awaits Roanuk's review.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

HAIKU = "claude-haiku-4-5-20251001"  # alias claude-haiku-4-5; retirement not sooner than 2026-10-15
SONNET = "claude-sonnet-5"
OPUS_BASELINE = "claude-opus-5"  # v1 baseline only; v2 never calls it

HAIKU_RETIREMENT_EARLIEST = "2026-10-15"

# Jev, TypeSafe's System One model, asked one Noul per try_first step by the
# safety_check node (decisions 53 to 57). Pinned to the documented versioned
# ID; the SDK default is "jev-latest" (decision 54).
JEV_MODEL = "jev-1.13.0"
JEV_PROVIDER = "typesafe"
JEV_SAFETY_ENABLED = True
TYPESAFE_KEY_NAME = "TYPESAFE_API_KEY"

MODES = ("replay", "cheap", "full")
LIVE_MODES = ("cheap", "full")

# Which model each paid step uses, per mode. Replay uses the cheap row for
# naming and prices every call at REPLAY_PRICES.
MODEL_FOR = {
    "replay": {"read_plate": HAIKU, "classifier": HAIKU, "research": HAIKU, "synthesize": HAIKU},
    "cheap": {"read_plate": HAIKU, "classifier": HAIKU, "research": HAIKU, "synthesize": HAIKU},
    "full": {"read_plate": HAIKU, "classifier": HAIKU, "research": HAIKU, "synthesize": SONNET},
}

# ---------------------------------------------------------------------------
# Prices, US dollars per million tokens
# ---------------------------------------------------------------------------

PRICES_PER_MTOK = {
    HAIKU: {"input": 1.00, "output": 5.00},
    SONNET: {"input": 2.00, "output": 10.00},
    OPUS_BASELINE: {"input": 5.00, "output": 25.00},
    # TypeSafe prices input only; the API still reports output_tokens, priced at 0.
    JEV_MODEL: {"input": 0.042, "output": 0.0},
}
# Cache pricing as multiples of the base input price.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.00

# v1 baseline only (Anthropic server web search); v2 searches with Tavily.
ANTHROPIC_WEB_SEARCH_USD_PER_SEARCH = 0.01

# Replay rows are priced at Haiku rates so the ledger logic runs, and they never
# count toward the build total.
REPLAY_PRICES = PRICES_PER_MTOK[HAIKU]

# ---------------------------------------------------------------------------
# Token budgets and request settings
# ---------------------------------------------------------------------------

# ChatAnthropic defaults max_tokens to the model maximum (64,000 on Haiku),
# which would reserve $0.32 of output against a $0.15 cap, so every call sets it.
MAX_TOKENS = {
    "read_plate": 512,
    "classifier": 64,
    "research": 1_024,
    "synthesize_cheap": 6_000,
    "synthesize_full": 16_000,  # includes adaptive thinking on Sonnet 5
}

TIMEOUT_BASE_S = 30
TIMEOUT_PER_MAX_TOKENS_S = 1 / 200  # plus 1 second per 200 max_tokens
TAVILY_TIMEOUT_S = 30


def model_timeout_s(max_tokens: int) -> float:
    """Client timeout for a model call with this output budget."""
    return TIMEOUT_BASE_S + max_tokens * TIMEOUT_PER_MAX_TOKENS_S


# Tool use system prompt Anthropic adds to every tool bearing call (the higher
# of the any/tool figures on the pricing page).
TOOL_PROMPT_TOKENS = {HAIKU: 588, SONNET: 474}

# count_tokens_approximately uses about 4 characters per token; Sonnet 5's
# newer tokenizer makes about 30% more tokens for the same text.
ESTIMATE_MARGIN = {HAIKU: 1.25, SONNET: 1.25 * 1.3}
IMAGE_TOKENS_ESTIMATE = 1_600

# Retries happen in our code or in ModelRetryMiddleware so the ledger sees
# every attempt.
CLIENT_MAX_RETRIES = 0
MODEL_RETRY_MAX = 1

FULL_SYNTH_EFFORT = "medium"  # never sent to Haiku

# Never sent to any model: Sonnet 5 returns 400 on non default sampling
# parameters, and Haiku 4.5 does not support effort.
NEVER_SEND_PARAMS = ("temperature", "top_p", "top_k")
NEVER_SEND_TO_HAIKU = ("effort", "reasoning_effort", "thinking")

# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------

TAVILY_SEARCH_SETTINGS = {
    "search_depth": "basic",  # 1 credit; advanced (2 credits) is pinned off
    "max_results": 5,
    "include_raw_content": "text",
    "include_usage": True,
}
TAVILY_EXTRACT_SETTINGS = {
    "extract_depth": "basic",
    "format": "text",
    "include_usage": True,
}
TAVILY_CREDITS = {
    "search_basic": 1,
    "search_advanced": 2,
    "extract_basic_per_5_urls": 1,
}
TAVILY_FREE_CREDITS_PER_MONTH = 1_000
# Credits are $0 on the free tier. If pay as you go is on (recorded at the
# Phase 5 gate), each credit is priced and counts toward the build cap.
TAVILY_PAYG_ENABLED = False
TAVILY_CREDIT_USD_PAYG = 0.008
TAVILY_PLAN_LIMIT_MARKERS = ("Error 432", "Error 433")
# A request abandoned by the wrapper's timeout may still reach Tavily, so it is
# charged as this many credits (PLAN 8.7).
TAVILY_TIMEOUT_CREDITS = 1


def tavily_credit_usd() -> float:
    return TAVILY_CREDIT_USD_PAYG if TAVILY_PAYG_ENABLED else 0.0


# ---------------------------------------------------------------------------
# Caps (SC11)
# ---------------------------------------------------------------------------

RUN_CAP_USD = {"cheap": 0.15, "full": 0.30}  # full is proposed, replay only for now
REPLAY_RUN_CAP_USD = 0.15
BUILD_CAP_USD = 5.00  # the lower of this and the Anthropic balance recorded at the Phase 5 gate
RUN_CREDIT_CAP = 10
# Raised from 150 to 300 by Roanuk at SC12b's Gate 3 on 30 September 2026 (brief decision 1):
# Tavily's free tier gives 1,000 credits a month, so it costs nothing.
BUILD_CREDIT_CAP = 300
RESEARCH_BUDGET_USD = 0.09  # reaching it ends research, not the run (decision 26)

# Retry affordability (decision 23): the reduced retry pass's typical cost.
RETRY_TYPICAL_USD = 0.016

# ---------------------------------------------------------------------------
# Research limits (decisions 10 and 46). Loop guard = search + fetch + 2.
# ---------------------------------------------------------------------------

RESEARCH_LIMITS = {
    "main": {"search": 5, "fetch": 3, "loop_guard": 10},
    "top_up": {"search": 2, "fetch": 1, "loop_guard": 5},
    "retry_cheap": {"search": 2, "fetch": 0, "loop_guard": 4},
}

# What the models see of a page (decision 24).
SEARCH_RESULT_MAX_CHARS = 1_500  # about 430 tokens with title and URL
FETCH_EXCERPT_MAX_CHARS = 1_500
SYNTH_SOURCE_TOKEN_BUDGET = 11_200
SYNTH_PROMPT_TOKENS_ESTIMATE = 3_000
# Characters kept on each side of a matched term when cutting an excerpt.
EXCERPT_WINDOW_CHARS = 200
# Symptom words shorter than this are not used as excerpt anchors.
EXCERPT_MIN_SYMPTOM_WORD_CHARS = 4

RECURSION_LIMIT = 40

# Section 9 "typical" research pass the replay cassettes are scaled to: 5
# searches, no fetch, then a final turn. Each research call starts at the base
# and each search adds about RESEARCH_TOKENS_PER_SEARCH.
RESEARCH_CALL_BASE_TOKENS = 2_000
RESEARCH_TOKENS_PER_SEARCH = 2_150
RESEARCH_OUTPUT_TOKENS = 250
SYNTH_EXCERPT_TOKENS_TYPICAL = 10_000
SYNTH_OUTPUT_TOKENS_TYPICAL = 3_000

# ---------------------------------------------------------------------------
# Evidence spans (section 8.11)
# ---------------------------------------------------------------------------

EVIDENCE_MIN_CHARS = 20
EVIDENCE_MAX_CHARS = 400
SNIPPET_JOINER = "[...]"
# A model quote that is verbatim in the page but leaves out the code may be
# widened by code to start at the code, when the code is printed on the same
# line at most this many characters before the quote (a table row such as
# "| FLO | Stands for Flow Switch."). Phase 5 finding; the widened span is
# still a contiguous verbatim span of the page.
EVIDENCE_ANCHOR_MAX_GAP_CHARS = 40

# Documented causes (DOCUMENTED_CAUSE edges; decisions D1 and D2 of 18
# September 2026, after the Phase 6 live evaluation).
# Route row 3 (graph only, no search) needs at least this many distinct
# verified causes for the model or its family, and a classifier match; a
# question with no confirmed code is never answered from code edges alone.
GRAPH_ONLY_MIN_CAUSES = 3
# Condition 5 for a cause edge: its evidence holds the whole label, or at
# least this many distinct content words of the label (letters only, at
# least CAUSE_WORD_MIN_CHARS long, casefolded, a trailing "s", "ed" or "ing"
# stripped, CAUSE_STOP_WORDS left out).
CAUSE_MIN_SHARED_WORDS = 2
CAUSE_WORD_MIN_CHARS = 4
CAUSE_STOP_WORDS = frozenset({
    "about", "after", "also", "been", "before", "being", "both", "could", "does", "done", "each",
    "from", "have", "having", "into", "just", "like", "made", "make", "many", "more", "most", "much",
    "must", "only", "other", "over", "should", "some", "such", "than", "that", "their", "them",
    "then", "there", "these", "they", "this", "those", "very", "were", "what", "when", "where",
    "which", "while", "will", "with", "would", "your",
    # Troubleshooting filler that names no component and no fault (review
    # finding E5): "Check the unit" is not a documented cause.
    "check", "ensure", "sure", "please", "contact", "call", "need",
})

# ---------------------------------------------------------------------------
# Tier ceiling by host (section 8.6). Extend only with Roanuk's review.
# ---------------------------------------------------------------------------

FORUM_HOSTS = (
    "reddit.com",
    "quora.com",
    "justanswer.com",
    "stackexchange.com",
)
FORUM_HOST_PREFIXES = ("forum.", "forums.")

# The maker's own domains. A host matches if it equals a domain or ends with
# "." + domain.
MAKER_DOMAINS = {
    "sundance spas": ("sundancespas.com",),
    "trane": ("trane.com",),
    "rheem": ("rheem.com",),  # the third real model (seed appliance appl-waterheater)
}

# ---------------------------------------------------------------------------
# Paths and environment
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = REPO_ROOT / "seed"
DATA_DIR = Path(os.environ.get("ADVISOR_DATA_DIR", REPO_ROOT / "data"))

LEDGER_PATH = DATA_DIR / "ledger" / "ledger.sqlite"  # never deleted (decision 52)
# Replay rows go to their own file, created on demand, so a replay run can
# never silently recreate a deleted live ledger (Phase 1 review).
REPLAY_LEDGER_PATH = DATA_DIR / "ledger" / "replay_ledger.sqlite"
# Replay cassettes: approved copies are tracked here; until the privacy diff
# is approved they are read from STAGING_DIR / "cassettes".
CASSETTE_DIR = REPO_ROOT / "agent" / "tests" / "cassettes"
REGISTRY_PATH = DATA_DIR / "registry.sqlite"
CHECKPOINT_PATH = DATA_DIR / "checkpoints.sqlite"
GRAPH_PATH = DATA_DIR / "graph.json"  # live runs only (decision 37)
REPLAY_GRAPH_PATH = DATA_DIR / "replay_graph.json"
PAGES_DIR = DATA_DIR / "pages"
LOOKUPS_DIR = DATA_DIR / "lookups"
BRIEFS_OUT_DIR = DATA_DIR / "briefs"
PROPERTY_OUT_DIR = DATA_DIR / "property"
EVAL_DIR = DATA_DIR / "eval"
STAGING_DIR = DATA_DIR / "staging"  # cassettes wait here for the privacy diff
DEFAULT_SEED_PATH = SEED_DIR / "registry_seed.json"

# sqlite busy timeouts. The ledger waits longer: a refused write there would
# lose a charge, while a registry read can simply be retried.
LEDGER_BUSY_TIMEOUT_MS = 30_000
REGISTRY_BUSY_TIMEOUT_MS = 5_000

# Published pages v2 never writes to (decision 51, PLAN section 12).
PUBLISHED_PATHS = ("index.html", "tool.html", "src", "briefs", "demo-assets")

ENV_MODE = "ADVISOR_MODE"
ENV_CASSETTE = "ADVISOR_CASSETTE"
ENV_TRACING = "ADVISOR_TRACING"
ENV_GATE = "ADVISOR_GATE"
ENV_GATE_INVENTORY = "ADVISOR_GATE_INVENTORY"  # lets gate mode tests use a synthetic inventory
ENV_GATE_REQUIREMENTS = "ADVISOR_GATE_REQUIREMENTS"  # and synthetic per phase gate requirements
ENV_V1_DIR = "V1_BRIEFCASE_DIR"
ENV_NODE_BIN = "NODE_BIN"
DEFAULT_NODE_BIN = Path.home() / ".local" / "node" / "bin" / "node"

# Tracing variables langsmith reads; cli.py unsets them unless ADVISOR_TRACING=1.
TRACING_ENV_VARS = (
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
    "LANGSMITH_GATEWAY",
)

# ---------------------------------------------------------------------------
# Nodes builder additions (Phase 2)
# ---------------------------------------------------------------------------

# count_tokens_approximately's default ratio; synthesize truncates source
# excerpts to SYNTH_SOURCE_TOKEN_BUDGET with it.
CHARS_PER_TOKEN_ESTIMATE = 4
# Observed code candidates read from a symptom (PLAN 8.3): 2 to 6 capital
# letters or digits.
OBSERVED_CODE_MIN_CHARS = 2
OBSERVED_CODE_MAX_CHARS = 6
# read_plate accepts these photo types (the image types the API takes).
PHOTO_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# ---------------------------------------------------------------------------
# Graph builder additions (Phase 2)
# ---------------------------------------------------------------------------

# The phase this build has reached. test_v1_inventory.py enforces every
# v1_inventory.json row at or below it (ADVISOR_GATE overrides it at a gate).
BUILD_PHASE = 4

# ---------------------------------------------------------------------------
# Evaluation builder additions (Phase 6, `advisor eval`; PLAN sections 5 and 9)
# ---------------------------------------------------------------------------

# Section 9 step estimates the preflight prices from, in tokens. read_plate is
# v1's measured call; the classifier runs only when the rules cannot decide.
READ_PLATE_TOKENS_TYPICAL = {"input": 2_798, "output": 95}
CLASSIFIER_TOKENS_TYPICAL = {"input": 800, "output": 20}

# SC3b: live runs of v1 case B4 per batch (section 9, Phase 6 row).
SC3B_RUNS = 3
# The fabricated model's maker's own site, seen in v1's B4 lookup; a run
# record says whether the trail reached it (section 5, SC3b row).
SC3B_MAKER_DOMAINS = ("aquarestspas.com",)
# SC7b passes only if every repeat used at most this many searches (decision 34).
SC7B_REPEAT_SEARCH_MAX = 2
# How often one eval run answers the confirmation pause before it gives up.
EVAL_MAX_PAUSES = 2
# The key names a live command reads, from the environment or the repo's
# gitignored .env; LANGSMITH_* only when ADVISOR_TRACING=1. TYPESAFE_API_KEY
# only while Jev is enabled, so a run that cannot call Jev never reads it.
LIVE_KEY_NAMES = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY") + ((TYPESAFE_KEY_NAME,) if JEV_SAFETY_ENABLED else ())
TRACING_KEY_PREFIX = "LANGSMITH_"
ENV_FILE = REPO_ROOT / ".env"

# ---------------------------------------------------------------------------
# Live path builder additions (Phase 5; PLAN sections 8.9, 8.10, 9, decision 22)
# ---------------------------------------------------------------------------

# `advisor check-schema --live`: one minimal synthesize call that confirms the
# API accepts the BriefDraft schema. The reply is a no_reliable_answer draft
# with every list empty (about 150 tokens); 512 leaves room for a longer
# why_insufficient line.
SCHEMA_CHECK_NODE = "schema_check"
SCHEMA_CHECK_MAX_TOKENS = 512
SCHEMA_CHECK_OUTPUT_TOKENS_TYPICAL = 150
# Live runs leave a cassette candidate here (PLAN 8.10). Nothing here is
# tracked until the privacy diff is approved.
RECORDINGS_DIR = DATA_DIR / "recordings"

# ---------------------------------------------------------------------------
# Review fixes (Phase 5 live path, before any paid call)
# ---------------------------------------------------------------------------

# Decision 18: no live full run without a separate approval, and PLAN section
# 4 (the TIMEOUT_S row) wants the full synthesize timeout confirmed, or
# synthesize streamed, first. Every live command refuses full mode while this
# is False; the typed "proceed" is the per phase approval, not this one.
FULL_LIVE_APPROVED = False
# Search trail statuses of calls that never reached Tavily: blocked by a tool
# call cap, or refused by the ledger before the request was made. The
# persisted run record and the eval records count searches with this one
# tuple. "tavily_plan_limit" is absent on purpose: Tavily answered it.
TRAIL_NOT_REACHED_STATUSES = ("blocked", "run_cap", "build_cap", "run_credit_cap", "build_credit_cap")
# `advisor eval plates --live` (PLAN section 9, the "6, plates" row; section
# 6.5 cases E1 and E2): one read_plate call per plate, then the run pauses.
PLATE_EVAL_SYMPTOM = "not heating"
# v1 case E1's printed serial on demo-assets/plate-clear.jpg (v1
# scripts/run-tests.mjs, case E1).
E1_PLATE_SERIAL = "100915742"

# ---------------------------------------------------------------------------
# Safety step flagging (the word rule and Jev; decisions 53 to 57)
# ---------------------------------------------------------------------------

# Per Jev call: a 5 second timeout per attempt, at most one retry, and a 10
# second budget for the call and its retry (decision 57).
JEV_TIMEOUT_S = 5.0
JEV_RETRY_MAX = 1
JEV_RETRY_BUDGET_S = 10.0
# Input tokens reserved per attempt; Gate 0's one call used 384.
JEV_EST_INPUT_TOKENS = 450
# The BriefDraft schema sets no maximum number of try_first steps, so the
# preflight plans this many Jev calls per synthesize pass. The most steps in a
# recorded draft is 7 (the hvac cassette).
JEV_PLAN_STEPS_PER_PASS = 8

# The word rule as published: whole words, any case, in the step or its detail.
SAFETY_WORDS_PUBLISHED = (
    "breaker", "breakers",
    "power", "powered", "powering",
    "heat", "heater", "heaters", "heating", "heated",
)
# The word rule, tuned on the SC12a tune half only (29 September 2026, each change
# logged with its tune result in DECISION-LOG): words that gained recall with no
# new false flag were added, and "heat", which caught only thermostat and cover
# steps, was dropped.
SAFETY_WORDS = (
    "breaker", "breakers",
    "power", "powered", "powering",
    "heater", "heaters", "heating", "heated",
    "wire", "wires", "panel", "panels", "door", "doors",
    "disconnect", "disconnects", "electrical", "jumper", "box",
)
# Which code layers may raise a flag in production. The SC12a choice rule
# sets one of ("word",), ("jev",) or ("word", "jev"); on the held out half on
# 30 September 2026 it picked Jev (decision 62).
SAFETY_LAYERS = ("jev",)
# The locked tune half threshold for Jev (data/eval/sc12a/lock.json). None would
# record Jev's answers without letting them raise a flag.
JEV_THRESHOLD: float | None = 0.51

# ---------------------------------------------------------------------------
# SC12a and SC12b, the safety evaluation (agent/safety_eval, agent/live/sc12a.py
# and agent/live/sc12b.py; build brief sections "The labeled set" to "SC12b")
# ---------------------------------------------------------------------------

# The labeled set: G plus D stop at this many items; the stopping rule then
# adds D items this many at a time until the held out half has this many in
# scope positives or the pages run out. D skips sentences longer than this.
SC12A_POOL_SIZE = 300
SC12A_EXTEND_BY = 50
SC12A_MIN_HELDOUT_POSITIVES = 30
SC12A_MAX_SENTENCE_CHARS = 400
# Decision 58: pool D keeps at most this many qualifying sentences from any one
# page (its first ones in document order), then moves to the next page. A URL
# stored in more than one version is one page: the cap counts across versions.
SC12A_D_DOC_CAP = 25
# Near duplicates across the halves are reported with the set, not removed: a
# pair counts when, with digits and punctuation stripped, the two texts match,
# reach one of these similarity ratios, or share a span of at least this many
# characters.
SC12A_NEAR_DUP_RATIOS = (0.9, 0.8)
SC12A_NEAR_DUP_SPAN = 60
# Jev question wordings tried on the tune half, at most; blind readers per item.
SC12A_MAX_WORDINGS = 5
SC12A_READERS = 3
# The choice rule drops a candidate below this precision, and the threshold
# rule's fallback keeps only thresholds at or above it (decision 4 default).
SAFETY_PRECISION_FLOOR = 0.60
# This work's stop line: stop and report once the ledger's spend since the
# first SC12 live command passes it (success criterion 7).
SC12_STOP_USD = 1.50
# SC12b: this many live first lookups with the input of each recorded run.
SC12B_RUNS_PER_INPUT = 5
SC12B_INPUT_RUNS = ("t-267045726dd04118", "t-a6cc7b63e63b41d0")
# Where an SC12a batch writes its Jev replies, relative to EVAL_DIR. The
# harness (safety_eval/harness.replies_dir) and the SC7b first lookup filter
# (live/eval_sc7b.py, which skips the run IDs those replies name) both read it
# here, so the filter imports nothing the build fingerprint leaves out.
SC12A_REPLIES_SUBDIR = Path("sc12a") / "replies"
