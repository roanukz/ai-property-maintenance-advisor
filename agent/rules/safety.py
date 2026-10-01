"""Safety flags and ordering (PLAN 8.5 rule 5, decision 8; safety step flagging).

Raise only (`raise_flags`): the writer's flag stands, and the word rule and
Jev's probability may each set a flag the writer left off, when config lets
that layer raise one in production. No layer clears a flag, adds a step or
drops one. Jev's answers are matched to steps by the hash of the normalized
step and detail, never by position (decision 56), so the upgrade pass, which
revalidates the same draft, reuses them.

The word fallback (decision 68): when Jev is a production layer and the word
rule is not, the word rule backs up each step whose Jev call failed, in a
check whose status is "partial" or "failed", and nothing else. A step Jev
answered, and a check that ran, was not recorded, was skipped or was disabled,
never gets a fallback flag. Like every layer it only raises.

Safety flagged steps then move to the front with a stable sort, so the
relative order within each group is the model's. The brief is reordered, never
rejected: two recorded v1 briefs put a safety step last.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

from agent.rules.step_text import step_key, word_rule

# The code layers that may raise a flag; the writer's own flag always stands.
LAYERS = ("word", "jev")
# The provenance name of a flag the word rule raised for a failed Jev call (decision 68).
WORD_FALLBACK = "word_fallback"
# The check statuses in which a Jev call was attempted and failed (safety_check.NOTICE_STATUSES).
FAILED_STATUSES = ("partial", "failed")
# A replay's missing answer: not a failure (safety_check.NOT_RECORDED).
NOT_RECORDED = "not_recorded"


def safety_first(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the steps with every safety step first, order otherwise kept."""
    return sorted(steps, key=lambda step: step.get("safety_flag") is not True)


def apply_safety_order(brief: dict[str, Any]) -> None:
    brief["try_first"] = safety_first(brief.get("try_first") or [])


def jev_nouls(signals: dict[str, Any] | None) -> dict[str, float]:
    """Jev's probability by step key, for the steps whose call answered (first entry wins)."""
    out: dict[str, float] = {}
    for entry in (signals or {}).get("steps") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("step_sha256"), str):
            continue
        noul = entry.get("noul")
        if isinstance(noul, (int, float)) and not isinstance(noul, bool):
            out.setdefault(entry["step_sha256"], float(noul))
    return out


def jev_failed(signals: dict[str, Any] | None) -> set[str]:
    """The step keys whose Jev call failed, in a check whose status is "partial" or "failed".

    Empty for any other status. A step whose error is "not_recorded" did not
    fail, and a step with an answer for its key is never counted.
    """
    signals = signals or {}
    if signals.get("status") not in FAILED_STATUSES:
        return set()
    answered = jev_nouls(signals)
    failed: set[str] = set()
    for entry in signals.get("steps") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("step_sha256"), str):
            continue
        error = entry.get("error")
        if error is not None and error != NOT_RECORDED and entry["step_sha256"] not in answered:
            failed.add(entry["step_sha256"])
    return failed


def raise_flags(steps: list[dict[str, Any]], signals: dict[str, Any] | None, *,
                layers: Iterable[str], words: Iterable[str],
                jev_threshold: float | None, word_fallback: bool = False) -> list[dict[str, Any]]:
    """Set `safety_flag` to True where a layer in `layers` fires (in place); never to False.

    Returns one provenance entry per step, in the order rule 5 will give the
    steps: the writer's flag, the word rule's answer, Jev's probability (None
    when no answer was recorded for the step), the final flag and the layer
    that set it ("writer" first, then "word", then "jev", then "word_fallback";
    None when unflagged). The word rule and Jev are reported for every step
    whether or not their layer may raise a flag, and Jev raises nothing while
    `jev_threshold` is None. With `word_fallback`, while Jev is active with a
    threshold and the word layer is not, the word rule raises a flag on a step
    whose Jev call failed (`jev_failed`) and on no other step.
    """
    active = tuple(layers)
    unknown = [layer for layer in active if layer not in LAYERS]
    if unknown:
        raise ValueError(f"unknown safety layer {unknown!r}; expected some of {LAYERS}")
    words = tuple(words)
    nouls = jev_nouls(signals)
    fallback_on = word_fallback and "jev" in active and jev_threshold is not None and "word" not in active
    failed = jev_failed(signals) if fallback_on else set()
    provenance: list[dict[str, Any]] = []
    for step in steps:
        writer = step.get("safety_flag") is True
        word = word_rule(step.get("step"), step.get("detail"), words)
        key = step_key(step.get("step"), step.get("detail"))
        noul = nouls.get(key)
        jev = jev_threshold is not None and noul is not None and noul >= jev_threshold
        if writer:
            raised_by = "writer"
        elif word and "word" in active:
            raised_by = "word"
        elif jev and "jev" in active:
            raised_by = "jev"
        elif word and key in failed:
            raised_by = WORD_FALLBACK
        else:
            raised_by = None
        if raised_by is not None:
            step["safety_flag"] = True
        provenance.append({"step_sha256": key, "writer_flag": writer, "word_rule": word, "jev_noul": noul,
                           "final_flag": raised_by is not None, "raised_by": raised_by})
    return sorted(provenance, key=lambda entry: entry["final_flag"] is not True)


def pair_provenance(steps: Iterable[dict[str, Any]], provenance: Iterable[Any]) -> list[dict[str, Any]]:
    """Each step's provenance entry, in the steps' order ({} when there is none).

    Joined by step key in order, one entry per step: two steps with the same
    key each get their own entry, not the last one's. Among entries for one
    key, the first whose final flag matches the step's flag is taken.
    """
    queues: dict[Any, deque[dict[str, Any]]] = {}
    for entry in provenance:
        if isinstance(entry, dict):
            queues.setdefault(entry.get("step_sha256"), deque()).append(entry)
    out: list[dict[str, Any]] = []
    for step in steps:
        queue = queues.get(step_key(step.get("step"), step.get("detail"))) or deque()
        flagged = step.get("safety_flag") is True
        entry = next((e for e in queue if (e.get("final_flag") is True) == flagged), None)
        if entry is None and queue:
            entry = queue[0]
        if entry is not None:
            queue.remove(entry)
        out.append(entry or {})
    return out
