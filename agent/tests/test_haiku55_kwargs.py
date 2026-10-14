"""Decision 69: the exact constructor arguments and request body of every live call, per node and mode.

Constructing ChatAnthropic builds no client and makes no call; the request body comes from
_get_request_payload. Sockets stay blocked. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import config, models
from agent.declined import ModelDeclined
from agent.research.agent import RETRY_ON
from agent.schemas import Extraction
from agent.state import RunContext

HAIKU, SONNET = config.HAIKU, config.SONNET


def haiku(max_tokens: int, effort: str) -> dict:
    return {"model": HAIKU, "max_tokens": max_tokens, "timeout": 30 + max_tokens / 200, "max_retries": 0,
            "reasoning_effort": effort}


EXPECTED = {
    ("cheap", "read_plate"): haiku(2_048, "low"),
    ("cheap", "classifier"): haiku(1_024, "low"),
    ("cheap", "research"): haiku(4_096, "high"),
    ("cheap", "synthesize"): haiku(16_000, "medium"),
    ("full", "read_plate"): haiku(2_048, "low"),
    ("full", "classifier"): haiku(1_024, "low"),
    ("full", "research"): haiku(4_096, "high"),
    # Full mode's Sonnet 5 synthesize, unchanged by decision 69.
    ("full", "synthesize"): {"model": SONNET, "max_tokens": 16_000, "timeout": 110.0, "max_retries": 0,
                             "reasoning_effort": "medium"},
}


@pytest.mark.parametrize(("mode", "node"), list(EXPECTED))
def test_exact_live_kwargs_per_node_and_mode(mode: str, node: str) -> None:
    """Mutation haiku55_effort_not_sent: live_model_kwargs sends no effort to Claude Haiku 5.5."""
    assert models.live_model_kwargs(node, mode) == EXPECTED[(mode, node)]


@pytest.mark.parametrize(("mode", "node"), list(EXPECTED))
def test_request_body_sends_effort_and_never_a_thinking_budget_sampling_or_fallbacks(
        mode: str, node: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation haiku55_thinking_budget_sent: a manual thinking budget, which Claude Haiku 5.5 answers
    with HTTP 400, is added to every Haiku call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")
    ctx = RunContext(run_id="r", mode=mode, ledger_path=tmp_path / "l", registry_path=tmp_path / "r",
                     graph_path=tmp_path / "g", pages_dir=tmp_path / "p")
    chat = models.make_model(node, ctx)
    assert isinstance(chat, models.DeclineAwareChatAnthropic)
    body = chat._get_request_payload([("user", "hi")])
    expected = EXPECTED[(mode, node)]
    assert {k: v for k, v in body.items() if k != "messages"} == {
        "model": expected["model"], "max_tokens": expected["max_tokens"],
        "output_config": {"effort": expected["reasoning_effort"]},
        **({"thinking": {"type": "adaptive", "display": "summarized"}} if expected["model"] == SONNET else {}),
    }
    if expected["model"] == HAIKU:
        text = json.dumps(body)
        for name in (*config.NEVER_SEND_PARAMS, *config.NEVER_SEND_TO_HAIKU):
            assert f'"{name}"' not in text, name
        # A structured call keeps the effort beside its schema.
        structured = chat.bind(output_config={"format": {"type": "json_schema",
                                                         "schema": Extraction.model_json_schema()}})
        body = chat._get_request_payload([("user", "hi")], **structured.kwargs)
        assert body["output_config"]["effort"] == expected["reasoning_effort"]
        assert "thinking" not in body


def test_live_calls_are_single_replies_and_a_decline_is_never_retried() -> None:
    """Mutations: haiku55_streaming_left_on (the live class may stream, and a streamed reply carries no
    stop_details, so a decline's category would be lost); haiku55_decline_retryable."""
    assert models.ChatAnthropic is models.DeclineAwareChatAnthropic
    chat = models.DeclineAwareChatAnthropic(model=HAIKU, max_tokens=10, api_key="test-dummy-key")
    assert chat.disable_streaming is True
    assert chat._should_stream(async_api=False, stream=True) is False
    # The research loop's one retry is for transport and rate limit errors only.
    assert not issubclass(ModelDeclined, RETRY_ON)
