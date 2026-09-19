"""`advisor eval sc3b --live`, and the batch harness both eval commands share.

SC3b (PLAN section 5, decisions 11, 33, 35): config.SC3B_RUNS cheap runs of v1
case B4, the fabricated Aquarest ZX-9000 Pro, "not heating", typed identity,
no photo. Each run goes through the normal graph (agent.graph) with the same
nodes, models factory and ledger as `advisor ask`; nothing here calls a model
or Tavily directly. One preflight covers the batch.

The harness (this module's second half) is shared with eval_sc7b and
eval_plates:

- refusals before anything is spent: the mode must be cheap with --live; the
  ledger must exist (or --new-ledger) and the remaining build budget and
  credits must cover every run of the batch at its cap; both keys must be
  present and the date must be before config.HAIKU_RETIREMENT_EARLIEST when
  the mode uses Haiku (both checked by cli.LiveSession); a rerun on a changed
  build must name its logged fix, and its preflight says what it replaces;
  a pooled rerun of a fixed build carries that build's logged fix (decision 35);
- the preflight (PRD Part A rule 1): planned calls and estimated cost, typical
  and worst case, computed from config values with the PLAN section 9
  arithmetic, the per run cap, the build spend so far and remaining, and the
  Tavily credits planned; then one line from stdin, which must be exactly
  "proceed". No flag skips it;
- one JSON run record per run in config.EVAL_DIR, with the spec's v1 case
  check (PLAN 6.5); a run the build budget stops before it starts still gets
  a "not_started" record, so no planned run goes missing from the result.

Keys (ANTHROPIC_API_KEY, TAVILY_API_KEY, and LANGSMITH_* only when
ADVISOR_TRACING=1) are loaded by the live path's own loader (agent/live/env.py
through cli.LiveSession), only after the mode and ledger checks passed, and
only for the batch: they sit in os.environ for the clients while it runs and
are removed afterwards. No key value is printed, logged or written: printed
text goes through the session's redacting streams and every record through
env.redact_value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from agent import cli, config
from agent.build_info import build_id as _build_id
from agent.cli import CliRefusal
from agent.live import evaluators
from agent.live.preflight import PROCEED, confirmed

SUITE = "sc3b"
EVAL_MODE = "cheap"

B4_IDENTITY = {"manufacturer": "Aquarest", "model": "ZX-9000 Pro"}
B4_SYMPTOM = "not heating"


# ---------------------------------------------------------------------------
# Run specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    """One planned eval run: its input, the owner's scripted answer, and its expected route.

    expected_route is "research" (route row 5, main caps), "top_up" (rows 2
    and 4) or "graph" (rows 1 and 3); it prices the preflight only and never
    steers the graph.
    """

    suite: str
    case: str
    label: str
    symptom: str
    expected_route: str
    identity: Mapping[str, str] | None = None
    photo: Path | None = None
    answer_identity: Mapping[str, str] | None = None
    answer_code: str | None = None
    role: str = "run"
    model_key: str | None = None
    note: str | None = None
    stop_at_pause: bool = False  # plate runs: read_plate only; the pause is never answered

    @property
    def classifier_possible(self) -> bool:
        """The route classifier may run: the graph knows the model and no code is confirmed."""
        return self.expected_route in ("graph", "top_up") and not self.answer_code


def sc3b_specs() -> list[RunSpec]:
    """config.SC3B_RUNS runs of v1 case B4 (PLAN 6.5)."""
    return [
        RunSpec(suite=SUITE, case="B4", label=f"B4 fabricated model, run {i}", symptom=B4_SYMPTOM,
                identity=dict(B4_IDENTITY), expected_route="research", role="run",
                model_key=model_key(B4_IDENTITY["model"]))
        for i in range(1, config.SC3B_RUNS + 1)
    ]


def model_key(model: str | None) -> str:
    """The key first lookups and repeats share: the model normalized the way the graph does."""
    from agent.kg import norm_model

    return norm_model(model or "")


# ---------------------------------------------------------------------------
# Estimates (PLAN section 9, from config values only)
# ---------------------------------------------------------------------------


def _price(node: str, input_tokens: int, output_tokens: int) -> float:
    from agent.ledger import price_usage

    model = config.MODEL_FOR[EVAL_MODE][node]
    return price_usage(config.PRICES_PER_MTOK[model], {"input_tokens": input_tokens, "output_tokens": output_tokens})


def research_typical(kind: str) -> dict[str, float]:
    """A typical research pass: every allowed search, no fetch, then a final turn (section 9 B)."""
    searches = config.RESEARCH_LIMITS[kind]["search"]
    calls = searches + 1
    input_tokens = calls * config.RESEARCH_CALL_BASE_TOKENS + config.RESEARCH_TOKENS_PER_SEARCH * sum(range(calls))
    output_tokens = calls * config.RESEARCH_OUTPUT_TOKENS
    return {"calls": calls, "searches": searches, "usd": _price("research", input_tokens, output_tokens)}


def synthesize_typical_usd() -> float:
    return _price(
        "synthesize",
        config.SYNTH_EXCERPT_TOKENS_TYPICAL + config.SYNTH_PROMPT_TOKENS_ESTIMATE,
        config.SYNTH_OUTPUT_TOKENS_TYPICAL,
    )


def _research_kind(route: str) -> str | None:
    return {"research": "main", "top_up": "top_up", "graph": None}[route]


@dataclass
class RunEstimate:
    typical_usd: float
    worst_usd: float
    typical_model_calls: int
    max_model_calls: int
    typical_credits: int
    first_pass_credits: int
    credit_ceiling: int


def estimate(spec: RunSpec, *, route: str | None = None) -> RunEstimate:
    """Typical and worst case for one run; the worst case is the per run cap the ledger enforces."""
    route = route or spec.expected_route
    kind = _research_kind(route)
    usd = synthesize_typical_usd()
    calls = 1
    max_calls = 1
    if spec.photo is not None:
        rp = config.READ_PLATE_TOKENS_TYPICAL
        usd += _price("read_plate", rp["input"], rp["output"])
        calls += 1
        max_calls += 1
    if spec.classifier_possible and route != "research":
        cl = config.CLASSIFIER_TOKENS_TYPICAL
        usd += _price("classifier", cl["input"], cl["output"])
        calls += 1
        max_calls += 1
    credits = first_pass = 0
    if kind is not None:
        research = research_typical(kind)
        usd += research["usd"]
        calls += research["calls"]
        max_calls += config.RESEARCH_LIMITS[kind]["loop_guard"]
        credits = research["searches"]
        first_pass = config.RESEARCH_LIMITS[kind]["search"] + config.RESEARCH_LIMITS[kind]["fetch"]
    # A validation failure may take the reduced retry pass and one more synthesize call.
    max_calls += config.RESEARCH_LIMITS["retry_cheap"]["loop_guard"] + 1
    return RunEstimate(
        typical_usd=usd, worst_usd=config.RUN_CAP_USD[EVAL_MODE], typical_model_calls=calls,
        max_model_calls=max_calls, typical_credits=credits,
        first_pass_credits=min(first_pass, config.RUN_CREDIT_CAP), credit_ceiling=config.RUN_CREDIT_CAP,
    )


# ---------------------------------------------------------------------------
# Refusals before anything is spent
# ---------------------------------------------------------------------------


def check_mode(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    """cheap with --live only: replay spends nothing and full has no live approval (decision 18)."""
    mode = cli.resolve_mode(environ, live=bool(getattr(args, "live", False)))
    if mode != EVAL_MODE or not getattr(args, "live", False):
        raise CliRefusal(f"advisor eval runs only with {config.ENV_MODE}={EVAL_MODE} and --live.")
    return mode


def open_batch_ledger(mode: str, runs: int, *, new_ledger: bool, need: tuple[str, ...],
                      per_run_usd: float | None = None, environ: Mapping[str, str] | None = None):
    """The live ledger, refused unless the rest of the build budget covers every run at its worst case.

    A run's worst case is its per run cap, or `per_run_usd` for a batch
    whose runs make one small call each (the plate runs).
    """
    ledger = cli.open_live_ledger(mode, new_ledger=new_ledger, need=need, need_usd=per_run_usd,
                                  **({} if environ is None else {"environ": environ}))
    remaining = config.BUILD_CAP_USD - ledger.build_total()
    each = config.RUN_CAP_USD[mode] if per_run_usd is None else per_run_usd
    need = runs * each
    if remaining + 1e-9 < need:
        what = f"the {mode} run cap" if per_run_usd is None else "its worst case"
        raise CliRefusal(
            f"remaining build budget {remaining:.4f} USD is below {runs} runs at {what} ({need:.4f} USD)."
        )
    credits_left = config.BUILD_CREDIT_CAP - ledger.build_credits()
    if credits_left < runs * config.RUN_CREDIT_CAP:
        raise CliRefusal(
            f"remaining build Tavily credits {credits_left} are below {runs} runs at "
            f"{config.RUN_CREDIT_CAP} credits each."
        )
    return ledger


# build_id lives in agent.build_info so run records carry it too (decision 35).
build_id = _build_id


def load_records(suite: str, eval_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every run record of one suite in config.EVAL_DIR, oldest first."""
    folder = Path(eval_dir or config.EVAL_DIR)
    records = []
    for path in sorted(folder.glob(f"{suite}-*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("kind") == "run" and record.get("suite") == suite:
            records.append(record)
    return sorted(records, key=lambda r: str(r.get("started_at") or ""))


@dataclass
class Rerun:
    """What decision 35 makes of this batch: the fix its records carry, and what it would supersede."""

    fix: str | None = None
    replaces: bool = False  # a changed build: the reported result becomes this build's alone
    superseded: list[dict[str, Any]] = field(default_factory=list)  # evaluators.build_summaries rows

    def lines(self, suite: str) -> list[str]:
        """Preflight lines, so the typed proceed knows what a rerun replaces (decision 35)."""
        if not self.replaces:
            if self.fix:
                return [f"decision 35: this build already ran {suite} after the logged fix {self.fix!r}; "
                        "these runs are pooled with its earlier runs and carry the same fix"]
            return []
        out = [f"decision 35: this rerun is on a changed build and REPLACES the reported {suite} result; "
               f"logged fix: {self.fix!r}",
               "  superseded (kept in the ledger and the decision log only):"]
        out += [f"    {evaluators.build_summary_text(b)}" for b in self.superseded]
        if len(self.superseded) > 1:
            out.append(f"  note: PLAN section 9 budgets one approved rerun; this would be rerun "
                       f"{len(self.superseded)}")
        return out


def check_rerun(suite: str, build: str, fix: str | None) -> Rerun:
    """Decision 35: a rerun on a changed build needs a logged fix; a fix needs a changed build.

    A pooled rerun of a build that was itself a fix carries that build's
    logged fix into its records, so the fixed build's runs stay one result.
    """
    records = load_records(suite)
    prior = {str(r.get("build_id")) for r in records}
    if not prior:
        return Rerun(fix=fix)
    if build not in prior and not fix:
        raise CliRefusal(
            f"earlier {suite} runs came from another build. A rerun needs a logged code fix that names the "
            "defect (decision 35): log it, then pass --fix \"<the defect>\"."
        )
    if build in prior and fix:
        raise CliRefusal(
            f"the build has not changed since earlier {suite} runs, so there is no fix to report; "
            "rerun without --fix and the runs are pooled (decision 35)."
        )
    if build in prior:
        logged = next((str(r.get("fix")) for r in records
                       if str(r.get("build_id")) == build and str(r.get("fix") or "").strip()), None)
        return Rerun(fix=logged)
    return Rerun(fix=fix, replaces=True, superseded=evaluators.build_summaries(records))


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _usd(amount: float) -> str:
    return f"{amount:.4f} USD"


def preflight_lines(suite: str, specs: Sequence[RunSpec], ledger, *, extra: Sequence[str] = (),
                    alt_routes: Mapping[str, str] | None = None,
                    estimator: Callable[[RunSpec], RunEstimate] | None = None,
                    worst_text: str | None = None) -> list[str]:
    """What the batch will do and may cost, from config values (PRD Part A rule 1)."""
    estimator = estimator or estimate
    ests = [estimator(s) for s in specs]
    runs = len(specs)
    cap = config.RUN_CAP_USD[EVAL_MODE]
    spent = ledger.build_total()
    credits_used = ledger.build_credits()
    models = ", ".join(f"{node} {model}" for node, model in config.MODEL_FOR[EVAL_MODE].items())
    lines = [
        f"advisor eval {suite}: preflight (PLAN section 9)",
        f"mode: {EVAL_MODE}; models: {models}",
        f"planned runs: {runs}",
    ]
    for i, (spec, est) in enumerate(zip(specs, ests, strict=True), start=1):
        source = f"photo {spec.photo.name}" if spec.photo is not None else "typed identity"
        route = "read_plate only, then the pause" if spec.stop_at_pause else f"expected route {spec.expected_route}"
        lines.append(
            f"  {i}. {spec.label} ({spec.case}, {source}, \"{spec.symptom}\"), {route}: "
            f"typical {_usd(est.typical_usd)}, {est.typical_model_calls} model calls, {est.typical_credits} searches"
        )
    lines += [
        f"planned paid calls: typical {sum(e.typical_model_calls for e in ests)} Anthropic calls and "
        f"{sum(e.typical_credits for e in ests)} Tavily searches; at most "
        f"{sum(e.max_model_calls for e in ests)} Anthropic calls",
        f"estimated cost: typical {_usd(sum(e.typical_usd for e in ests))}; worst case "
        f"{_usd(sum(e.worst_usd for e in ests))} "
        f"({worst_text or f'{runs} runs at the {_usd(cap)} per run cap'})",
        f"per run cap: {_usd(cap)} and {config.RUN_CREDIT_CAP} Tavily credits",
        f"build spend so far: {_usd(spent)} of {_usd(config.BUILD_CAP_USD)}; remaining "
        f"{_usd(config.BUILD_CAP_USD - spent)}",
        f"Tavily credits planned: typical {sum(e.typical_credits for e in ests)}, at most "
        f"{sum(e.first_pass_credits for e in ests)} on first passes, ceiling "
        f"{sum(e.credit_ceiling for e in ests)} with retries ({runs} runs at {max(e.credit_ceiling for e in ests)})",
    ]
    if alt_routes:
        alt = sum(estimate(s, route=alt_routes.get(s.expected_route)).first_pass_credits
                  if s.expected_route in alt_routes else e.first_pass_credits for s, e in zip(specs, ests, strict=True))
        moved = ", ".join(f"{a} to {b}" for a, b in alt_routes.items())
        lines.append(f"  at most {alt} on first passes if a run falls from its expected route ({moved})")
    lines += [
        f"build Tavily credits: {credits_used} of {config.BUILD_CREDIT_CAP} used; remaining "
        f"{config.BUILD_CREDIT_CAP - credits_used}",
        f"Tavily pay as you go: {'on' if config.TAVILY_PAYG_ENABLED else 'off'} "
        f"(each credit priced at {_usd(config.tavily_credit_usd())})",
        *extra,
        f"Type {PROCEED} and press return to spend; anything else stops with nothing spent.",
    ]
    return lines


def read_confirmation(stdin: IO[str]) -> bool:
    """One line from stdin; True only for exactly "proceed" (the live path's own rule)."""
    try:
        line = stdin.readline()
    except (OSError, ValueError):
        return False
    return confirmed(line)


# ---------------------------------------------------------------------------
# Running one spec through the normal graph
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def scripted_answer(spec: RunSpec, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The owner's answer at the pause: the proposal, with the spec's identity fields over it, and the code."""
    proposed = dict((payload or {}).get("identity") or {})
    identity = {f: proposed.get(f) for f in ("manufacturer", "model", "serial", "manufacture_date")}
    for name, value in (spec.answer_identity or spec.identity or {}).items():
        identity[name] = value
    return {"identity": identity, "observed_code": spec.answer_code}


def reached_maker_docs(sources: Sequence[Mapping[str, Any]], domains: Sequence[str]) -> bool:
    hosts = {str(s.get("host") or "").lower().rstrip(".") for s in sources}
    return any(h == d or h.endswith("." + d) for h in hosts for d in domains)


def run_record(spec: RunSpec, *, run_id: str, thread_id: str, build: str, fix: str | None,
               started_at: str, wall_s: float, outcome: Any, pauses: int, answers: list[dict[str, Any]],
               ledger, error: str | None = None, raw_extraction: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The eval run record: status, origin, counts from the trail, ledger spend and credits, latency,
    and the spec's v1 case checks (PLAN 6.5)."""
    state = dict(getattr(outcome, "state", None) or {})
    trail = list(state.get("search_trail") or [])
    brief = state.get("brief") or {}
    nra = brief.get("no_reliable_answer") or {}
    status = "error" if error else ("paused" if getattr(outcome, "paused", False) else state.get("status"))
    record = {
        "kind": "run",
        "suite": spec.suite,
        "case": spec.case,
        "label": spec.label,
        "role": spec.role,
        "model_key": spec.model_key,
        "note": spec.note,
        "expected_route": spec.expected_route,
        "run_id": run_id,
        "thread_id": thread_id,
        "mode": EVAL_MODE,
        "build_id": build,
        "fix": fix,
        "started_at": started_at,
        "finished_at": _now(),
        "input": {"symptom": spec.symptom, "identity": dict(spec.identity) if spec.identity else None,
                  "photo": str(spec.photo) if spec.photo else None},
        "pauses": pauses,
        "answers": answers,
        "status": status,
        "refusal_origin": state.get("refusal_origin"),
        "stop_reason": state.get("stop_reason"),
        "budget_stop_from_unaffordable_retry": (
            state.get("status") == "budget_stopped" and state.get("stop_reason") == evaluators.RETRY_NOT_AFFORDABLE
        ),
        "route": list(state.get("route") or []),
        "route_reason": state.get("route_reason"),
        "research_limits": state.get("research_limits"),
        "searches": evaluators.count_searches(trail),
        "fetches": evaluators.count_fetches(trail),
        "not_reached": evaluators.not_reached_calls(trail),
        "search_trail": trail,
        "searched": list(nra.get("searched") or []),
        "candidates": len(brief.get("candidates") or []),
        "source_hosts": sorted({str(s.get("host")) for s in state.get("sources") or [] if s.get("host")}),
        "credits": ledger.run_credits(run_id),
        "cost_usd": ledger.run_total(run_id),
        "run_cap_usd": config.RUN_CAP_USD[EVAL_MODE],
        "latency_s": wall_s,
        "node_latency_s": dict(state.get("latency") or {}),
        "html_path": state.get("html_path"),
        "run_record": (state.get("persist_report") or {}).get("run_record"),
        "error": error,
    }
    if spec.suite == SUITE:
        record["reached_maker_docs"] = reached_maker_docs(state.get("sources") or [], config.SC3B_MAKER_DOMAINS)
    if spec.photo is not None:
        record["extraction"] = {"raw": dict(raw_extraction) if raw_extraction is not None else None,
                                "final": state.get("extraction")}
    if error is None and outcome is not None and (spec.stop_at_pause or not getattr(outcome, "paused", False)):
        record["v1_checks"] = evaluators.v1_case_checks(spec.case, state, raw_extraction)
    record["sc11"] = evaluators.sc11(record)
    return record


def not_started_record(spec: RunSpec, *, build: str, fix: str | None, reason: str) -> dict[str, Any]:
    """A planned run the batch never started, so it cannot go missing from the result (finding E1)."""
    import uuid

    now = _now()
    return {
        "kind": "run", "suite": spec.suite, "case": spec.case, "label": spec.label, "role": spec.role,
        "model_key": spec.model_key, "note": spec.note, "expected_route": spec.expected_route,
        "run_id": f"not-started-{uuid.uuid4().hex[:12]}", "thread_id": None, "mode": EVAL_MODE, "build_id": build,
        "fix": fix, "started_at": now, "finished_at": now, "status": evaluators.NOT_STARTED,
        "refusal_origin": None, "stop_reason": None, "route": [], "search_trail": [], "searches": 0,
        "fetches": 0, "credits": 0, "cost_usd": 0.0, "run_cap_usd": config.RUN_CAP_USD[EVAL_MODE],
        "latency_s": 0.0, "error": reason, "sc11": "pass",
    }


def run_one(spec: RunSpec, graph: Any, session: Any, *, build: str, fix: str | None) -> dict[str, Any]:
    """Ask, answer each pause with the scripted answer, finish as a live run, and return the run record.

    `session` is the live path's cli.LiveSession: its capture records every
    model reply for the recording candidate, and its finish() writes the SC11
    post condition into the persisted run record, scrubs key values from the
    run's files and writes the cassette candidate, as for `advisor ask --live`.
    """
    from langgraph.types import Command

    from agent.graph import run_until_pause_or_end
    from agent.live.env import redact, redact_value
    from agent.nodes.intake import intake_input

    thread_id = cli.new_thread_id()
    run_id = thread_id  # as `advisor ask` does
    ctx = cli.run_context(run_id, EVAL_MODE, None, secrets=session.secrets)
    data = intake_input(symptom=spec.symptom, photo_path=str(spec.photo) if spec.photo else None,
                        identity=dict(spec.identity) if spec.identity else None)
    data["html_path"] = str(cli.default_html_path("brief", thread_id))
    started_at = _now()
    started = time.monotonic()
    outcome = None
    pauses = 0
    answers: list[dict[str, Any]] = []
    error = None
    try:
        with cli._tracing(), cli._live_capture(session, run_id, "ask"):
            outcome = run_until_pause_or_end(graph, data, ctx, thread_id)
        while outcome.paused and not spec.stop_at_pause and pauses < config.EVAL_MAX_PAUSES:
            pauses += 1
            answer = scripted_answer(spec, outcome.interrupt)
            answers.append(answer)
            with cli._tracing(), cli._live_capture(session, run_id, "resume"):
                outcome = run_until_pause_or_end(graph, Command(resume=answer), ctx, thread_id)
    except Exception as exc:  # recorded, then the batch stops (PRD Part A rule 7)
        error = redact(f"{type(exc).__name__}: {exc}", session.secrets)
    finally:
        # Whatever happened, no key value stays in the files this run wrote.
        session.scrub(ctx)
    wall = time.monotonic() - started
    if outcome is not None and error is None:
        session.finish(outcome, ctx)
    from agent.live.recorder import raw_reply

    raw = raw_reply(run_id, "read_plate") if spec.photo is not None else None
    record = run_record(spec, run_id=run_id, thread_id=thread_id, build=build, fix=fix,
                        started_at=started_at, wall_s=wall, outcome=outcome, pauses=pauses,
                        answers=answers, ledger=session.ledger, error=error, raw_extraction=raw)
    return redact_value(record, session.secrets)


def write_json(path: Path, data: Mapping[str, Any], secrets: Sequence[str]) -> Path:
    """Write a record atomically, with every key value redacted."""
    from agent.live.env import redact_value

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(redact_value(dict(data), secrets), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# The batch
# ---------------------------------------------------------------------------


@dataclass
class Batch:
    """What differs between the two eval commands."""

    suite: str
    specs: list[RunSpec]
    score: Callable[[list[dict[str, Any]]], dict[str, Any]]
    format: Callable[[Mapping[str, Any]], list[str]]
    preflight_extra: Callable[[], list[str]] = field(default=lambda: [])
    alt_routes: Mapping[str, str] | None = None
    estimator: Callable[[RunSpec], RunEstimate] | None = None  # default: estimate (a whole run)
    per_run_usd: float | None = None  # a run's worst case when it is not the run cap
    worst_text: str | None = None


def run_batch(batch: Batch, args: argparse.Namespace, environ: MutableMapping[str, str], *,
              stdin: IO[str] | None = None) -> int:
    """Refuse, preflight, confirm, then run every spec through the normal graph as a live run.

    Keys, the Haiku date check, redacted output and the per run finish come
    from the live path's cli.LiveSession, so `advisor eval` and `advisor ask
    --live` load keys and record runs the same way.
    """
    mode = check_mode(args, environ)
    # A batch of plate runs makes read_plate calls only, so it needs no Tavily key.
    need = (cli.ANTHROPIC_KEY_NAME,) if all(s.stop_at_pause for s in batch.specs) else tuple(config.LIVE_KEY_NAMES)
    ledger = open_batch_ledger(mode, len(batch.specs), new_ledger=bool(getattr(args, "new_ledger", False)),
                               need=need, per_run_usd=batch.per_run_usd, environ=environ)
    session = cli.LiveSession(f"eval {batch.suite}", mode, ledger, need=need, environ=environ)
    build = build_id()
    rerun = check_rerun(batch.suite, build, (getattr(args, "fix", None) or "").strip() or None)
    fix = rerun.fix

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, Any]] = []
    code = cli.EXIT_OK
    with session.active():
        keys = ", ".join(f"{name} from the {source}" for name, source in session.sources.items())
        lines = preflight_lines(batch.suite, batch.specs, ledger, extra=[*batch.preflight_extra(), *rerun.lines(batch.suite)],
                                alt_routes=batch.alt_routes, estimator=batch.estimator, worst_text=batch.worst_text)
        lines.insert(2, f"keys: {keys} (values are never shown)")
        for line in lines:
            print(line)
        sys.stdout.flush()
        if not read_confirmation(stdin if stdin is not None else sys.stdin):
            raise CliRefusal(f"the typed confirmation was not exactly {PROCEED!r}; nothing was spent.")

        from agent.graph import build_graph, open_checkpointer

        graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
        for i, spec in enumerate(batch.specs, start=1):
            try:
                ledger.assert_can_start_live_run(mode, need_usd=batch.per_run_usd,
                                                 need_credits=0 if batch.per_run_usd is not None else None)
            except Exception as exc:  # BudgetExceeded: the build cannot cover another run
                print(f"advisor eval {batch.suite}: stopped before run {i}: {exc}")
                for rest in batch.specs[i - 1:]:
                    missing = not_started_record(rest, build=build, fix=fix, reason=f"not started: {exc}")
                    write_json(config.EVAL_DIR / f"{batch.suite}-{missing['run_id']}.json", missing, session.secrets)
                    records.append(missing)
                code = cli.EXIT_REFUSED
                break
            record = run_one(spec, graph, session, build=build, fix=fix)
            path = write_json(config.EVAL_DIR / f"{batch.suite}-{record['run_id']}.json", record, session.secrets)
            records.append(record)
            print(
                f"run {i} of {len(batch.specs)}: {spec.label}: {record['run_id']} status {record['status']}, "
                f"origin {record['refusal_origin']}, route {'+'.join(record['route']) or 'none'}, "
                f"{record['searches']} searches, {record['fetches']} fetches, {record['credits']} credits, "
                f"{_usd(record['cost_usd'])}, SC11 {record['sc11']}, {record['latency_s']:.1f} s; record {path}"
            )
            if record["error"]:
                print(f"advisor eval {batch.suite}: run {i} failed ({record['error']}); the batch stopped. "
                      "Stop and report this (PRD Part A rule 7); every call made so far is in the ledger.",
                      file=sys.stderr)
                code = cli.EXIT_LIVE_ERROR
                break
        score = batch.score(load_records(batch.suite))
        write_json(config.EVAL_DIR / f"summary-{batch.suite}-{stamp}.json",
                   {"kind": "summary", "suite": batch.suite, "this_batch": [r["run_id"] for r in records],
                    "rerun": {"fix": rerun.fix, "replaces": rerun.replaces, "superseded": rerun.superseded},
                    "score": score}, session.secrets)
        for line in batch.format(score):
            print(line)
    return code


def cmd_eval_sc3b(args: argparse.Namespace, environ: MutableMapping[str, str], *,
                  stdin: IO[str] | None = None) -> int:
    """`advisor eval sc3b --live`: SC3B_RUNS runs of B4, one preflight, one record each."""
    batch = Batch(suite=SUITE, specs=sc3b_specs(), score=evaluators.score_sc3b, format=evaluators.format_sc3b)
    return run_batch(batch, args, environ, stdin=stdin)
