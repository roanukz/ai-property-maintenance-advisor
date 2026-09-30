"""SC12a files, scoring, the lock and the held out rule (offline; no call is made here).

Under data/eval/sc12a/ (config.EVAL_DIR / "sc12a"; page text stays there):
- items.json: every item with its text, pool, document, family, half and
  writer flag, each family's half and size, the sampling it was built under,
  and the sha256 of the maker_documents.json it was built with;
- reader_items.json: what the three readers get, each unlabeled item as only
  its id, appliance, step and detail (data/eval/sc12b/ holds SC12b's);
- labels_raw.json: every reader's vote by item id;
- replies/<question hash>.jsonl: Jev's recorded replies, one line per call;
- lock.json: Gate 2, written once before any held out number exists;
- heldout_scores.json: every held out scoring, with the reason for any after the first.

Committed beside this module (IDs, hashes, halves and labels only):
item_ids.json, labels.json and wordings.json (the question wordings tried).

The tune half is scored as often as needed; thresholds come from it alone.
The held out half is scored only against a lock whose hashes match the
current config and prompt, and only once unless a reason is given, which is
recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent import config
from agent.rules.step_text import word_rule
from agent.safety_eval import labels as labels_mod
from agent.safety_eval import pools, stats

COMMITTED_DIR = Path(__file__).resolve().parent
ITEM_IDS = "item_ids.json"
LABELS = "labels.json"
WORDINGS = "wordings.json"


class EvalRefusal(Exception):
    """A rule of the SC12a protocol refused the command before anything was scored or written."""


def eval_dir() -> Path:
    return config.EVAL_DIR / "sc12a"


def items_path() -> Path:
    return eval_dir() / "items.json"


def labels_raw_path() -> Path:
    return eval_dir() / "labels_raw.json"


def replies_dir() -> Path:
    return eval_dir() / "replies"


def sc12b_items_path() -> Path:
    return config.EVAL_DIR / "sc12b" / "items.json"


def lock_path() -> Path:
    return eval_dir() / "lock.json"


def heldout_path() -> Path:
    return eval_dir() / "heldout_scores.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


def write_items(result: pools.BuildResult, *, maker_documents_sha256: str | None = None,
                replaced: Mapping[str, Any] | None = None) -> tuple[Path, Path]:
    """items.json under data/eval/sc12a (with text) and the committed item_ids.json (without).

    Both record the sha256 of the maker document list the set was built with
    (decision 53), which --extend and the lock check, the sampling it was
    built under, each item's family (decision 58), each family's stratum
    (decision 59) and the near duplicate counts across the halves.
    """
    doc = {"built_at": _now(), "sampling": pools.SAMPLING, "exhausted": result.exhausted,
           "forced_tune": result.forced_tune, "forced_families": result.forced_families,
           "families": result.families, "near_duplicates": result.near_duplicates,
           "maker_documents_sha256": maker_documents_sha256, "items": result.items}
    if replaced:
        doc["replaced"] = dict(replaced)
    ids = {
        "note": "SC12a item IDs (sha256 of the normalized step and detail); text stays in data/eval/sc12a/items.json",
        "sampling": pools.SAMPLING, "exhausted": result.exhausted, "forced_tune": result.forced_tune,
        "forced_families": result.forced_families, "families": result.families,
        "near_duplicates": result.near_duplicates, "maker_documents_sha256": maker_documents_sha256,
        "items": [{"id": i["id"], "pool": i["pool"], "document_sha256": i["document_sha256"],
                   "family": i["family"], "half": i["half"]} for i in result.items],
    }
    return write_json(items_path(), doc), write_json(COMMITTED_DIR / ITEM_IDS, ids)


def load_items() -> dict[str, Any]:
    if not items_path().is_file():
        raise EvalRefusal(f"no labeled set at {items_path()}: run advisor safety-set build first.")
    return read_json(items_path())


def seen_by_anyone() -> list[Path]:
    """Every file that shows a reader or a scorer has had the set: the packet, votes, labels, replies, lock."""
    found = [path for path in (reader_packet_path(), labels_raw_path(), COMMITTED_DIR / LABELS, lock_path(),
                               heldout_path()) if path.exists()]
    if replies_dir().is_dir() and any(replies_dir().iterdir()):
        found.append(replies_dir())
    return found


def build_set(roots: pools.Roots, documents: pools.DocumentList) -> pools.BuildResult:
    """`advisor safety-set build`: the set is built once under a sampling, since a rebuild could reorder it.

    A set built under an earlier sampling (decisions 58 and 59 replaced decision 53's
    split before any labeling) is replaced only while no reader or scorer has
    had it; its size and build time are recorded in the new items.json.
    """
    replaced = None
    if items_path().exists():
        old = load_items()
        sampling = old.get("sampling") or "decision 53"
        if sampling == pools.SAMPLING:
            raise EvalRefusal(f"{items_path()} exists; the set is built once. Use --extend for the stopping rule.")
        seen = seen_by_anyone()
        if seen:
            raise EvalRefusal(f"{items_path()} was built under {sampling} sampling, and a reader or scorer has had it "
                              f"({', '.join(str(p) for p in seen)}); it is never rebuilt. Log the change and "
                              "start over by hand.")
        replaced = {"sampling": sampling, "built_at": old.get("built_at"), "items": len(old.get("items") or [])}
    result = pools.build_items(roots, documents.documents, excluded=documents.excluded,
                               also_in_family=documents.also_in_family)
    write_items(result, maker_documents_sha256=documents.sha256, replaced=replaced)
    return result


def require_same_maker_documents(doc: Mapping[str, Any], current_sha256: str) -> None:
    """The maker document list must be the one the set was built with (decision 53)."""
    built = doc.get("maker_documents_sha256")
    if built != current_sha256:
        raise EvalRefusal(f"{pools.MAKER_DOCUMENTS_PATH.name} changed since the set was built (sha256 {built} then, "
                          f"{current_sha256} now); D's order depends on it. Restore it, or log the change, delete "
                          f"{items_path()} and build and label the set again.")


def extend_set(roots: pools.Roots, documents: pools.DocumentList) -> pools.BuildResult:
    """`advisor safety-set build --extend`: the stopping rule's next config.SC12A_EXTEND_BY D items."""
    if lock_path().exists():
        raise EvalRefusal("the set is locked (Gate 2); it cannot grow after the lock.")
    doc = load_items()
    require_same_maker_documents(doc, documents.sha256)
    if doc.get("sampling") != pools.SAMPLING:
        raise EvalRefusal(f"{items_path()} was built under {doc.get('sampling') or 'decision 53'} sampling, not "
                          f"{pools.SAMPLING}; it cannot be extended under another.")
    status = stopping_status(doc, load_labels())
    if status["unlabeled"]:
        raise EvalRefusal(f"{len(status['unlabeled'])} items have no label yet; label them before extending.")
    if status["satisfied"]:
        why = "the pages ran out" if status["exhausted"] else (
            f"the held out half has {status['heldout_positives']} in scope positives")
        raise EvalRefusal(f"the stopping rule is met ({why}); nothing is added.")
    result = pools.extend_items(doc["items"], roots, documents.documents, forced_tune=doc.get("forced_tune") or [],
                                forced_families=doc.get("forced_families") or [], excluded=documents.excluded,
                                also_in_family=documents.also_in_family)
    write_items(result, maker_documents_sha256=documents.sha256, replaced=doc.get("replaced"))
    return result


