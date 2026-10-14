"""Decision 69 review findings H1 to H4, offline.

- H1: a research turn declined after a search lists that search in the decline brief.
- H2: replay reservations stay as recorded: replay estimates take the recorded model's margin and
  tool prompt tokens, not Claude Haiku 5.5's, and so does the replay retry threshold (decision 23).
- H3: an HTTP 400 on the plate probe is blamed on the photo only when its message names the image.
- H4: every test file config.py names exists.

The replay runs read synthetic or recorded cassettes; the capabilities check runs on a fake client
with fake keys in a temp .env. Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from agent import cli, config, graph, models
from agent.ledger import Ledger, price_usage
from agent.replay.replay_model import ReplayChatModel
from agent.tests.helpers import load_case, replay_ctx, run_case
from agent.tests.test_haiku55_capabilities import FakeClient, check_capabilities, read_record
from agent.tests.test_haiku55_declines_graph import REFUSAL
from agent.tests.test_live_path import live_env  # noqa: F401  (live_env is a fixture)
from agent.tests.test_sc8_history import FIXTURE_GRAPH, OPTIMA, final_state, run_phase3_case

# ---------------------------------------------------------------------------
# H1
# ---------------------------------------------------------------------------


def test_a_research_decline_after_a_search_lists_that_search(tmp_path: Path) -> None:
    """The research model searches once, then declines: the decline brief and the run's trail both
    name the search that was made and charged. Mutation: research_decline_brief_misses_this_pass."""
    cassette = load_case("synthetic/fanout_top_up")
    first_turn = cassette.data["research"]["script"][0]
    ctx = replay_ctx(tmp_path, cassette)
    ctx.replay.setdefault("models", {})["research"] = ReplayChatModel(
        responses=[first_turn, dict(REFUSAL)], model=config.MODEL_FOR["replay"]["research"],
        max_tokens=models.max_tokens_for("research", "replay"))
    _, ctx, outcomes = run_phase3_case(cassette, tmp_path, appliance_id=OPTIMA, graph=FIXTURE_GRAPH, ctx=ctx)
    state = final_state(outcomes)
    assert state["stop_reason"] == "model_declined: cyber (research)"
    query = first_turn["message"]["tool_calls"][0]["args"]["query"]
    assert [e["query"] for e in state["search_trail"] if e["tool"] == "search"] == [query]
    assert ctx.replay["stubs"]["search"].calls == 1
    assert state["brief"]["no_reliable_answer"]["searched"] == [query]


# ---------------------------------------------------------------------------
# H2
# ---------------------------------------------------------------------------

# Each replay reservation's input estimate, (node, input tokens), as build b9e4ca3 (HEAD before this
# PRD, on Claude Haiku 4.5) made them. The synthesize rows are 40 tokens above that build's 2799 and
# 2843 because decision 76 added one sentence to the synthesis prompt; on Claude Haiku 5.5's margin
# they read 3691 and 3748, and the research rows 1765 onward.
RECORDED_RESERVATIONS = {
    "flo": [("read_plate", 1923), ("research", 1137), ("research", 1464), ("research", 1799), ("research", 2110),
            ("research", 2443), ("research", 2774), ("synthesize", 2839)],
    "hvac": [("research", 1137), ("research", 1483), ("research", 1818), ("research", 2153), ("research", 2489),
             ("research", 2825), ("synthesize", 2883)],
}


@pytest.mark.parametrize("case", sorted(RECORDED_RESERVATIONS))
def test_replay_reservations_use_the_recorded_models_estimate(case: str, tmp_path: Path) -> None:
    """Mutations: haiku55_replay_estimate_live_margin (replay estimates take Claude Haiku 5.5's margin),
    haiku55_replay_tool_tokens_live (replay estimates take its tool prompt tokens),
    haiku55_research_estimate_mode_dropped, haiku55_synthesize_estimate_mode_dropped."""
    _, ctx, outcomes = run_case(load_case(case), tmp_path)
    assert outcomes[-1].state["status"] == "ok"
    rows = Ledger(ctx.ledger_path).rows(ctx.run_id)
    reserved = [(r["node"], r["input_tokens"]) for r in rows if r["kind"] == "reserve" and r["provider"] == "anthropic"]
    assert reserved == RECORDED_RESERVATIONS[case]
    assert (config.REPLAY_ESTIMATE_MARGIN, config.REPLAY_TOOL_PROMPT_TOKENS) == (1.25, 588)


def test_the_replay_retry_threshold_is_the_recorded_one() -> None:
    """graph.retry_need_usd("replay") and the replay synthesize reservation are build b9e4ca3's, while
    live modes keep Claude Haiku 5.5's margin. Mutation: haiku55_replay_synthesize_reservation_live_margin."""
    assert graph.synthesize_reservation_usd("replay") == pytest.approx(0.04775)
    assert graph.retry_need_usd("replay") == pytest.approx(0.064052, abs=1e-6)
    live_in = math.ceil((config.SYNTH_SOURCE_TOKEN_BUDGET + config.SYNTH_PROMPT_TOKENS_ESTIMATE)
                        * config.ESTIMATE_MARGIN[config.HAIKU])
    assert graph.synthesize_reservation_usd("cheap") == pytest.approx(price_usage(
        config.HAIKU, {"input_tokens": live_in, "output_tokens": models.max_tokens_for("synthesize", "cheap")}))


# ---------------------------------------------------------------------------
# H3
# ---------------------------------------------------------------------------


class _Http400(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@pytest.mark.parametrize(("message", "image"), [
    ("output_config.effort: Input should be 'low', 'medium' or 'high' (synthetic)", "unchecked"),
    ("output_config.format.schema: unsupported keyword (synthetic)", "unchecked"),
    ("Image input is not supported for this model (synthetic)", "refused"),
    ("messages.0.content.1.source.media_type: unsupported (synthetic)", "refused"),
], ids=["effort", "schema", "image", "media_type"])
def test_only_a_400_that_names_the_image_turns_plate_reading_off(
        message: str, image: str, live_env: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """A 400 about effort or the schema still stops the check, with its message, but leaves image
    input unchecked, so plate reading stays on. Mutation: haiku55_capabilities_any_400_refuses_photo."""
    client = FakeClient(fail={"read_plate": _Http400(message)})
    monkeypatch.setattr(models, "ChatAnthropic", client)
    code, out, err = check_capabilities(monkeypatch, capsys, "proceed\n", "--new-ledger")
    assert code == cli.EXIT_LIVE_ERROR, out + err
    record = read_record()
    assert record["image_input"] == image and not record["passed"] and len(client.built) == 1
    assert message in out
    if image == "unchecked":
        assert "refused the plate photo" not in out
        assert "STOP: a call was not accepted or did not parse" in out
    else:
        assert "refused the plate photo" in out


# ---------------------------------------------------------------------------
# H4
# ---------------------------------------------------------------------------


def test_every_test_file_config_names_exists() -> None:
    """A comment that cites a test for its arithmetic names a real file and test. Mutation:
    config_cites_missing_test."""
    text = (config.REPO_ROOT / "agent" / "config.py").read_text(encoding="utf-8")
    tests_dir = config.REPO_ROOT / "agent" / "tests"
    named = re.findall(r"\b(test_\w+\.py)\b", text)
    assert named, "config.py names no test file"
    for name in named:
        assert (tests_dir / name).exists(), name
    cited = "test_a_cheap_runs_worst_case_reservation_fits_the_run_and_build_caps"
    assert cited in text and f"def {cited}(" in (tests_dir / "test_haiku55_config.py").read_text(encoding="utf-8")
