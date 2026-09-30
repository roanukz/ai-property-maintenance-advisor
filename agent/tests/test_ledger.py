"""SC11 ledger tests (PLAN section 5, section 8.9). Offline, $0.

Each test names the mutation that turns it red.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from pathlib import Path

import pytest
from anthropic.types import CacheCreation, Usage
from langchain_anthropic.chat_models import _create_usage_metadata
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool

from agent import config
from agent.ledger import (
    BudgetExceeded,
    Ledger,
    LedgerMissing,
    estimate_input_tokens,
    price_usage,
)

HAIKU = config.HAIKU
IN = config.PRICES_PER_MTOK[HAIKU]["input"] / 1_000_000
OUT = config.PRICES_PER_MTOK[HAIKU]["output"] / 1_000_000


def _cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * IN + output_tokens * OUT


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger" / "ledger.sqlite", create=True)


def _preload(path: Path, *, mode: str, usd: float = 0.0, credits: int = 0) -> None:
    """Write a past charge row straight into the file, as an earlier phase would have."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd, tavily_credits)"
            " VALUES ('2026-09-01T00:00:00+00:00', 'earlier', ?, 'synthesize', 'anthropic',"
            " 'charge', ?, ?)",
            (mode, usd, credits),
        )


class _FakeModel:
    """Stands in for a paid model: counts how often it is actually called."""

    def __init__(self) -> None:
        self.received: list[str] = []

    def invoke(self, prompt: str) -> dict:
        self.received.append(prompt)
        return {"input_tokens": 100, "output_tokens": 10}


def _guarded_call(ledger: Ledger, fake: _FakeModel, run_id: str, **reserve_kw) -> float:
    """The pattern every paid node follows: reserve, call, charge."""
    reservation = ledger.reserve(run_id, **reserve_kw)
    usage = fake.invoke("prompt")
    return ledger.charge(reservation, usage)


def test_call_refused_before_it_is_made(ledger: Ledger) -> None:
    # Mutation: move the cap check in reserve() after the reserve row insert (or
    # drop it); the reserve row appears and the model is called.
    model = _FakeModel()
    kw = dict(mode="replay", node="synthesize", model=HAIKU, run_cap_usd=0.01)
    # 5,000 in and 2,000 out at Haiku rates is 0.015, over a 0.01 cap.
    with pytest.raises(BudgetExceeded) as err:
        _guarded_call(ledger, model, "r1", input_tokens_est=5_000, max_tokens=2_000, **kw)
    assert err.value.reason == "run_cap"
    assert model.received == []
    kinds = [r["kind"] for r in ledger.rows("r1")]
    assert kinds == ["stop"]
    assert ledger.run_total("r1") == 0
    # A call that fits the same cap goes through, so the refusal was the cap.
    _guarded_call(ledger, model, "r1", input_tokens_est=1_000, max_tokens=500, **kw)
    assert len(model.received) == 1


def test_actual_over_reservation_is_recorded_and_blocks_next_call(ledger: Ledger) -> None:
    # Mutation: cap the charge at the reservation (usd = min(usd, reservation.usd));
    # the recorded total drops to the reservation and the next call fits.
    first = ledger.reserve("run", mode="cheap", node="research", model=HAIKU,
                           input_tokens_est=20_000, max_tokens=6_000)
    assert first.usd == pytest.approx(_cost(20_000, 6_000))
    actual = ledger.charge(first, {"input_tokens": 40_000, "output_tokens": 6_000})
    assert actual == pytest.approx(_cost(40_000, 6_000))
    assert ledger.run_total("run") == pytest.approx(actual)

    charge_row = [r for r in ledger.rows("run") if r["kind"] == "charge"][0]
    overshoot = actual - first.usd
    assert "above reservation" in charge_row["note"]
    assert f"{overshoot:.6f}" in charge_row["note"]
    # The overshoot is exactly this call's excess input, nothing more.
    assert overshoot == pytest.approx(20_000 * IN)

    # 60,000 + 6,000 fits the cap next to a 20,000 + 6,000 charge but not next
    # to the 40,000 + 6,000 actually spent.
    assert _cost(20_000, 6_000) + _cost(60_000, 6_000) <= config.RUN_CAP_USD["cheap"]
    assert actual + _cost(60_000, 6_000) > config.RUN_CAP_USD["cheap"]
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve("run", mode="cheap", node="synthesize", model=HAIKU,
                       input_tokens_est=60_000, max_tokens=6_000)
    assert err.value.reason == "run_cap"
    assert ledger.rows("run")[-1]["kind"] == "stop"


