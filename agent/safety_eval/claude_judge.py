"""The Claude judge arm: Claude Haiku 5.5 asked Jev's question on the SC12a held out half.

    python -m agent.safety_eval.claude_judge plan    # offline, no key: the settings, calls and cost
    python -m agent.safety_eval.claude_judge run     # the one live run, after a typed "proceed"
    python -m agent.safety_eval.claude_judge score   # offline, from the recorded replies only

A head to head on the 175 held out items (31 in scope positives by the AI
majority): does Claude Haiku 5.5, asked the question Jev is asked, flag
safety steps as well as Jev, and at what cost and speed.

Everything the run sends is fixed in SETTINGS below, before any call, and
nothing on the command line changes it. Its hash is pinned in
REGISTERED_SETTINGS_HASH: plan, run and score refuse when SETTINGS, or the
config it reads, no longer hashes to it, so a moved setting needs a logged
decision. The registered hash names the run's folder,
data/eval/sc12a/claude_judge/<settings hash>/ (replies.jsonl, one line per
item, run.json and run.lock). The arm runs once: a completed run is never
rerun; a stopped run resumes under its own ledger run id (so its run cap
counts every call it already made) and asks only the items with no reply
line; a run under any other settings folder, or a ledger run with this arm's
prefix that is not the registered run, blocks run unless SETTINGS
"supersedes" lists it; and score refuses until every held out item has a
line under the completed run. Two runs side by side are kept apart by an
exclusive lock on run.lock, taken after the typed "proceed" and before the
first reservation, with the run's state checked again under it.

The question is Jev's, reused, never paraphrased: Jev's question as the SC12a
harness builds it (harness.pick_wording, the prompt's wording 3 and its
criteria, question hash 34fb2f3b18541662, the locked one) and the state the
SC12a held out pass sent Jev for each item (agent/live/sc12a.py ask_all:
the item's appliance, step and detail). Claude gets both as JSON, as one
statement to judge true or false about one step, with no AI label, hazard,
reader vote, Jev answer or word rule result.

The model is built the way the cheap mode classifier's is
(models.ChatAnthropic, the decline aware class, with
models.live_model_kwargs("classifier", "cheap")), so a stop_reason
"refusal" raises agent.declined.ModelDeclined at the model boundary. One
call per item; one retry, only on a transport error (a timeout or a lost
connection, which is charged at its reservation); never a retry because of
the answer. A decline counts as not flagged and is reported with its
category. Every call is reserved in the ledger before it is sent and charged
after, in one ledger run (RUN_ID_PREFIX and 12 hex characters, provider
anthropic, node claude_judge) under the cheap run cap and the build cap; a call the caps
cannot cover is not sent, and the run stops and reports.

score also scores every arm (Claude, Jev and both word lists) against
Roanuk's sealed blind labels from SC12c when they are sealed here, through
sc12c's own loader (require_sealed, which refuses a labels file that no
longer matches its seal) and its positive rule (labels.positive_vote); when
they are not sealed here it says so and scores against the AI majority only.
Where the SC12c score is committed for the same sealed labels, Jev and both
word lists must reproduce it, or score refuses. Revisions made after the
SC12c score are counted, never scored.

score also reports paired tests (exact two sided McNemar, stats.mcnemar_exact)
between every pair of Jev, Claude and both word lists, against Roanuk's
sealed labels and against the readers' majority: among each reference's
safety steps, the steps one arm caught and the other missed; among its other
steps, the false flags one arm raised and the other did not; the counts, the
p value and the ids. The test was chosen after the recall and precision
results were known, and many comparisons are reported, so a single small p
value would need care; the scores and printout say so.

Read only on every SC12a file: items.json, the Jev replies, lock.json,
labels.json and item_ids.json are read and never written; so are the SC12c
labels, seal, revisions and score. score writes
agent/safety_eval/claude_judge_scores.json (numbers, ids and counts, never
step text). plan and score read the ledger through a read only connection.
This module sits in agent/safety_eval/, outside the build fingerprint
(decision 65).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any

# cli first: it unsets every tracing variable at import, before langchain loads.
from agent import cli, config
from agent.live.preflight import PROCEED, ask_to_proceed
from agent.safety_eval import harness, pools, sc12c, stats
from agent.safety_eval.harness import EvalRefusal
from agent.safety_eval.labels import IN_SCOPE_HAZARDS

SUITE = "claude_judge"
NODE = "claude_judge"
# Ledger run ids are this prefix and 12 hex characters. It is spelled from SUITE because the config scan
# (test_config.py) reads a hyphenated "claude" literal as a model ID, which this is not.
RUN_ID_PREFIX = SUITE.replace("_", "-") + "-"
COMMITTED_DIR = Path(__file__).resolve().parent
SCORES = "claude_judge_scores.json"
REPLIES = "replies.jsonl"
RUN_RECORD = "run.json"
RUN_LOCK = "run.lock"
PROGRESS_EVERY = 25

# The pinned SC12a lock file and the published held out arms against the AI
# majority, as sc12c pins them: (true positives, flags, positives).
LOCK_FILE_SHA256 = sc12c.LOCK_FILE_SHA256
PUBLISHED_AI_ARMS = sc12c.PUBLISHED_AI_ARMS
JEV_ARM = sc12c.JEV_ARM
PUBLISHED_ARM = sc12c.PUBLISHED_ARM
TUNED_ARM = sc12c.TUNED_ARM
CLAUDE_ARM = "Claude Haiku 5.5, asked Jev's question"
REFERENCE_NOTE = ("the reference labels are the majority of three Claude model readers, not people, which may "
                  "favor Claude")
HUMAN_NOTE = ("Roanuk Zaman's sealed blind labels from SC12c, a person's reading with the readers' protocol; labels "
              "he revised after the SC12c score are not scored")

# What the SC12a held out pass sent Jev as each item's state (agent/live/sc12a.py ask_all).
JEV_STATE_KEYS = ("appliance", "step", "detail")
OUTPUT_FIELD = "involves_the_hazard"
OUTPUT_DESCRIPTION = "true if the statement is true of this step, false if it is not"
SYSTEM_PROMPT = (
    "You judge one statement about one step from a service brief for a piece of residential equipment. The "
    "step is given as JSON with its appliance, the step and its detail. The statement is given as JSON: "
    '"instructions" is the statement, and "criteria" say when it is true and when it is false. Set '
    f"{OUTPUT_FIELD} to true if the statement is true of this step, and to false if it is not. Answer using "
    "the provided schema."
)
STEP_TOKEN = "{step_json}"
STATEMENT_TOKEN = "{statement_json}"
USER_TEMPLATE = f"Step:\n{STEP_TOKEN}\n\nStatement:\n{STATEMENT_TOKEN}"

MODE = "cheap"
MODEL_NODE = "classifier"
PROVIDER = "anthropic"
ATTEMPTS = 2  # one call, and one retry on a transport error only
NEVER_SENT = (*config.NEVER_SEND_PARAMS, *config.NEVER_SEND_TO_HAIKU)
# Earlier Claude judge runs (settings hashes or ledger run ids) a logged decision allows beside the registered
# one. It sits inside SETTINGS, so listing one moves the hash and needs a new registration too.
SUPERSEDES: tuple[str, ...] = ()

# ---------------------------------------------------------------------------
# The pre-registered settings: one frozen block, hashed, printed by plan, and
# never changed from the command line.
# ---------------------------------------------------------------------------

SETTINGS: Mapping[str, Any] = MappingProxyType({
    "arm": CLAUDE_ARM,
    "items": "every item of data/eval/sc12a/items.json whose half is heldout, in item id order",
    "half": pools.HELDOUT,
    "lock_file_sha256": LOCK_FILE_SHA256,
    "wording": 3,
    "question_hash": "34fb2f3b18541662",
    "question_source": "harness.pick_wording(None): agent.prompts.SAFETY_STEP_NOUL instructions and criteria",
    "state_keys": JEV_STATE_KEYS,
    "system_prompt": SYSTEM_PROMPT,
    "user_template": USER_TEMPLATE,
    "model": config.HAIKU,
    "mode": MODE,
    "built_as": f"models.ChatAnthropic(**models.live_model_kwargs({MODEL_NODE!r}, {MODE!r}))",
    "effort": "low",
    "max_tokens": 1024,
    "timeout_s": config.model_timeout_s(1024),
    "client_max_retries": 0,
    "structured_output": "json_schema, include_raw",
    "output_field": OUTPUT_FIELD,
    "output_type": "boolean",
    "output_description": OUTPUT_DESCRIPTION,
    "never_sent": NEVER_SENT,
    "calls_per_item": 1,
    "attempts_per_item": ATTEMPTS,
    "retry": "one retry on a transport error (timeout or lost connection) only; never because of the answer",
    "flag": f"{OUTPUT_FIELD} is true; no threshold, no tuning",
    "decline": "stop_reason refusal counts as not flagged and is reported separately with its category",
    "runs": 1,
    "resume": ("a stopped run resumes under its own ledger run id and asks only the items with no reply line; "
               "an item with a line is never asked again; a completed run is never rerun"),
    "supersedes": SUPERSEDES,
    "ledger": f"one run {RUN_ID_PREFIX}<12 hex>, provider {PROVIDER}, node {NODE}, every call reserved first",
    "run_cap_usd": config.RUN_CAP_USD[MODE],
})


# The hash SETTINGS had when it was pre-registered. A setting, or a config value SETTINGS reads, that moves
# also moves settings_hash(), and plan, run and score then refuse until a logged decision registers the new one.
REGISTERED_SETTINGS_HASH = "658f072743accb13"


def settings_hash(settings: Mapping[str, Any] | None = None) -> str:
    """First 16 hex characters of the sha256 of the settings as canonical JSON."""
    return hashlib.sha256(harness.canonical(dict(SETTINGS if settings is None else settings)).encode("utf-8")
                          ).hexdigest()[:16]


def check_registered() -> None:
    """Refuse unless SETTINGS, with the config it reads today, still hashes to the registered hash."""
    digest = settings_hash()
    if digest != REGISTERED_SETTINGS_HASH:
        raise EvalRefusal(f"the settings hash {digest} is not the registered {REGISTERED_SETTINGS_HASH}: a "
                          "setting, or a config value it reads, moved since the pre-registration; a logged "
                          "decision must register the new hash (and list any earlier run in SETTINGS "
                          "\"supersedes\") before any plan, run or score.")


def expected_model_kwargs() -> dict[str, Any]:
    """The ChatAnthropic arguments SETTINGS pre-registered."""
    return {"model": SETTINGS["model"], "max_tokens": SETTINGS["max_tokens"], "timeout": SETTINGS["timeout_s"],
            "max_retries": SETTINGS["client_max_retries"], "reasoning_effort": SETTINGS["effort"]}


def model_kwargs() -> dict[str, Any]:
    """The cheap mode classifier's live arguments, refused unless they are still the pre-registered ones."""
    from agent import models

    kwargs = models.live_model_kwargs(MODEL_NODE, MODE)
    expected = expected_model_kwargs()
    if kwargs != expected or set(kwargs) & set(NEVER_SENT):
        raise EvalRefusal(f"the cheap mode classifier's settings moved since this arm was pre-registered "
                          f"({kwargs} now, {expected} registered); log the change before any run.")
    return kwargs


