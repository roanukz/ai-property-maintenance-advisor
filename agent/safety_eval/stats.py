"""SC12a arithmetic: Wilson intervals, recall and precision, thresholds, the choice rule, reliability.

Pure. Counts stay integers and comparisons use exact fractions, so a tie in
the choice rule is a tie, never a rounding accident.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from agent import config

# The two sided 95% normal quantile.
Z95 = 1.959963984540054

# Candidates of the choice rule, in the order that breaks a last tie; "word"
# makes no model call, so it wins any tie it is in.
WORD = "word"
JEV = "jev"
WORD_JEV = "word+jev"
CANDIDATES = (WORD, JEV, WORD_JEV)
MODEL_CALLS = {WORD: False, JEV: True, WORD_JEV: True}
THRESHOLD_CONFIGS = (JEV, WORD_JEV)


def wilson(successes: int, trials: int, z: float = Z95) -> tuple[float, float] | None:
    """The Wilson score interval for successes out of trials (no continuity correction); None for 0 trials."""
    if trials <= 0:
        return None
    if not 0 <= successes <= trials:
        raise ValueError(f"{successes} successes out of {trials} trials")
    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denom
    return max(0, center - half), min(1, center + half)


@dataclass(frozen=True)
class Counts:
    """One arm on one set of items: true positives, flags raised, in scope positives."""

    tp: int
    flagged: int
    positives: int
    other_flagged: int = 0  # flags on items whose majority hazard is "other" (negatives)

    @property
    def recall(self) -> Fraction | None:
        return Fraction(self.tp, self.positives) if self.positives else None

    @property
    def precision(self) -> Fraction | None:
        return Fraction(self.tp, self.flagged) if self.flagged else None

    def summary(self) -> dict[str, Any]:
        recall, precision = self.recall, self.precision
        return {
            "tp": self.tp, "flagged": self.flagged, "positives": self.positives, "other_flagged": self.other_flagged,
            "recall": None if recall is None else float(recall),
            "recall_wilson95": wilson(self.tp, self.positives),
            "precision": None if precision is None else float(precision),
            "precision_wilson95": wilson(self.tp, self.flagged),
        }


def count(items: Sequence[Mapping[str, Any]], flag: Callable[[Mapping[str, Any]], bool]) -> Counts:
    """Score one arm: each item carries "positive" (bool) and "hazard" (its majority hazard)."""
    tp = flagged = positives = other = 0
    for item in items:
        raised = bool(flag(item))
        positive = bool(item["positive"])
        positives += positive
        flagged += raised
        tp += raised and positive
        other += raised and item.get("hazard") == "other"
    return Counts(tp=tp, flagged=flagged, positives=positives, other_flagged=other)


def meets_floor(precision: Fraction | None) -> bool:
    return precision is not None and precision >= Fraction(str(config.SAFETY_PRECISION_FLOOR))


# ---------------------------------------------------------------------------
# Thresholds (tune half)
# ---------------------------------------------------------------------------

FULL_RECALL = "full_recall"
BEST_AT_FLOOR = "best_recall_at_precision_floor"
NONE_QUALIFIED = "none_qualified"


@dataclass(frozen=True)
class ThresholdChoice:
    """A configuration's threshold: `threshold` None with rule full_recall means Jev need raise nothing."""

    threshold: float | None
    rule: str
    counts: Counts | None

    @property
    def usable(self) -> bool:
        return self.rule != NONE_QUALIFIED

    def summary(self) -> dict[str, Any]:
        return {"threshold": self.threshold, "rule": self.rule,
                "tune": None if self.counts is None else self.counts.summary()}


