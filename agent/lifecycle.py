"""Lifecycle research mode for the replacement sweep (SC14, decision 72).

`advisor property sweep <property_id> --research` (or `--all --research`)
looks up, for each unit the graph holds no lifecycle facts for, only whether
its model is discontinued and which models succeed it, then saves what the
existing rules verify as graph edges, which the offline sweep shows.

Where it runs: its own small path, not a mode of the main graph. The main
graph is built around a symptom: intake, the identity confirmation pause,
route, the history and graph branches, a brief draft, the safety check and
the render all assume one, and an intent in state would have to be read by
most of those nodes to switch them off. A lookup here is a sequence of the
same parts the graph uses, called directly with one RunContext per lookup:

1. Refusal before any call (`sweep.lookup_refusal`): a unit whose maker or
   model is labeled synthetic, or whose maker is not one whose own web domain
   the tool knows (config.MAKER_DOMAINS), is refused, and nothing is saved.
   A unit that already has lifecycle facts is not looked up.
2. Research: the research agent (agent/research/agent.py) with the same
   tools, middleware, ledger reservations and source building as the
   research node, under the lifecycle system prompt and the
   config.RESEARCH_LIMITS "top_up" row: at most 2 searches and 1 fetch, a
   loop guard of 5 model calls, config.MAX_TOKENS["research"] output tokens
   per call and the research sub budget. Excerpts are anchored on the model,
   its family and the lifecycle words in ANCHOR_WORDS.
3. One structured call (`paid_structured_call` on the synthesize node, so
   config.MAX_TOKENS["synthesize_cheap"] output tokens in cheap) for a
   `LifecycleDraft`: upgrade options only, each with a source number and a
   verbatim quote. With no source found the call is not made.
4. Validation with the brief's own rules: rule 1 (an option whose source
   number does not resolve is dropped), rule 2 (tiers lowered to the host
   ceiling), rule 9 (the quote must verify verbatim in the cited page with
   the successor inside it, as its own token) and rule 11 (pruning). Then a
   lookup's own check (`check_unit_named`): the quote must also name the
   unit's full registry model as its own token, or the option is dropped as
   UNIT_NOT_IN_SPAN, and a successor maker neither the unit's nor in the
   quote is cleared. Summaries are never stored or shown, so rule 10 has
   nothing to check.
5. Saving through persist's edge path (`persist.persist_edges`), which saves
   edges only from these validated options, checks each quote against its
   page again, and writes only where decision 37 allows: the replay graph in
   replay, the runtime graph on a live run with live charges.
6. A run record in the data folder's runs/ (kind "lifecycle_lookup").

A declined model call (decision 69), in research or in the structured call,
is charged, ends the lookup with status "model_declined" and saves nothing;
it is never retried, on this model or another.

Spending: one run cap for the whole sweep (Roanuk, 13 October 2026). All of
a sweep's lookups together spend at most config.RUN_CAP_USD for the mode
(`sweep_cap_usd`: config.RUN_CAP_USD["cheap"] in cheap; replay rehearses the
cheap sweep, so it uses the cheap cap). The mechanism is a sweep budget, not one shared ledger
run: each lookup stays its own run in the ledger, with its own run ID and run
record, every call reserved before it is sent and charged after, and
`SweepBudget` hands each lookup, as its run cap, what is left of the sweep's
cap after the lookups before it, read back from the ledger as each one's run
total. A cassette's caps block can lower that, never raise it. So the ledger
holds every call, retries included, to what is left, and the lookups run one
after another, so their totals add up to at most the cap (plus at most the
last call's estimate error, as for any run, PLAN 8.9).

A lookup starts only when what is left covers its worst case
(`lookup_worst_usd`: every call the top_up limits allow, each at its full
output budget and largest prompt, priced as the ledger prices that mode, so
at the recorded replay prices in replay, or at the live mode's when the
replayed cassettes are recordings that name it, `sweep_price_mode`). One that
it cannot cover is not
started: it is reported as not looked up because the sweep's run cap was
reached, and nothing is saved for it (no ledger row, no run record, no graph
edge). The preflight and the replay plan show the sweep as one plan whose
worst case is the cap (`sweep_plan`, `cap_scope_line`); the build budget and
credit checks before the preflight cover the cap and every lookup's credits.
The build cap is unchanged. A live sweep prints the planned calls and cost
from config prices through the preflight functions and waits for a typed
"proceed" (agent/cli.py).
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import HumanMessage, SystemMessage

from agent import config, prompts, sweep
from agent.build_info import build_id
from agent.declined import REFUSAL_ORIGIN, ModelDeclined, declined_stop_reason
from agent.kg import KnowledgeGraph, model_key, norm_maker
from agent.ledger import PROVIDER_ANTHROPIC, BudgetExceeded, call_prices, price_basis, price_usage
from agent.live.preflight import Plan, _price
from agent.models import make_model, make_tools, max_tokens_for
from agent.nodes.persist import persist_edges, run_record_path
from agent.nodes.research import BUDGET_STOP_REASONS, TRAIL_KEYS, build_sources, in_call_order
from agent.nodes.synthesize import build_sources_block, open_ledger, paid_structured_call
from agent.nodes.validate import page_texts_for
from agent.research.agent import build_research_agent
from agent.research.excerpts import Terms, excerpt_terms
from agent.research.tools import make_research_tools
from agent.rules import citations, tiers
from agent.rules import upgrades as upgrades_rule
from agent.rules.evidence import target_is_token
from agent.schemas import LifecycleDraft, _attach_sources
from agent.state import RunContext

NODE_RESEARCH = "research"
NODE_EXTRACT = "synthesize"
LIMITS_ROW = "top_up"
RECORD_KIND = "lifecycle_lookup"
# The lookups call Anthropic and Tavily only; Jev is never asked.
KEY_NAMES = tuple(name for name in config.LIVE_KEY_NAMES if name != config.TYPESAFE_KEY_NAME)
ANCHOR_WORDS = ("discontinued", "replaced", "replaces", "replacement", "successor", "superseded", "obsolete")

ELIGIBLE = "eligible"
REFUSED = "refused"
HAS_FACTS = "has_facts"

SAVED = "saved"
ALREADY_SAVED = "already_saved"
NOTHING_SAVED = "nothing_saved"
NO_SOURCES = "no_sources"
STOPPED = "budget_stopped"
UNREADABLE = "unreadable_reply"
DECLINED = REFUSAL_ORIGIN

# Why validation drops an option whose quote never names the unit looked up.
UNIT_NOT_IN_SPAN = "unit_not_in_span"

# Fields a validated source keeps (pipeline's brief source keys).
_SOURCE_KEYS = ("url", "title", "host", "retrieved_at", "origin")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    return f"lifecycle-{uuid.uuid4().hex[:16]}"


def identity_of(appliance: Mapping[str, Any]) -> dict[str, Any]:
    return {"manufacturer": appliance.get("manufacturer"), "model": appliance.get("model"),
            "serial": None, "manufacture_date": None}


def unit_label(appliance: Mapping[str, Any]) -> str:
    return f"{appliance.get('id')} {appliance.get('manufacturer')} {appliance.get('model')}"


# ---------------------------------------------------------------------------
# Which units are looked up
# ---------------------------------------------------------------------------


def eligibility(kg: KnowledgeGraph | None, appliances: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One decision per appliance: refused (with why), has_facts, or eligible. Refusal is checked first."""
    out = []
    for appliance in appliances:
        reason = sweep.lookup_refusal(appliance)
        if reason is not None:
            out.append({"appliance": appliance, "decision": REFUSED, "reason": reason})
        elif sweep.has_lifecycle_facts(kg, appliance):
            out.append({"appliance": appliance, "decision": HAS_FACTS, "reason": None})
        else:
            out.append({"appliance": appliance, "decision": ELIGIBLE, "reason": None})
    return out