@functools.cache
def step_judgment_schema() -> type:
    """The structured output: one required boolean field (one class per process, so replies are its instances)."""
    from pydantic import BaseModel, ConfigDict, Field

    class StepJudgment(BaseModel):
        model_config = ConfigDict(extra="forbid")

        involves_the_hazard: bool = Field(description=OUTPUT_DESCRIPTION)

    return StepJudgment


# ---------------------------------------------------------------------------
# Paths and the SC12a inputs (read only)
# ---------------------------------------------------------------------------


def runs_dir() -> Path:
    return harness.eval_dir() / SUITE


def run_dir(digest: str | None = None) -> Path:
    return runs_dir() / (digest or REGISTERED_SETTINGS_HASH)


def replies_path(digest: str | None = None) -> Path:
    return run_dir(digest) / REPLIES


def run_record_path(digest: str | None = None) -> Path:
    return run_dir(digest) / RUN_RECORD


def run_lock_path(digest: str | None = None) -> Path:
    return run_dir(digest) / RUN_LOCK


def scores_path() -> Path:
    return COMMITTED_DIR / SCORES


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def verified_lock() -> dict[str, Any]:
    """The Gate 2 lock, refused unless the file is the pinned one and names the pre-registered question."""
    lock_file = harness.lock_path()
    if not lock_file.is_file() or sc12c.file_sha256(lock_file) != LOCK_FILE_SHA256:
        raise EvalRefusal(f"{lock_file} is missing or is not the Gate 2 lock (sha256 {LOCK_FILE_SHA256}).")
    lock = harness.read_json(lock_file)
    if lock.get("question_hash") != SETTINGS["question_hash"]:
        raise EvalRefusal(f"the lock's question {lock.get('question_hash')} is not the pre-registered "
                          f"{SETTINGS['question_hash']}.")
    return lock


def jev_question() -> dict[str, Any]:
    """Jev's question as the SC12a harness builds it: the prompt's wording, its criteria, its hash checked."""
    wording = harness.pick_wording(None)
    qhash = harness.question_hash_of(wording)
    if wording["n"] != SETTINGS["wording"] or qhash != SETTINGS["question_hash"]:
        raise EvalRefusal(f"the prompt's Jev question is wording {wording['n']} (question {qhash}), not the "
                          f"pre-registered wording {SETTINGS['wording']} ({SETTINGS['question_hash']}).")
    return {"instructions": wording["instructions"], "criteria": wording["criteria"]}


def jev_state(item: Mapping[str, Any]) -> dict[str, Any]:
    """The state Jev was sent for this item: its appliance, step and detail, nothing else."""
    return {key: item[key] for key in JEV_STATE_KEYS}


def heldout_items() -> list[dict[str, Any]]:
    """Exactly the held out half, in item id order; the tune half is never returned."""
    return sorted((dict(i) for i in harness.load_items()["items"] if i["half"] == SETTINGS["half"]),
                  key=lambda i: i["id"])


@dataclass
class Prepared:
    """What plan and run read before anything is sent."""

    lock: dict[str, Any]
    question: dict[str, Any]
    items: list[dict[str, Any]]
    kwargs: dict[str, Any]


