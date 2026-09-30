"""`advisor eval sc12a --live`: Jev's answer for every SC12a item and wording not yet recorded.

Without --heldout it asks, on the tune half only, every wording in
agent/safety_eval/wordings.json (or the one --wording names; at most
config.SC12A_MAX_WORDINGS). With --heldout it asks the held out half, and
only after the lock (Gate 2), with the locked wording alone.

Each call goes through agent.safety_judge.build_live_judge, which reserves
it in the ledger with provider "typesafe" before it is made (one reservation
per call) and charges it after. The standard preflight prints the planned
calls and their cost and waits for a typed "proceed"; no flag skips it. The
batch also refuses to start when this work's spend so far plus the batch's
worst case would pass config.SC12_STOP_USD.

Replies go to data/eval/sc12a/replies/<question hash>.jsonl: item ID, step
hash, question hash, model string, probability, request ID, token counts and
any failure kind. No step or page text.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import IO, Any

from agent import cli, config
from agent.cli import CliRefusal
from agent.live.eval_sc3b import check_mode, read_confirmation
from agent.live.preflight import PROCEED, Plan, preflight_lines
from agent.safety_eval import harness, pools, sc12b
from agent.safety_eval.harness import EvalRefusal

KEYS = (config.TYPESAFE_KEY_NAME,)


def _call_usd(calls: int) -> float:
    from agent.ledger import price_usage

    return price_usage(config.PRICES_PER_MTOK[config.JEV_MODEL],
                       {"input_tokens": calls * config.JEV_EST_INPUT_TOKENS, "output_tokens": 0})


def planned(args: argparse.Namespace) -> list[tuple[dict[str, Any], str, list[dict[str, Any]]]]:
    """(wording, question hash, items to ask) for this command; refusals come before any key is read."""
    doc = harness.load_items()
    labels = harness.load_labels()
    harness.require_scorable(doc, labels)
    if args.heldout:
        if args.wording is not None:
            raise EvalRefusal("--heldout asks the locked wording only; leave out --wording.")
        if not harness.lock_path().is_file():
            raise EvalRefusal("the held out half is asked only after the lock (advisor safety-set lock).")
        lock = harness.read_json(harness.lock_path())
        wording = {"n": None, "instructions": lock["wording"], "criteria": lock["criteria"]}
        qhash = lock["question_hash"]
        return [(wording, qhash, harness.pending(doc["items"], qhash, pools.HELDOUT))]
    chosen = [harness.pick_wording(args.wording)] if args.wording is not None else [
        harness.pick_wording(w["n"]) for w in harness.wordings()]
    return [(w, harness.question_hash_of(w), harness.pending(doc["items"], harness.question_hash_of(w), pools.TUNE))
            for w in chosen]


def cmd_eval_sc12a(args: argparse.Namespace, environ: MutableMapping[str, str], *,
                        stdin: IO[str] | None = None) -> int:
    mode = check_mode(args, environ)
    try:
        batches = planned(args)
    except EvalRefusal as exc:
        raise CliRefusal(str(exc)) from None
    calls = sum(len(items) for _, _, items in batches)
    if calls == 0:
        print("advisor eval sc12a --live: every item already has a recorded reply; nothing to ask.")
        return cli.EXIT_OK
    typical = _call_usd(calls)
    worst = _call_usd(calls * (1 + config.JEV_RETRY_MAX))
    ledger = cli.open_live_ledger(mode, new_ledger=bool(args.new_ledger), need=KEYS, need_usd=worst,
                                  environ=environ)
    spent = sc12b.work_spend(ledger)
    if spent + worst > config.SC12_STOP_USD:
        raise CliRefusal(f"this work has spent {spent:.4f} USD; the batch's worst case {worst:.4f} USD would "
                         f"pass the {config.SC12_STOP_USD:.2f} USD stop line. Stop and report.")
    session = cli.LiveSession("eval sc12a", mode, ledger, need=KEYS, environ=environ)
    run_id = f"sc12a-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    notes = [f"{len(items)} calls for wording {w['n'] if w['n'] is not None else 'locked'} (question {q})"
             for w, q, items in batches]
    notes += [f"model {config.JEV_MODEL}; one reservation per call, charged at "
              f"{config.JEV_EST_INPUT_TOKENS} input tokens each until the reply's usage is known; output is free",
              f"this work's spend so far {spent:.4f} USD against the {config.SC12_STOP_USD:.2f} USD stop line",
              f"ledger run id {run_id}"]
    plan = Plan(label=f"{calls} Jev calls ({'held out' if args.heldout else 'tune'} half)", typical_calls=calls,
                max_calls=calls * (1 + config.JEV_RETRY_MAX), typical_usd=typical, worst_usd=worst,
                typical_credits=0, max_credits=0, notes=notes)
    with session.active():
        for line in preflight_lines("eval sc12a", mode, [plan], ledger):
            print(line)
        sys.stdout.flush()
        if not read_confirmation(stdin if stdin is not None else sys.stdin):
            raise CliRefusal(f"the typed confirmation was not exactly {PROCEED!r}; nothing was spent.")
        return ask_all(batches, ledger=ledger, mode=mode, run_id=run_id, secrets=session.secrets)


def ask_all(batches, *, ledger, mode: str, run_id: str, secrets) -> int:
    """Ask every pending item, one reservation per call, appending each reply or failure as it comes."""
    from agent.safety_judge import SafetyCheckError, build_live_judge

    code = cli.EXIT_OK
    for wording, qhash, items in batches:
        capture: list[dict[str, Any]] = []
        judge = build_live_judge(ledger=ledger, run_id=run_id, mode=mode, instructions=wording["instructions"],
                                 criteria=wording["criteria"], capture=capture, secrets=secrets)
        answered = failed = 0
        for item in items:
            state = {"appliance": item["appliance"], "step": item["step"], "detail": item["detail"]}
            entry: dict[str, Any] = {"item_id": item["id"], "step_sha256": item["id"], "question_hash": qhash,
                                     "wording": wording["n"], "half": item["half"], "run_id": run_id,
                                     "recorded_at": datetime.now(UTC).isoformat(timespec="seconds")}
            before = len(capture)
            try:
                reply = judge.ask(item["id"], state)
            except SafetyCheckError as exc:
                seen = capture[before] if len(capture) > before else {}
                entry.update({"model": seen.get("model"), "noul": None, "request_id": seen.get("request_id"),
                              "input_tokens": seen.get("input_tokens"), "output_tokens": seen.get("output_tokens"),
                              "error": exc.kind})
                harness.append_reply(qhash, entry)
                failed += 1
                if exc.kind == "reservation_refused":
                    print(f"advisor eval sc12a: the ledger refused a reservation ({exc.detail}); the batch "
                          "stopped. Every call so far is in the ledger.", file=sys.stderr)
                    return cli.EXIT_LIVE_ERROR
                continue
            entry.update({"model": reply.model, "noul": reply.noul, "request_id": reply.request_id,
                          "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens, "error": None})
            harness.append_reply(qhash, entry)
            answered += 1
        print(f"wording {wording['n'] if wording['n'] is not None else 'locked'} (question {qhash}): "
              f"{answered} answered, {failed} failed; replies in {harness.reply_path(qhash)}")
        if failed:
            code = cli.EXIT_LIVE_ERROR
    print(f"ledger: run {run_id} spent {ledger.run_total(run_id):.6f} USD")
    return code