def decision_line(decision: Mapping[str, Any]) -> str:
    unit = unit_label(decision["appliance"])
    if decision["decision"] == REFUSED:
        return f"  {unit}: refused before any call: {decision['reason']}; nothing is saved"
    if decision["decision"] == HAS_FACTS:
        return f"  {unit}: the graph already holds lifecycle facts for it; no lookup"
    return f"  {unit}: one lifecycle lookup planned"


def cassette_for(appliance: Mapping[str, Any], cassettes: Sequence[Any]) -> Any:
    """The replay cassette whose typed identity is this unit's maker and model, or None."""
    try:
        wanted = model_key(appliance.get("manufacturer") or "", appliance.get("model") or "")
    except ValueError:
        return None
    for cassette in cassettes:
        identity = cassette.input.get("identity") or {}
        try:
            if model_key(identity.get("manufacturer") or "", identity.get("model") or "") == wanted:
                return cassette
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# One run cap for the whole sweep, and the preflight plan
# ---------------------------------------------------------------------------

CAP_REACHED = "sweep_cap_reached"
# Float sums of a few small charges drift by far less than this (as in agent/ledger.py).
_EPSILON = 1e-9


def sweep_cap_usd(mode: str) -> float:
    """The one run cap every lookup of a sweep shares: config.RUN_CAP_USD for the mode.

    Replay rehearses the cheap sweep (its models are the cheap row's), so it uses the cheap cap.
    """
    return config.RUN_CAP_USD["cheap" if mode == "replay" else mode]


