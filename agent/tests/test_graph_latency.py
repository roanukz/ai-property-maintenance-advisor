"""Latency is recorded for every node run (PLAN section 5 and 8.2; reported, no target).

Each node run writes its own `latency` entry: `node` the first time, then
`node_2`, `node_3` when it runs again (a retry or a confirm loop), so the run
record's latency_s, the sum of the entries, covers every run.
"""

from __future__ import annotations

import json

import pytest

from agent.nodes.persist import run_record_path
from agent.tests.helpers import load_case, run_case


def expected_latency_keys(nodes: list[str]) -> set[str]:
    keys: set[str] = set()
    runs: dict[str, int] = {}
    for name in nodes:
        runs[name] = runs.get(name, 0) + 1
        keys.add(name if runs[name] == 1 else f"{name}_{runs[name]}")
    return keys


def assert_latency_for_every_node(case: str, tmp_path) -> tuple[dict[str, float], list[str], dict]:
    _, ctx, outcomes = run_case(load_case(case), tmp_path)
    ran = [name for outcome in outcomes for name in outcome.nodes]
    latency = outcomes[-1].state["latency"]
    assert set(latency) == expected_latency_keys(ran)
    assert all(isinstance(v, float) and v >= 0 for v in latency.values())
    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    assert record["latency"] == latency
    assert record["latency_s"] == pytest.approx(sum(latency.values()))
    return latency, ran, record


@pytest.mark.parametrize("case", ["synthetic/flo_three_candidates", "synthetic/retry_typical",
                                  "synthetic/at_caps_first_pass"])
def test_latency_recorded_for_every_node_that_ran(case: str, tmp_path) -> None:
    """Mutations: latency_route_dropped (route writes no entry);
    latency_retry_gate_dropped; latency_research_retry_overwrites (the retry
    pass reuses the "research" key); latency_attempt_key_ignored (every
    repeated node overwrites its first entry, so latency_s undercounts)."""
    latency, ran, _ = assert_latency_for_every_node(case, tmp_path)
    if case.endswith("retry_typical"):
        assert ran.count("synthesize") == 2
        assert {"research", "research_2", "retry_gate", "synthesize", "synthesize_2", "validate",
                "validate_2", "gather", "gather_2"} <= set(latency)
