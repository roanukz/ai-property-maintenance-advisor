"""The cassette shapes a Claude Haiku 5.5 recording adds, at the loader and at replay (decision 69).

Content blocks, thinking markers, a recorded decline and the recorded models are optional; every
tracked cassette (recorded on Claude Haiku 4.5) uses none of them and loads and serves exactly as
before. What a recording may hold is checked at load time, so a malformed one fails there, not
halfway through a replay. Each test names the mutation in mutations.toml that turns it red.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from agent import config
from agent.declined import ModelDeclined
from agent.replay.cassettes import (
    THINKING_MARKERS,
    CassetteError,
    cassette_from_dict,
    lookup_recording_state,
)
from agent.replay.replay_model import ReplayChatModel
from agent.tests.helpers import SYNTHETIC_CASSETTE_DIR

MARKER = THINKING_MARKERS["thinking"]
USAGE = {"input_tokens": 900, "output_tokens": 260, "output_token_details": {"reasoning": 180}}
REFUSAL = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber"}, "usage": USAGE}


def flo() -> dict[str, Any]:
    return json.loads((SYNTHETIC_CASSETTE_DIR / "flo_three_candidates.json").read_text(encoding="utf-8"))


def in_blocks(data: dict[str, Any]) -> dict[str, Any]:
    """flo in Claude Haiku 5.5's shapes: each research turn in blocks, each structured reply with a marker."""
    data = copy.deepcopy(data)
    for i, step in enumerate(data["research"]["script"]):
        message = step["message"]
        for j, call in enumerate(message["tool_calls"]):
            call["id"] = f"toolu_{i}_{j}"
        text = message["content"]
        message["content"] = [dict(MARKER), *([{"type": "text", "text": text}] if text else []),
                              *({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                                for c in message["tool_calls"])]
    data["read_plate"]["thinking"] = [dict(MARKER)]
    for attempt in data["synthesize"]:
        attempt["thinking"] = [dict(MARKER), dict(THINKING_MARKERS["redacted_thinking"])]
    data["recorded"] = {"mode": "cheap", "models": {"read_plate": config.HAIKU, "research": config.HAIKU,
                                                    "synthesize": config.HAIKU}}
    return data


def test_haiku_5_5_shapes_load_and_serve_their_blocks() -> None:
    """Mutation round2_responses_for_drops_thinking: a structured reply is served without its markers."""
    cassette = cassette_from_dict(in_blocks(flo()), "flo in blocks")
    [plate] = cassette.responses_for("read_plate")
    assert plate["thinking"] == [MARKER]
    model = ReplayChatModel(responses=cassette.responses_for("synthesize"), model=config.HAIKU, max_tokens=1)
    reply = model.invoke("synthetic")
    assert [b["type"] for b in reply.content] == ["thinking", "redacted_thinking", "text"]
    research = ReplayChatModel(responses=cassette.responses_for("research"), model=config.HAIKU, max_tokens=1)
    turn = research.invoke("synthetic")
    assert turn.content[0] == MARKER
    assert [(c["id"], c["name"], c["args"]) for c in turn.tool_calls] == \
        [(b["id"], b["name"], b["input"]) for b in turn.content if b["type"] == "tool_use"]
    assert cassette.recorded["models"]["synthesize"] == config.HAIKU


def _rejects(data: dict[str, Any], message: str) -> None:
    with pytest.raises(CassetteError, match=message):
        cassette_from_dict(data, "edited")


def test_a_thinking_marker_holds_no_text_or_signature() -> None:
    """Mutation round2_marker_signature_accepted: the loader accepts a thinking block with its signature."""
    data = in_blocks(flo())
    data["research"]["script"][0]["message"]["content"][0]["signature"] = "EqQBCkYIBxgCKkB"
    _rejects(data, "its text and signature are never kept")
    data = in_blocks(flo())
    data["synthesize"][0]["thinking"][0]["thinking"] = "The owner said the panel shows FLO."
    _rejects(data, "its text and signature are never kept")


def test_a_tool_use_block_repeats_its_tool_call() -> None:
    """Mutation round2_tool_use_block_unchecked: the loader accepts a tool_use block that repeats no tool call."""
    data = in_blocks(flo())
    step = next(s for s in data["research"]["script"] if s["message"]["tool_calls"])
    next(b for b in step["message"]["content"] if b["type"] == "tool_use")["input"] = {"query": "something else"}
    _rejects(data, "does not repeat its tool call")
    data = in_blocks(flo())
    step = next(s for s in data["research"]["script"] if s["message"]["tool_calls"])
    step["message"]["content"] = [b for b in step["message"]["content"] if b["type"] != "tool_use"]
    _rejects(data, "tool_use block")


def test_a_decline_ends_the_run_and_keeps_only_its_category_and_usage() -> None:
    """Mutations: round2_refusal_not_last_accepted (a decline may be followed by more responses);
    round2_refusal_explanation_accepted (a recorded decline may keep its explanation)."""
    data = flo()
    data["synthesize"] = [{"attempt": 1, **REFUSAL}]
    cassette = cassette_from_dict(data, "declined synthesize")
    model = ReplayChatModel(responses=cassette.responses_for("synthesize"), model=config.HAIKU, max_tokens=1)
    with pytest.raises(ModelDeclined) as caught:
        model.invoke("synthetic")
    assert caught.value.category == "cyber" and caught.value.ai_message.usage_metadata["output_tokens"] == 260

    later = flo()
    later["research"]["script"].insert(1, copy.deepcopy(REFUSAL))
    _rejects(later, "a decline must be the last response of its node")
    after = flo()
    after["research"]["script"] = [copy.deepcopy(REFUSAL)]
    after["research"]["tool_results"] = []
    _rejects(after, "holds responses after the decline in research.script")
    explained = flo()
    explained["synthesize"] = [{"attempt": 1, **copy.deepcopy(REFUSAL)}]
    explained["synthesize"][0]["stop_details"]["explanation"] = "Explanation text from the API."
    _rejects(explained, "unknown key")


def test_read_plates_recorded_decline_is_served_as_a_decline() -> None:
    """Mutation round2_responses_for_read_plate_refusal (also in test_round2_cassettes)."""
    data = flo()
    data["read_plate"] = copy.deepcopy(REFUSAL)
    data["research"]["script"], data["research"]["tool_results"], data["synthesize"] = [], [], []
    cassette = cassette_from_dict(data, "declined plate read")
    assert cassette.responses_for("read_plate") == [REFUSAL]


def test_recorded_names_a_live_mode_and_each_nodes_model() -> None:
    """Mutation round2_recorded_mode_unchecked: the loader accepts any recorded mode."""
    data = in_blocks(flo())
    data["recorded"]["mode"] = "replay"
    _rejects(data, "recorded.mode")
    data = in_blocks(flo())
    data["recorded"]["models"]["safety_check"] = config.HAIKU
    _rejects(data, "recorded.models.safety_check")


def test_a_lookups_recording_state_names_the_unit_and_the_top_up_limits() -> None:
    """Mutation round2_lookup_state_no_identity (also in test_round2_cassettes)."""
    identity = {"manufacturer": "Trane", "model": "XR16 4TTR6036", "serial": None, "manufacture_date": None}
    state = lookup_recording_state(identity, {"status": "model_declined", "stop_reason": "model_declined: cyber "
                                              "(research)", "search_trail": []}, research_limits="top_up")
    assert state["identity"] == identity and state["photo"] is None and state["symptom"]
    assert (state["refusal_origin"], state["research_limits"], state["route"]) == ("model_declined", "top_up", [])