def lookup_caps(caps: Mapping[str, Any] | None, remaining: float) -> dict[str, Any]:
    """One lookup's run caps: what is left of the sweep's cap, or a cassette's lower cap."""
    out = dict(caps or {})
    out["run_cap_usd"] = min(float(out.get("run_cap_usd", remaining)), remaining)
    return out


def lookup_worst_usd(mode: str) -> float:
    """The most one lookup is expected to hold in the ledger, at the prices the ledger charges in `mode`.

    Every call the config.RESEARCH_LIMITS top_up row allows, each at its full
    output budget: research call k (from 0 to the loop guard) sees the base
    prompt, every search and fetch result the row allows, the tool prompt and
    k earlier replies, times the estimate margin; then the structured call at
    the synthesize source budget plus its prompt; then every Tavily credit the
    row allows. Replay uses the recorded model's margin, tool prompt, budgets
    and prices, as its ledger does. It is arithmetic, not a measurement, and
    it leaves out retries of failed requests; the ledger still holds every
    call, retries included, to what is left of the sweep's cap.
    """
    replay = mode == "replay"
    limits = config.RESEARCH_LIMITS[LIMITS_ROW]
    research_model = config.MODEL_FOR[mode][NODE_RESEARCH]
    extract_model = config.MODEL_FOR[mode][NODE_EXTRACT]

    def margin(model: str) -> float:
        return config.REPLAY_ESTIMATE_MARGIN if replay else config.ESTIMATE_MARGIN[model]

    def call_usd(model: str, prompt: float, out: int) -> float:
        tokens = math.ceil(prompt * margin(model))
        return price_usage(call_prices(mode, PROVIDER_ANTHROPIC, model, tokens),
                           {"input_tokens": tokens, "output_tokens": out})

    tool_prompt = config.REPLAY_TOOL_PROMPT_TOKENS if replay else config.TOOL_PROMPT_TOKENS[research_model]
    research_out = max_tokens_for(NODE_RESEARCH, mode)
    results = (limits["search"] * config.RESEARCH_TOKENS_PER_SEARCH
               + limits["fetch"] * math.ceil(config.FETCH_EXCERPT_MAX_CHARS / config.CHARS_PER_TOKEN_ESTIMATE))
    research = sum(call_usd(research_model, config.RESEARCH_CALL_BASE_TOKENS + results + tool_prompt + k * research_out,
                            research_out)
                   for k in range(limits["loop_guard"]))
    extract = call_usd(extract_model, config.SYNTH_SOURCE_TOKEN_BUDGET + config.SYNTH_PROMPT_TOKENS_ESTIMATE,
                       max_tokens_for(NODE_EXTRACT, mode))
    credits = (limits["search"] * config.TAVILY_CREDITS["search_basic"]
               + limits["fetch"] * config.TAVILY_CREDITS["extract_basic_per_5_urls"])
    return research + extract + credits * config.tavily_credit_usd()


