"""The suite runs on a fixed date, never on the wall clock (PRD 2, 13 October read-through).

cli._today() reads the UTC wall clock and the Haiku date gate refuses a Haiku
mode from config.HAIKU_RETIREMENT_EARLIEST on, so without conftest's pin the
live path tests would fail from that date with no change to the code. These
tests move the wall clock past the gate and run the live path on fakes.

Each test names the mutation that turns it red.
"""

from __future__ import annotations

import datetime as datetime_module
from datetime import UTC, date, timedelta, timezone
from pathlib import Path

import pytest

from agent import cli, config
from agent.tests.conftest import TEST_TODAY
from agent.tests.test_live_path import LiveFakes, flo_data, live_env, run_flo_live  # noqa: F401 (live_env is a fixture)

# The unpinned function, taken at import: conftest's pin replaces cli._today only once a test starts.
WALL_CLOCK_TODAY = cli._today
REAL_DATETIME = datetime_module.datetime
# Noon UTC on the day after the Haiku date gate, wherever config puts the gate.
GATE_DAY = date.fromisoformat(config.HAIKU_RETIREMENT_EARLIEST)
PAST_THE_GATE = REAL_DATETIME.combine(GATE_DAY + timedelta(days=1), REAL_DATETIME.min.time(), tzinfo=UTC).replace(
    hour=12)


class _ClockPastTheGate(REAL_DATETIME):
    """datetime with now() fixed on the day after the Haiku date gate."""

    @classmethod
    def now(cls, tz=None):
        return PAST_THE_GATE if tz is None else PAST_THE_GATE.astimezone(tz)


class _LocalClockBehindUtc(REAL_DATETIME):
    """datetime at 02:00 UTC on 16 October 2026 on a computer whose local clock runs 7 hours behind UTC."""

    INSTANT = REAL_DATETIME(2026, 10, 16, 2, 0, tzinfo=UTC)
    LOCAL = timezone(timedelta(hours=-7))

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.INSTANT.astimezone(cls.LOCAL).replace(tzinfo=None)
        return cls.INSTANT.astimezone(tz)


def _move_the_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datetime_module, "datetime", _ClockPastTheGate)
    # The clock really moved: the unpinned function now reads a date the gate refuses.
    assert WALL_CLOCK_TODAY() == GATE_DAY + timedelta(days=1)
    assert WALL_CLOCK_TODAY().isoformat() >= config.HAIKU_RETIREMENT_EARLIEST


def test_today_is_pinned_before_the_haiku_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: conftest_today_unpinned (the autouse fixture no longer sets cli._today)."""
    _move_the_wall_clock(monkeypatch)
    assert cli._today() == TEST_TODAY
    assert TEST_TODAY.isoformat() < config.HAIKU_RETIREMENT_EARLIEST


def test_live_path_passes_with_the_wall_clock_past_the_haiku_gate(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """The live path tests' preflight and run, on fakes, with the wall clock past the Haiku date gate.

    Mutation: conftest_today_unpinned (the Haiku date gate refuses the live ask)."""
    _move_the_wall_clock(monkeypatch)
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    _, out = run_flo_live(monkeypatch, capsys, fakes)
    assert "status: ok" in out
    assert "Recheck the model list" not in out


def test_unpinned_today_reads_the_utc_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pages label cli._today()'s date "(UTC)", so the real function reads the UTC date, not the local one:
    at 02:00 UTC on 16 October a local clock 7 hours behind still reads 15 October.

    Mutation: cli_today_local_clock (cli._today reads the local clock)."""
    monkeypatch.setattr(datetime_module, "datetime", _LocalClockBehindUtc)
    assert _LocalClockBehindUtc.now().date() == date(2026, 10, 15)
    assert WALL_CLOCK_TODAY() == date(2026, 10, 16)
