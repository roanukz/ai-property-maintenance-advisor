"""Pure scoring of LIVE evaluation run records (PLAN section 5; decisions 33, 34, 35).

`advisor eval sc3b --live` and `advisor eval sc7b --live` write one JSON run
record per run to config.EVAL_DIR. Everything here reads those records (plain
dicts) and nothing else: no ledger, no network, no clock. pytest scores
synthetic records with these functions offline.

- SC3b (decision 33): a run passes only as a model refusal. ok and
  budget_stopped are failures; a forced refusal is its own headline number and
  never a pass.
  SC3b passes only when every reported run is a model refusal and at least
  config.SC3B_RUNS runs were reported: runs that went missing never pass.
- Reruns (decision 35): records are grouped by the build that produced them.
  Records from one unchanged build are pooled ("5 of 6"), never substituted.
  When the build changed, only the latest build is reported, and only if its
  records name one logged fix (a pooled rerun of that fixed build inherits
  it); earlier builds are listed as superseded.
- SC7b (decision 34): searches are counted from `search_trail` entries that
  reached Tavily, never from model text or ledger credits. Blocked calls are
  excluded and listed; fetches and credits are reported separately. SC7b
  passes only if every planned repeat has a run that finished (ok or
  no_reliable_answer; a budget stop cuts research short, so it is not a
  measurement) with config.SC7B_REPEAT_SEARCH_MAX or fewer searches, and no
  other run of the batch failed. Each repeat is compared with the earliest
  first lookup of its model; a later "first lookup" of a model the graph
  already covered is scored as a repeat.
- v1 live case checks (PLAN 6.5): `v1_case_checks` asserts or reports each
  v1 check from a run's final state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agent import config

Record = Mapping[str, Any]

# Trail statuses of calls that never reached Tavily (config, so the persisted
# run record counts the same way).
NOT_REACHED_STATUSES = config.TRAIL_NOT_REACHED_STATUSES

MODEL_REFUSAL = "model_refusal"
FORCED_REFUSAL = "forced_refusal"
BUDGET_STOPPED = "budget_stopped"
OK = "ok"
OTHER = "other"  # an error, a run left paused or running, or a refusal of unknown origin

RETRY_NOT_AFFORDABLE = "retry not affordable"  # graph.RETRY_NOT_AFFORDABLE; kept here so this module stays pure
FINISHED_STATUSES = ("ok", "no_reliable_answer", "budget_stopped")
# A repeat measures SC7b only if research ran to its end: a budget stop's
# search count shows where the ledger stopped it, not what the question needed.
SC7B_MEASURED_STATUSES = ("ok", "no_reliable_answer")
NOT_STARTED = "not_started"  # a planned run the batch never started (budget stop between runs)
FLO_REPEAT_NOTE = "identical question; graph route caps searches at 0 by design"


# ---------------------------------------------------------------------------
# Trail counting (SC7b evaluator row)
# ---------------------------------------------------------------------------


def reached_tavily(entry: Mapping[str, Any]) -> bool:
    """True when a trail entry's request reached Tavily.

    An unknown status counts as reached, so an unfamiliar failure can only
    raise a search count, never hide a search.
    """
    return entry.get("status") not in NOT_REACHED_STATUSES


def count_calls(trail: Iterable[Mapping[str, Any]], tool: str) -> int:
    """Calls of one tool in the trail that reached Tavily."""
    return sum(1 for e in trail if e.get("tool") == tool and reached_tavily(e))


def count_searches(trail: Iterable[Mapping[str, Any]]) -> int:
    return count_calls(trail, "search")


def count_fetches(trail: Iterable[Mapping[str, Any]]) -> int:
    return count_calls(trail, "fetch")


def not_reached_calls(trail: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Search and fetch calls that never reached Tavily, with their status, for the report."""
    return [
        {"tool": e.get("tool"), "query": e.get("query"), "status": e.get("status")}
        for e in trail
        if e.get("tool") in ("search", "fetch") and not reached_tavily(e)
    ]


def trail_of(record: Record) -> list[Mapping[str, Any]]:
    return list(record.get("search_trail") or [])


