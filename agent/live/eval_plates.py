"""`advisor eval plates --live`: read_plate on the E1 and E2 plates (PLAN section 9, "6, plates").

Two cheap runs through the normal graph, one per plate, each ending at the
confirmation pause: read_plate is the only paid call, and the pause is never
answered. The shared batch harness (eval_sc3b) gives them the same refusals,
typed "proceed", ledger, key handling and run records as the other evals.
Each record holds the raw read_plate reply (from the run's capture, before
code nulls unreadable fields) and the final extraction, the v1 case check
(section 6.5: E1 reads model, maker and serial; E2 must return model, serial
and date null and unreadable), the ledger cost and SC11, computed although
the thread stays paused.

The preflight is priced from config: typical is read_plate's section 9
estimate per plate; the worst case is the read_plate reservation the ledger
holds before the call (the call is charged in full, so the actual cost can
pass it only by the input estimate's error, and never the per run cap).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import IO

from agent import config
from agent.live import evaluators
from agent.live.eval_sc3b import EVAL_MODE, Batch, RunEstimate, RunSpec, run_batch

SUITE = "plates"
PLATES = {"E1": "plate-clear.jpg", "E2": "plate-blurry.jpg"}  # demo-assets, read only


def plate_specs() -> list[RunSpec]:
    """One run per plate, in case order; read_plate only."""
    return [
        RunSpec(suite=SUITE, case=case, label=f"{case} plate {name}", symptom=config.PLATE_EVAL_SYMPTOM,
                photo=config.REPO_ROOT / "demo-assets" / name, expected_route="graph", role="plate",
                stop_at_pause=True)
        for case, name in PLATES.items()
    ]


def read_plate_reservation_usd(spec: RunSpec) -> float:
    """What the ledger reserves before this plate's read_plate call: the estimate plus max_tokens."""
    from agent.ledger import estimate_input_tokens, price_usage
    from agent.models import max_tokens_for
    from agent.nodes.read_plate import plate_messages

    model = config.MODEL_FOR[EVAL_MODE]["read_plate"]
    _, estimate_messages = plate_messages(str(spec.photo))
    tokens = estimate_input_tokens(estimate_messages, model=model, images=1)
    return price_usage(config.PRICES_PER_MTOK[model],
                       {"input_tokens": tokens, "output_tokens": max_tokens_for("read_plate", EVAL_MODE)})


def plate_estimate(spec: RunSpec) -> RunEstimate:
    """One read_plate call, no search: typical from section 9, worst case the call's reservation."""
    from agent.live.preflight import read_plate_typical_usd

    return RunEstimate(
        typical_usd=read_plate_typical_usd(EVAL_MODE), worst_usd=read_plate_reservation_usd(spec),
        typical_model_calls=1, max_model_calls=1, typical_credits=0, first_pass_credits=0, credit_ceiling=0,
    )


def cmd_eval_plates(args: argparse.Namespace, environ: Mapping[str, str], *, stdin: IO[str] | None = None) -> int:
    """`advisor eval plates --live`: the E1 and E2 plate runs, one preflight, one record each."""
    specs = plate_specs()
    worst = max(plate_estimate(s).worst_usd for s in specs)
    batch = Batch(
        suite=SUITE,
        specs=specs,
        score=evaluators.score_plates,
        format=evaluators.format_plates,
        preflight_extra=lambda: [
            "each run makes the read_plate call only and stays paused at confirm_identity; no Tavily "
            "credit is spent and no Tavily key is needed",
        ],
        estimator=plate_estimate,
        per_run_usd=worst,
        worst_text=f"{len(specs)} read_plate reservations; the {config.RUN_CAP_USD[EVAL_MODE]:.4f} USD "
                   "per run cap still applies",
    )
    return run_batch(batch, args, environ, stdin=stdin)