def test_timed_out_call_is_charged_at_reservation(ledger: Ledger) -> None:
    # Mutation: charge_timeout() only releases (no charge row), or writes
    # estimated=0; run_total becomes 0 or the estimated flag is missing.
    res = ledger.reserve("t", mode="cheap", node="synthesize", model=HAIKU,
                         input_tokens_est=20_000, max_tokens=6_000)
    charged = ledger.charge_timeout(res)
    assert charged == pytest.approx(res.usd)
    assert ledger.run_total("t") == pytest.approx(res.usd)
    charges = [r for r in ledger.rows("t") if r["kind"] == "charge"]
    assert len(charges) == 1
    assert charges[0]["estimated"] == 1
    assert charges[0]["usd"] == pytest.approx(res.usd)
    # The hold is released, not counted twice: two more calls of the same size
    # fit beside the charge (3 x 0.05 = the 0.15 cap), a third does not. Were the
    # hold left open, the second of them would already be refused.
    assert 3 * res.usd == pytest.approx(config.RUN_CAP_USD["cheap"])
    for _ in range(2):
        ledger.reserve("t", mode="cheap", node="synthesize", model=HAIKU,
                       input_tokens_est=20_000, max_tokens=6_000)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("t", mode="cheap", node="synthesize", model=HAIKU,
                       input_tokens_est=20_000, max_tokens=6_000)


def test_live_run_refused_when_build_total_would_pass_cap(ledger: Ledger) -> None:
    # Mutation: assert_can_start_live_run() compares against 0 instead of the
    # run cap, or reserve() skips the build cap check in live modes.
    _preload(ledger.path, mode="cheap", usd=4.90)
    assert ledger.build_total() == pytest.approx(4.90)
    remaining = config.BUILD_CAP_USD - 4.90
    assert remaining < config.RUN_CAP_USD["cheap"]
    for mode in config.LIVE_MODES:
        with pytest.raises(BudgetExceeded) as err:
            ledger.assert_can_start_live_run(mode)
        assert err.value.reason == "build_cap"
    # A single call that would carry the build past its cap is refused too,
    # even though it fits the fresh run's own cap.
    big = dict(input_tokens_est=80_000, max_tokens=config.MAX_TOKENS["synthesize_cheap"])
    assert remaining < _cost(80_000, config.MAX_TOKENS["synthesize_cheap"]) <= config.RUN_CAP_USD["cheap"]
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve("new", mode="cheap", node="synthesize", model=HAIKU, **big)
    assert err.value.reason == "build_cap"
    # Replay is unaffected by live spend.
    ledger.reserve("replay-run", mode="replay", node="synthesize", model=HAIKU, **big)


def test_live_run_refused_when_ledger_missing(tmp_path: Path) -> None:
    # Mutation: Ledger() creates the file when it is missing and create is False.
    path = tmp_path / "ledger" / "ledger.sqlite"
    with pytest.raises(LedgerMissing):
        Ledger(path)
    assert not path.exists()
    Ledger(path, create=True).charge_credits("x", mode="cheap", node="research", credits=1)
    # The next open finds the recorded spend instead of a fresh file.
    assert Ledger(path).build_credits() == 1


def test_charge_splits_cache_tokens(ledger: Ledger) -> None:
    # Mutation: price cache tokens at the base input rate (skip the multipliers),
    # or skip subtracting them from input_tokens; either changes the total.
    # The usage_metadata is built by langchain-anthropic's own converter from an
    # Anthropic Usage object, so the shape is the installed package's.
    usage = _create_usage_metadata(Usage(
        input_tokens=3_000,  # Anthropic's count excludes cached tokens
        output_tokens=1_000,
        cache_read_input_tokens=4_000,
        cache_creation_input_tokens=3_000,
        cache_creation=CacheCreation(ephemeral_5m_input_tokens=2_000, ephemeral_1h_input_tokens=1_000),
    ))
    assert usage["input_tokens"] == 10_000
    details = usage["input_token_details"]
    assert details["cache_read"] == 4_000
    assert details["cache_creation"] == 0
    assert details["ephemeral_5m_input_tokens"] == 2_000
    assert details["ephemeral_1h_input_tokens"] == 1_000

    expected = (
        3_000 * IN
        + 4_000 * IN * config.CACHE_READ_MULTIPLIER
        + 2_000 * IN * config.CACHE_WRITE_5M_MULTIPLIER
        + 1_000 * IN * config.CACHE_WRITE_1H_MULTIPLIER
        + 1_000 * OUT
    )
    res = ledger.reserve("c", mode="cheap", node="synthesize", model=HAIKU,
                         input_tokens_est=12_000, max_tokens=2_000)
    assert ledger.charge(res, usage) == pytest.approx(expected)
    row = [r for r in ledger.rows("c") if r["kind"] == "charge"][0]
    assert (row["input_tokens"], row["cache_read"], row["cache_write"]) == (10_000, 4_000, 3_000)

    # Without a TTL breakdown the generic cache_creation is a 5 minute write.
    plain = _create_usage_metadata(Usage(
        input_tokens=1_000, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=500,
    ))
    assert price_usage(HAIKU, plain) == pytest.approx(
        1_000 * IN + 500 * IN * config.CACHE_WRITE_5M_MULTIPLIER
    )