class SweepBudget:
    """What is left of a sweep's one run cap as its lookups spend it, one after another.

    `record` adds each finished lookup's ledger run total; `covers_next` says
    whether what is left covers one more lookup's worst case; `caps_for` gives
    the next lookup, as its run cap, what is left.
    """

    def __init__(self, mode: str, price_mode: str | None = None) -> None:
        self.mode = mode
        # A replay of recordings that name their live mode is held to that mode (sweep_price_mode).
        self.price_mode = price_mode or mode
        self.cap = sweep_cap_usd(self.price_mode)
        self.worst = lookup_worst_usd(self.price_mode)
        self.spent = 0.0

    @property
    def remaining(self) -> float:
        return max(self.cap - self.spent, 0.0)

    def covers_next(self) -> bool:
        return self.remaining + _EPSILON >= self.worst

    def caps_for(self, caps: Mapping[str, Any] | None) -> dict[str, Any]:
        return lookup_caps(caps, self.remaining)

    def record(self, result: Mapping[str, Any]) -> None:
        self.spent += float(result.get("cost_usd") or 0.0)


def sweep_price_mode(mode: str, cassettes: Sequence[Any]) -> str:
    """The mode a sweep's lookups are priced and capped as (ledger.price_basis): live, its own mode.

    A replay sweep is priced as the cassettes it replays were recorded: the
    frozen replay settings for the synthetic and Haiku 4.5 cassettes, or the
    live mode a Claude Haiku 5.5 recording names, so a replayed sweep starts
    and skips the same lookups as the live sweep it recorded. One sweep has one
    cap, so cassettes recorded under different bases are refused together
    (ValueError), never mixed.
    """
    if mode != "replay":
        return mode
    bases = sorted({price_basis(mode, c) for c in cassettes})
    if len(bases) > 1:
        raise ValueError(f"the cassettes for this sweep were recorded under different prices ({', '.join(bases)}); "
                         "a sweep has one run cap, so replay the cassettes of one recording at a time")
    return bases[0] if bases else mode


def cap_reached_result(appliance: Mapping[str, Any], budget: SweepBudget) -> dict[str, Any]:
    """The result of a unit not looked up because the sweep's cap cannot cover it; nothing is saved."""
    return {"appliance_id": appliance.get("id"), "unit": unit_label(appliance), "run_id": None,
            "mode": budget.mode, "status": CAP_REACHED, "stop_reason": None, "kept": [], "dropped": [],
            "graph_edges": None, "search_trail": [], "cost_usd": 0.0, "remaining_usd": budget.remaining,
            "cap_usd": budget.cap, "worst_usd": budget.worst}