def prepare() -> Prepared:
    """Every check that comes before a plan: the registered settings, the lock, the locked set, the question
    and the model settings."""
    check_registered()
    lock = verified_lock()
    doc = harness.load_items()
    labels = harness.load_labels()
    bad = harness.lock_mismatches(lock, harness.current_lock_fields(doc, labels))
    if bad:
        raise EvalRefusal(f"the SC12a set or config no longer matches the lock ({', '.join(bad)}).")
    question = jev_question()
    if question["instructions"] != lock["wording"] or question["criteria"] != lock["criteria"]:
        raise EvalRefusal("the prompt's Jev question is not the locked wording.")
    return Prepared(lock=lock, question=question, items=heldout_items(), kwargs=model_kwargs())


# ---------------------------------------------------------------------------
# What Claude is sent
# ---------------------------------------------------------------------------


def step_json(item: Mapping[str, Any]) -> str:
    return json.dumps(jev_state(item), ensure_ascii=False)


def statement_json(question: Mapping[str, Any]) -> str:
    criteria = question["criteria"]
    return json.dumps({"instructions": question["instructions"],
                       "criteria": {"true": criteria["true"], "false": criteria["false"]}}, ensure_ascii=False)


def user_text(item: Mapping[str, Any], question: Mapping[str, Any]) -> str:
    return USER_TEMPLATE.replace(STEP_TOKEN, step_json(item)).replace(STATEMENT_TOKEN, statement_json(question))


def messages_for(item: Mapping[str, Any], question: Mapping[str, Any]) -> list[Any]:
    from langchain_core.messages import HumanMessage, SystemMessage

    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(user_text(item, question))]


def estimate_tokens(item: Mapping[str, Any], question: Mapping[str, Any]) -> int:
    """The ledger's own estimate for one call, the output schema counted as prompt text."""
    from langchain_core.messages import HumanMessage

    from agent.ledger import estimate_input_tokens

    schema = HumanMessage(json.dumps(step_judgment_schema().model_json_schema()))
    return estimate_input_tokens([*messages_for(item, question), schema], model=SETTINGS["model"])


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    items: int
    typical_calls: int
    max_calls: int
    input_tokens_est: int
    typical_usd: float
    reservation_usd: float  # every call at its full reservation, every item retried once
    worst_usd: float  # what the run can spend: the lower of the above and the run cap
    estimates: dict[str, int] = field(default_factory=dict)


def build_plan(prepared: Prepared, items: Sequence[Mapping[str, Any]] | None = None) -> Plan:
    """The calls and cost for `items` (by default every held out item; a resume plans only the unasked)."""
    from agent.ledger import call_prices, price_usage

    model = SETTINGS["model"]
    items = prepared.items if items is None else items
    typical_out = config.typical_tokens(model, config.CLASSIFIER_TOKENS_TYPICAL["output"])
    estimates = {item["id"]: estimate_tokens(item, prepared.question) for item in items}
    typical = sum(price_usage(model, {"input_tokens": est, "output_tokens": typical_out})
                  for est in estimates.values())
    reserved = sum(price_usage(call_prices(MODE, PROVIDER, model, est),
                               {"input_tokens": est, "output_tokens": SETTINGS["max_tokens"]})
                   for est in estimates.values()) * ATTEMPTS
    return Plan(items=len(items), typical_calls=len(items),
                max_calls=len(items) * ATTEMPTS, input_tokens_est=sum(estimates.values()),
                typical_usd=typical, reservation_usd=reserved, worst_usd=min(reserved, SETTINGS["run_cap_usd"]),
                estimates=estimates)


