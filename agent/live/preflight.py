"""Preflight for live commands: planned calls, estimated cost, typed "proceed" (PRD Part A rule 1).

Before any paid call a live command prints the planned number of model calls
and Tavily credits, the estimated cost (typical and worst case), the per run
cap, and the build spend so far and remaining, then reads one line from stdin
and continues only if it is exactly "proceed". No flag skips it.

Every figure is computed here from config values with the PLAN section 9
arithmetic; nothing is typed in:

- read_plate: config.READ_PLATE_TOKENS_TYPICAL (v1's measured call).
- research, typical: config.RESEARCH_LIMITS["main"]["search"] searches and a
  final answer turn; call k (from 0) starts at RESEARCH_CALL_BASE_TOKENS plus
  k * RESEARCH_TOKENS_PER_SEARCH and writes RESEARCH_OUTPUT_TOKENS.
- synthesize, typical: SYNTH_EXCERPT_TOKENS_TYPICAL + SYNTH_PROMPT_TOKENS_ESTIMATE
  in, SYNTH_OUTPUT_TOKENS_TYPICAL out.
- the classifier runs only when the rules cannot decide, so it is in the
  "at most" call count and not in the typical cost.
- Jev, while config.JEV_SAFETY_ENABLED: one call per try_first step per
  synthesize pass. The BriefDraft schema sets no maximum, so each pass plans
  config.JEV_PLAN_STEPS_PER_PASS calls of config.JEV_EST_INPUT_TOKENS input
  tokens, output free; typical is one pass, and SYNTHESIZE_PASSES passes are
  planned too. Neither is an upper bound, since a longer draft makes more
  calls; each is reserved and held under the run cap. A missing TYPESAFE_API_KEY is refused before this preflight prints, since
  config.LIVE_KEY_NAMES holds it while Jev is enabled.
- worst case for a run: its per run cap (config.RUN_CAP_USD), which the ledger
  enforces before every call; for a resumed run, the cap less what the run
  already spent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import IO, Any

from agent import config

PROCEED = "proceed"
STAGE_ASK = "ask"
STAGE_RESUME = "resume"
# The first draft and the draft after the retry pass (max_model_calls counts both).
SYNTHESIZE_PASSES = 2


@dataclass
class Plan:
    """What one paid step or run is expected to cost."""

    label: str
    typical_calls: int
    max_calls: int
    typical_usd: float
    worst_usd: float
    typical_credits: int
    max_credits: int
    notes: list[str] = field(default_factory=list)
    typical_jev_calls: int = 0
    max_jev_calls: int = 0


def _usd(amount: float) -> str:
    return f"{amount:.4f} USD"


def _price(model: str, input_tokens: float, output_tokens: float) -> float:
    prices = config.PRICES_PER_MTOK[model]
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def read_plate_typical_usd(mode: str) -> float:
    tokens = config.READ_PLATE_TOKENS_TYPICAL
    return _price(config.MODEL_FOR[mode]["read_plate"], tokens["input"], tokens["output"])


def research_typical(mode: str) -> tuple[int, float, int]:
    """(model calls, cost, credits) of a typical first research pass."""
    searches = config.RESEARCH_LIMITS["main"]["search"]
    calls = searches + 1  # one call per search, then the final answer turn
    input_tokens = sum(
        config.RESEARCH_CALL_BASE_TOKENS + k * config.RESEARCH_TOKENS_PER_SEARCH for k in range(calls)
    )
    output_tokens = calls * config.RESEARCH_OUTPUT_TOKENS
    credits = searches * config.TAVILY_CREDITS["search_basic"]
    usd = _price(config.MODEL_FOR[mode]["research"], input_tokens, output_tokens)
    return calls, usd + credits * config.tavily_credit_usd(), credits


def synthesize_typical_usd(mode: str) -> float:
    input_tokens = config.SYNTH_EXCERPT_TOKENS_TYPICAL + config.SYNTH_PROMPT_TOKENS_ESTIMATE
    return _price(config.MODEL_FOR[mode]["synthesize"], input_tokens, config.SYNTH_OUTPUT_TOKENS_TYPICAL)


def jev_calls(passes: int) -> int:
    """Jev calls planned for this many synthesize passes: one per step, none while Jev is disabled."""
    return passes * config.JEV_PLAN_STEPS_PER_PASS if config.JEV_SAFETY_ENABLED else 0


def jev_typical_usd(calls: int) -> float:
    """Jev's cost for this many calls: input only, output free."""
    return _price(config.JEV_MODEL, calls * config.JEV_EST_INPUT_TOKENS, 0)


def jev_line(typical: int, planned: int) -> str:
    """The preflight's Jev line. It claims no upper bound: the draft schema caps no step count."""
    return (f"Jev calls ({config.JEV_PROVIDER}, {config.JEV_MODEL}): planned {typical} for one draft, "
            f"{planned} for {SYNTHESIZE_PASSES} drafts at {config.JEV_PLAN_STEPS_PER_PASS} steps each; one call "
            "per try_first step (a longer draft makes more), each reserved and held under the run cap")