def lookup_plan(mode: str, appliance: Mapping[str, Any]) -> Plan:
    """What one lookup is expected to cost, from config prices (PLAN section 9 arithmetic).

    Typical: one model call per allowed search plus a final turn, each
    starting at RESEARCH_CALL_BASE_TOKENS and growing by RESEARCH_TOKENS_PER_SEARCH
    per search, then the structured call, which sees about the searched text
    plus SYNTH_PROMPT_TOKENS_ESTIMATE and writes at most a brief's typical
    output. Worst case: `lookup_worst_usd` at live prices (cheap mode's in
    replay, which prices the plan as if live).
    """
    limits = config.RESEARCH_LIMITS[LIMITS_ROW]
    searches = limits["search"]
    calls = searches + 1
    research_in = sum(config.RESEARCH_CALL_BASE_TOKENS + k * config.RESEARCH_TOKENS_PER_SEARCH for k in range(calls))
    research_usd = _price(config.MODEL_FOR[mode][NODE_RESEARCH], research_in, calls * config.RESEARCH_OUTPUT_TOKENS)
    extract_in = searches * config.RESEARCH_TOKENS_PER_SEARCH + config.SYNTH_PROMPT_TOKENS_ESTIMATE
    extract_usd = _price(config.MODEL_FOR[mode][NODE_EXTRACT], extract_in, config.SYNTH_OUTPUT_TOKENS_TYPICAL)
    credits = searches * config.TAVILY_CREDITS["search_basic"]
    max_credits = credits + limits["fetch"] * config.TAVILY_CREDITS["extract_basic_per_5_urls"]
    return Plan(
        label=f"one lifecycle lookup for {unit_label(appliance)} (discontinuation and successors only)",
        typical_calls=calls + 1,
        max_calls=limits["loop_guard"] + 1,
        typical_usd=research_usd + extract_usd + credits * config.tavily_credit_usd(),
        worst_usd=lookup_worst_usd("cheap" if mode == "replay" else mode),
        typical_credits=credits,
        max_credits=max_credits,
        notes=[
            f"research: at most {searches} searches and {limits['fetch']} fetch, a loop guard of "
            f"{limits['loop_guard']} model calls, {max_tokens_for(NODE_RESEARCH, mode)} output tokens per call",
            f"then one structured call with at most {max_tokens_for(NODE_EXTRACT, mode)} output tokens, "
            "skipped when no source is found",
            "a failed research request may be retried once (ModelRetryMiddleware); every attempt is reserved "
            "in the ledger before it is sent, under what is left of the sweep's run cap",
        ],
    )


def sweep_plan(mode: str, appliances: Sequence[Mapping[str, Any]]) -> Plan:
    """Every planned lookup of a sweep as one plan: summed calls, credits and typical cost, worst case the cap."""
    plans = [lookup_plan(mode, a) for a in appliances]
    lookups = [f"lookup {i}: {unit_label(a)}, typical {p.typical_usd:.4f} USD, at most {p.worst_usd:.4f} USD"
               for i, (a, p) in enumerate(zip(appliances, plans, strict=True), start=1)]
    return Plan(
        label=f"{len(plans)} lifecycle lookup(s) for the sweep (discontinuation and successors only), "
              "under one run cap",
        typical_calls=sum(p.typical_calls for p in plans),
        max_calls=sum(p.max_calls for p in plans),
        typical_usd=sum(p.typical_usd for p in plans),
        worst_usd=sweep_cap_usd(mode),
        typical_credits=sum(p.typical_credits for p in plans),
        max_credits=sum(p.max_credits for p in plans),
        notes=[*lookups, *(plans[0].notes if plans else [])],
    )


def cap_scope_line(plan: Plan, lookups: int, mode: str, *, recorded: bool = False) -> str:
    """What the run cap covers: the whole sweep, so the sweep's worst case is the cap.

    `mode` is the mode the lookups are priced as; `recorded` says a replay
    prices them as the live mode its recordings name.
    """
    priced = (" at the replay ledger's prices" if mode == "replay"
              else f" at {mode} mode's prices, as recorded" if recorded else "")
    return (f"run cap {sweep_cap_usd(mode):.2f} USD for the whole sweep: {lookups} lookup(s), typical total "
            f"{plan.typical_usd:.4f} USD, worst case {plan.worst_usd:.4f} USD; a lookup starts only while what is "
            f"left of the cap covers its worst case of {lookup_worst_usd(mode):.4f} USD{priced}, and the ledger "
            "holds each of its calls to what is left")