def test_replay_rows_never_count_toward_build_total(ledger: Ledger) -> None:
    # Mutation: drop the "mode IN LIVE_MODES" filter from the build sums.
    for i in range(5):
        res = ledger.reserve(f"replay-{i}", mode="replay", node="synthesize", model=HAIKU,
                             input_tokens_est=10_000, max_tokens=4_000)
        ledger.charge(res, {"input_tokens": 10_000, "output_tokens": 4_000})
        ledger.charge_credits(f"replay-{i}", mode="replay", node="research", credits=3)
    assert ledger.run_total("replay-0") == pytest.approx(
        10_000 * config.REPLAY_PRICES["input"] / 1e6 + 4_000 * config.REPLAY_PRICES["output"] / 1e6
    )
    assert ledger.build_total() == 0
    assert ledger.build_credits() == 0

    res = ledger.reserve("live", mode="cheap", node="read_plate", model=HAIKU,
                         input_tokens_est=2_800, max_tokens=512)
    live = ledger.charge(res, {"input_tokens": 2_798, "output_tokens": 95})
    ledger.charge_credits("live", mode="cheap", node="research", credits=2)
    assert ledger.build_total() == pytest.approx(live)
    assert ledger.build_credits() == 2


def test_credit_caps(ledger: Ledger) -> None:
    # Mutation: check run credits with "<" in place of "<=" (the 10th credit is
    # refused), or drop the build credit check in reserve_credits().
    for _ in range(config.RUN_CREDIT_CAP):
        hold = ledger.reserve_credits("run", mode="cheap", node="research", credits=1)
        ledger.charge_credits("run", mode="cheap", node="research", credits=1, reservation=hold)
    assert ledger.run_credits("run") == config.RUN_CREDIT_CAP
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve_credits("run", mode="cheap", node="research", credits=1)
    assert err.value.reason == "run_credit_cap"

    # Open holds count too: two parallel calls cannot both take the last credit.
    held = [ledger.reserve_credits("par", mode="cheap", node="research", credits=config.RUN_CREDIT_CAP - 1),
            ledger.reserve_credits("par", mode="cheap", node="research", credits=1)]
    with pytest.raises(BudgetExceeded):
        ledger.reserve_credits("par", mode="cheap", node="research", credits=1)
    for hold in held:  # open live holds count toward the build cap too; settle them first
        ledger.release(hold)

    # Build cap: live credits so far plus this call must stay within the cap.
    _preload(ledger.path, mode="cheap", credits=config.BUILD_CREDIT_CAP - config.RUN_CREDIT_CAP - 5)
    assert ledger.build_credits() == config.BUILD_CREDIT_CAP - 5
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve_credits("late", mode="cheap", node="research", credits=6)
    assert err.value.reason == "build_credit_cap"
    ledger.reserve_credits("late", mode="cheap", node="research", credits=5)
    with pytest.raises(BudgetExceeded) as err:
        ledger.assert_can_start_live_run("cheap")
    assert err.value.reason == "build_credit_cap"
    # Replay credits are outside the build cap.
    ledger.reserve_credits("replay", mode="replay", node="research", credits=6)
    # Free tier: credits cost $0 unless pay as you go is on (decision 16).
    assert ledger.build_total() == pytest.approx(0.0 if not config.TAVILY_PAYG_ENABLED
                                                 else ledger.build_credits() * config.tavily_credit_usd())