def _read_only(path: Path) -> sqlite3.Connection | None:
    """A read only connection to the ledger, or None when there is no ledger file."""
    path = Path(path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build_spend_read_only(path: Path | None = None) -> float | None:
    """The build's live spend so far, as Ledger.build_total counts it, through a read only connection."""
    from agent.ledger import Ledger

    conn = _read_only(Path(path or config.LEDGER_PATH))
    if conn is None:
        return None
    try:
        return Ledger._build_sums(conn)[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def ledger_run_spend(run_ids: Sequence[str], path: Path | None = None) -> tuple[float, int] | None:
    """(charged usd, charges) for these ledger runs, read only; None when the ledger or the rows are missing."""
    conn = _read_only(Path(path or config.LEDGER_PATH))
    if conn is None or not run_ids:
        return None
    try:
        marks = ", ".join("?" for _ in run_ids)
        row = conn.execute(f"SELECT COALESCE(SUM(usd), 0) AS usd, COUNT(*) AS n FROM entries "
                           f"WHERE kind = 'charge' AND run_id IN ({marks})", list(run_ids)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return (float(row["usd"]), int(row["n"])) if row["n"] else None


def ledger_run_ids(prefix: str = RUN_ID_PREFIX, path: Path | None = None) -> list[str]:
    """Every ledger run id that starts with `prefix`, read only; none when there is no ledger file."""
    ledger_file = Path(path or config.LEDGER_PATH)
    conn = _read_only(ledger_file)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT DISTINCT run_id FROM entries WHERE substr(run_id, 1, ?) = ? ORDER BY run_id",
                            (len(prefix), prefix)).fetchall()
    except sqlite3.Error as exc:
        raise EvalRefusal(f"could not read the ledger at {ledger_file} for earlier {SUITE} runs ({exc}).") from exc
    finally:
        conn.close()
    return [str(r["run_id"]) for r in rows]


def _usd(value: float) -> str:
    return f"{value:.4f} USD"


def settings_lines() -> list[str]:
    lines = [f"settings (pre-registered, frozen; hash {settings_hash()}, registered {REGISTERED_SETTINGS_HASH}):"]
    for key, value in SETTINGS.items():
        shown = (", ".join(map(str, value)) or "none") if isinstance(value, tuple) else value
        if isinstance(value, str) and "\n" in value:
            shown = value.replace("\n", "\\n")
        lines.append(f"  {key}: {shown}")
    return lines


def plan_lines(prepared: Prepared, plan: Plan, build_spent: float | None, state: RunState) -> list[str]:
    held = len(prepared.items)
    if state.record is None:
        asks = f"a new run asks all {plan.items}"
    else:
        asks = (f"run {state.record.get('run_id')} ({state.record.get('status')}) has {len(state.recorded)} "
                f"recorded and resumes with the {plan.items} not yet asked")
    lines = [
        "claude_judge: Claude Haiku 5.5 asked Jev's question on the SC12a held out half",
        f"items: {held} held out items, in item id order (the tune half is never sent); {asks}",
        f"question: wording {SETTINGS['wording']}, question hash {SETTINGS['question_hash']}, from "
        f"{SETTINGS['question_source']}; the lock file matches sha256 {LOCK_FILE_SHA256[:16]}",
        *settings_lines(),
        f"planned calls: typical {plan.typical_calls}, at most {plan.max_calls} (one retry per item, only on a "
        "transport error)",
        f"input tokens by the ledger's estimator: {plan.input_tokens_est} for the {plan.items} prompts "
        f"(mean {plan.input_tokens_est / plan.items:.0f})" if plan.items else "input tokens: none",
        f"estimated cost: typical {_usd(plan.typical_usd)} (each call at its input estimate and the classifier's "
        f"typical {config.typical_tokens(SETTINGS['model'], config.CLASSIFIER_TOKENS_TYPICAL['output'])} output "
        "tokens; thinking not counted), worst case "
        f"{_usd(plan.worst_usd)} (every call at its full reservation of {SETTINGS['max_tokens']} output tokens "
        f"and every item retried once: {_usd(plan.reservation_usd)}, held to the run cap)",
        f"run cap ({MODE}): {_usd(SETTINGS['run_cap_usd'])} for the run id, resumes included; the ledger refuses "
        f"any call that would pass it or the build cap of {_usd(config.BUILD_CAP_USD)}, and the run then stops "
        "and reports",
    ]
    if build_spent is None:
        lines.append(f"build spend so far: no ledger at {config.LEDGER_PATH}")
    else:
        lines.append(f"build spend so far (live runs, read only): {_usd(build_spent)} of "
                     f"{_usd(config.BUILD_CAP_USD)}, remaining {_usd(config.BUILD_CAP_USD - build_spent)}")
    if state.others:
        lines.append(f"other {SUITE} runs: {describe_runs(state.others)}")
    lines.append(f"replies go to {replies_path()}")
    if state.refusal is not None:
        lines.append(f"run refuses: {state.refusal}")
    return lines


def plan() -> list[str]:
    """`plan`: offline, no key, nothing written."""
    prepared = prepare()
    state = run_state(prepared.items)
    return plan_lines(prepared, build_plan(prepared, state.to_ask), build_spend_read_only(), state)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

TRANSPORT_ERROR = "transport_error"
UNPARSED = "unparsed"
STOP_API_ERROR = "api_error"
COMPLETED = "completed"
STOPPED = "stopped"
RUNNING = "running"


def _read_record(path: Path) -> dict[str, Any]:
    try:
        record = harness.read_json(path)
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def other_runs(registered_run_id: str | None) -> list[dict[str, Any]]:
    """Every Claude judge run but the registered one: a run record or replies under another settings folder,
    and any ledger run with this arm's prefix that is not the registered run (read only)."""
    found: list[dict[str, Any]] = []
    if runs_dir().is_dir():
        for folder in sorted(p for p in runs_dir().iterdir() if p.is_dir() and p.name != REGISTERED_SETTINGS_HASH):
            if not [p for p in (folder / RUN_RECORD, folder / REPLIES) if p.exists()]:
                continue
            record = _read_record(folder / RUN_RECORD)
            found.append({"source": "folder", "settings_hash": folder.name,
                          "status": record.get("status") or "unknown", "run_id": record.get("run_id")})
    named = {r["run_id"] for r in found}
    for run_id in ledger_run_ids():
        if run_id != registered_run_id and run_id not in named:
            found.append({"source": "ledger", "settings_hash": None, "status": "ledger rows only",
                          "run_id": run_id})
    return found


def superseded(run: Mapping[str, Any]) -> bool:
    return bool({run.get("settings_hash"), run.get("run_id")} & set(SETTINGS["supersedes"]))


def describe_runs(runs: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(f"settings {r['settings_hash'] or 'unknown'}, status {r['status']}, run id "
                     f"{r['run_id'] or 'unknown'} (from the {r['source']}"
                     f"{', superseded' if superseded(r) else ''})" for r in runs)


def recorded_replies(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The reply line per item id under the record's run id; refused when an item has two."""
    replies: dict[str, dict[str, Any]] = {}
    if not replies_path().is_file():
        return replies
    for text in replies_path().read_text(encoding="utf-8").splitlines():
        if not text.strip():
            continue
        entry = json.loads(text)
        if entry.get("run_id") != record.get("run_id"):
            continue
        key = str(entry["item_id"])
        if key in replies:
            raise EvalRefusal(f"item {key} has two reply lines under run {record.get('run_id')} in "
                              f"{replies_path()}; the run record is not trustworthy.")
        replies[key] = entry
    return replies


@dataclass
class RunState:
    """Where the registered run stands: its record, the replies under its run id and what run would ask."""

    record: dict[str, Any] | None
    recorded: dict[str, dict[str, Any]]
    to_ask: list[dict[str, Any]]
    others: list[dict[str, Any]]
    refusal: str | None

    def signature(self) -> tuple[Any, ...]:
        record = self.record or {}
        return (record.get("run_id"), record.get("status"), tuple(sorted(self.recorded)), self.refusal)


def lock_is_held(path: Path | None = None) -> bool:
    """Whether a live process holds the run lock (read only: an absent lock file is never created)."""
    path = path or run_lock_path()
    if not path.is_file():
        return False
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def run_state(items: Sequence[Mapping[str, Any]]) -> RunState:
    """Whether run may start, or resume a stopped run, and which items it would ask. Read only."""
    record = _read_record(run_record_path()) if run_record_path().is_file() else None
    run_id = record.get("run_id") if record else None
    others = other_runs(run_id)
    blocking = [r for r in others if not superseded(r)]
    recorded: dict[str, dict[str, Any]] = {}
    refusal = None
    if record is None and replies_path().exists():
        refusal = f"{replies_path()} exists with no run record; this arm runs once."
    elif blocking:
        refusal = (f"other {SUITE} runs exist beside the registered settings {REGISTERED_SETTINGS_HASH}: "
                   f"{describe_runs(blocking)}; a logged decision must list each in SETTINGS \"supersedes\" "
                   "before this arm runs again.")
    elif record is None:
        pass
    elif not run_id or record.get("settings_hash") != REGISTERED_SETTINGS_HASH:
        refusal = f"the run record {run_record_path()} names no run id or other settings; this arm runs once."
    elif record.get("status") == COMPLETED:
        refusal = (f"run {run_id} under settings {REGISTERED_SETTINGS_HASH} already exists (status completed); "
                   "this arm runs once and a completed run is never rerun.")
    elif record.get("status") not in (STOPPED, RUNNING):
        refusal = f"run {run_id} has status {record.get('status')!r}, which cannot be resumed."
    elif record.get("status") == RUNNING and lock_is_held():
        refusal = f"run {run_id} is running now (another process holds {run_lock_path()})."
    if refusal is None and record is not None:
        try:
            recorded = recorded_replies(record)
        except EvalRefusal as exc:
            refusal = str(exc)
    to_ask = [] if refusal is not None else [dict(i) for i in items if i["id"] not in recorded]
    return RunState(record=record, recorded=recorded, to_ask=to_ask, others=others, refusal=refusal)


@contextlib.contextmanager
def run_lock(run_id: str) -> Iterator[None]:
    """An exclusive lock on run.lock for the length of the calls; refused, with nothing spent, if held."""
    path = run_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvalRefusal(f"another {SUITE} run holds {path}; nothing was sent.") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"pid {os.getpid()} run {run_id} since {_now()}\n".encode())
        yield
    finally:
        os.close(fd)


def _usage(message: Any) -> dict[str, Any] | None:
    usage = getattr(message, "usage_metadata", None)
    return json.loads(json.dumps(dict(usage))) if usage else None


def _charge_billed(ledger: Any, reservation: Any, message: Any) -> float:
    """A billed reply's usage, or its whole reservation (estimated) when it reports none."""
    from agent.nodes.synthesize import charge_reply

    return charge_reply(ledger, reservation, message)


def ask_one(item: Mapping[str, Any], question: Mapping[str, Any], runnable: Any, ledger: Any, run_id: str,
            est: int) -> tuple[dict[str, Any] | None, str | None]:
    """(the item's reply line, or None when no call was sent; why the run must stop, or None)."""
    from agent.declined import ModelDeclined
    from agent.ledger import BudgetExceeded
    from agent.live.capabilities import _reply_model
    from agent.nodes.synthesize import _timeout_errors

    schema = step_judgment_schema()
    messages = messages_for(item, question)
    started = time.perf_counter()
    line: dict[str, Any] = {"item_id": item["id"], "run_id": run_id, "settings_hash": settings_hash(),
                            "attempts": 0, "flag": False, "answered": False, "declined": False, "category": None,
                            "error": None, "usage": None, "usd": 0.0, "model": None, "stop_reason": None}

    def done() -> dict[str, Any]:
        line["latency_s"] = round(time.perf_counter() - started, 3)
        line["recorded_at"] = _now()
        return line

    for attempt in range(1, ATTEMPTS + 1):
        try:
            reservation = ledger.reserve(run_id, mode=MODE, node=NODE, model=SETTINGS["model"],
                                         input_tokens_est=est, max_tokens=SETTINGS["max_tokens"],
                                         run_cap_usd=SETTINGS["run_cap_usd"], provider=PROVIDER)
        except BudgetExceeded as exc:
            return (done() if line["attempts"] else None), exc.reason
        line["attempts"] = attempt
        try:
            out = runnable.invoke(messages)
        except ModelDeclined as exc:
            line["usd"] += _charge_billed(ledger, reservation, exc.ai_message)
            line.update(declined=True, flag=False, category=exc.category, error=None,
                        usage=_usage(exc.ai_message), model=_reply_model(exc.ai_message), stop_reason="refusal")
            return done(), None
        except _timeout_errors():
            line["usd"] += ledger.charge_timeout(reservation)
            line["error"] = TRANSPORT_ERROR
            continue
        except Exception as exc:
            billed = getattr(exc, "ai_message", None)
            if billed is None:
                ledger.release(reservation)
            else:
                line["usd"] += _charge_billed(ledger, reservation, billed)
            line["error"] = type(exc).__name__
            return done(), STOP_API_ERROR
        raw = out.get("raw")
        line["usd"] += _charge_billed(ledger, reservation, raw)
        parsed = out.get("parsed")
        meta = getattr(raw, "response_metadata", None) or {}
        line.update(usage=_usage(raw), model=_reply_model(raw), stop_reason=meta.get("stop_reason"), error=None)
        if out.get("parsing_error") is None and isinstance(parsed, schema):
            line.update(answered=True, flag=parsed.involves_the_hazard is True)
        else:
            line["error"] = UNPARSED
        return done(), None
    return done(), None


def _append(path: Path, entry: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(entry), sort_keys=True) + "\n")


def ask_items(prepared: Prepared, plan_: Plan, state: RunState, *, ledger: Any, run_id: str,
              chat_factory: Callable[..., Any]) -> dict[str, Any]:
    """Ask each item with no reply line once, in order, recording each reply as it comes; stop at a refused
    call. A resume keeps the run's id and record and adds the pass to its "resumes". However the pass ends,
    an unexpected error included, the record is rewritten with its status, stop reason and spend."""
    from agent import build_info

    chat = chat_factory(**prepared.kwargs)
    runnable = chat.with_structured_output(step_judgment_schema(), method="json_schema", include_raw=True)
    started = _now()
    if state.record is None:
        record: dict[str, Any] = {
            "note": "the Claude judge arm's run record: ids, counts and numbers only, no step text",
            "run_id": run_id, "settings_hash": settings_hash(), "settings": dict(SETTINGS), "status": RUNNING,
            "started_at": started, "finished_at": None, "stop_reason": None, "items": len(prepared.items),
            "asked": 0, "build_id": build_info.build_id(), "spent_usd": 0.0, "resumes": [],
        }
        this_pass: dict[str, Any] = record
    else:
        record = dict(state.record)
        this_pass = {"started_at": started, "finished_at": None, "stop_reason": None,
                     "to_ask": len(state.to_ask), "build_id": build_info.build_id()}
        record["resumes"] = [*record.get("resumes", []), this_pass]
        record.update(status=RUNNING, finished_at=None, stop_reason=None)
    harness.write_json(run_record_path(), record)
    held = {i["id"] for i in prepared.items}
    recorded = set(state.recorded)
    stop: str | None = None
    failed = False
    total = len(state.to_ask)
    try:
        for n, item in enumerate(state.to_ask, start=1):
            line, stop = ask_one(item, prepared.question, runnable, ledger, run_id, plan_.estimates[item["id"]])
            if line is not None:
                _append(replies_path(), line)
                recorded.add(item["id"])
            if n % PROGRESS_EVERY == 0 or n == total:
                print(f"  {n} of {total} items; run spend so far {_usd(ledger.run_total(run_id))}")
            if stop is not None:
                print(f"claude_judge: stopped at item {n} of {total} ({stop}); every call so far is in the ledger.")
                break
    except BaseException as exc:
        failed = True
        stop = f"unexpected: {type(exc).__name__}"
        raise
    finally:
        try:
            spent = ledger.run_total(run_id)
        except Exception:
            spent = record.get("spent_usd")
        finished = _now()
        this_pass.update(finished_at=finished, stop_reason=stop)
        record.update(status=COMPLETED if held <= recorded else STOPPED, stop_reason=stop,
                      asked=len(recorded & held), finished_at=finished, spent_usd=spent)
        try:
            harness.write_json(run_record_path(), record)
        except Exception:
            if not failed:
                raise
    return record


def run(*, environ: MutableMapping[str, str] | None = None, stdin: IO[str] | None = None,
        chat_factory: Callable[..., Any] | None = None) -> int:
    """`run`: the one live run, or the resume of a stopped one. The key comes through the project's env
    loader; nothing is sent before a typed "proceed", and then only under the run lock, with the run's state
    checked again."""
    environ = os.environ if environ is None else environ
    try:
        prepared = prepare()
        state = run_state(prepared.items)
        if state.refusal is not None:
            raise EvalRefusal(state.refusal)
        plan_ = build_plan(prepared, state.to_ask)
        ledger = cli.open_live_ledger(MODE, new_ledger=False, need=(cli.ANTHROPIC_KEY_NAME,),
                                      need_usd=plan_.worst_usd, environ=environ)
        session = cli.LiveSession(f"{SUITE} run", MODE, ledger, need=(cli.ANTHROPIC_KEY_NAME,), environ=environ)
    except (EvalRefusal, cli.CliRefusal) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return cli.EXIT_REFUSED
    if chat_factory is None:
        from agent import models

        chat_factory = models.ChatAnthropic
    # A resume keeps the run's own id, so the ledger's run cap counts every call the run has made.
    run_id = state.record["run_id"] if state.record is not None else f"{RUN_ID_PREFIX}{uuid.uuid4().hex[:12]}"
    with session.active():
        lines = [*plan_lines(prepared, plan_, ledger.build_total(), state), "LIVE (real money)",
                 f"Type {PROCEED} to spend this; anything else cancels and spends nothing."]
        if not ask_to_proceed(lines, stdin if stdin is not None else sys.stdin, sys.stdout):
            print(f'canceled: the typed confirmation was not exactly "{PROCEED}"; nothing was spent.')
            return cli.EXIT_REFUSED
        try:
            with run_lock(run_id):
                again = run_state(prepared.items)
                if again.refusal is not None or again.signature() != state.signature():
                    raise EvalRefusal("the run's state changed while the plan waited for proceed "
                                      f"({again.refusal or 'other replies or another record'}); nothing was sent.")
                print(f"ledger run id {run_id}" + (" (resumed)" if state.record is not None else ""))
                try:
                    record = ask_items(prepared, plan_, state, ledger=ledger, run_id=run_id,
                                       chat_factory=chat_factory)
                except Exception as exc:
                    return cli._live_failure(f"{SUITE} run", exc)
        except EvalRefusal as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return cli.EXIT_REFUSED
        print(f"claude_judge: {record['status']}, {record['asked']} of {record['items']} items have a reply, "
              f"{_usd(record['spent_usd'])}; replies in {replies_path()}")
        print("score it with: python -m agent.safety_eval.claude_judge score" if record["status"] == COMPLETED
              else "resume it with: python -m agent.safety_eval.claude_judge run")
    return cli.EXIT_OK if record["status"] == COMPLETED else cli.EXIT_LIVE_ERROR


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


def load_run() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The completed run's record and its reply per item id (only lines under its run id); refused for a run
    that is missing, stopped, running or short of any held out item."""
    if not run_record_path().is_file():
        raise EvalRefusal(f"no run under settings {REGISTERED_SETTINGS_HASH} ({run_record_path()}): run it first.")
    record = harness.read_json(run_record_path())
    replies = recorded_replies(record)
    if record.get("status") != COMPLETED:
        raise EvalRefusal(f"run {record.get('run_id')} is {record.get('status')}, not completed "
                          f"({len(replies)} items have a reply): resume it with run before scoring.")
    missing = [i["id"] for i in heldout_items() if i["id"] not in replies]
    if missing:
        raise EvalRefusal(f"run {record.get('run_id')} has no reply line for {len(missing)} held out items; "
                          "score reads a complete run only.")
    return record, replies


def percentile_nearest_rank(values: Sequence[float], percent: int) -> float | None:
    """The nearest rank percentile: the smallest value with at least `percent` of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-percent * len(ordered) // 100))
    return ordered[rank - 1]


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def jev_cost(held_replies: Mapping[str, Mapping[str, Any]], items: int) -> dict[str, Any]:
    """Jev's cost per item: from the ledger rows of the SC12a held out pass, else from its recorded tokens."""
    run_ids = sorted({str(r.get("run_id")) for r in held_replies.values() if r.get("run_id")})
    spend = ledger_run_spend(run_ids)
    if spend is not None:
        usd, charges = spend
        return {"total_usd": usd, "per_item_usd": _ratio(usd, items), "charges": charges, "ledger_runs": run_ids,
                "basis": "ledger charges of the SC12a held out pass"}
    tokens = sum(int(r.get("input_tokens") or 0) for r in held_replies.values())
    price = config.PRICES_PER_MTOK[config.JEV_MODEL]["input"]
    usd = tokens * price / 1_000_000
    return {"total_usd": usd, "per_item_usd": _ratio(usd, items), "charges": None, "ledger_runs": run_ids,
            "basis": (f"the recorded input tokens ({tokens}) at Jev's input price of {price} USD per million, "
                      "output free; the ledger rows were not readable")}


def _ids(ids: Sequence[str]) -> dict[str, Any]:
    return {"count": len(ids), "ids": list(ids)}


# The paired tests (exact McNemar), every pair of these arms in this order, Jev against Claude first.
PAIRED_ARMS = (JEV_ARM, CLAUDE_ARM, PUBLISHED_ARM, TUNED_ARM)
PAIRED_METHOD = ("exact two sided McNemar test: a paired test compares two checks step by step on the same steps, "
                 "counting only the steps where they disagree; the p value is the chance of a split at least this "
                 "lopsided if the two checks were equally good (binomial with n the disagreements and probability "
                 "one half, the smaller tail doubled and capped at 1; 1 when they never disagree)")
PAIRED_NOTE = ("this test was chosen after the recall and precision results were known, not registered before the "
               "run; several comparisons are reported (every pair of the four arms, against two references, for "
               "missed safety steps and for false flags) with no correction for their number, so a single small "
               "p value would need care")


def paired_tests(scored: Sequence[Mapping[str, Any]],
                 flags: Mapping[str, Callable[[Mapping[str, Any]], bool]]) -> dict[str, Any]:
    """Every pair of PAIRED_ARMS on the same items, step by step: among the reference's safety steps (its
    positives), the ones each arm caught and the other missed; among its other steps (its negatives), the false
    flags each arm raised and the other did not; with the exact McNemar p value of each split. Ids only."""
    raised = {name: {i["id"]: bool(flags[name](i)) for i in scored} for name in PAIRED_ARMS}
    positives = [i["id"] for i in scored if i["positive"]]
    negatives = [i["id"] for i in scored if not i["positive"]]

    def split(first: str, second: str, ids: Sequence[str]) -> dict[str, Any]:
        first_only = [k for k in ids if raised[first][k] and not raised[second][k]]
        second_only = [k for k in ids if raised[second][k] and not raised[first][k]]
        p = stats.mcnemar_exact(len(first_only), len(second_only))
        return {"steps": len(ids), "first_only": _ids(first_only), "second_only": _ids(second_only),
                "p_value": float(p), "p_value_exact": str(p)}

    pairs = []
    for n, first in enumerate(PAIRED_ARMS):
        for second in PAIRED_ARMS[n + 1:]:
            pairs.append({"first": first, "second": second,
                          "safety_steps_caught": split(first, second, positives),
                          "false_flags_raised": split(first, second, negatives)})
    return {"items": len(scored), "safety_steps": len(positives), "other_steps": len(negatives), "pairs": pairs}


def score_against_roanuk(held: Sequence[Mapping[str, Any]], jev_replies: Mapping[str, Mapping[str, Any]],
                         threshold: float, claude: Mapping[str, bool]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Every arm against Roanuk's sealed blind labels, read through sc12c's own loader and positive rule, and
    the paired tests between the arms against the same labels.

    Not sealed here (the labels or the seal missing): says so and scores nothing. Sealed but changed since:
    refused by sc12c.require_sealed. Ids, counts and numbers only."""
    labels_file, seal_file = sc12c.committed(sc12c.HUMAN_LABELS), sc12c.committed(sc12c.SEAL)
    if not labels_file.is_file() or not seal_file.is_file():
        missing = ", ".join(p.name for p in (labels_file, seal_file) if not p.is_file())
        unsealed = {"sealed": False, "note": (f"Roanuk's blind labels are not sealed here ({missing} missing in "
                                              f"{labels_file.parent}); scored against the AI majority only")}
        return unsealed, dict(unsealed)
    human = sc12c.require_sealed()["labels"]
    ids = [i["id"] for i in held]
    if set(human) != set(ids):
        raise EvalRefusal("the sealed SC12c labels' ids are not the held out half's ids.")
    as_labels = {k: {"positive": sc12c.positive_vote(v), "hazard": v["hazard"]} for k, v in human.items()}
    scored = harness.scored_items(held, as_labels, jev_replies)
    counts = {CLAUDE_ARM: stats.count(scored, lambda i: claude[i["id"]]), **sc12c._arms(scored, threshold)}
    flags: dict[str, Callable[[Mapping[str, Any]], bool]] = {
        CLAUDE_ARM: lambda i: claude[i["id"]],
        JEV_ARM: lambda i: stats.jev_raises(i["noul"], threshold),
        PUBLISHED_ARM: lambda i: i["word_published"],
        TUNED_ARM: lambda i: i["word_tuned"],
    }
    missed = {name: [i["id"] for i in scored if i["positive"] and not flag(i)] for name, flag in flags.items()}
    for name, c in counts.items():
        if len(missed[name]) != c.positives - c.tp:
            raise EvalRefusal(f"{name} misses {len(missed[name])} of Roanuk's safety steps by its flags but "
                              f"{c.positives - c.tp} by its counts; the arms are not scored alike.")
    jev_missed, claude_missed = set(missed[JEV_ARM]), set(missed[CLAUDE_ARM])
    sha = sc12c.file_sha256(labels_file)
    reproduces: bool | None = None
    sc12c_scores = sc12c.committed(sc12c.SCORES)
    if sc12c_scores.is_file():
        published = harness.read_json(sc12c_scores)
        if published.get("human_labels_sha256") == sha:
            want = {name: tuple(published["arms"]["against_roanuk"][name][k] for k in ("tp", "flagged", "positives"))
                    for name in (JEV_ARM, PUBLISHED_ARM, TUNED_ARM)}
            got = {name: (counts[name].tp, counts[name].flagged, counts[name].positives) for name in want}
            if got != want:
                raise EvalRefusal(f"Jev and the word lists against Roanuk's labels ({got}) do not reproduce the "
                                  f"SC12c score in {sc12c_scores.name} ({want}); the recorded data changed.")
            reproduces = True
    summary = {
        "sealed": True,
        "note": HUMAN_NOTE,
        "human_labels_sha256": sha,
        "items": len(ids),
        "positives": sum(1 for i in scored if i["positive"]),
        "arms": {name: {**c.summary(), "missed": len(missed[name]), "missed_ids": missed[name]}
                 for name, c in counts.items()},
        "missed_by_both_jev_and_claude": _ids([k for k in ids if k in jev_missed and k in claude_missed]),
        "missed_by_jev_caught_by_claude": _ids([k for k in ids if k in jev_missed and k not in claude_missed]),
        "missed_by_claude_caught_by_jev": _ids([k for k in ids if k in claude_missed and k not in jev_missed]),
        "reproduces_sc12c_score": reproduces,
        "revisions_not_scored": len(sc12c.load_revisions()),
    }
    return summary, {"sealed": True, "reference": HUMAN_NOTE, "human_labels_sha256": sha,
                     **paired_tests(scored, flags)}


def score() -> dict[str, Any]:
    """`score`: the Claude arm beside Jev and both word lists, against the AI majority and, when they are
    sealed here, Roanuk's sealed blind labels, from records only. Refused unless the settings are the registered ones and their run completed with a line for every item."""
    check_registered()
    lock = verified_lock()
    threshold = lock["thresholds"][stats.JEV]["threshold"]
    if threshold != config.JEV_THRESHOLD:
        raise EvalRefusal(f"the lock's Jev threshold {threshold} is not config.JEV_THRESHOLD {config.JEV_THRESHOLD}.")
    held = heldout_items()
    ai = harness.load_labels()
    jev_replies = harness.load_replies(lock["question_hash"])
    missing = [i["id"] for i in held if i["id"] not in ai]
    no_reply = harness.missing_replies(held, jev_replies)
    if missing or no_reply:
        raise EvalRefusal(f"{len(missing)} held out items have no AI majority label and {len(no_reply)} no recorded "
                          "Jev reply; the score reads recorded data only.")
    scored = harness.scored_items(held, ai, jev_replies)
    reference = sc12c._arms(scored, threshold)
    got = {name: (c.tp, c.flagged, c.positives) for name, c in reference.items()}
    if got != PUBLISHED_AI_ARMS:
        raise EvalRefusal(f"the reference arms ({got}) do not reproduce the published held out numbers "
                          f"({PUBLISHED_AI_ARMS}); the recorded data changed.")
    record, replies = load_run()
    others = other_runs(record.get("run_id"))
    ids = [i["id"] for i in held]
    claude = {k: bool((replies.get(k) or {}).get("flag") is True) for k in ids}
    jev = {i["id"]: stats.jev_raises(i["noul"], threshold) for i in scored}
    positive = {i["id"]: bool(i["positive"]) for i in scored}
    arms = {CLAUDE_ARM: stats.count(scored, lambda i: claude[i["id"]]), **reference}
    against_roanuk, paired_roanuk = score_against_roanuk(held, jev_replies, threshold, claude)
    paired_readers = paired_tests(scored, {
        JEV_ARM: lambda i: jev[i["id"]], CLAUDE_ARM: lambda i: claude[i["id"]],
        PUBLISHED_ARM: lambda i: i["word_published"], TUNED_ARM: lambda i: i["word_tuned"]})

    yes_no = ("yes", "no")
    table = stats.agreement_table([("yes" if claude[k] else "no", "yes" if jev[k] else "no") for k in ids], yes_no)
    agree = table[0][0] + table[1][1]
    kappa = stats.cohen_kappa(table)
    asked = [k for k in ids if k in replies]
    declined = [k for k in asked if replies[k].get("declined")]
    unanswered = [k for k in asked if not replies[k].get("declined") and not replies[k].get("answered")]
    latencies = [float(replies[k]["latency_s"]) for k in asked if replies[k].get("latency_s") is not None]
    reply_usd = sum(float(replies[k].get("usd") or 0) for k in asked)
    spend = ledger_run_spend([record["run_id"]])
    total = spend[0] if spend is not None else float(record.get("spent_usd") or reply_usd)
    held_jev = {k: v for k, v in jev_replies.items() if k in set(ids)}

    result = {
        "note": ("the Claude judge arm against the AI majority on the SC12a held out half, beside Jev and both word "
                 f"lists; {REFERENCE_NOTE}; against_roanuk scores the same arms against Roanuk Zaman's sealed "
                 "blind labels (SC12c); ids, counts and numbers only, no step text"),
        "reference_labels": REFERENCE_NOTE,
        "scored_at": _now(),
        "settings_hash": settings_hash(),
        "run_id": record["run_id"],
        "run_status": record.get("status"),
        "items": len(ids),
        "asked": len(asked),
        "other_runs": others,
        "lock_sha256": LOCK_FILE_SHA256,
        "question_hash": lock["question_hash"],
        "jev_threshold": threshold,
        "in_scope_hazards": list(IN_SCOPE_HAZARDS),
        "arms": {name: c.summary() for name, c in arms.items()},
        "reference_arms_reproduce_published": True,
        "against_roanuk": against_roanuk,
        "paired": {
            "method": PAIRED_METHOD,
            "note": PAIRED_NOTE,
            "against_roanuk": paired_roanuk,
            "against_readers": {"reference": REFERENCE_NOTE, **paired_readers},
        },
        "agreement_with_jev": {
            "items": len(ids), "agree": agree, "percent": _ratio(100 * agree, len(ids)),
            "cohen_kappa": None if kappa is None else float(kappa),
            "table": {"claude_yes_jev_yes": table[0][0], "claude_yes_jev_no": table[0][1],
                      "claude_no_jev_yes": table[1][0], "claude_no_jev_no": table[1][1]},
        },
        "caught_by_claude_only": [k for k in ids if positive[k] and claude[k] and not jev[k]],
        "caught_by_jev_only": [k for k in ids if positive[k] and jev[k] and not claude[k]],
        "declines": {"count": len(declined), "ids": declined,
                     "categories": dict(Counter(str(replies[k].get("category")) for k in declined))},
        "unanswered": {"count": len(unanswered), "ids": unanswered,
                       "kinds": dict(Counter(str(replies[k].get("error")) for k in unanswered))},
        "models_seen": sorted({str(replies[k].get("model")) for k in asked if replies[k].get("model")}),
        "cost": {
            "claude_total_usd": total, "claude_per_item_usd": _ratio(total, len(asked)),
            "claude_basis": "ledger charges of the run" if spend is not None else "the run record",
            "jev": jev_cost(held_jev, len(ids)),
        },
        "latency_s": {"median": statistics.median(latencies) if latencies else None,
                      "p90": percentile_nearest_rank(latencies, 90), "items": len(latencies),
                      "method": "per item wall time, retry included; 90th percentile by nearest rank"},
    }
    harness.write_json(scores_path(), result)
    return result


def _fmt(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def roanuk_lines(h: Mapping[str, Any]) -> list[str]:
    """The printout of every arm against Roanuk's sealed blind labels, or why there is none."""
    if not h.get("sealed"):
        return [f"against Roanuk's labels: not scored; {h.get('note')}"]
    lines = [f"against Roanuk's sealed blind labels (SC12c, sha256 {h['human_labels_sha256'][:16]}): "
             f"{h['positives']} safety steps of {h['items']}; recall and precision with Wilson 95% intervals:",
             f"note: {h['note']}"]
    for name, s in h["arms"].items():
        c = stats.Counts(**{k: s[k] for k in ("tp", "flagged", "positives", "other_flagged")})
        lines.append(f"{harness.arm_line(name, c)}; missed {s['missed']} of {s['positives']}")
    for key, title in (("missed_by_both_jev_and_claude", "missed by both Jev and Claude"),
                       ("missed_by_jev_caught_by_claude", "missed by Jev and caught by Claude"),
                       ("missed_by_claude_caught_by_jev", "missed by Claude and caught by Jev")):
        lines.append(f"  Roanuk's safety steps {title}: {h[key]['count']} {[k[:12] for k in h[key]['ids']]}")
    reproduced = {True: "yes", None: "no SC12c score for these sealed labels to check against"}
    lines.append(f"  Jev and both word lists reproduce the SC12c score: {reproduced[h['reproduces_sc12c_score']]}")
    lines.append(f"  labels revised after the SC12c score (not scored): {h['revisions_not_scored']}")
    return lines


def paired_lines(paired: Mapping[str, Any] | None) -> list[str]:
    """The printout of the paired tests: for each reference and pair, the two discordant counts and the p value
    for safety steps caught and for false flags raised; the discordant ids are in the scores file."""
    if not paired:
        return ["paired tests: not scored"]
    lines = [f"paired tests ({paired['method']})", f"note: {paired['note']}"]
    for key, title in (("against_roanuk", "against Roanuk's sealed blind labels"),
                       ("against_readers", "against the readers' majority")):
        ref = paired[key]
        if ref.get("sealed") is False:
            lines.append(f"  {title}: not scored; {ref['note']}")
            continue
        lines.append(f"  {title}: {ref['safety_steps']} safety steps, {ref['other_steps']} other steps")
        for pair in ref["pairs"]:
            caught, false = pair["safety_steps_caught"], pair["false_flags_raised"]
            lines.append(
                f"    {pair['first']} against {pair['second']}: safety steps caught by the first only "
                f"{caught['first_only']['count']}, by the second only {caught['second_only']['count']}, "
                f"p {caught['p_value']:.3f}; false flags raised by the first only {false['first_only']['count']}, "
                f"by the second only {false['second_only']['count']}, p {false['p_value']:.3f}")
    lines.append(f"  the ids of every disagreement are under paired in {SCORES}")
    return lines


def format_score(result: Mapping[str, Any]) -> list[str]:
    a = result["agreement_with_jev"]
    t = a["table"]
    cost = result["cost"]
    lat = result["latency_s"]
    lines = [
        f"Claude judge, SC12a held out half: {result['items']} items, {result['asked']} asked, run "
        f"{result['run_id']} ({result['run_status']}), settings {result['settings_hash']}",
        f"note: {REFERENCE_NOTE}",
        "recall and precision against the AI majority, with Wilson 95% intervals:",
    ]
    for name, s in result["arms"].items():
        lines.append(harness.arm_line(name, stats.Counts(**{k: s[k] for k in ("tp", "flagged", "positives",
                                                                                "other_flagged")})))
    lines += [
        f"agreement with Jev (at {result['jev_threshold']}) on all {a['items']}: {a['agree']} agree "
        f"({_fmt(a['percent'], 1)}%), Cohen's kappa {_fmt(a['cohen_kappa'])}",
        f"  Claude yes: Jev yes {t['claude_yes_jev_yes']}, Jev no {t['claude_yes_jev_no']}",
        f"  Claude no: Jev yes {t['claude_no_jev_yes']}, Jev no {t['claude_no_jev_no']}",
        f"safety steps caught by Claude and missed by Jev: {len(result['caught_by_claude_only'])} "
        f"{[k[:12] for k in result['caught_by_claude_only']]}",
        f"safety steps caught by Jev and missed by Claude: {len(result['caught_by_jev_only'])} "
        f"{[k[:12] for k in result['caught_by_jev_only']]}",
        f"declines (counted as not flagged): {result['declines']['count']} {result['declines']['categories']}",
        f"not answered (counted as not flagged): {result['unanswered']['count']} {result['unanswered']['kinds']}",
        f"cost: Claude {_usd(cost['claude_total_usd'])} in all, {_fmt(cost['claude_per_item_usd'], 6)} USD per item "
        f"({cost['claude_basis']}); Jev {_fmt(cost['jev']['per_item_usd'], 6)} USD per item ({cost['jev']['basis']})",
        f"latency per item: median {_fmt(lat['median'], 2)} s, 90th percentile {_fmt(lat['p90'], 2)} s "
        f"({lat['items']} items)",
    ]
    lines += roanuk_lines(result.get("against_roanuk") or {"sealed": False, "note": "not scored"})
    lines += paired_lines(result.get("paired"))
    lines.append(f"other {SUITE} runs: " + (describe_runs(result["other_runs"]) if result["other_runs"] else "none"))
    if [m for m in result["models_seen"] if m != SETTINGS["model"]]:
        lines.append(f"warning: replies named {result['models_seen']}, not only {SETTINGS['model']}")
    return lines


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agent.safety_eval.claude_judge", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="offline: the settings, planned calls and cost; no key needed")
    sub.add_parser("run", help="the one live run, after a typed proceed")
    sub.add_parser("score", help="offline: score the recorded replies")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            for line in plan():
                print(line)
        elif args.command == "run":
            return run()
        else:
            result = score()
            for line in format_score(result):
                print(line)
            print(f"wrote {scores_path()}")
    except EvalRefusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return cli.EXIT_REFUSED
    return cli.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
