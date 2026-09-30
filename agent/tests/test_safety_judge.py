"""The Jev judge (agent/safety_judge.py): live on a mock transport, and replay. Offline, $0.

The live judge runs the real typesafe-sdk client with api_key="offline-test"
and an httpx2.MockTransport, so the SDK's own parsing, error classes and
retries run while the network stays blocked. Each failure kind must come out
as SafetyCheckError of that kind, settle its ledger reservation the right way
(charged, charged at the reservation, or released), and land in the capture
list the recorder writes into a cassette.

Every test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from agent import config, prompts
from agent.ledger import Ledger
from agent.replay.cassettes import CassetteError, check_safety_check_entries
from agent.safety_judge import (
    FAILURE_KINDS,
    JudgeReply,
    SafetyCheckError,
    build_live_judge,
    build_replay_judge,
    capture_entry,
    question_hash,
)

RUN = "t-jev"
STEP_A = "a1" * 32
STEP_B = "b2" * 32
STATE = {"appliance": "hot tub", "step": "Turn off power at the breaker, wait 20 minutes, then restore power",
         "detail": "This resets the flow switch sensor"}
NOUL = prompts.SAFETY_STEP_NOUL
QHASH = question_hash(NOUL["instructions"], NOUL["criteria"])
PLANTED_KEY = "offline-test-" + "planted-key-" + "0123456789"  # split: no key shaped literal
JEV_INPUT_PRICE = config.PRICES_PER_MTOK[config.JEV_MODEL]["input"] / 1_000_000
RESERVED_USD = config.JEV_EST_INPUT_TOKENS * (1 + config.JEV_RETRY_MAX) * JEV_INPUT_PRICE
# Retry-After of 0 ms, so a retried status is retried at once.
NO_WAIT = {"retry-after-ms": "0"}


def answer(noul: Any = 0.97, *, model: str = config.JEV_MODEL, usage: dict | None = None,
           request_id: str | None = "req-jev-1") -> httpx2.Response:
    body = {"model": model, "answers": {"safety_step": {"type": "noul", "noul": noul}},
            "usage": usage if usage is not None else {"input_tokens": 384, "output_tokens": 3}}
    headers = {"x-typesafe-request-id": request_id} if request_id else {}
    return httpx2.Response(200, json=body, headers=headers)


class Api:
    """A scripted TypeSafe API: each request gets the next reply, after the ledger check."""

    def __init__(self, ledger_path: Path, *replies: httpx2.Response | Callable[[httpx2.Request], Any]) -> None:
        self.ledger_path = ledger_path
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.reserved_at_call: list[bool] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.reserved_at_call.append(open_jev_reservations(self.ledger_path) > 0)
        self.requests.append(json.loads(request.content))
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        return reply(request) if callable(reply) else reply


def open_jev_reservations(path: Path) -> int:
    rows = Ledger(path).rows()
    reserved = {r["id"] for r in rows if r["kind"] == "reserve" and r["provider"] == config.JEV_PROVIDER}
    settled = {int(r["note"].split()[1]) for r in rows if r["kind"] == "release"}
    return len(reserved - settled)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite", create=True)


def judge_for(ledger: Ledger, api: Api | None, *, capture: list | None = None, key: str | None = "offline-test",
              run_cap_usd: float | None = None, mode: str = "cheap") -> Any:
    options: dict[str, Any] = {}
    if key is not None:
        options["api_key"] = key
    if api is not None:
        options["transport"] = httpx2.MockTransport(api)
    return build_live_judge(ledger=ledger, run_id=RUN, mode=mode, instructions=NOUL["instructions"],
                            criteria=NOUL["criteria"], capture=capture, run_cap_usd=run_cap_usd,
                            secrets=[PLANTED_KEY], client_options=options)


def failure(judge: Any, step: str = STEP_A) -> SafetyCheckError:
    with pytest.raises(SafetyCheckError) as caught:
        judge.ask(step, dict(STATE))
    return caught.value


def ledger_rows(ledger: Ledger) -> list[tuple[str, str | None, int]]:
    return [(r["kind"], r["provider"], r["estimated"]) for r in ledger.rows(RUN)]


# ---------------------------------------------------------------------------
# Live: success
# ---------------------------------------------------------------------------


def test_success_is_reserved_first_charged_after_and_captured(ledger: Ledger) -> None:
    """Mutations: safety_judge_calls_before_reserve (a call leaves before its reservation);
    safety_judge_charges_the_estimate (a reply with usage is charged at the reservation);
    safety_judge_skips_capture (a reply is not captured); ledger_jev_output_priced (output is
    charged); ledger_provider_not_carried (the reserve row says anthropic)."""
    api = Api(ledger.path, answer(0.97, usage={"input_tokens": 384, "output_tokens": 3}))
    capture: list = []
    reply = judge_for(ledger, api, capture=capture).ask(STEP_A, dict(STATE))
    assert reply == JudgeReply(step_sha256=STEP_A, noul=0.97, model=config.JEV_MODEL, request_id="req-jev-1",
                               input_tokens=384, output_tokens=3, question_hash=QHASH)
    assert api.reserved_at_call == [True]
    assert api.requests[0]["state"] == STATE and api.requests[0]["model"] == config.JEV_MODEL
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("charge", "typesafe", 0), ("release", "typesafe", 0)]
    charge = [r for r in ledger.rows(RUN) if r["kind"] == "charge"][0]
    assert (charge["input_tokens"], charge["output_tokens"]) == (384, 3)
    assert charge["usd"] == pytest.approx(384 * JEV_INPUT_PRICE)  # output at $0
    assert charge["node"] == "safety_check" and charge["model"] == config.JEV_MODEL
    assert capture == [capture_entry(STEP_A, QHASH, model=config.JEV_MODEL, noul=0.97, input_tokens=384,
                                     output_tokens=3, request_id="req-jev-1")]
    check_safety_check_entries(capture)


def test_request_id_read_defensively(ledger: Ledger) -> None:
    """Mutation safety_judge_request_id_raises: the reply's request_id property is read bare, so a
    reply with no request ID header becomes a failed check."""
    api = Api(ledger.path, answer(0.2, request_id=None), answer(0.3, request_id="req-2"))
    judge = judge_for(ledger, api)
    assert judge.ask(STEP_A, dict(STATE)).request_id is None
    assert judge.ask(STEP_B, dict(STATE)).request_id == "req-2"


def test_a_reply_with_no_usage_is_charged_at_its_reservation(ledger: Ledger) -> None:
    """Mutation safety_judge_no_usage_free: a reply that reports no input tokens is charged 0."""
    api = Api(ledger.path, answer(0.4, usage={}))
    reply = judge_for(ledger, api).ask(STEP_A, dict(STATE))
    assert reply.input_tokens is None and reply.noul == 0.4
    [charge] = [r for r in ledger.rows(RUN) if r["kind"] == "charge"]
    assert charge["estimated"] == 1 and charge["usd"] == pytest.approx(RESERVED_USD)


# ---------------------------------------------------------------------------
# Live: each failure kind
# ---------------------------------------------------------------------------


def test_401_is_an_api_error_released_with_no_key_in_the_detail(ledger: Ledger) -> None:
    """Mutations: safety_judge_detail_not_redacted (an error body that echoes the key reaches the
    detail); safety_judge_error_charged (an error status is charged instead of released)."""
    body = {"error": {"message": f"bad key {PLANTED_KEY}"}}
    api = Api(ledger.path, httpx2.Response(401, json=body, headers={"x-typesafe-request-id": "req-401"}))
    capture: list = []
    err = failure(judge_for(ledger, api, capture=capture, key=PLANTED_KEY))
    assert err.kind == "api_error" and "401" in err.detail
    assert PLANTED_KEY not in err.detail and PLANTED_KEY not in str(err)
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("release", "typesafe", 0)]
    assert ledger.run_total(RUN) == 0
    assert capture == [capture_entry(STEP_A, QHASH, model=config.JEV_MODEL, request_id="req-401", error="api_error")]
    assert len(api.requests) == 1  # 401 is not retried


def test_429_is_rate_limited_retried_once_then_released(ledger: Ledger) -> None:
    """Mutations: safety_judge_429_as_api_error; safety_judge_retry_default (the SDK's two retries)."""
    api = Api(ledger.path, httpx2.Response(429, json={"error": "slow down"}, headers=NO_WAIT))
    err = failure(judge_for(ledger, api))
    assert err.kind == "rate_limited"
    assert len(api.requests) == 1 + config.JEV_RETRY_MAX
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("release", "typesafe", 0)]


