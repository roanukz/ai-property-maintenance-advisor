"""The observed code is enforced only once the owner confirms it (PLAN 8.3, decision 7).

intake keeps a code guessed from the symptom text in observed_code_candidate;
observed_code is set only from --code or the confirm_identity resume. A typed
identity with a model therefore still pauses when the symptom gave a guess, and
both models are told which code the owner confirmed. Each test names the
mutation that turns it red.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent import config, prompts
from agent.nodes.confirm_identity import confirm_identity, next_after_confirm
from agent.nodes.intake import intake, intake_input
from agent.nodes.route import route
from agent.replay.cassettes import cassette_from_dict
from agent.rules.observed_code import NO_MATCH
from agent.state import AdvisorState, RunContext
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR, replay_ctx, run_case

TYPED = {"manufacturer": "Examplecorp", "model": "EX-100", "serial": None, "manufacture_date": None}


def _synthetic(name: str, **changes: Any):
    data = copy.deepcopy(json.loads((SYNTHETIC_CASSETTE_DIR / f"{name}.json").read_text(encoding="utf-8")))
    for key, value in changes.items():
        section, _, field = key.partition("__")
        if field:
            data[section][field] = value
        else:
            data[section] = value
    return cassette_from_dict(data, f"{name} with {sorted(changes)}")


def _confirm_graph():
    builder = StateGraph(AdvisorState, context_schema=RunContext)
    builder.add_node("confirm_identity", confirm_identity)
    builder.add_node("route", route)
    builder.add_edge(START, "confirm_identity")
    builder.add_conditional_edges("confirm_identity", next_after_confirm, ["confirm_identity", "route"])
    builder.add_edge("route", END)
    return builder.compile(checkpointer=InMemorySaver())


def _run(graph, value, ctx: RunContext, thread: str):
    return graph.invoke(
        value, context=ctx, durability="sync", version="v2",
        config={"configurable": {"thread_id": thread}, "recursion_limit": config.RECURSION_LIMIT},
    )


def _text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in messages)


def test_intake_keeps_the_symptom_guess_unconfirmed(tmp_path: Path) -> None:
    """Mutation intake_guess_is_confirmed: intake writes the symptom guess into observed_code."""
    ctx = replay_ctx(tmp_path, None)
    out = intake(intake_input(symptom="thermostat shows 72 but the house is 80", identity=TYPED),
                 Runtime(context=ctx))
    assert out["observed_code"] is None
    assert out["observed_code_candidate"] == "72"
    flagged = intake(intake_input(symptom="panel shows FLO", identity=TYPED, code="OH"), Runtime(context=ctx))
    assert flagged["observed_code"] == "OH" and flagged["observed_code_candidate"] is None


def test_typed_identity_with_a_guessed_code_pauses(tmp_path: Path) -> None:
    """Mutation confirm_skips_unconfirmed_code: the typed pass through ignores an unconfirmed guess."""
    graph, ctx = _confirm_graph(), replay_ctx(tmp_path, None)
    start = {"identity": TYPED, "extraction": None, "observed_code": None, "observed_code_candidate": "FLO"}
    paused = _run(graph, start, ctx, "t1")
    assert len(paused.interrupts) == 1
    payload = paused.interrupts[0].value
    assert payload["observed_code_candidate"] == "FLO" and payload["identity"]["model"] == "EX-100"
    # A resume without the code key confirms the code shown.
    done = _run(graph, Command(resume={"identity": TYPED}), ctx, "t1")
    assert done.interrupts == ()
    assert done.value["observed_code"] == "FLO" and done.value["identity_confirmed"] is True
    # --code confirms the code up front, and a symptom with no code has nothing to confirm.
    for thread, state in (("t2", {**start, "observed_code": "FLO", "observed_code_candidate": None}),
                          ("t3", {**start, "observed_code_candidate": None})):
        out = _run(graph, state, ctx, thread)
        assert out.interrupts == (), thread
        assert out.value["identity_confirmed"] is True


def test_typed_run_with_a_guessed_code_is_not_forced_to_refuse(tmp_path: Path) -> None:
    """A typed identity and "thermostat shows 72": the run pauses, the owner clears the
    guess, and the narrowing rule never runs on it.

    Mutations: intake_guess_is_confirmed (validate enforces 72 and the run ends in
    a forced refusal); confirm_skips_unconfirmed_code (no pause)."""
    cassette = _synthetic("retry_typical", input__symptom="thermostat shows 72 but the house is 80",
                          resume={"identity": {**TYPED, "serial": "", "manufacture_date": ""},
                                  "observed_code": None})
    _, _, outcomes = run_case(cassette, tmp_path)
    assert len(outcomes) == 2 and outcomes[0].paused
    assert outcomes[0].interrupt["observed_code_candidate"] == "72"
    assert "research" not in outcomes[0].nodes
    final = outcomes[-1]
    for name, state in final.states:
        if name == "validate":
            assert not any(e.startswith(NO_MATCH) for e in state.get("validation_errors") or [])
    assert final.state["status"] == "ok" and final.state["refusal_origin"] is None
    assert final.state["brief"]["observed_code"] is None


def test_prompts_carry_the_code_confirmed_at_resume(tmp_path: Path) -> None:
    """The owner adds FLO at resume to a symptom that names no code; both models see it.

    Mutations: prompts_code_line_dropped (observed_code_lines returns []);
    research_prompt_no_confirmed_code; synth_prompt_no_confirmed_code."""
    cassette = _synthetic("flo_three_candidates", input__symptom="hot tub not heating",
                          resume={"identity": {"manufacturer": "Sundance Spas", "model": "Optima 880",
                                               "serial": "", "manufacture_date": ""},
                                  "observed_code": "FLO"})
    _, ctx, outcomes = run_case(cassette, tmp_path)
    assert outcomes[-1].state["observed_code"] == "FLO"
    line = prompts.OBSERVED_CODE_LINE.format(code="FLO")
    models = ctx.replay["models"]
    assert line in _text(models["research"].received[0])
    assert line in _text(models["synthesize"].received[0])
    assert prompts.NO_OBSERVED_CODE_LINE not in _text(models["synthesize"].received[0])


def test_prompts_say_no_code_when_the_owner_clears_it(tmp_path: Path) -> None:
    """The symptom names FLO but the owner clears it: neither model is told FLO was observed.

    Mutation prompts_code_line_dropped."""
    data = json.loads((SYNTHETIC_CASSETTE_DIR / "flo_three_candidates.json").read_text(encoding="utf-8"))
    synthesize = copy.deepcopy(data["synthesize"])
    # With no confirmed code, rule 4 (Phase 3) looks for "flo." as printed, and
    # the page prints "FLO", so the candidate here spells it as the page does.
    for candidate in synthesize[0]["draft"]["candidates"]:
        if candidate["code"] == "flo.":
            candidate["code"] = "FLO"
    cassette = _synthetic("flo_three_candidates",
                          resume={"identity": {"manufacturer": "Sundance Spas", "model": "Optima 880",
                                               "serial": "", "manufacture_date": ""},
                                  "observed_code": None},
                          synthesize=synthesize)
    _, ctx, outcomes = run_case(cassette, tmp_path)
    assert outcomes[-1].state["observed_code"] is None
    models = ctx.replay["models"]
    for node in ("research", "synthesize"):
        text = _text(models[node].received[0])
        assert prompts.NO_OBSERVED_CODE_LINE in text, node
        assert prompts.OBSERVED_CODE_LINE.format(code="FLO") not in text, node