def later_routes_line(graph_path: Path | str) -> str:
    """The live sweep's warning about what its saved edges do to later questions (route row 4).

    kg.has_model counts any edge of a model, so once a sweep saves a
    successor or discontinued edge, the next troubleshooting question about
    that unit takes the graph route with a short top up research pass rather
    than a first lookup. First lookups being measured go before the sweep.
    """
    return (f"graph: each successor or discontinued edge this sweep saves to {graph_path} gives that model verified "
            "edges, so a later question about the unit takes the graph route with a short top up research pass "
            "(route row 4), not a first lookup; run any first lookups being measured (SC7b, the demo re-record) "
            "before this sweep")


def plan_lines(plan: Plan, lookups: int, price_mode: str = "replay") -> list[str]:
    """A replay sweep's plan: the same figures, priced as config prices them; replay spends nothing.

    `price_mode` is sweep_price_mode's: "replay", or the live mode the replayed recordings name.
    """
    if not lookups:
        return []
    return [f"planned: {plan.label}",
            f"  model calls: typical {plan.typical_calls}, at most {plan.max_calls}",
            f"  estimated cost if live: typical {plan.typical_usd:.4f} USD, worst case {plan.worst_usd:.4f} USD",
            *(f"  note: {note}" for note in plan.notes),
            cap_scope_line(plan, lookups, price_mode, recorded=price_mode != "replay")]


# ---------------------------------------------------------------------------
# One lookup
# ---------------------------------------------------------------------------


def lifecycle_terms(identity: Mapping[str, Any]) -> Terms:
    """Excerpt anchors: the model and its family, then the lifecycle words."""
    return Terms(ids=excerpt_terms(dict(identity), None, None).ids, symptom=ANCHOR_WORDS)


def research_pass(identity: Mapping[str, Any], ctx: RunContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                                                          str | None]:
    """(sources, search trail, stop reason or None) from one bounded research pass.

    The stop reason is the ledger's (a budget stop) or, for a declined model
    turn, declined_stop_reason's "model_declined: <category> (research)".
    """
    limits = dict(config.RESEARCH_LIMITS[LIMITS_ROW])
    terms = lifecycle_terms(identity)
    trail: list[dict[str, Any]] = []
    base = make_tools(ctx)
    tools = make_research_tools(ctx, search_inner=base[0].inner, fetch_inner=base[1].inner, terms=terms, trail=trail)
    agent = build_research_agent(make_model(NODE_RESEARCH, ctx), tools, limits, ctx, trail,
                                 system_prompt=prompts.LIFECYCLE_RESEARCH_PROMPT)
    task = prompts.LIFECYCLE_RESEARCH_USER.format(manufacturer=identity.get("manufacturer"),
                                                  model=identity.get("model"))
    start = len(ctx.collector)
    stop: str | None = None
    messages: list[Any] = []
    try:
        for values in agent.graph.stream({"messages": [HumanMessage(task)]}, durability="exit",
                                         stream_mode="values"):
            messages = values.get("messages") or messages
    except ModelCallLimitExceededError:
        pass  # the loop guard: research ends with what was collected
    except BudgetExceeded as exc:
        if exc.reason in BUDGET_STOP_REASONS:
            stop = exc.reason
        elif exc.reason != "research_budget":
            raise
    except ModelDeclined as exc:  # charged by SpendCap; never retried (decision 69)
        stop = declined_stop_reason(NODE_RESEARCH, exc)
    artifacts = in_call_order(ctx.collector[start:], messages)
    sources = build_sources(ctx, artifacts, trail, set(), terms)
    return sources, [{k: e[k] for k in TRAIL_KEYS} for e in trail], stop


def extract_messages(identity: Mapping[str, Any], sources: Sequence[dict[str, Any]], ctx: RunContext) -> list[Any]:
    block = build_sources_block(list(sources), ctx.pages_dir) or prompts.LIFECYCLE_NO_SOURCES
    user = prompts.LIFECYCLE_EXTRACT_USER.format(manufacturer=identity.get("manufacturer"),
                                                 model=identity.get("model"), sources=block)
    return [SystemMessage(prompts.LIFECYCLE_EXTRACT_SYSTEM), HumanMessage(user)]