def test_500_is_an_api_error_released(ledger: Ledger) -> None:
    """Mutation safety_judge_error_charged."""
    api = Api(ledger.path, httpx2.Response(500, json={"error": "boom"}, headers=NO_WAIT))
    err = failure(judge_for(ledger, api))
    assert err.kind == "api_error" and len(api.requests) == 1 + config.JEV_RETRY_MAX
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("release", "typesafe", 0)]


def test_500_then_success_is_answered(ledger: Ledger) -> None:
    """Mutation safety_judge_no_retry (max_retries 0): one 500 fails the check."""
    api = Api(ledger.path, httpx2.Response(500, json={"error": "boom"}, headers=NO_WAIT), answer(0.8))
    assert judge_for(ledger, api).ask(STEP_A, dict(STATE)).noul == 0.8
    assert len(api.requests) == 2 and ledger_rows(ledger)[1] == ("charge", "typesafe", 0)


def _raise(exc_type: type[Exception]) -> Callable[[httpx2.Request], Any]:
    def reply(request: httpx2.Request) -> Any:
        raise exc_type("offline", request=request)
    return reply


def test_timeout_is_charged_at_its_reservation(ledger: Ledger) -> None:
    """Mutation safety_judge_timeout_released: a timed out call, which may have been billed, is
    released instead of charged at its reservation."""
    api = Api(ledger.path, _raise(httpx2.ReadTimeout))
    err = failure(judge_for(ledger, api))
    assert err.kind == "timeout"
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("charge", "typesafe", 1), ("release", "typesafe", 0)]
    assert ledger.run_total(RUN) == pytest.approx(RESERVED_USD)