# ---------------------------------------------------------------------------
# SC11 live post condition
# ---------------------------------------------------------------------------


def sc11(record: Record) -> str:
    """"pass" when the run's ledger total stayed at or under its cap, else "miss"."""
    cap = record.get("run_cap_usd")
    cost = record.get("cost_usd")
    if cap is None or cost is None:
        return "miss"
    return "pass" if float(cost) <= float(cap) + 1e-9 else "miss"


# ---------------------------------------------------------------------------
# Reruns (decision 35)
# ---------------------------------------------------------------------------


def _builds(records: Sequence[Record]) -> list[str]:
    """Build IDs in the order their first record started."""
    first: dict[str, str] = {}
    for record in records:
        build = str(record.get("build_id") or "")
        started = str(record.get("started_at") or "")
        if build not in first or started < first[build]:
            first[build] = started
    return sorted(first, key=lambda b: (first[b], b))


def reported_records(records: Sequence[Record]) -> dict[str, Any]:
    """Split records into the reported set and the superseded ones (decision 35).

    One build: every record is reported (pooled). Several builds: the latest
    build alone is reported, and its records must name exactly one logged fix
    (at least one record names it; a pooled rerun of the fixed build carries
    none and inherits it); otherwise the result is marked invalid.
    """
    builds = _builds(records)
    problems: list[str] = []
    if len(builds) <= 1:
        return {"reported": list(records), "superseded": [], "pooled": len(records),
                "build_id": builds[0] if builds else None, "fix": None, "problems": problems}
    latest = builds[-1]
    reported = [r for r in records if str(r.get("build_id") or "") == latest]
    superseded = [r for r in records if str(r.get("build_id") or "") != latest]
    fixes = {str(r.get("fix") or "").strip() for r in reported} - {""}
    fix = next(iter(fixes)) if len(fixes) == 1 else None
    if not fix:
        problems.append(
            f"records come from {len(builds)} builds and the latest build's runs do not all name "
            "the same logged fix (decision 35); nothing can be reported until they do"
        )
    return {"reported": reported, "superseded": superseded, "pooled": len(reported),
            "build_id": latest, "fix": fix, "problems": problems}


# ---------------------------------------------------------------------------
# SC3b
# ---------------------------------------------------------------------------


def classify_sc3b(record: Record) -> str:
    """One run's SC3b outcome (decision 33)."""
    status = record.get("status")
    if status == "no_reliable_answer":
        origin = record.get("refusal_origin")
        if origin == "model":
            return MODEL_REFUSAL
        if origin == "forced":
            return FORCED_REFUSAL
        return OTHER
    if status == "budget_stopped":
        return BUDGET_STOPPED
    if status == "ok":
        return OK
    return OTHER


def retry_not_affordable(record: Record) -> bool:
    return record.get("status") == "budget_stopped" and record.get("stop_reason") == RETRY_NOT_AFFORDABLE


def _sc3b_row(record: Record) -> dict[str, Any]:
    trail = trail_of(record)
    return {
        "run_id": record.get("run_id"),
        "build_id": record.get("build_id"),
        "outcome": classify_sc3b(record),
        "passed": classify_sc3b(record) == MODEL_REFUSAL,
        "status": record.get("status"),
        "refusal_origin": record.get("refusal_origin"),
        "stop_reason": record.get("stop_reason"),
        "budget_stop_from_unaffordable_retry": retry_not_affordable(record),
        "searches": count_searches(trail),
        "fetches": count_fetches(trail),
        "not_reached": not_reached_calls(trail),
        "credits": record.get("credits"),
        "cost_usd": record.get("cost_usd"),
        "latency_s": record.get("latency_s"),
        "reached_maker_docs": record.get("reached_maker_docs"),
        "sc11": sc11(record),
    }