def max_model_calls(mode: str, *, photo: bool) -> int:
    """Every model request one run can make, before retries of failed requests."""
    retry_row = "main" if mode == "full" else "retry_cheap"
    return (
        (1 if photo else 0)  # read_plate
        + 1  # classifier
        + config.RESEARCH_LIMITS["main"]["loop_guard"]
        + 1  # synthesize
        + config.RESEARCH_LIMITS[retry_row]["loop_guard"]
        + 1  # synthesize after the retry pass
    )


def run_plan(mode: str, *, photo: bool, stage: str, spent_in_run: float = 0.0) -> Plan:
    """The section 9 plan for one run (ask) or the rest of a paused run (resume)."""
    cap = config.RUN_CAP_USD[mode]
    research_calls, research_usd, credits = research_typical(mode)
    plate = photo and stage == STAGE_ASK
    typical_jev = jev_calls(1)
    typical_usd = (research_usd + synthesize_typical_usd(mode) + (read_plate_typical_usd(mode) if plate else 0.0)
                   + jev_typical_usd(typical_jev))
    typical_calls = research_calls + 1 + (1 if plate else 0)
    notes = ["a failed research request may be retried once (ModelRetryMiddleware); every attempt, "
             "retries included, is reserved in the ledger before it is sent"]
    if stage == STAGE_ASK and photo:
        notes.append("this command makes only the read_plate call, then pauses for your confirmation; "
                     "resume shows its own preflight for the rest of the run")
    if stage == STAGE_RESUME:
        notes.append(f"this run has already spent {_usd(spent_in_run)}")
    return Plan(
        label=f"one {mode} run" if stage == STAGE_ASK else f"the rest of one {mode} run",
        typical_calls=typical_calls,
        max_calls=max_model_calls(mode, photo=plate),
        typical_usd=typical_usd,
        worst_usd=max(cap - spent_in_run, 0.0),
        typical_credits=credits,
        max_credits=config.RUN_CREDIT_CAP,
        notes=notes,
        typical_jev_calls=typical_jev,
        max_jev_calls=jev_calls(SYNTHESIZE_PASSES),
    )


def haiku_mode(mode: str) -> bool:
    """True when any step of this mode runs on Haiku."""
    return config.HAIKU in config.MODEL_FOR[mode].values()


def haiku_refusal(mode: str, today: date) -> str | None:
    """Why a Haiku mode may not start on this date, or None (decision 14)."""
    if haiku_mode(mode) and today.isoformat() >= config.HAIKU_RETIREMENT_EARLIEST:
        return (
            f"{mode} mode uses {config.HAIKU}, which may be retired on or after "
            f"{config.HAIKU_RETIREMENT_EARLIEST}. Recheck the model list (config.MODEL_FOR and the "
            "Anthropic model deprecations page) before any live run."
        )
    return None


def preflight_lines(command: str, mode: str, plans: Sequence[Plan], ledger: Any) -> list[str]:
    """The text a live command prints before its first paid call."""
    spent = ledger.build_total()
    credits = ledger.build_credits()
    lines = [f"advisor {command}: preflight, mode {mode}, LIVE (real money)"]
    total_typical = total_worst = 0.0
    for plan in plans:
        lines += [
            f"planned: {plan.label}",
            f"  model calls: typical {plan.typical_calls}, at most {plan.max_calls}",
            f"  Tavily credits: typical {plan.typical_credits}, at most {plan.max_credits}",
        ]
        if plan.max_jev_calls:
            lines.append("  " + jev_line(plan.typical_jev_calls, plan.max_jev_calls))
        lines += [
            f"  estimated cost: typical {_usd(plan.typical_usd)}, worst case {_usd(plan.worst_usd)}",
        ]
        lines += [f"  note: {note}" for note in plan.notes]
        total_typical += plan.typical_usd
        total_worst += plan.worst_usd
    if len(plans) > 1:
        lines.append(f"total estimated cost: typical {_usd(total_typical)}, worst case {_usd(total_worst)}")
    lines += [
        f"per run cap ({mode}): {_usd(config.RUN_CAP_USD[mode])}; the ledger refuses any call that would "
        "pass it, so a run can exceed it by at most one call's input estimate error (PLAN 8.9)",
        f"build spend so far (live runs): {_usd(spent)} of {_usd(config.BUILD_CAP_USD)}, "
        f"remaining {_usd(config.BUILD_CAP_USD - spent)}",
        f"build Tavily credits so far: {credits} of {config.BUILD_CREDIT_CAP}, "
        f"remaining {config.BUILD_CREDIT_CAP - credits}; each credit is priced at "
        f"{_usd(config.tavily_credit_usd())} (pay as you go {'on' if config.TAVILY_PAYG_ENABLED else 'off'})",
    ]
    if mode == "full":
        lines.append("note: full mode: Sonnet 5 synthesize with reasoning effort; config.FULL_LIVE_APPROVED "
                     "records its decision 18 approval")
    lines.append(f'Type {PROCEED} to spend this; anything else cancels and spends nothing.')
    return lines


def confirmed(line: str) -> bool:
    """True only for exactly "proceed", with at most its line ending."""
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
    return line == PROCEED


def ask_to_proceed(lines: Sequence[str], stdin: IO[str], stdout: IO[str]) -> bool:
    """Print the preflight, read one line, and say whether it was exactly "proceed"."""
    for line in lines:
        print(line, file=stdout)
    stdout.flush()
    return confirmed(stdin.readline())