def test_timeout_then_answer_charges_the_lost_attempt(ledger: Ledger) -> None:
    """The SDK retries a timed out attempt inside one call (decision 57), and that attempt may
    have been billed, so the answered call is charged at its two attempt reservation, marked
    estimated, not at the answer's usage alone.

    Mutations: safety_judge_lost_not_counted (the SDK's own timeout rule retries, so the judge
    never sees the lost attempt); safety_judge_timeouts_not_retried (the predicate counts a lost
    attempt but does not retry it)."""
    api = Api(ledger.path, _raise(httpx2.ReadTimeout), answer(0.9))
    capture: list = []
    reply = judge_for(ledger, api, capture=capture).ask(STEP_A, dict(STATE))
    assert reply.noul == 0.9 and reply.input_tokens == 384
    assert len(api.requests) == 2
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("charge", "typesafe", 1), ("release", "typesafe", 0)]
    assert ledger.run_total(RUN) == pytest.approx(RESERVED_USD)
    assert capture[0]["noul"] == 0.9 and capture[0]["error"] is None


def test_timeout_then_error_status_charges_the_reservation(ledger: Ledger) -> None:
    """A timed out attempt then an error status: the call failed, but its first attempt may have
    been billed, so the reservation is charged (estimated), not released.

    Mutations: safety_judge_lost_then_error_released; safety_judge_lost_not_counted."""
    api = Api(ledger.path, _raise(httpx2.ReadTimeout), httpx2.Response(503, json={"error": "down"}, headers=NO_WAIT))
    err = failure(judge_for(ledger, api))
    assert err.kind == "api_error" and len(api.requests) == 2
    assert ledger_rows(ledger) == [("reserve", "typesafe", 0), ("charge", "typesafe", 1), ("release", "typesafe", 0)]
    assert ledger.run_total(RUN) == pytest.approx(RESERVED_USD)


def test_connection_error_is_an_api_error_charged_at_its_reservation(ledger: Ledger) -> None:
    """Mutation safety_judge_connection_released."""
    api = Api(ledger.path, _raise(httpx2.ConnectError))
    err = failure(judge_for(ledger, api))
    assert err.kind == "api_error" and "connection" in err.detail
    assert ledger_rows(ledger)[1] == ("charge", "typesafe", 1)


def test_unparseable_body_is_unparseable(ledger: Ledger) -> None:
    """Mutation safety_judge_validation_as_api_error: a 200 that does not parse is taken for an error
    status and released, though it was billed."""
    api = Api(ledger.path, httpx2.Response(200, content=b"<html>not json</html>"))
    err = failure(judge_for(ledger, api))
    assert err.kind == "unparseable"
    assert ledger_rows(ledger)[1] == ("charge", "typesafe", 1)


def test_an_answer_without_its_noul_is_unparseable(ledger: Ledger) -> None:
    """Mutations: safety_judge_missing_noul_is_zero (a reply with no safety_step answer reads as 0);
    safety_judge_noul_range_unchecked (a probability above 1 is accepted)."""
    usage = {"input_tokens": 380, "output_tokens": 0}
    no_answer = httpx2.Response(200, json={"model": config.JEV_MODEL, "answers": {}, "usage": usage})
    no_field = httpx2.Response(200, json={"model": config.JEV_MODEL, "usage": usage,
                                          "answers": {"safety_step": {"type": "noul"}}})
    api = Api(ledger.path, no_answer, no_field, answer(1.5))
    judge = judge_for(ledger, api)
    for step in (STEP_A, STEP_B, STEP_A):
        assert failure(judge, step).kind == "unparseable"
    charges = [r for r in ledger.rows(RUN) if r["kind"] == "charge"]
    # Each reply was billed: its reported usage is charged (the SDK's parse error keeps the body).
    assert [(c["input_tokens"], c["estimated"]) for c in charges] == [(380, 0), (380, 0), (384, 0)]


