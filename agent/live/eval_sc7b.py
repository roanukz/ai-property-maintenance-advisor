"""`advisor eval sc7b --live`: repeats after a first lookup (PLAN sections 5 and 9, decision 34).

The section 9 SC7b plan, in order, one preflight for the batch:

1. FLO repeat (v1 case B2): the Phase 5 FLO run on plate-clear.jpg was its
   first lookup, so this is the identical question, and the graph route caps
   searches at 0 by design.
2. Vague symptom repeat (v1 case B1): the same plate, "not heating"; expected
   to take the graph plus research top up route.
3. Trane XR16 first lookup (v1 case B3), typed identity, research route.
4. Trane repeat with the decision 34 symptom "outdoor unit runs but the fan
   does not spin"; expected to take the top up route.

Photo runs pause at confirm_identity; the scripted owner answer confirms the
identity and code in the spec. Each run goes through the normal graph and
ledger (eval_sc3b's harness). SC7b is scored by evaluators.score_sc7b: every
repeat with its route, its search count from `search_trail`, and the same
model's first lookup count.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from typing import IO, Any

from agent import config
from agent.live import evaluators
from agent.live.eval_sc3b import Batch, RunSpec, model_key, run_batch

SUITE = "sc7b"
PLATE_CLEAR = config.REPO_ROOT / "demo-assets" / "plate-clear.jpg"  # read only; published, never written
OPTIMA = {"manufacturer": "Sundance Spas", "model": "Optima 880"}  # v1 fixtures' confirmed identity
TRANE = {"manufacturer": "Trane", "model": "XR16 (4TTR6036)"}  # v1 case B3's typed identity
TRANE_REPEAT_SYMPTOM = "outdoor unit runs but the fan does not spin"  # decision 34
SC12B_SUITE = "sc12b"  # agent/safety_eval/sc12b.SUITE, named here so nothing under safety_eval/ is imported


def sc7b_specs() -> list[RunSpec]:
    """The four SC7b runs of PLAN section 9, in order."""
    optima = model_key(OPTIMA["model"])
    trane = model_key(TRANE["model"])
    return [
        RunSpec(suite=SUITE, case="B2", label="FLO repeat", symptom="panel shows FLO", photo=PLATE_CLEAR,
                answer_identity=dict(OPTIMA), answer_code="FLO", expected_route="graph", role="repeat",
                model_key=optima, note=evaluators.FLO_REPEAT_NOTE),
        RunSpec(suite=SUITE, case="B1", label="vague symptom repeat", symptom="not heating", photo=PLATE_CLEAR,
                answer_identity=dict(OPTIMA), answer_code=None, expected_route="top_up", role="repeat",
                model_key=optima),
        RunSpec(suite=SUITE, case="B3", label="Trane XR16 first lookup", symptom="AC not cooling upstairs",
                identity=dict(TRANE), expected_route="research", role="first", model_key=trane),
        RunSpec(suite=SUITE, case="B3", label="Trane repeat", symptom=TRANE_REPEAT_SYMPTOM,
                identity=dict(TRANE), expected_route="top_up", role="repeat", model_key=trane),
    ]


def planned_repeats() -> list[dict[str, Any]]:
    """Label and model key of every repeat the batch plans; each needs a finished run to pass (finding E1)."""
    return [{"label": s.label, "model_key": s.model_key} for s in sc7b_specs() if s.role == "repeat"]


# ---------------------------------------------------------------------------
# First lookups run elsewhere (the Phase 5 FLO run)
# ---------------------------------------------------------------------------


def _trail_from_lookup_log(run_id: str) -> list[dict[str, Any]]:
    """A run's search trail from its lookup log (data/lookups/<run_id>.jsonl)."""
    path = config.PAGES_DIR.parent / config.LOOKUPS_DIR.name / f"{run_id}.jsonl"
    trail = []
    if not path.is_file():
        return trail
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            trail.append({k: entry.get(k) for k in ("tool", "query", "n_results", "credits", "at", "status")})
    return trail


def _thread_identity(thread_id: str) -> dict[str, Any]:
    """The confirmed identity of a finished thread, read from the checkpointer (no call is made)."""
    from agent.graph import build_graph, open_checkpointer, thread_config

    if not config.CHECKPOINT_PATH.exists():
        return {}
    graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
    return dict((graph.get_state(thread_config(thread_id)).values or {}).get("identity") or {})