def test_concurrent_charges_from_threads(ledger: Ledger) -> None:
    # Mutation: open one shared connection in __init__ (default
    # check_same_thread=True raises ProgrammingError on worker threads), or drop
    # BEGIN IMMEDIATE so two threads can pass the same cap check (probabilistic).
    errors: list[BaseException] = []
    per_thread, threads = 20, 8
    start = threading.Barrier(threads)

    def work(n: int) -> None:
        try:
            start.wait()
            for _ in range(per_thread):
                res = ledger.reserve(f"run-{n % 2}", mode="replay", node="research", model=HAIKU,
                                     input_tokens_est=1_000, max_tokens=100, run_cap_usd=10.0)
                ledger.charge(res, {"input_tokens": 1_000, "output_tokens": 100})
        except BaseException as exc:  # noqa: BLE001, surfaced below
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    assert errors == []
    each = _cost(1_000, 100)
    assert ledger.run_total("run-0") + ledger.run_total("run-1") == pytest.approx(
        each * per_thread * threads
    )
    assert len([r for r in ledger.rows() if r["kind"] == "charge"]) == per_thread * threads

    # Racing reservations never oversubscribe a cap that fits exactly five.
    cap = 5 * each + each / 2
    won: list[int] = []
    gate = threading.Barrier(threads)

    def grab() -> None:
        gate.wait()
        try:
            ledger.reserve("race", mode="replay", node="research", model=HAIKU,
                           input_tokens_est=1_000, max_tokens=100, run_cap_usd=cap)
            won.append(1)
        except BudgetExceeded:
            pass

    racers = [threading.Thread(target=grab) for _ in range(threads)]
    for t in racers:
        t.start()
    for t in racers:
        t.join()
    assert len(won) == 5


def test_estimate_includes_tool_prompt_and_margin() -> None:
    # Mutation: drop TOOL_PROMPT_TOKENS, the ESTIMATE_MARGIN multiplier, or the
    # per image estimate from estimate_input_tokens().
    @tool
    def search(query: str) -> str:
        """Search the web."""
        return query

    messages = [SystemMessage("You are a maintenance researcher." * 20),
                HumanMessage("Find the manual for a Sundance spa model 880." * 10)]
    base = count_tokens_approximately(messages)
    with_tools = count_tokens_approximately(messages, tools=[search])

    assert estimate_input_tokens(messages, model=HAIKU) == math.ceil(
        base * config.ESTIMATE_MARGIN[HAIKU]
    )
    assert estimate_input_tokens(messages, model=HAIKU, tools=[search]) == math.ceil(
        (with_tools + config.TOOL_PROMPT_TOKENS[HAIKU]) * config.ESTIMATE_MARGIN[HAIKU]
    )
    assert estimate_input_tokens(messages, model=HAIKU, images=2) == math.ceil(
        base * config.ESTIMATE_MARGIN[HAIKU] + 2 * config.IMAGE_TOKENS_ESTIMATE
    )
    # Sonnet's newer tokenizer gets the larger margin.
    assert estimate_input_tokens(messages, model=config.SONNET) > estimate_input_tokens(
        messages, model=HAIKU
    )


def test_live_run_cap_override_can_only_lower_the_cap(ledger: Ledger) -> None:
    # Mutation: in Ledger.run_cap return run_cap_usd itself in live modes
    # instead of min(cap, run_cap_usd); the tenfold override then lets a call
    # over the configured cap through.
    cap = config.RUN_CAP_USD["cheap"]
    kw = dict(mode="cheap", node="synthesize", model=HAIKU)
    # 100,000 in and 20,000 out is 0.20: over the 0.15 cap, under a 1.50 override.
    assert cap < _cost(100_000, 20_000) < 10 * cap
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve("up", input_tokens_est=100_000, max_tokens=20_000, run_cap_usd=10 * cap, **kw)
    assert err.value.reason == "run_cap"
    # A lower override is applied: 0.015 fits the configured cap but not 0.01.
    ledger.reserve("fits", input_tokens_est=5_000, max_tokens=2_000, **kw)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("down", input_tokens_est=5_000, max_tokens=2_000, run_cap_usd=0.01, **kw)


def test_release_frees_the_hold_once(ledger: Ledger) -> None:
    # Mutation: make release() a no-op; the hold stays open and the same size
    # call after it is refused.
    kw = dict(mode="replay", node="research", model=HAIKU, input_tokens_est=5_000,
              max_tokens=2_000, run_cap_usd=0.02)  # each hold is 0.015
    hold = ledger.reserve("r", **kw)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("r", **kw)
    ledger.release(hold)
    assert ledger.run_total("r") == 0
    ledger.reserve("r", **kw)  # fits now that the first hold is gone
    with pytest.raises(ValueError, match="already settled"):
        ledger.release(hold)