def test_a_different_model_string_is_a_failed_check(ledger: Ledger) -> None:
    """Mutation safety_judge_model_unchecked: an answer from another Jev version counts."""
    api = Api(ledger.path, answer(0.97, model="jev-latest"))
    capture: list = []
    err = failure(judge_for(ledger, api, capture=capture))
    assert err.kind == "model_mismatch"
    assert capture[0]["model"] == "jev-latest" and capture[0]["noul"] is None
    assert capture[0]["error"] == "model_mismatch" and capture[0]["request_id"] == "req-jev-1"
    assert ledger_rows(ledger)[1] == ("charge", "typesafe", 0)  # billed all the same


def test_missing_key_fails_at_construction_before_any_reservation(ledger: Ledger,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation safety_judge_client_outside_guard: the client is built outside the guard, so a
    missing key is reported as some other failure."""
    monkeypatch.delenv(config.TYPESAFE_KEY_NAME, raising=False)
    api = Api(ledger.path, answer())
    capture: list = []
    err = failure(judge_for(ledger, api, capture=capture, key=None))
    assert err.kind == "missing_key"
    assert api.requests == [] and ledger.rows(RUN) == []
    assert capture == [capture_entry(STEP_A, QHASH, model=config.JEV_MODEL, error="missing_key")]


def test_refused_reservation_is_a_failed_check_and_nothing_is_sent(ledger: Ledger) -> None:
    """Mutation safety_judge_reservation_escapes: a refused reservation is re-raised and reported as
    some other failure."""
    api = Api(ledger.path, answer())
    err = failure(judge_for(ledger, api, run_cap_usd=0.0))
    assert err.kind == "reservation_refused" and "run_cap" in err.detail
    assert api.requests == []
    assert ledger_rows(ledger) == [("stop", "typesafe", 0)]


def test_any_other_exception_is_a_failed_check(ledger: Ledger) -> None:
    """Mutation safety_judge_other_escapes: an unexpected error leaves ask as itself."""
    def broken(request: httpx2.Request) -> Any:
        raise RuntimeError("an unexpected transport bug")

    err = failure(judge_for(ledger, Api(ledger.path, broken)))
    assert err.kind == "other" and "RuntimeError" in err.detail
    assert ledger_rows(ledger)[1] == ("charge", "typesafe", 1)


def test_live_judge_refuses_replay_mode(ledger: Ledger) -> None:
    with pytest.raises(ValueError):
        judge_for(ledger, None, mode="replay")


# ---------------------------------------------------------------------------
# Question hash and errors
# ---------------------------------------------------------------------------


def test_question_hash_is_canonical_and_short() -> None:
    """Mutation safety_judge_hash_ignores_criteria: a new criteria keeps the old hash."""
    assert len(QHASH) == 16 and all(ch in "0123456789abcdef" for ch in QHASH)
    assert QHASH == question_hash(NOUL["instructions"], NOUL["criteria"])
    criteria = {"true": "switches power", "false": "normal use"}
    reordered = {"false": "normal use", "true": "switches power"}
    assert question_hash("q", criteria) == question_hash("q", reordered)
    assert question_hash("q", criteria) != question_hash("q", None)
    assert question_hash("q", None) != question_hash("q.", None)


def test_failure_kinds_are_closed() -> None:
    assert set(FAILURE_KINDS) == {"reservation_refused", "timeout", "rate_limited", "api_error", "unparseable",
                                  "model_mismatch", "missing_key", "not_recorded", "other"}
    with pytest.raises(ValueError):
        SafetyCheckError("flaky")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def recorded(step: str, noul: float | None = 0.9, **overrides: Any) -> dict[str, Any]:
    entry = capture_entry(step, QHASH, model=config.JEV_MODEL, noul=noul, input_tokens=384, output_tokens=3,
                          request_id=f"req-{step[:4]}")
    entry.update(overrides)
    return entry


def test_replay_serves_by_step_hash_in_recorded_order() -> None:
    """Mutations: safety_judge_replay_by_position (entries served in list order, whatever the step);
    safety_judge_replay_reuses_entry (an entry is served again, so a second pass never runs out)."""
    judge = build_replay_judge([recorded(STEP_A, 0.1), recorded(STEP_B, 0.9), recorded(STEP_A, 0.2)],
                               question_hash=QHASH)
    assert judge.question_hash == QHASH
    assert judge.ask(STEP_B, {}).noul == 0.9
    first = judge.ask(STEP_A, {})
    assert (first.noul, first.request_id, first.question_hash) == (0.1, f"req-{STEP_A[:4]}", QHASH)
    assert judge.ask(STEP_A, {}).noul == 0.2
    with pytest.raises(SafetyCheckError) as caught:
        judge.ask(STEP_A, {})
    assert caught.value.kind == "not_recorded"


def test_replay_refuses_another_question_hash_or_model() -> None:
    """Mutations: safety_judge_replay_hash_unchecked; safety_judge_replay_model_unchecked."""
    with pytest.raises(CassetteError, match="question hash"):
        build_replay_judge([recorded(STEP_A, question_hash="0" * 16)], question_hash=QHASH)
    with pytest.raises(CassetteError, match="model"):
        build_replay_judge([recorded(STEP_A, model="jev-1.12.0")], question_hash=QHASH)
    # A recorded mismatch replays as the failure it was, and must name the other model.
    judge = build_replay_judge([recorded(STEP_A, None, model="jev-latest", error="model_mismatch")],
                               question_hash=QHASH)
    with pytest.raises(SafetyCheckError) as caught:
        judge.ask(STEP_A, {})
    assert caught.value.kind == "model_mismatch"
    with pytest.raises(CassetteError):
        build_replay_judge([recorded(STEP_A, None, error="model_mismatch")], question_hash=QHASH)


def test_replay_not_recorded_and_recorded_failures() -> None:
    """Mutation safety_judge_replay_error_served_as_answer: a recorded failure replays as an answer."""
    judge = build_replay_judge([], question_hash=QHASH)
    with pytest.raises(SafetyCheckError) as caught:
        judge.ask(STEP_A, {})
    assert caught.value.kind == "not_recorded"
    judge = build_replay_judge([recorded(STEP_A, None, error="timeout", input_tokens=None, output_tokens=None,
                                         request_id=None)], question_hash=QHASH)
    with pytest.raises(SafetyCheckError) as caught:
        judge.ask(STEP_A, {})
    assert caught.value.kind == "timeout"


def test_live_capture_replays_to_the_same_answers(ledger: Ledger) -> None:
    """A live run's capture, recorded as a cassette's safety_check list, replays to the same
    replies and failures (live and replay share the entry format). Mutation
    safety_judge_replay_drops_request_id: the replayed reply loses its request ID."""
    api = Api(ledger.path, answer(0.97), httpx2.Response(429, json={"error": "slow"}, headers=NO_WAIT))
    capture: list = []
    live = judge_for(ledger, api, capture=capture)
    live_reply = live.ask(STEP_A, dict(STATE))
    live_error = failure(live, STEP_B)
    check_safety_check_entries(capture)
    replay = build_replay_judge(json.loads(json.dumps(capture)), question_hash=live.question_hash)
    assert replay.ask(STEP_A, {}) == live_reply
    with pytest.raises(SafetyCheckError) as caught:
        replay.ask(STEP_B, {})
    assert caught.value.kind == live_error.kind == "rate_limited"


def test_capture_holds_no_step_text(ledger: Ledger, tmp_path: Path) -> None:
    """Mutation safety_judge_capture_keeps_state: the capture entry carries the state Jev read."""
    capture: list = []
    judge_for(ledger, Api(ledger.path, answer()), capture=capture).ask(STEP_A, dict(STATE))
    text = json.dumps(capture)
    for value in STATE.values():
        assert value not in text
    assert list(capture[0]) == ["step_sha256", "question_hash", "model", "noul", "input_tokens",
                                "output_tokens", "request_id", "error"]


def test_build_cap_refuses_a_jev_call_that_would_cross_it(ledger: Ledger) -> None:
    """Mutation safety_judge_reserves_as_replay: the Jev hold is written in replay mode, which the
    build cap never counts, so a call over the cap is sent."""
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd) VALUES "
                     "('2026-09-01T00:00:00+00:00', 'earlier', 'cheap', 'synthesize', 'anthropic', 'charge', ?)",
                     (config.BUILD_CAP_USD - RESERVED_USD / 2,))
    api = Api(ledger.path, answer())
    err = failure(judge_for(ledger, api))
    assert err.kind == "reservation_refused" and "build_cap" in err.detail
    assert api.requests == []
    [stop] = ledger.rows(RUN)
    assert (stop["kind"], stop["provider"]) == ("stop", config.JEV_PROVIDER)