def score_sc3b(records: Sequence[Record], planned: int | None = None) -> dict[str, Any]:
    """The SC3b result: separate headline numbers, the pooled count, and a row per run.

    `planned` is the fewest runs a result needs (config.SC3B_RUNS); fewer
    reported runs never pass, however they ended.
    """
    planned = config.SC3B_RUNS if planned is None else planned
    split = reported_records(records)
    rows = [_sc3b_row(r) for r in split["reported"]]
    outcomes = [row["outcome"] for row in rows]
    runs = len(rows)
    model = outcomes.count(MODEL_REFUSAL)
    problems = list(split["problems"])
    if runs < planned:
        problems.append(f"only {runs} run(s) reported; SC3b needs at least {planned} (config.SC3B_RUNS)")
    headline = {
        "runs": runs,
        "model_refusals": model,
        "forced_refusals": outcomes.count(FORCED_REFUSAL),
        "budget_stops": outcomes.count(BUDGET_STOPPED),
        "budget_stops_from_unaffordable_retry": sum(1 for r in rows if r["budget_stop_from_unaffordable_retry"]),
        "ok": outcomes.count(OK),
        "other": outcomes.count(OTHER),
    }
    return {
        "criterion": "SC3b",
        "headline": headline,
        "result": f"{model} of {runs}",
        "planned": planned,
        "passed": runs >= planned and runs > 0 and model == runs and not problems,
        "rows": rows,
        "superseded_runs": [r.get("run_id") for r in split["superseded"]],
        "superseded_builds": build_summaries(split["superseded"]),
        "build_id": split["build_id"],
        "fix": split["fix"],
        "problems": problems,
        "sc11_misses": [row["run_id"] for row in rows if row["sc11"] != "pass"],
        "v1_checks": [{"run_id": r.get("run_id"), **r["v1_checks"]} for r in split["reported"] if r.get("v1_checks")],
    }


def format_sc3b(score: Mapping[str, Any]) -> list[str]:
    """The lines `advisor eval sc3b` prints after its runs."""
    h = score["headline"]
    lines = [
        f"SC3b: {score['result']} runs refused by the model; "
        f"{'PASS' if score['passed'] else 'MISS'} (decision 33: only model refusals pass)",
        f"  planned at least {score['planned']} runs; reported {h['runs']}",
        f"  model refusals {h['model_refusals']}, forced refusals {h['forced_refusals']}, "
        f"budget stops {h['budget_stops']} (from an unaffordable retry {h['budget_stops_from_unaffordable_retry']}), "
        f"ok {h['ok']}, other {h['other']}",
    ]
    for row in score["rows"]:
        lines.append(
            f"  {row['run_id']}: {row['outcome']}, status {row['status']}, origin {row['refusal_origin']}, "
            f"{row['searches']} searches, {row['fetches']} fetches, {row['credits']} credits, "
            f"{_usd(row['cost_usd'])}, {_secs(row['latency_s'])}, SC11 {row['sc11']}, "
            f"maker docs reached: {row['reached_maker_docs']}"
        )
    if score["superseded_runs"]:
        lines.append(f"  superseded (decision log and ledger only): {', '.join(map(str, score['superseded_runs']))}")
    lines += format_v1_checks(score.get("v1_checks") or [])
    for problem in score["problems"]:
        lines.append(f"  problem: {problem}")
    return lines


def build_summaries(records: Sequence[Record]) -> list[dict[str, Any]]:
    """Per build, oldest first: its runs and how they ended, for a rerun's preflight and summary."""
    out = []
    for build in _builds(records):
        mine = [r for r in records if str(r.get("build_id") or "") == build]
        statuses: dict[str, int] = {}
        for record in mine:
            key = str(record.get("status"))
            statuses[key] = statuses.get(key, 0) + 1
        out.append({"build_id": build, "runs": len(mine), "statuses": dict(sorted(statuses.items())),
                    "model_refusals": sum(1 for r in mine if classify_sc3b(r) == MODEL_REFUSAL),
                    "fix": next((str(r.get("fix")) for r in mine if r.get("fix")), None)})
    return out


def build_summary_text(summary: Mapping[str, Any]) -> str:
    statuses = ", ".join(f"{k} {v}" for k, v in summary["statuses"].items())
    return (f"build {summary['build_id']}: {summary['runs']} run(s) ({statuses}); "
            f"model refusals {summary['model_refusals']}")


# ---------------------------------------------------------------------------
# SC7b
# ---------------------------------------------------------------------------