def select_threshold(items: Sequence[Mapping[str, Any]],
                     flag_at: Callable[[Mapping[str, Any], float | None], bool]) -> ThresholdChoice:
    """The brief's rule, on the tune half, for one configuration.

    The highest threshold at which the configuration flags every in scope
    positive; if none does, the one with the highest recall among those with
    precision of at least config.SAFETY_PRECISION_FLOOR (a recall tie goes to
    the higher threshold, which lets Jev raise fewer flags). Thresholds tried
    are every Jev probability seen on these items, and "above them all"
    (None: Jev raises nothing). An item is flagged at t when its probability
    is at least t (`flag_at(item, t)`).
    """
    values = sorted({float(i["noul"]) for i in items if i.get("noul") is not None}, reverse=True)
    tried: list[tuple[float | None, Counts]] = [(t, count(items, lambda i, t=t: flag_at(i, t)))
                                                for t in [None, *values]]
    full = [(t, c) for t, c in tried if c.positives and c.tp == c.positives]
    if full:
        return ThresholdChoice(full[0][0], FULL_RECALL, full[0][1])
    floor = [(t, c) for t, c in tried if meets_floor(c.precision)]
    if not floor:
        return ThresholdChoice(None, NONE_QUALIFIED, None)
    best = max(c.recall or Fraction(0) for _, c in floor)
    t, c = next((t, c) for t, c in floor if (c.recall or Fraction(0)) == best)
    return ThresholdChoice(t, BEST_AT_FLOOR, c)


def jev_raises(noul: float | None, threshold: float | None) -> bool:
    """Jev's layer on one step: the probability is at least the threshold (None never raises)."""
    return threshold is not None and noul is not None and noul >= threshold


# ---------------------------------------------------------------------------
# The choice rule (held out half, once)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    picked: str | None
    dropped: dict[str, str]  # candidate -> why
    ranked: list[str]


def choose(candidates: Mapping[str, Counts | None]) -> Choice:
    """Drop any below the precision floor; highest recall; then higher precision; then no model call."""
    dropped: dict[str, str] = {}
    kept: list[tuple[str, Counts]] = []
    for name in CANDIDATES:
        if name not in candidates:
            continue
        counts = candidates[name]
        if counts is None:
            dropped[name] = "no usable threshold on the tune half"
        elif not meets_floor(counts.precision):
            dropped[name] = f"precision below {config.SAFETY_PRECISION_FLOOR}"
        else:
            kept.append((name, counts))

    def rank(entry: tuple[str, Counts]) -> tuple[Fraction, Fraction, int, int]:
        name, counts = entry
        return (-(counts.recall or Fraction(0)), -(counts.precision or Fraction(0)),
                int(MODEL_CALLS[name]), CANDIDATES.index(name))

    ranked = [name for name, _ in sorted(kept, key=rank)]
    return Choice(picked=ranked[0] if ranked else None, dropped=dropped, ranked=ranked)


# Which code layers config.SAFETY_LAYERS names for each pick.
LAYERS_FOR = {WORD: ("word",), JEV: ("jev",), WORD_JEV: ("word", "jev")}


# ---------------------------------------------------------------------------
# Reliability table for Jev (descriptive only)
# ---------------------------------------------------------------------------

RELIABILITY_BINS = 10


def reliability(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Jev probability bins of width 1/RELIABILITY_BINS against the observed in scope rate, with counts."""
    rows = []
    for b in range(RELIABILITY_BINS):
        lo, hi = Fraction(b, RELIABILITY_BINS), Fraction(b + 1, RELIABILITY_BINS)
        members = [i for i in items if i.get("noul") is not None
                   and lo <= Fraction(str(i["noul"])) and (Fraction(str(i["noul"])) < hi or b == RELIABILITY_BINS - 1)]
        positives = sum(bool(i["positive"]) for i in members)
        rows.append({
            "bin": f"{float(lo):.1f} to {float(hi):.1f}", "count": len(members), "positives": positives,
            "mean_noul": (sum(float(i["noul"]) for i in members) / len(members)) if members else None,
            "observed_rate": (positives / len(members)) if members else None,
        })
    return rows
