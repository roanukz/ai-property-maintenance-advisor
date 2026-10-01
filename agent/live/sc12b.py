"""`advisor eval sc12b --live`: 10 live first lookups on the new build (SC12b, Gate 3).

Cheap mode, after the lock (Gate 2) and after arm A (the revised writer
instruction) is in. config.SC12B_RUNS_PER_INPUT runs with the input of each
run in config.SC12B_INPUT_RUNS, read from its live recording
(data/recordings/live_t_<id>.json: input and resume answer; the photo is the
demo asset whose sha256 is the recorded plate hash). Each run is a first
lookup: that model's nodes and edges leave data/graph.json before the run,
and the file is restored afterward, checked by hash.

Before any ledger or key is opened, the command refuses unless the lock
still matches config, the held out half was scored under that lock, and
config ships what the choice rule picked (SAFETY_LAYERS, JEV_SAFETY_ENABLED
and JEV_THRESHOLD), so the paid runs measure the chosen build.

Gate 3, before any call: the preflight prints the planned runs, dollars and
Tavily credits (planned on first passes, and the ceiling at the per run cap)
against what the ledger has left, and waits for "proceed". When the credits
left cannot cover the planned first passes it prints the plan and then stops
and asks (decision 1); when they cover the plan but not the ceiling it says
so, and every run is still checked against the build caps before it starts.
It also refuses once this work's spend has passed the config.SC12_STOP_USD
stop line. After every run, the last included, it checks the cumulative
spend again: past the line it prints "stop and report", ends with a refused
exit code and marks the batch summary.

Each run goes through eval_sc3b's harness (`run_one`: the normal graph, the
ledger and the live session). Its eval record, in config.EVAL_DIR, adds the
final brief's steps and the per step safety provenance from the run record,
for the three readers and `advisor eval sc12b --score`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from agent import cli, config
from agent.cli import CliRefusal
from agent.live.eval_sc3b import (
    RunSpec,
    build_id,
    check_mode,
    check_rerun,
    estimate,
    model_key,
    not_started_record,
    open_batch_ledger,
    preflight_lines,
    read_confirmation,
    run_one,
    write_json,
)
from agent.live.preflight import PROCEED
from agent.safety_eval import harness, stats
from agent.safety_eval import sc12b as offline

SUITE = offline.SUITE


def sc12b_specs() -> list[tuple[RunSpec, str]]:
    """(run spec, graph scope) for every planned run, inputs in config order."""
    out = []
    for source in config.SC12B_INPUT_RUNS:
        recording = offline.recording_path(source)
        if not recording.is_file():
            # A superseded build's runs are filed under data/superseded/, recordings included.
            raise CliRefusal(f"the recording of {source}, SC12b's input, is not at {recording} (a superseded "
                             "build's recordings are filed under data/superseded/). Nothing was spent.")
        given = offline.read_input(source)
        photo = offline.resolve_photo(given["plate_sha256"])
        identity = given["answer_identity"] or given["identity"]
        if not identity or not identity.get("model"):
            raise CliRefusal(f"the recording of {source} names no model to take out of the graph.")
        scope = offline.model_scope(identity)
        for k in range(1, config.SC12B_RUNS_PER_INPUT + 1):
            out.append((RunSpec(
                suite=SUITE, case="SC12b", label=f"input of {source}, run {k}", symptom=given["symptom"],
                identity=given["identity"], photo=photo, answer_identity=given["answer_identity"],
                answer_code=given["answer_code"], expected_route="research", role="first",
                model_key=model_key(identity["model"]), note=f"input of {source}"), scope))
    return out


def refuse_before_keys() -> None:
    """The lock, arm A and the configuration the choice rule picked come before SC12b."""
    if not harness.lock_path().is_file():
        raise CliRefusal("SC12b runs after the lock (Gate 2): run advisor safety-set lock first.")
    from agent.prompts import SYNTHESIS_SYSTEM

    if not offline.arm_a_in(SYNTHESIS_SYSTEM):
        raise CliRefusal("SC12b runs after arm A: the revised writer instruction is not in SYNTHESIS_SYSTEM.")
    problems = shipped_config_problems()
    if problems:
        raise CliRefusal("SC12b measures the configuration the choice rule picked, and this build does not "
                         "ship it: " + "; ".join(problems))


def shipped_config_problems() -> list[str]:
    """Each way the current config differs from what the lock and the held out choice require."""
    try:
        lock = harness.read_json(harness.lock_path())
        bad = harness.lock_mismatches(lock, harness.current_lock_fields(harness.load_items(), harness.load_labels()))
    except harness.EvalRefusal as exc:
        return [str(exc)]
    if bad:
        return [f"the lock does not match the current config and prompt ({', '.join(bad)})"]
    history = harness.read_json(harness.heldout_path()) if harness.heldout_path().is_file() else {"attempts": []}
    pinned = harness.lock_sha256(lock)
    attempts = [a for a in history.get("attempts") or [] if a.get("lock_sha256") == pinned]
    if not attempts:
        return ["the held out half has not been scored under the current lock (advisor eval sc12a --heldout)"]
    picked = ((attempts[-1].get("result") or {}).get("choice") or {}).get("picked")
    layers = stats.LAYERS_FOR[picked] if picked is not None else ()
    jev = "jev" in layers
    problems = []
    if tuple(config.SAFETY_LAYERS) != layers:
        problems.append(f"config.SAFETY_LAYERS is {tuple(config.SAFETY_LAYERS)!r}; the choice rule picked "
                        f"{picked or 'no candidate'}, so it must be {layers!r}")
    if config.JEV_SAFETY_ENABLED is not jev:
        problems.append(f"config.JEV_SAFETY_ENABLED is {config.JEV_SAFETY_ENABLED}; it must be {jev}")
    if jev:
        threshold = ((lock.get("thresholds") or {}).get(picked) or {}).get("threshold")
        if config.JEV_THRESHOLD != threshold:
            problems.append(f"config.JEV_THRESHOLD is {config.JEV_THRESHOLD!r}; the lock's {picked} threshold "
                            f"is {threshold!r}")
    return problems


def with_steps(record: dict[str, Any]) -> dict[str, Any]:
    """The eval record plus the final steps and per step provenance from the persisted run record."""
    path = record.get("run_record")
    run = {}
    if path and Path(path).is_file():
        run = json.loads(Path(path).read_text(encoding="utf-8"))
    safety = run.get("safety") or {}
    out = dict(record)
    out["steps"] = [{"step": s.get("step"), "detail": s.get("detail"), "safety_flag": s.get("safety_flag") is True}
                    for s in ((run.get("brief") or {}).get("try_first") or [])]
    out["safety_provenance"] = [
        {"step_sha256": s.get("step_sha256"), "writer_flag": s.get("writer_flag"), "word_rule": s.get("word_rule"),
         "jev_noul": s.get("jev_noul"), "jev_error": s.get("jev_error"), "final_flag": s.get("final_flag"),
         "raised_by": s.get("raised_by")} for s in safety.get("steps") or []]
    out["safety_status"] = {k: safety.get(k) for k in ("status", "reason", "question_hash", "model")}
    return out


def credit_plan(specs: list[tuple[RunSpec, str]], ledger: Any) -> dict[str, int]:
    """Tavily credits: planned on first passes, the ceiling at the per run cap, and what the build has left."""
    return {"planned": sum(estimate(spec).first_pass_credits for spec, _ in specs),
            "ceiling": sum(estimate(spec).credit_ceiling for spec, _ in specs),
            "left": config.BUILD_CREDIT_CAP - ledger.build_credits()}


def gate3_extra(specs: list[tuple[RunSpec, str]], spent: float, credits: Mapping[str, int]) -> list[str]:
    """Gate 3's lines; the Jev calls and their cost are in the preflight's own lines."""
    runs = len(specs)
    scopes = sorted({scope for _, scope in specs})
    lines = [
        f"Gate 3: {runs} first lookups; Tavily credits planned on first passes {credits['planned']}, ceiling "
        f"{credits['ceiling']} at the per run cap; the build has {credits['left']} left",
    ]
    if credits["left"] < credits["planned"]:
        lines.append(f"the {credits['left']} credits left cannot cover the planned {credits['planned']}: stop and "
                     "ask Roanuk (decision 1: BUILD_CREDIT_CAP stays unless he changes it)")
    elif credits["left"] < credits["ceiling"]:
        lines.append(f"the {credits['left']} credits left cover the planned {credits['planned']} but not the "
                     f"ceiling of {credits['ceiling']}; each run is checked against the build caps before it "
                     "starts, and the batch stops at the first that cannot start")
    lines += [
        f"this work's spend so far: {spent:.4f} USD; the batch stops and reports once it passes "
        f"{config.SC12_STOP_USD:.2f} USD, checked before and after every run",
        f"graph: before each run the model's nodes and edges leave {config.GRAPH_PATH} ({', '.join(scopes)}); "
        "the file is restored afterward and checked by hash",
    ]
    return lines


def cmd_eval_sc12b(args: argparse.Namespace, environ: MutableMapping[str, str], *,
                   stdin: IO[str] | None = None) -> int:
    mode = check_mode(args, environ)
    refuse_before_keys()
    specs = sc12b_specs()
    ledger = open_batch_ledger(mode, len(specs), new_ledger=bool(getattr(args, "new_ledger", False)),
                               need=tuple(config.LIVE_KEY_NAMES), environ=environ, check_credits=False)
    spent = offline.work_spend(ledger)
    if offline.past_stop_line(spent):
        raise CliRefusal(f"this work has spent {spent:.4f} USD, past the {config.SC12_STOP_USD:.2f} USD stop line. "
                         "Stop and report.")
    build = build_id()
    rerun = check_rerun(SUITE, build, (getattr(args, "fix", None) or "").strip() or None)
    credits = credit_plan(specs, ledger)
    lines = preflight_lines(SUITE, [s for s, _ in specs], ledger,
                            extra=[*gate3_extra(specs, spent, credits), *rerun.lines(SUITE)])
    if credits["left"] < credits["planned"]:
        # Gate 3: the plan is printed against what is left, then the command stops and asks.
        for line in lines[:-1]:
            print(line)
        sys.stdout.flush()
        raise CliRefusal(f"Gate 3: the build has {credits['left']} Tavily credits left, below the planned "
                         f"{credits['planned']}; stop and ask Roanuk (decision 1). Nothing was spent.")
    session = cli.LiveSession(f"eval {SUITE}", mode, ledger, need=tuple(config.LIVE_KEY_NAMES), environ=environ)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, Any]] = []
    code = cli.EXIT_OK
    stop_line_passed = False
    with session.active():
        keys = ", ".join(f"{name} from the {source}" for name, source in session.sources.items())
        lines.insert(2, f"keys: {keys} (values are never shown)")
        for line in lines:
            print(line)
        sys.stdout.flush()
        if not read_confirmation(stdin if stdin is not None else sys.stdin):
            raise CliRefusal(f"the typed confirmation was not exactly {PROCEED!r}; nothing was spent.")

        from agent.graph import build_graph, open_checkpointer

        graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
        for i, (spec, scope) in enumerate(specs, start=1):
            stop = None
            try:
                ledger.assert_can_start_live_run(mode)
            except Exception as exc:  # BudgetExceeded
                stop = f"not started: {exc}"
            if stop is None and offline.past_stop_line(offline.work_spend(ledger)):
                stop = f"not started: this work passed the {config.SC12_STOP_USD:.2f} USD stop line"
            if stop is not None:
                print(f"advisor eval {SUITE}: stopped before run {i}: {stop}")
                for rest, _ in specs[i - 1:]:
                    missing = not_started_record(rest, build=build, fix=rerun.fix, reason=stop)
                    write_json(config.EVAL_DIR / f"{SUITE}-{missing['run_id']}.json", missing, session.secrets)
                    records.append(missing)
                code = cli.EXIT_REFUSED
                break
            with offline.first_lookup_graph(config.GRAPH_PATH, scope) as graph_info:
                record = run_one(spec, graph, session, build=build, fix=rerun.fix)
            record = with_steps(record)
            record["graph"] = graph_info
            record["work_spend_usd"] = offline.work_spend(ledger)
            path = write_json(config.EVAL_DIR / f"{SUITE}-{record['run_id']}.json", record, session.secrets)
            records.append(record)
            print(f"run {i} of {len(specs)}: {spec.label}: {record['run_id']} status {record['status']}, "
                  f"route {'+'.join(record['route']) or 'none'}, {len(record['steps'])} steps, "
                  f"{record['credits']} credits, {record['cost_usd']:.4f} USD; this work "
                  f"{record['work_spend_usd']:.4f} USD; graph restored {graph_info.get('restored')}; record {path}")
            if record["error"]:
                print(f"advisor eval {SUITE}: run {i} failed ({record['error']}); the batch stopped. Stop and "
                      "report this; every call made so far is in the ledger.", file=sys.stderr)
                code = cli.EXIT_LIVE_ERROR
                break
            if offline.past_stop_line(record["work_spend_usd"]):
                # The next run's check writes the not_started records; after the last run this is the report.
                stop_line_passed = True
                print(f"advisor eval {SUITE}: this work passed the {config.SC12_STOP_USD:.2f} USD stop line after "
                      f"run {i} ({record['work_spend_usd']:.4f} USD): stop and report.", file=sys.stderr)
                code = cli.EXIT_REFUSED
        write_json(config.EVAL_DIR / f"summary-{SUITE}-{stamp}.json",
                   {"kind": "summary", "suite": SUITE, "this_batch": [r["run_id"] for r in records],
                    "work_spend_usd": offline.work_spend(ledger), "stop_line_usd": config.SC12_STOP_USD,
                    "stop_line_passed": stop_line_passed}, session.secrets)
        print(f"next: advisor safety-set build --sc12b, label the steps with the same three readers, then "
              f"advisor eval {SUITE} --score")
    return code