def _order(record: Record) -> tuple[str, str]:
    return str(record.get("started_at") or ""), str(record.get("run_id") or "")


def _first_counts(first_lookups: Iterable[Record]) -> dict[str, dict[str, Any]]:
    """The earliest first lookup per model key (ties by run ID), with its search count from its trail.

    Every later run of that model found the graph already holding it, so the
    earliest is the one each repeat is compared with (decision 34), as
    eval_sc7b.find_first_lookups picks.
    """
    earliest: dict[str, Record] = {}
    for record in first_lookups:
        key = str(record.get("model_key") or "")
        if key and (key not in earliest or _order(record) < _order(earliest[key])):
            earliest[key] = record
    return {
        key: {"run_id": r.get("run_id"), "searches": count_searches(trail_of(r)), "route": r.get("route")}
        for key, r in earliest.items()
    }


SECOND_FIRST_NOTE = "a later first lookup of a model the graph already covered; scored as a repeat"


def score_sc7b(records: Sequence[Record], first_lookups: Sequence[Record] = (),
               planned_repeats: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The SC7b result: every repeat with its route, search count and the same model's first lookup count.

    `records` are this command's run records (roles "first" and "repeat");
    `first_lookups` are first lookups run elsewhere (the Phase 5 FLO run).
    `planned_repeats` (label and model_key of each repeat eval_sc7b plans)
    makes a planned repeat with no finished run a miss; None skips that check.
    First lookups of superseded builds still count when picking the
    earliest: their edges stay in the graph.
    """
    split = reported_records(records)
    reported = split["reported"]
    firsts = _first_counts([*first_lookups, *(r for r in records if r.get("role") == "first")])
    chosen = {f["run_id"] for f in firsts.values()}
    limit = config.SC7B_REPEAT_SEARCH_MAX
    problems = list(split["problems"])
    rows = []
    for record in reported:
        role = record.get("role")
        if role == "first" and record.get("run_id") in chosen:
            if record.get("status") not in FINISHED_STATUSES:
                problems.append(f"first lookup {record.get('label') or record.get('run_id')} "
                                f"({record.get('run_id')}) did not finish: status {record.get('status')}")
            continue
        if role not in ("repeat", "first"):
            if record.get("status") not in FINISHED_STATUSES:
                problems.append(f"run {record.get('label') or record.get('run_id')} ({record.get('run_id')}) "
                                f"did not finish: status {record.get('status')}")
            continue
        trail = trail_of(record)
        searches = count_searches(trail)
        first = firsts.get(str(record.get("model_key") or ""))
        measured = record.get("status") in SC7B_MEASURED_STATUSES
        rows.append({
            "run_id": record.get("run_id"),
            "case": record.get("case"),
            "label": record.get("label"),
            "note": record.get("note") if role == "repeat" else SECOND_FIRST_NOTE,
            "model_key": record.get("model_key"),
            "route": list(record.get("route") or []),
            "status": record.get("status"),
            "stop_reason": record.get("stop_reason"),
            "searches": searches,
            "fetches": count_fetches(trail),
            "credits": record.get("credits"),
            "not_reached": not_reached_calls(trail),
            "first_lookup_run_id": first["run_id"] if first else None,
            "first_lookup_searches": first["searches"] if first else None,
            "measured": measured,
            "passed": measured and searches <= limit,
            "cost_usd": record.get("cost_usd"),
            "latency_s": record.get("latency_s"),
            "sc11": sc11(record),
        })
    for plan in planned_repeats or ():
        mine = [row for row in rows if row["label"] == plan.get("label")
                and row["model_key"] == plan.get("model_key")]
        if not any(row["measured"] for row in mine):
            problems.append(f"planned repeat {plan.get('label')!r} has no finished run "
                            f"({', '.join(str(r['status']) for r in mine) or 'no run record'})")
    counts = [row["searches"] for row in rows]
    return {
        "criterion": "SC7b",
        "limit": limit,
        "passed": bool(rows) and all(row["passed"] for row in rows) and not problems,
        "rows": rows,
        "repeat_search_range": [min(counts), max(counts)] if counts else None,
        "first_lookups": firsts,
        "superseded_runs": [r.get("run_id") for r in split["superseded"]],
        "superseded_builds": build_summaries(split["superseded"]),
        "build_id": split["build_id"],
        "fix": split["fix"],
        "problems": problems,
        "sc11_misses": [r.get("run_id") for r in reported if sc11(r) != "pass"],
        "v1_checks": [{"run_id": r.get("run_id"), **r["v1_checks"]} for r in reported if r.get("v1_checks")],
    }


def format_sc7b(score: Mapping[str, Any]) -> list[str]:
    """The lines `advisor eval sc7b` prints after its runs."""
    span = score["repeat_search_range"]
    span_text = "no repeats" if span is None else (str(span[0]) if span[0] == span[1] else f"{span[0]} to {span[1]}")
    lines = [
        f"SC7b: {'PASS' if score['passed'] else 'MISS'}; every repeat must use {score['limit']} or fewer searches; "
        f"repeats used {span_text}",
    ]
    for row in score["rows"]:
        first = row["first_lookup_searches"]
        first_text = "no first lookup found" if first is None else f"first lookup {row['first_lookup_run_id']} used {first}"
        note = f" ({row['note']})" if row["note"] else ""
        stop = f" (stop reason {row['stop_reason']}; not a measurement)" if row.get("stop_reason") else ""
        lines.append(
            f"  {row['label']}{note}: route {'+'.join(row['route']) or 'none'}, status {row['status']}{stop}, "
            f"{row['searches']} searches, {row['fetches']} fetches, {row['credits']} credits; {first_text}; "
            f"{_usd(row['cost_usd'])}, SC11 {row['sc11']}"
        )
        for call in row["not_reached"]:
            lines.append(f"    not sent: {call['tool']} {call['query']!r} ({call['status']})")
    if score["superseded_runs"]:
        lines.append(f"  superseded (decision log and ledger only): {', '.join(map(str, score['superseded_runs']))}")
    lines += format_v1_checks(score.get("v1_checks") or [])
    for problem in score["problems"]:
        lines.append(f"  problem: {problem}")
    return lines


# ---------------------------------------------------------------------------
# v1 live case checks (PLAN 6.5)
# ---------------------------------------------------------------------------

PLATE_FIELDS_E2 = ("model", "serial", "manufacture_date")
V1_CASE_INPUTS = {
    # case: (symptom, the model key its identity carries)
    "B1": ("not heating", "optima880"),
    "B2": ("panel shows flo", "optima880"),
    "B3": ("ac not cooling upstairs", "xr164ttr6036"),
    "B4": ("not heating", "zx9000pro"),
}


def _key(text: Any) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def v1_cases_for_run(state: Mapping[str, Any], plate_cases: Mapping[str, str]) -> list[str]:
    """Which v1 live cases a run is: a plate by its sha256 (E1, E2), and a B case by symptom and model."""
    cases = []
    photo = state.get("photo")
    sha = photo.get("sha256") if isinstance(photo, Mapping) else None
    if sha and sha in plate_cases:
        cases.append(plate_cases[sha])
    symptom = " ".join(str(state.get("symptom") or "").lower().split())
    model = _key((state.get("identity") or {}).get("model"))
    for case, (want_symptom, want_model) in V1_CASE_INPUTS.items():
        if symptom == want_symptom and model == want_model:
            cases.append(case)
    return cases


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def _brief(state: Mapping[str, Any]) -> Mapping[str, Any]:
    brief = state.get("brief")
    return brief if isinstance(brief, Mapping) else {}


def _candidates(brief: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [c for c in brief.get("candidates") or [] if isinstance(c, Mapping)]


def _cited(brief: Mapping[str, Any], candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    sources = brief.get("sources") or []
    index = candidate.get("source_index")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(sources):
        return sources[index]
    return None


def _e1(state: Mapping[str, Any], raw: Mapping[str, Any] | None) -> tuple[list, list]:
    x = state.get("extraction") or {}
    model, maker, serial = x.get("model"), x.get("manufacturer"), x.get("serial")
    asserted = [
        _check("model matches Optima 880", "optima880" in _key(model), f"read {model!r}"),
        _check("manufacturer matches Sundance", "sundance" in _key(maker), f"read {maker!r}"),
        _check(f"serial equals {config.E1_PLATE_SERIAL}", serial == config.E1_PLATE_SERIAL, f"read {serial!r}"),
    ]
    reported = [_check("raw model output captured", raw is not None,
                       "" if raw is None else f"raw model {raw.get('model')!r}, serial {raw.get('serial')!r}")]
    return asserted, reported


def _e2(state: Mapping[str, Any], raw: Mapping[str, Any] | None) -> tuple[list, list]:
    final = state.get("extraction") or {}
    confidence = (raw or {}).get("confidence") or {}
    asserted = []
    for name in PLATE_FIELDS_E2:
        raw_value = (raw or {}).get(name)
        ok = raw is not None and raw_value is None and confidence.get(name) == "unreadable" and final.get(name) is None
        detail = ("raw model output not captured" if raw is None else
                  f"raw {raw_value!r} ({confidence.get(name)}), final {final.get(name)!r}")
        asserted.append(_check(f"{name} null and unreadable in the raw model output and null in the final",
                               ok, detail))
    return asserted, []


def _b1(state: Mapping[str, Any]) -> tuple[list, list]:
    brief = _brief(state)
    codes = [str(c.get("code") or "").upper() for c in _candidates(brief)]
    tiers = {(_cited(brief, c) or {}).get("tier") for c in _candidates(brief)}
    steps = " | ".join(f"{s.get('step', '')} {s.get('detail', '')}".lower()
                       for s in brief.get("try_first") or [] if isinstance(s, Mapping))
    asserted = [
        _check("status ok", state.get("status") == "ok", _status_detail(state)),
        _check("a FLO family candidate", any("FLO" in c for c in codes), f"codes {codes}"),
        _check("a candidate cites a manufacturer or dealer source", bool({"manufacturer", "dealer"} & tiers),
               f"cited tiers {sorted(str(t) for t in tiers)}"),
    ]
    reported = [_check("try_first mentions a filter check", "filter" in steps),
                _check("try_first mentions a water level check", "water level" in steps)]
    return asserted, reported


def _b2(state: Mapping[str, Any]) -> tuple[list, list]:
    brief = _brief(state)
    confirmed = [c for c in _candidates(brief) if c.get("confirmed") is True]
    observed = brief.get("observed_code")
    return [
        _check("status ok", state.get("status") == "ok", _status_detail(state)),
        _check("exactly 1 confirmed candidate", len(confirmed) == 1,
               f"{len(confirmed)} of {len(_candidates(brief))} candidates confirmed"),
        _check("observed_code set", bool(observed), f"observed_code {observed!r}"),
    ], []


def _b3(state: Mapping[str, Any]) -> tuple[list, list]:
    brief = _brief(state)
    status = state.get("status")
    if status == "ok":
        missing = [c.get("code") or c.get("documented_meaning") for c in _candidates(brief) if _cited(brief, c) is None]
        return [
            _check("sources non empty", bool(brief.get("sources")), f"{len(brief.get('sources') or [])} sources"),
            _check("every candidate's source index resolves", not missing, f"unresolved {missing}"),
        ], []
    if status == "no_reliable_answer":
        return [_check("explanation block present", bool(brief.get("no_reliable_answer")))], []
    return [], [_check("a third outcome, reported separately (PLAN 6.5)", False, _status_detail(state))]


def _b4(state: Mapping[str, Any]) -> tuple[list, list]:
    brief = _brief(state)
    searched = (brief.get("no_reliable_answer") or {}).get("searched") or []
    return [
        _check("status no_reliable_answer", state.get("status") == "no_reliable_answer", _status_detail(state)),
        _check("no candidates", not _candidates(brief), f"{len(_candidates(brief))} candidates"),
        _check("searched non empty", bool(searched), f"{len(searched)} searched"),
    ], []


def _status_detail(state: Mapping[str, Any]) -> str:
    stop = state.get("stop_reason")
    return f"status {state.get('status')}" + (f", stop reason {stop}" if stop else "")


_V1_CHECKS = {"E1": _e1, "E2": _e2, "B1": _b1, "B2": _b2, "B3": _b3, "B4": _b4}


def v1_case_checks(case: str, state: Mapping[str, Any], raw_extraction: Mapping[str, Any] | None = None
                   ) -> dict[str, Any]:
    """The PLAN 6.5 checks of one v1 case on a run's final state: asserted and reported.

    `raw_extraction` is read_plate's reply before code nulls unreadable
    fields (E1 and E2 read it). `passed` is None when nothing is asserted.
    """
    fn = _V1_CHECKS[case]
    asserted, reported = fn(state, raw_extraction) if case in ("E1", "E2") else fn(state)
    return {"case": case, "asserted": asserted, "reported": reported,
            "passed": all(c["passed"] for c in asserted) if asserted else None}


def format_v1_checks(checks: Iterable[Mapping[str, Any]]) -> list[str]:
    """One line per case, then one per failed or reported check."""
    lines = []
    for entry in checks:
        verdict = {True: "pass", False: "MISS", None: "reported only"}[entry.get("passed")]
        who = f" {entry['run_id']}" if entry.get("run_id") else ""
        lines.append(f"  v1 case {entry['case']}{who}: {verdict}")
        for check in entry.get("asserted") or []:
            if not check["passed"]:
                lines.append(f"    failed: {check['check']} ({check['detail']})")
        for check in entry.get("reported") or []:
            lines.append(f"    reported: {check['check']}: {'yes' if check['passed'] else 'no'}"
                         + (f" ({check['detail']})" if check.get("detail") else ""))
    return lines


# ---------------------------------------------------------------------------
# Plate runs (PLAN section 9 "6, plates"; 6.5 cases E1 and E2)
# ---------------------------------------------------------------------------


def score_plates(records: Sequence[Record], planned_cases: Sequence[str] = ("E1", "E2")) -> dict[str, Any]:
    """The plate runs: each one's v1 check, cost and SC11. read_plate only; each run stays paused."""
    split = reported_records(records)
    problems = list(split["problems"])
    rows = []
    for record in split["reported"]:
        checks = record.get("v1_checks") or {}
        rows.append({"run_id": record.get("run_id"), "case": record.get("case"), "status": record.get("status"),
                     "passed": checks.get("passed"), "cost_usd": record.get("cost_usd"), "sc11": sc11(record),
                     "error": record.get("error")})
        if record.get("status") != "paused":
            problems.append(f"plate run {record.get('case')} ({record.get('run_id')}) did not reach the "
                            f"confirmation pause: status {record.get('status')}")
    for case in planned_cases:
        if not any(r["case"] == case for r in rows):
            problems.append(f"planned plate run {case} has no run record")
    return {
        "criterion": "plates",
        "passed": bool(rows) and all(r["passed"] is True for r in rows) and not problems,
        "rows": rows,
        "superseded_runs": [r.get("run_id") for r in split["superseded"]],
        "superseded_builds": build_summaries(split["superseded"]),
        "build_id": split["build_id"],
        "fix": split["fix"],
        "problems": problems,
        "sc11_misses": [r["run_id"] for r in rows if r["sc11"] != "pass"],
        "v1_checks": [{"run_id": r.get("run_id"), **r["v1_checks"]} for r in split["reported"] if r.get("v1_checks")],
    }


def format_plates(score: Mapping[str, Any]) -> list[str]:
    """The lines `advisor eval plates` prints after its runs."""
    lines = [f"plates: {'PASS' if score['passed'] else 'MISS'} (v1 cases E1 and E2, read_plate on Haiku)"]
    for row in score["rows"]:
        lines.append(f"  {row['case']} {row['run_id']}: status {row['status']}, {_usd(row['cost_usd'])}, "
                     f"SC11 {row['sc11']}")
    lines += format_v1_checks(score.get("v1_checks") or [])
    for problem in score["problems"]:
        lines.append(f"  problem: {problem}")
    return lines


def _usd(amount: Any) -> str:
    return "cost unknown" if amount is None else f"{float(amount):.4f} USD"


def _secs(seconds: Any) -> str:
    return "latency unknown" if seconds is None else f"{float(seconds):.1f} s"