# ---------------------------------------------------------------------------
# What the readers get
# ---------------------------------------------------------------------------

READER_KEYS = ("id", "appliance", "step", "detail")


def reader_packet_path(sc12b: bool = False) -> Path:
    return (config.EVAL_DIR / "sc12b" if sc12b else eval_dir()) / "reader_items.json"


def write_reader_packet(items: Iterable[Mapping[str, Any]], labels: Mapping[str, Any], *,
                        sc12b: bool = False) -> tuple[Path, int]:
    """The items the three readers label: each unlabeled item as exactly READER_KEYS, sorted by id.

    No pool, source, document, half, writer flag or any other layer's output
    goes to a reader (the brief: no reader sees any layer's output). Items
    that already have a label are left out, so a reader never votes twice.
    """
    packet = sorted(({key: item.get(key) or "" for key in READER_KEYS} for item in items
                     if item["id"] not in labels and not item.get("reused_label")), key=lambda i: i["id"])
    path = write_json(reader_packet_path(sc12b), {"items": packet})
    return path, len(packet)


def write_sc12b_items(items: list[dict[str, Any]]) -> tuple[Path, int]:
    """data/eval/sc12b/items.json; a step SC12a already labeled keeps that label ("reused_label")."""
    labels = load_labels()
    for item in items:
        item["reused_label"] = item["id"] in labels
    path = write_json(sc12b_items_path(), {"items": items})
    return path, sum(1 for item in items if item["reused_label"])


