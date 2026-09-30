"""The three blind readers' labels: import, majority and unanimity (PROTOCOL.md).

A reader answers two questions per item: safety step yes or no, and the
hazard (electrical, heat, gas, other or none). Per the protocol a step whose
only hazard is "other" is a no, so a vote counts as positive only when it
says yes and names an in scope hazard (electrical, heat or gas). The label is
the majority of config.SC12A_READERS votes; the hazard is the one at least
two readers gave, else "split". Unanimity is reported for the safety answer
and for both answers together.

Import file shapes, one reader per file or several in one:
    {"reader": "reader-1", "labels": [{"id": ..., "safety": "yes", "hazard": "electrical"}, ...]}
    {"readers": [<the shape above>, ...]}
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from agent import config

SAFETY_ANSWERS = ("yes", "no")
HAZARDS = ("electrical", "heat", "gas", "other", "none")
IN_SCOPE_HAZARDS = ("electrical", "heat", "gas")
SPLIT = "split"


class LabelError(ValueError):
    """A labels file is malformed, names an unknown item, or changes a vote already imported."""


def reader_blocks(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if "readers" in payload:
        blocks = payload["readers"]
        if not isinstance(blocks, list):
            raise LabelError('"readers" must be a list')
        return blocks
    return [payload]


def merge_votes(raw: Mapping[str, Mapping[str, Any]], payload: Mapping[str, Any],
                known_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Add one file's votes to the stored raw votes {item id: {reader: vote}}; a changed vote is refused."""
    known = set(known_ids)
    out = {item: dict(votes) for item, votes in raw.items()}
    for block in reader_blocks(payload):
        reader = str(block.get("reader") or "").strip()
        if not reader:
            raise LabelError("every block needs a reader name")
        for entry in block.get("labels") or []:
            item = str(entry.get("id") or "")
            if item not in known:
                raise LabelError(f"reader {reader}: unknown item id {item!r}")
            safety = str(entry.get("safety") or "").strip().lower()
            hazard = str(entry.get("hazard") or "").strip().lower()
            if safety not in SAFETY_ANSWERS or hazard not in HAZARDS:
                raise LabelError(f"reader {reader}, item {item}: safety must be one of {SAFETY_ANSWERS} "
                                 f"and hazard one of {HAZARDS}")
            vote = {"safety": safety, "hazard": hazard}
            previous = out.get(item, {}).get(reader)
            if previous is not None and previous != vote:
                raise LabelError(f"reader {reader} already labeled item {item} differently; labels are final")
            out.setdefault(item, {})[reader] = vote
            if len(out[item]) > config.SC12A_READERS:
                raise LabelError(f"item {item} has votes from more than {config.SC12A_READERS} readers")
    return out


def positive_vote(vote: Mapping[str, str]) -> bool:
    return vote["safety"] == "yes" and vote["hazard"] in IN_SCOPE_HAZARDS


def majority(votes: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """The label of one item from its readers' votes."""
    readers = sorted(votes)
    answers = [positive_vote(votes[r]) for r in readers]
    hazards = Counter(votes[r]["hazard"] for r in readers)
    top, top_count = hazards.most_common(1)[0]
    positive = sum(answers) * 2 > len(answers)
    hazard = top if top_count * 2 > len(readers) else SPLIT
    return {
        "label": "yes" if positive else "no",
        "positive": positive,
        "hazard": hazard,
        "votes": {r: dict(votes[r]) for r in readers},
        "unanimous": len(set(answers)) == 1,
        "unanimous_with_hazard": len(set(answers)) == 1 and len(hazards) == 1,
        "contradictory_votes": sum(votes[r]["safety"] == "yes" and not positive_vote(votes[r]) for r in readers),
    }


def labels_by_id(raw: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """(label by item id for items with every reader's vote, ids still waiting for votes)."""
    labels: dict[str, dict[str, Any]] = {}
    incomplete: list[str] = []
    for item, votes in raw.items():
        if len(votes) == config.SC12A_READERS:
            labels[item] = majority(votes)
        else:
            incomplete.append(item)
    return labels, incomplete


def unanimity(labels: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    n = len(labels)
    safety = sum(bool(label["unanimous"]) for label in labels.values())
    both = sum(bool(label["unanimous_with_hazard"]) for label in labels.values())
    return {"items": n, "unanimous": safety, "unanimous_with_hazard": both,
            "rate": (safety / n) if n else None, "rate_with_hazard": (both / n) if n else None}