def test_stop_records_its_reason(ledger: Ledger) -> None:
    # Mutation: write the stop row with an empty note; advisor ledger then
    # cannot say why a run ended.
    ledger.stop("r", mode="cheap", reason="tavily_plan_limit", node="research")
    [row] = ledger.rows("r")
    assert (row["kind"], row["note"], row["node"]) == ("stop", "tavily_plan_limit", "research")
    with pytest.raises(ValueError):
        ledger.stop("r", mode="cheap", reason="felt_like_it")


def test_credit_reservations_honor_cassette_caps(ledger: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    # Mutation: have reserve_credits ignore run_cap_usd (use self.run_cap(mode)),
    # or ignore run_credit_cap; a cassette's caps then reach model calls only.
    monkeypatch.setattr(config, "TAVILY_PAYG_ENABLED", True)  # so credits cost dollars
    two_credits = 2 * config.tavily_credit_usd()
    assert 0.01 < two_credits < config.REPLAY_RUN_CAP_USD
    ledger.reserve_credits("default", mode="replay", node="research", credits=2)
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve_credits("capped", mode="replay", node="research", credits=2, run_cap_usd=0.01)
    assert err.value.reason == "run_cap"

    ledger.reserve_credits("few", mode="replay", node="research", credits=3, run_credit_cap=3)
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve_credits("few", mode="replay", node="research", credits=1, run_credit_cap=3)
    assert err.value.reason == "run_credit_cap"
    # Live: an override can lower the credit cap but never raise it.
    with pytest.raises(BudgetExceeded):
        ledger.reserve_credits("live", mode="cheap", node="research", credits=config.RUN_CREDIT_CAP + 1,
                               run_credit_cap=2 * config.RUN_CREDIT_CAP)
    with pytest.raises(BudgetExceeded):
        ledger.reserve_credits("live2", mode="cheap", node="research", credits=3, run_credit_cap=2)


def test_latency_recorded_per_node(tmp_path):
    """The PLAN section 5 latency row: every node that ran has a recorded duration.

    Mutation: latency_route_dropped (see test_graph_latency.py, which carries
    the per node detail)."""
    from agent.tests.test_graph_latency import assert_latency_for_every_node

    latency, _, _ = assert_latency_for_every_node("synthetic/flo_three_candidates", tmp_path)
    assert {"intake", "read_plate", "confirm_identity", "route", "research", "synthesize",
            "validate", "render"} <= set(latency)


def test_empty_ledger_file_is_not_a_ledger(tmp_path: Path) -> None:
    """Mutation ledger_empty_file_accepted: Ledger() takes a 0 byte file for a ledger (finding M1).

    sqlite reads an empty or truncated file as an empty database, so a
    cleared ledger would otherwise start a fresh build cap (decision 52).
    """
    path = tmp_path / "ledger" / "ledger.sqlite"
    path.parent.mkdir(parents=True)
    path.touch()
    with pytest.raises(LedgerMissing, match="no entries table"):
        Ledger(path)
    with pytest.raises(LedgerMissing):
        Ledger(path)  # still refused on the next open: nothing was created
    ledger = Ledger(path, create=True)  # only --new-ledger makes it a ledger
    ledger.charge_credits("x", mode="cheap", node="research", credits=1)
    assert Ledger(path).build_credits() == 1


def test_open_holds_of_other_runs_count_toward_the_build_cap(ledger: Ledger) -> None:
    """Mutation ledger_build_cap_ignores_holds: reserve and reserve_credits count only charged
    build spend, so two live commands at once pass the build cap together (finding M6)."""
    room = 0.01
    _preload(ledger.path, mode="cheap", usd=config.BUILD_CAP_USD - room,
             credits=config.BUILD_CREDIT_CAP - 3)
    call = dict(mode="cheap", node="synthesize", model=HAIKU, input_tokens_est=6_000, max_tokens=0)
    assert _cost(6_000, 0) < room < 2 * _cost(6_000, 0)
    first = ledger.reserve("run-a", **call)  # fits the room left
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve("run-b", **call)  # another run's open hold already takes most of it
    assert err.value.reason == "build_cap"
    ledger.release(first)
    ledger.release(ledger.reserve("run-b", **call))  # with the hold settled, it fits again

    held = ledger.reserve_credits("run-a", mode="cheap", node="research", credits=2)
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve_credits("run-b", mode="cheap", node="research", credits=2)
    assert err.value.reason == "build_credit_cap"
    ledger.release(held)
    ledger.reserve_credits("run-b", mode="cheap", node="research", credits=2)
    # Replay holds never count toward the live build.
    ledger.reserve("replay", mode="replay", node="synthesize", model=HAIKU, input_tokens_est=6_000, max_tokens=0)


def test_charge_estimated_charges_the_whole_reservation(ledger: Ledger) -> None:
    """Mutation ledger_estimated_charge_free: charge_estimated records 0 instead of the reservation."""
    hold = ledger.reserve("run", mode="cheap", node="synthesize", model=HAIKU, input_tokens_est=1_000, max_tokens=100)
    usd = ledger.charge_estimated(hold, "a reply with no usage")
    charge = [r for r in ledger.rows("run") if r["kind"] == "charge"]
    assert usd == pytest.approx(hold.usd) and len(charge) == 1
    assert charge[0]["usd"] == pytest.approx(hold.usd) and charge[0]["estimated"] == 1
    assert "a reply with no usage" in charge[0]["note"]
    assert ledger.run_total("run") == pytest.approx(hold.usd)


JEV_IN = config.PRICES_PER_MTOK[config.JEV_MODEL]["input"] / 1_000_000


def test_jev_call_priced_on_input_only_with_typesafe_on_every_row(ledger: Ledger) -> None:
    # Mutations: ledger_jev_output_priced (output tokens are charged);
    # ledger_provider_not_carried (the reserve row says anthropic);
    # ledger_charge_ignores_provider (the charge prices a Jev call at Anthropic
    # or replay rates). The judge's own reserve before call order is held by
    # test_safety_judge.py.
    model = _FakeModel()
    kw = dict(mode="cheap", node="safety_check", model=config.JEV_MODEL, provider=config.JEV_PROVIDER)
    hold = ledger.reserve("jev", input_tokens_est=900, max_tokens=1_000, **kw)
    assert hold.usd == pytest.approx(900 * JEV_IN) and hold.provider == config.JEV_PROVIDER
    usage = model.invoke("step")  # 100 in, 10 out
    assert ledger.charge(hold, usage) == pytest.approx(100 * JEV_IN)
    ledger.release(ledger.reserve("jev", input_tokens_est=450, max_tokens=0, **kw))
    rows = ledger.rows("jev")
    assert [(r["kind"], r["provider"]) for r in rows] == [
        ("reserve", "typesafe"), ("charge", "typesafe"), ("release", "typesafe"),
        ("reserve", "typesafe"), ("release", "typesafe")]
    charge = rows[1]
    assert (charge["input_tokens"], charge["output_tokens"]) == (100, 10)  # recorded, output priced at 0
    # The same call in replay is priced the same way, never at the Haiku replay rates.
    replay = ledger.reserve("jev-replay", input_tokens_est=900, max_tokens=1_000, **{**kw, "mode": "replay"})
    assert replay.usd == pytest.approx(900 * JEV_IN)
    assert ledger.charge(replay, {"input_tokens": 900, "output_tokens": 1_000}) == pytest.approx(900 * JEV_IN)
    # Anthropic stays the default, and an unknown provider is refused.
    assert ledger.reserve("a", mode="cheap", node="synthesize", model=HAIKU, input_tokens_est=10,
                          max_tokens=10).provider == "anthropic"
    with pytest.raises(ValueError):
        ledger.reserve("x", input_tokens_est=10, max_tokens=0, **{**kw, "provider": "tavily"})


def test_build_cap_refuses_a_jev_reservation_that_would_cross_it(ledger: Ledger) -> None:
    # Mutation ledger_refusal_drops_provider: the stop row of a refused Jev
    # call carries no provider.
    need = 450 * JEV_IN
    _preload(ledger.path, mode="cheap", usd=config.BUILD_CAP_USD - need / 2)
    with pytest.raises(BudgetExceeded) as err:
        ledger.reserve("jev", mode="cheap", node="safety_check", model=config.JEV_MODEL, input_tokens_est=450,
                       max_tokens=0, provider=config.JEV_PROVIDER)
    assert err.value.reason == "build_cap"
    [stop] = ledger.rows("jev")
    assert (stop["kind"], stop["provider"]) == ("stop", config.JEV_PROVIDER)
    # A Jev call that fits what is left goes through.
    ledger.reserve("jev", mode="cheap", node="safety_check", model=config.JEV_MODEL, input_tokens_est=100,
                   max_tokens=0, provider=config.JEV_PROVIDER)