def counts_lines(doc: Mapping[str, Any], labels: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Gate 1: counts per pool and half, positives by hazard, and the unanimity rate."""
    items = doc["items"]
    by = Counter((i["pool"], i["half"]) for i in items)
    lines = [f"items: {len(items)} (limit reached: {'no, the pages ran out' if doc.get('exhausted') else 'yes'})"]
    for pool in (pools.POOL_G, pools.POOL_D):
        lines.append(f"  pool {pool}: tune {by[(pool, pools.TUNE)]}, held out {by[(pool, pools.HELDOUT)]}")
    families = doc.get("families") or {}
    if families:
        per = {h: [f for f in families.values() if f["half"] == h] for h in pools.HALVES}
        lines.append(f"families: tune {len(per[pools.TUNE])} ({sum(f['items'] for f in per[pools.TUNE])} items), "
                     f"held out {len(per[pools.HELDOUT])} ({sum(f['items'] for f in per[pools.HELDOUT])} items); "
                     f"largest family {max(f['items'] for f in families.values())} items")
    appliances = Counter((i["half"], i.get("appliance") or pools.UNKNOWN_APPLIANCE) for i in items)
    for half in pools.HALVES:
        name = "held out" if half == pools.HELDOUT else half
        detail = ", ".join(f"{a} {n}" for (h, a), n in sorted(appliances.items()) if h == half) or "none"
        lines.append(f"  {name} by appliance: {detail}")
    near = doc.get("near_duplicates") or {}
    if near:
        across = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in near["across_halves"].items())
        within = near["within_a_half"]
        lines.append(f"near duplicate pairs across the halves (reported, not removed): {across}")
        lines.append(f"  within one half, identical with digits and punctuation stripped: "
                     f"{within['identical_stripped_groups']} groups ({within['items']} items)")
    lines.append(f"forced to tune (worked examples): {', '.join(doc.get('forced_tune') or []) or 'none'}")
    if doc.get("forced_families"):
        lines.append(f"  their families: {', '.join(doc['forced_families'])}")
    if doc.get("replaced"):
        old = doc["replaced"]
        lines.append(f"replaced an unlabeled set of {old.get('items')} items built {old.get('built_at')} under "
                     f"{old.get('sampling')} sampling; no reader or scorer had it")
    labeled = [i for i in items if i["id"] in labels]
    lines.append(f"labeled: {len(labeled)} of {len(items)}")
    if labeled:
        for half in pools.HALVES:
            hazards = Counter(labels[i["id"]]["hazard"] for i in labeled
                              if i["half"] == half and labels[i["id"]]["positive"])
            positives = sum(hazards.values())
            detail = ", ".join(f"{h} {n}" for h, n in sorted(hazards.items())) or "none"
            name = "held out" if half == pools.HELDOUT else half
            lines.append(f"  {name} in scope positives: {positives} ({detail})")
        rate = labels_mod.unanimity({i["id"]: labels[i["id"]] for i in labeled})
        lines.append(f"unanimity: {rate['unanimous']} of {rate['items']} on the safety answer "
                     f"({rate['rate']:.3f}); {rate['unanimous_with_hazard']} with the hazard too "
                     f"({rate['rate_with_hazard']:.3f})")
    status = stopping_status(doc, labels)
    lines.append(f"stopping rule: held out in scope positives {status['heldout_positives']} of "
                 f"{config.SC12A_MIN_HELDOUT_POSITIVES}; {'met' if status['satisfied'] else 'not met'}"
                 f"{' (pages ran out)' if status['exhausted'] else ''}")
    return lines


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def import_labels(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """`advisor safety-set labels`: merge the readers' files, then write labels.json by id."""
    doc = load_items()
    known = [i["id"] for i in doc["items"]]
    if sc12b_items_path().is_file():
        known += [i["id"] for i in read_json(sc12b_items_path())["items"]]
    raw = read_json(labels_raw_path()) if labels_raw_path().is_file() else {}
    for path in paths:
        try:
            payload = read_json(Path(path))
        except (OSError, ValueError) as exc:
            raise EvalRefusal(f"cannot read labels file {path}: {exc}") from None
        try:
            raw = labels_mod.merge_votes(raw, payload, known)
        except labels_mod.LabelError as exc:
            raise EvalRefusal(str(exc)) from None
    labels, incomplete = labels_mod.labels_by_id(raw)
    write_json(labels_raw_path(), raw)
    write_json(COMMITTED_DIR / LABELS, {
        "note": "SC12a majority labels by item ID from three blind model readers (PROTOCOL.md)",
        "readers": sorted({r for votes in raw.values() for r in votes}),
        "unanimity": labels_mod.unanimity(labels),
        "incomplete": sorted(incomplete),
        "labels": labels,
    })
    return labels


def load_labels() -> dict[str, dict[str, Any]]:
    path = COMMITTED_DIR / LABELS
    return dict(read_json(path).get("labels") or {}) if path.is_file() else {}


def stopping_status(doc: Mapping[str, Any], labels: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The stopping rule: 30 in scope positives in the held out half, or the pages ran out."""
    items = doc["items"]
    unlabeled = [i["id"] for i in items if i["id"] not in labels]
    positives = sum(1 for i in items if i["half"] == pools.HELDOUT and labels.get(i["id"], {}).get("positive"))
    exhausted = bool(doc.get("exhausted"))
    enough = positives >= config.SC12A_MIN_HELDOUT_POSITIVES
    return {"heldout_positives": positives, "exhausted": exhausted, "unlabeled": unlabeled,
            "satisfied": not unlabeled and (enough or exhausted)}


def require_scorable(doc: Mapping[str, Any], labels: Mapping[str, Mapping[str, Any]]) -> None:
    """Every item labeled and the stopping rule met before any layer is scored."""
    status = stopping_status(doc, labels)
    if status["unlabeled"]:
        raise EvalRefusal(f"{len(status['unlabeled'])} items have no majority label yet; import every "
                          "reader's labels (advisor safety-set labels) before any layer is scored.")
    if not status["satisfied"]:
        raise EvalRefusal(f"the held out half has {status['heldout_positives']} in scope positives, fewer than "
                          f"{config.SC12A_MIN_HELDOUT_POSITIVES}: run advisor safety-set build --extend and "
                          "label the new items before any layer is scored.")


# ---------------------------------------------------------------------------
# Wordings and replies
# ---------------------------------------------------------------------------


def _current_question() -> dict[str, Any]:
    from agent.prompts import SAFETY_STEP_NOUL

    return {"instructions": SAFETY_STEP_NOUL["instructions"], "criteria": SAFETY_STEP_NOUL.get("criteria")}


def wordings() -> list[dict[str, Any]]:
    """The wordings tried, numbered from 1; the prompt's current wording is added if it is not listed."""
    path = COMMITTED_DIR / WORDINGS
    listed = list(read_json(path).get("wordings") or []) if path.is_file() else []
    out = [{"n": int(w["n"]), "instructions": w["instructions"], "criteria": w.get("criteria")} for w in listed]
    current = _current_question()
    if not any(w["instructions"] == current["instructions"] and w["criteria"] == current["criteria"] for w in out):
        out.append({"n": len(out) + 1, **current})
    return out


def question_hash_of(wording: Mapping[str, Any]) -> str:
    from agent.safety_judge import question_hash

    return question_hash(wording["instructions"], wording.get("criteria"))


def pick_wording(n: int | None) -> dict[str, Any]:
    """Wording n, or the prompt's current wording; never one past config.SC12A_MAX_WORDINGS."""
    options = wordings()
    if n is None:
        current = _current_question()
        chosen = next(w for w in options if w["instructions"] == current["instructions"]
                      and w["criteria"] == current["criteria"])
    else:
        chosen = next((w for w in options if w["n"] == n), None)
        if chosen is None:
            raise EvalRefusal(f"no wording {n}; wordings are numbered 1 to {len(options)}.")
    if chosen["n"] > config.SC12A_MAX_WORDINGS:
        raise EvalRefusal(f"wording {chosen['n']} is past the limit of {config.SC12A_MAX_WORDINGS} wordings.")
    return chosen


def reply_path(qhash: str) -> Path:
    return replies_dir() / f"{qhash}.jsonl"


def append_reply(qhash: str, entry: Mapping[str, Any]) -> None:
    path = reply_path(qhash)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical(dict(entry)) + "\n")


def load_replies(qhash: str) -> dict[str, dict[str, Any]]:
    """The recorded answer per item: its latest reply with a probability and no error.

    A reply under another question hash or model string is not an answer to
    this question (the lock pins both).
    """
    path = reply_path(qhash)
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if (entry.get("error") is None and entry.get("noul") is not None and entry.get("question_hash") == qhash
                and entry.get("model") == config.JEV_MODEL):
            out[str(entry["item_id"])] = entry
    return out


def pending(items: Sequence[Mapping[str, Any]], qhash: str, half: str) -> list[dict[str, Any]]:
    """Items of one half with no recorded answer for this question yet."""
    done = load_replies(qhash)
    return [dict(i) for i in items if i["half"] == half and i["id"] not in done]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def scored_items(items: Sequence[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]],
                 replies: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What every arm reads per item: label, hazard, writer flag, both word rules and Jev's probability."""
    out = []
    for item in items:
        label = labels[item["id"]]
        reply = replies.get(item["id"])
        out.append({
            "id": item["id"], "pool": item["pool"], "half": item["half"],
            "positive": bool(label["positive"]), "hazard": label["hazard"],
            "writer": item.get("writer_flag") is True,
            "word_published": word_rule(item["step"], item["detail"], config.SAFETY_WORDS_PUBLISHED),
            "word_tuned": word_rule(item["step"], item["detail"], config.SAFETY_WORDS),
            "noul": None if reply is None else float(reply["noul"]),
        })
    return out


def production(layer):
    """A layer combined raise only with the writer's flag, as it would run in production."""
    return lambda i, t=None: i["writer"] or layer(i, t)


def _word(i, t=None) -> bool:
    return i["word_tuned"]


def _jev(i, t=None) -> bool:
    return stats.jev_raises(i["noul"], t)


def _word_jev(i, t=None) -> bool:
    return i["word_tuned"] or stats.jev_raises(i["noul"], t)


CONFIG_LAYERS = {stats.WORD: _word, stats.JEV: _jev, stats.WORD_JEV: _word_jev}


def tune_thresholds(tune: Sequence[Mapping[str, Any]]) -> dict[str, stats.ThresholdChoice]:
    """Per configuration with a Jev layer, the brief's threshold rule on the tune half, in production form."""
    return {name: stats.select_threshold(tune, production(CONFIG_LAYERS[name])) for name in stats.THRESHOLD_CONFIGS}


def threshold_values(choices: Mapping[str, stats.ThresholdChoice]) -> dict[str, dict[str, Any]]:
    return {name: {"threshold": c.threshold, "rule": c.rule} for name, c in choices.items()}


WRITER_ARM = "writer (generated steps only)"
WRITER_ARM_ALL_G = "writer (generated steps only, all of pool G)"
WRITER_ALL_G_NOTE = ("no held out item is a generated step (every G family holds a worked example and went to "
                     "tune), so the writer's own flags are scored on all of pool G; the writer has no tuned "
                     "parameter, so the tune half is a fair place to measure it")
NO_WRITER_FLAG_NOTE = ("no item scored here carries a writer flag, so each candidate below equals its layer "
                       "alone")


def arms(scored: Sequence[Mapping[str, Any]], thresholds: Mapping[str, Mapping[str, Any]],
         writer_items: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Success criterion 3's arms (each layer alone) and the choice rule's candidates (with the writer).

    The writer's own flags are scored on the generated steps of `scored`, or
    on `writer_items` when given (all of pool G, when the held out half has no
    generated step), with the reason in "notes".
    """
    t_jev = thresholds[stats.JEV]["threshold"]
    t_wj = thresholds[stats.WORD_JEV]["threshold"]
    generated = [i for i in scored if i["pool"] == pools.POOL_G]
    writer_arm, notes = WRITER_ARM, []
    if writer_items is not None:
        generated, writer_arm = list(writer_items), WRITER_ARM_ALL_G
        notes.append(WRITER_ALL_G_NOTE)
    if not any(i["writer"] for i in scored):
        notes.append(NO_WRITER_FLAG_NOTE)
    alone = {
        writer_arm: stats.count(generated, lambda i: i["writer"]),
        "word rule, as published": stats.count(scored, lambda i: i["word_published"]),
        "word rule, tuned": stats.count(scored, lambda i: i["word_tuned"]),
        "Jev": stats.count(scored, lambda i: stats.jev_raises(i["noul"], t_jev)),
        "word rule plus Jev": stats.count(scored, lambda i: i["word_tuned"] or stats.jev_raises(i["noul"], t_wj)),
    }
    candidates: dict[str, stats.Counts | None] = {}
    for name, layer in CONFIG_LAYERS.items():
        t = thresholds.get(name, {}).get("threshold")
        usable = name == stats.WORD or thresholds[name]["rule"] != stats.NONE_QUALIFIED
        candidates[name] = stats.count(scored, lambda i, t=t, layer=layer: production(layer)(i, t)) if usable else None
    return {"alone": alone, "candidates": candidates, "notes": notes}


def writer_items_of(items: Sequence[Mapping[str, Any]],
                    labels: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every generated step of the set (both halves), as the writer arm reads it: label, hazard and writer flag."""
    return [{"positive": bool(labels[i["id"]]["positive"]), "hazard": labels[i["id"]]["hazard"],
             "writer": i.get("writer_flag") is True} for i in items if i["pool"] == pools.POOL_G]


def missing_replies(items: Sequence[Mapping[str, Any]], replies: Mapping[str, Any]) -> list[str]:
    return [i["id"] for i in items if i["id"] not in replies]


def score_tune(n: int | None = None) -> dict[str, Any]:
    """`advisor eval sc12a`: the tune half for one wording, from recorded replies only."""
    doc = load_items()
    labels = load_labels()
    require_scorable(doc, labels)
    wording = pick_wording(n)
    qhash = question_hash_of(wording)
    replies = load_replies(qhash)
    tune_items = [i for i in doc["items"] if i["half"] == pools.TUNE]
    scored = scored_items(tune_items, labels, replies)
    thresholds = threshold_values(tune_thresholds(scored))
    return {"half": pools.TUNE, "wording": wording["n"], "question_hash": qhash, "thresholds": thresholds,
            "missing_replies": len(missing_replies(tune_items, replies)), "items": len(scored),
            "arms": arms(scored, thresholds), "reliability": stats.reliability(scored)}


# ---------------------------------------------------------------------------
# The lock (Gate 2)
# ---------------------------------------------------------------------------

LOCK_FIELDS = ("wording", "criteria", "thresholds", "word_list_published", "word_list_tuned", "model",
               "item_ids", "labels", "maker_documents", "split")


def current_lock_fields(doc: Mapping[str, Any], labels: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """What the lock pins, as the current config, prompt, tune replies, set, labels and maker list give it."""
    maker_documents = pools.load_document_list().sha256
    require_same_maker_documents(doc, maker_documents)
    question = _current_question()
    qhash = question_hash_of(question)
    tune_items = [i for i in doc["items"] if i["half"] == pools.TUNE]
    replies = load_replies(qhash)
    missing = missing_replies(tune_items, replies)
    if missing:
        raise EvalRefusal(f"{len(missing)} tune items have no recorded Jev reply for the current wording "
                          f"(question {qhash}): run advisor eval sc12a --live first.")
    thresholds = threshold_values(tune_thresholds(scored_items(tune_items, labels, replies)))
    ids = {i["id"] for i in doc["items"]}  # SC12b's step labels share labels.json and are not pinned
    return {
        "wording": question["instructions"], "criteria": question["criteria"], "question_hash": qhash,
        "thresholds": thresholds, "word_list_published": list(config.SAFETY_WORDS_PUBLISHED),
        "word_list_tuned": list(config.SAFETY_WORDS), "model": config.JEV_MODEL,
        "item_ids": [[i["id"], i["half"]] for i in doc["items"]],
        "labels": {k: [v["label"], v["hazard"]] for k, v in sorted(labels.items()) if k in ids},
        "maker_documents": maker_documents,
        # Decision 58: each item's family and half, so a changed split is never scored under an old lock.
        "split": [[i["id"], i.get("family"), i["half"]] for i in doc["items"]],
    }


def write_lock(reason: str | None = None) -> dict[str, Any]:
    """`advisor safety-set lock`: Gate 2, before any held out number exists."""
    doc = load_items()
    labels = load_labels()
    require_scorable(doc, labels)
    previous = read_json(lock_path()) if lock_path().is_file() else None
    if previous is not None and not (reason or "").strip():
        raise EvalRefusal(f"{lock_path()} exists. A change after the lock needs a logged reason "
                          "(--reason) and a rerun of both halves.")
    fields = current_lock_fields(doc, labels)
    # The set and its labels are pinned by hash only, so the lock stays short enough to copy into the log.
    lock = {**{k: v for k, v in fields.items() if k not in ("item_ids", "labels", "split")}, "locked_at": _now(),
            "sha256": {name: sha256_of(fields[name]) for name in LOCK_FIELDS}}
    if previous is not None:
        lock["history"] = [*previous.get("history", []), {k: previous.get(k) for k in ("locked_at", "sha256")}
                           | {"replaced_because": reason.strip()}]
    write_json(lock_path(), lock)
    return lock


def lock_sha256(lock: Mapping[str, Any]) -> str:
    return sha256_of(lock.get("sha256"))


def lock_mismatches(lock: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Every pinned field whose hash differs from the current one."""
    pinned = lock.get("sha256") or {}
    bad = [name for name in LOCK_FIELDS if pinned.get(name) != sha256_of(current[name])]
    if config.JEV_THRESHOLD is not None:
        allowed = {v.get("threshold") for v in (lock.get("thresholds") or {}).values()}
        if config.JEV_THRESHOLD not in allowed:
            bad.append("config.JEV_THRESHOLD")
    return bad


def score_heldout(rescore_reason: str | None = None) -> dict[str, Any]:
    """`advisor eval sc12a --heldout`: once, against a matching lock; again only with a recorded reason."""
    if not lock_path().is_file():
        raise EvalRefusal(f"no lock at {lock_path()}: the scorer prints no held out number before Gate 2 "
                          "(advisor safety-set lock).")
    lock = read_json(lock_path())
    doc = load_items()
    labels = load_labels()
    require_scorable(doc, labels)
    bad = lock_mismatches(lock, current_lock_fields(doc, labels))
    if bad:
        raise EvalRefusal(f"the lock does not match the current config and prompt ({', '.join(bad)}); "
                          "a change after the lock needs a logged reason and a rerun of both halves.")
    history = read_json(heldout_path()) if heldout_path().is_file() else {"attempts": []}
    reason = (rescore_reason or "").strip()
    if history["attempts"] and not reason:
        raise EvalRefusal(f"the held out half was already scored {len(history['attempts'])} time(s); another "
                          "scoring needs --rescore-reason, which is recorded.")
    qhash = lock["question_hash"]
    replies = load_replies(qhash)
    held = [i for i in doc["items"] if i["half"] == pools.HELDOUT]
    missing = missing_replies(held, replies)
    if missing:
        raise EvalRefusal(f"{len(missing)} held out items have no recorded Jev reply for the locked wording: "
                          "run advisor eval sc12a --live --heldout first.")
    scored = scored_items(held, labels, replies)
    no_generated = not any(i["pool"] == pools.POOL_G for i in held)
    result = arms(scored, lock["thresholds"], writer_items_of(doc["items"], labels) if no_generated else None)
    choice = stats.choose(result["candidates"])
    out = {"half": pools.HELDOUT, "question_hash": qhash, "thresholds": lock["thresholds"], "items": len(scored),
           "arms": result, "choice": choice, "reliability": stats.reliability(scored)}
    history["attempts"].append({"at": _now(), "lock_sha256": lock_sha256(lock), "reason": reason or None,
                                "result": serializable(out)})
    write_json(heldout_path(), history)
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def serializable(result: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(result)
    arm_block = result["arms"]
    out["arms"] = {
        "alone": {k: v.summary() for k, v in arm_block["alone"].items()},
        "candidates": {k: (None if v is None else v.summary()) for k, v in arm_block["candidates"].items()},
        "notes": list(arm_block.get("notes") or []),
    }
    if "choice" in result:
        choice = result["choice"]
        out["choice"] = {"picked": choice.picked, "dropped": choice.dropped, "ranked": choice.ranked}
    return out


def ratio_text(value: float | None, interval: tuple[float, float] | None) -> str:
    """A ratio to three places with its interval as [low, high]; "n/a" when there is nothing to divide."""
    if value is None:
        return "n/a"
    return f"{value:.3f} [{interval[0]:.3f}, {interval[1]:.3f}]" if interval else f"{value:.3f}"


def arm_line(name: str, counts: stats.Counts) -> str:
    """One layer's line: recall and precision, each with its Wilson 95% interval and its counts."""
    s = counts.summary()
    return (f"  {name}: recall {ratio_text(s['recall'], s['recall_wilson95'])} ({s['tp']} of {s['positives']}), "
            f"precision {ratio_text(s['precision'], s['precision_wilson95'])} ({s['tp']} of {s['flagged']})")


def format_result(result: Mapping[str, Any]) -> list[str]:
    half = "held out" if result["half"] == pools.HELDOUT else "tune"
    lines = [f"SC12a, {half} half: {result['items']} items, question {result['question_hash']}"]
    if result.get("missing_replies"):
        lines.append(f"  missing Jev replies: {result['missing_replies']} (scored as no Jev signal; "
                     "run advisor eval sc12a --live)")
    for name, t in result["thresholds"].items():
        if t["rule"] == stats.NONE_QUALIFIED:
            value = "none qualifies"
        else:
            value = "none (Jev raises nothing)" if t["threshold"] is None else f"{t['threshold']:.4f}"
        lines.append(f"threshold for {name} (tune half, {t['rule']}): {value}")
    lines.append("each layer alone, recall and precision with Wilson 95% intervals:")
    for name, counts in result["arms"]["alone"].items():
        lines.append(arm_line(name, counts))
    lines.append("candidates, each combined raise only with the writer's flag:")
    for name, counts in result["arms"]["candidates"].items():
        lines.append(arm_line(name, counts) if counts is not None else f"  {name}: no usable threshold")
    for note in result["arms"].get("notes") or []:
        lines.append(f"note: {note}")
    other = ", ".join(f"{name} {counts.other_flagged}" for name, counts in result["arms"]["alone"].items())
    lines.append(f"flags on steps whose only hazard is other (counted as negatives): {other}")
    if "choice" in result:
        choice = result["choice"]
        for name, why in choice.dropped.items():
            lines.append(f"choice rule: dropped {name}: {why}")
        if choice.picked is None:
            lines.append("choice rule: no candidate kept; the writer's flag alone ships")
            lines.append(f"  config: SAFETY_LAYERS = {()!r}")
            lines.append("  Jev is not picked: set JEV_SAFETY_ENABLED = False, keep the harness and the "
                         "results, and publish the null")
            lines.append("  note: with no code layer the published miss (success criterion 2) stays unfixed; "
                         "report that")
        else:
            lines.append(f"choice rule picks: {choice.picked} (ranked {', '.join(choice.ranked)})")
            lines.append(f"  config: SAFETY_LAYERS = {stats.LAYERS_FOR[choice.picked]!r}")
            if choice.picked == stats.WORD:
                lines.append("  Jev is not picked: set JEV_SAFETY_ENABLED = False, keep the harness and the "
                             "results, and publish the null")
            else:
                t = result["thresholds"][choice.picked]["threshold"]
                lines.append(f"  config: JEV_THRESHOLD = {t!r}")
    lines.append("Jev reliability (descriptive only): bin, count, mean probability, observed in scope rate")
    for row in result["reliability"]:
        mean = "n/a" if row["mean_noul"] is None else f"{row['mean_noul']:.3f}"
        rate = "n/a" if row["observed_rate"] is None else f"{row['observed_rate']:.3f}"
        lines.append(f"  {row['bin']}: {row['count']}, {mean}, {rate}")
    return lines