def _eval_run_ids() -> set[str]:
    """Every run an `advisor eval` batch made (any suite): none of them is an outside first lookup.

    The run records of SC3b, SC7b, plates and SC12b (config.EVAL_DIR/<suite>-<run>.json),
    and the run ID of every SC12a reply (config.EVAL_DIR/config.SC12A_REPLIES_SUBDIR).
    SC12b's ten live first lookups are SC12b's, so they are never SC7b's comparator.
    Nothing from agent/safety_eval/ is imported: the build fingerprint leaves that
    folder out, so the filter names the SC12b suite and the replies folder itself.
    """
    from agent.live.eval_plates import SUITE as PLATES_SUITE
    from agent.live.eval_sc3b import SUITE as SC3B_SUITE
    from agent.live.eval_sc3b import load_records

    ids = {str(r.get("run_id")) for suite in (SC3B_SUITE, SUITE, PLATES_SUITE, SC12B_SUITE)
           for r in load_records(suite)}
    replies = config.EVAL_DIR / config.SC12A_REPLIES_SUBDIR
    for path in sorted(replies.glob("*.jsonl")) if replies.is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                reply = json.loads(line) if line.strip() else {}
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("run_id"):
                ids.add(str(reply["run_id"]))
    return ids


def find_first_lookups(keys: set[str], build: str | None = None) -> list[dict[str, Any]]:
    """Live first lookups for these model keys that no eval batch ran, on `build`.

    `build` is the build whose repeats are scored: `advisor eval sc7b --score`
    passes the reported build, and None means the current build
    (build_info.build_id()), the one a new batch runs on. Reads the persisted
    run records under data/runs/ (live modes only), the identity from the
    checkpointer and the trail from the lookup log. Only runs whose record
    names that build count, so a repeat is never compared with a first lookup
    from another build (decision 35). Runs that an eval batch made are skipped
    (a repeat is never its own first lookup, and SC12b's first lookups are not
    SC7b's), and the earliest remaining run per model key wins, ties broken by
    run ID, so the build's first lookup stays the first lookup however many
    batches follow it. Records with role "first" in config.EVAL_DIR come in
    through the batch's own records instead.
    """
    from agent.build_info import build_id
    from agent.nodes.persist import RUNS_DIRNAME

    runs = config.PAGES_DIR.parent / RUNS_DIRNAME
    skip = _eval_run_ids()
    build = build or build_id()
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("mode") not in config.LIVE_MODES:
            continue
        if str(record.get("run_id")) in skip:
            continue
        if str(record.get("build_id")) != build:
            continue
        identity = _thread_identity(str(record.get("thread_id") or record.get("run_id")))
        key = model_key(identity.get("model"))
        if key not in keys:
            continue
        first = {
            "kind": "run", "suite": "external", "role": "first", "model_key": key,
            "run_id": record.get("run_id"), "route": record.get("route"), "status": record.get("status"),
            "started_at": record.get("generated_at"), "search_trail": _trail_from_lookup_log(str(record.get("run_id"))),
        }
        order = (str(first["started_at"]), str(first["run_id"]))
        if key not in found or order < (str(found[key]["started_at"]), str(found[key]["run_id"])):
            found[key] = first
    return list(found.values())


def _repeat_keys_without_batch_first() -> set[str]:
    specs = sc7b_specs()
    in_batch = {s.model_key for s in specs if s.role == "first"}
    return {s.model_key for s in specs if s.role == "repeat" and s.model_key not in in_batch}


def first_lookup_lines(firsts: list[dict[str, Any]]) -> list[str]:
    """Preflight lines naming the first lookups the repeats will be compared with."""
    lines = []
    by_key = {f["model_key"]: f for f in firsts}
    for key in sorted(_repeat_keys_without_batch_first()):
        first = by_key.get(key)
        if first is None:
            lines.append(f"first lookup for {key}: none found on this build; the FLO first lookup "
                         "(advisor ask --live on plate-clear.jpg) must run on this build first, and "
                         "without it the repeat will likely take the research route")
        else:
            lines.append(f"first lookup for {key}: run {first['run_id']} (route "
                         f"{'+'.join(first.get('route') or []) or 'none'}), "
                         f"{evaluators.count_searches(first['search_trail'])} searches")
    return lines


def external_first_lookups(build: str | None = None) -> list[dict[str, Any]]:
    """First lookups run outside this command, on `build` (None: the current build), for the
    repeats that need one (the FLO first lookup)."""
    return find_first_lookups(_repeat_keys_without_batch_first(), build)


def cmd_eval_sc7b(args: argparse.Namespace, environ: Mapping[str, str], *,
                  stdin: IO[str] | None = None) -> int:
    """`advisor eval sc7b --live`: the four SC7b runs, one preflight, one record each."""
    cache: dict[str, list[dict[str, Any]]] = {}

    def firsts() -> list[dict[str, Any]]:
        # The current build's: the batch runs on it, so its runs are the reported ones.
        if "firsts" not in cache:
            cache["firsts"] = external_first_lookups()
        return cache["firsts"]

    batch = Batch(
        suite=SUITE,
        specs=sc7b_specs(),
        score=lambda records: evaluators.score_sc7b(records, firsts(), planned_repeats()),
        format=evaluators.format_sc7b,
        preflight_extra=lambda: first_lookup_lines(firsts()),
        alt_routes={"graph": "top_up"},
    )
    return run_batch(batch, args, environ, stdin=stdin)