def validate_lifecycle(draft: Mapping[str, Any], sources: Sequence[dict[str, Any]], identity: Mapping[str, Any],
                       page_texts: Mapping[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the validated brief shaped dict, dropped options with their reasons): rules 1, 2, 9, the unit check, 11.

    Raises pydantic's ValidationError when the draft does not match LifecycleDraft.
    """
    parsed = LifecycleDraft.model_validate(dict(draft))
    registry = [{k: s[k] for k in _SOURCE_KEYS if k in s} for s in sources]
    brief: dict[str, Any] = {
        "status": "ok", "sources": _attach_sources(parsed, registry),  # type: ignore[arg-type]
        "upgrade_options": [u.model_dump(mode="json") for u in parsed.upgrade_options],
        "try_first": [], "candidates": [], "maintenance_due": [],
    }
    citations.resolve_citations(brief)
    brief["sources"] = tiers.apply_tiers(brief["sources"], manufacturer=identity.get("manufacturer"),
                                         page_texts=dict(page_texts))
    dropped: list[dict[str, Any]] = []
    upgrades_rule.check_upgrade_options(brief, page_texts=dict(page_texts), report=dropped)
    check_unit_named(brief, identity, page_texts, dropped)
    citations.prune_and_renumber(brief)
    return brief, dropped


def check_unit_named(brief: dict[str, Any], identity: Mapping[str, Any], page_texts: Mapping[str, str],
                     report: list[dict[str, Any]]) -> None:
    """Keep only options whose quote names the unit's full registry model as its own token (in place).

    Rule 9 checks only the successor, so a quote about a sibling model ("The
    XR13 is discontinued and is replaced by the XR99") would otherwise be
    saved as this unit's discontinuation. The family alone is not enough,
    since it admits sibling models. A kept option's successor maker is
    cleared unless it is the unit's maker or the quote names it, so a maker
    the model invented never reaches the graph (persist then uses the unit's).
    """
    model, maker = identity.get("model"), identity.get("manufacturer")
    kept: list[dict[str, Any]] = []
    for i, entry in enumerate(brief.get(upgrades_rule.LIST) or []):
        url = upgrades_rule.cited_url(brief, entry)
        quote = entry.get("evidence")
        if not (isinstance(model, str) and sweep.names_successor(quote, model)
                and target_is_token(quote, page_texts.get(url) or "", target=model)):
            report.append({"list": upgrades_rule.LIST, "index": i, "successor": entry.get("successor_model"),
                           "reason": UNIT_NOT_IN_SPAN})
            continue
        named = entry.get("successor_manufacturer")
        if named and not (_same_maker(named, maker) or sweep.names_successor(quote, named)):
            entry["successor_manufacturer"] = None
        kept.append(entry)
    brief[upgrades_rule.LIST] = kept


def _same_maker(a: Any, b: Any) -> bool:
    try:
        return norm_maker(str(a)) == norm_maker(str(b))
    except ValueError:
        return False


def lookup(appliance: Mapping[str, Any], ctx: RunContext) -> dict[str, Any]:
    """Run one lifecycle lookup for one unit and save what verifies; returns the result."""
    from pydantic import ValidationError

    identity = identity_of(appliance)
    result: dict[str, Any] = {"appliance_id": appliance.get("id"), "unit": unit_label(appliance),
                              "run_id": ctx.run_id, "mode": ctx.mode, "status": None, "stop_reason": None,
                              "kept": [], "dropped": [], "graph_edges": None, "search_trail": []}
    refusal = sweep.lookup_refusal(appliance)
    if refusal is not None:  # the CLI never sends one here; refused again, before any call
        result.update(status=REFUSED, stop_reason=refusal, cost_usd=0.0)
        return result
    ledger = open_ledger(ctx)
    sources, trail, stop = research_pass(identity, ctx)
    result["search_trail"] = trail
    if stop is not None:
        result.update(status=DECLINED if stop.startswith(f"{REFUSAL_ORIGIN}:") else STOPPED, stop_reason=stop)
    elif not sources:
        result["status"] = NO_SOURCES
    else:
        try:
            out, _ = paid_structured_call(ctx, NODE_EXTRACT, LifecycleDraft,
                                          extract_messages(identity, sources, ctx))
        except BudgetExceeded as exc:
            result.update(status=STOPPED, stop_reason=exc.reason)
        except ModelDeclined as exc:  # charged by paid_structured_call; never retried (decision 69)
            result.update(status=DECLINED, stop_reason=declined_stop_reason(NODE_EXTRACT, exc))
        else:
            parsed = out.get("parsed")
            if out.get("parsing_error") is not None or parsed is None:
                result["status"] = UNREADABLE
            else:
                try:
                    brief, dropped = validate_lifecycle(parsed.model_dump(mode="json"), sources, identity,
                                                        page_texts_for(sources, ctx))
                except ValidationError:
                    result["status"] = UNREADABLE
                else:
                    result["kept"], result["dropped"] = brief["upgrade_options"], dropped
                    state = {"status": "ok", "brief": brief, "sources": sources, "identity": identity,
                             "run_id": ctx.run_id}
                    report = persist_edges(state, ctx, ctx.run_id)  # type: ignore[arg-type]
                    result["graph_edges"] = report
                    result["status"] = (SAVED if report["written"] else
                                        ALREADY_SAVED if report["already_present"] else NOTHING_SAVED)
    result["cost_usd"] = ledger.run_total(ctx.run_id)
    result["tavily_credits"] = ledger.run_credits(ctx.run_id)
    write_record(ctx, result)
    return result


def write_record(ctx: RunContext, result: Mapping[str, Any]) -> None:
    """The lookup's run record, beside the brief run records (never under seed/)."""
    path = run_record_path(ctx, ctx.run_id)
    record = {"kind": RECORD_KIND, "build_id": build_id(), "generated_at": _now(), **result}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")


def result_lines(result: Mapping[str, Any]) -> list[str]:
    """What the sweep prints for one lookup."""
    status = result["status"]
    if status == CAP_REACHED:
        return [f"  {result['unit']}: not looked up because the sweep's run cap was reached: "
                f"{float(result['remaining_usd']):.4f} USD of the {float(result['cap_usd']):.4f} USD cap left, "
                f"below one lookup's worst case of {float(result['worst_usd']):.4f} USD; nothing saved"]
    head = f"  {result['unit']}: lookup {result['run_id']}"
    edges = result.get("graph_edges") or {}
    if status == SAVED:
        what = f"{edges.get('written', 0)} graph edge(s) saved"
    elif status == ALREADY_SAVED:
        what = "every verified edge was already in the graph"
    elif status == NO_SOURCES:
        what = "no source found; no structured call made; nothing saved"
    elif status == STOPPED:
        what = f"stopped by the ledger ({result.get('stop_reason')}); nothing saved"
    elif status == UNREADABLE:
        what = "the reply did not match the schema; nothing saved"
    elif status == DECLINED:
        what = f"the model declined ({result.get('stop_reason')}); nothing saved, and no other model was asked"
    elif status == REFUSED:
        what = f"refused before any call: {result.get('stop_reason')}; nothing saved"
    else:
        what = "no successor verified in the sources found; nothing saved"
    priced = "at replay prices on the replay ledger, no money" if result.get("mode") == "replay" else "live"
    lines = [f"{head}: {what}; spent {float(result.get('cost_usd') or 0):.4f} USD ({priced})"]
    for option in result.get("kept") or []:
        lines.append(f"    verified: {option.get('reason')}, successor {option.get('successor_model')}")
    for drop in result.get("dropped") or []:
        lines.append(f"    dropped: successor {drop.get('successor')}: {drop.get('reason')}")
    if edges.get("skipped"):
        lines.append(f"    graph: {edges['skipped']}")
    return lines
