"""The spend ledger (PLAN section 8.9, SC11, decisions 16, 26 and 52).

Every paid call is reserved before it is made and charged in full after. The
ledger is a sqlite file that is never deleted; each operation opens its own
short lived connection so parallel branches and the Tavily wrapper's thread can
write safely.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages.utils import count_tokens_approximately

from agent import config

BUSY_TIMEOUT_MS = config.LEDGER_BUSY_TIMEOUT_MS
# Float sums of many small charges drift by far less than this.
_EPSILON = 1e-9

STOP_REASONS = (
    "run_cap",
    "build_cap",
    "run_credit_cap",
    "build_credit_cap",
    "tavily_plan_limit",
    "research_budget",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL,
    thread_id TEXT,
    mode TEXT NOT NULL,
    node TEXT,
    provider TEXT,
    model TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('reserve', 'charge', 'release', 'stop')),
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    tavily_credits INTEGER NOT NULL DEFAULT 0,
    usd REAL NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS entries_run ON entries (run_id, kind);
CREATE INDEX IF NOT EXISTS entries_mode ON entries (mode, kind);
"""

_COLUMNS = (
    "ts",
    "run_id",
    "thread_id",
    "mode",
    "node",
    "provider",
    "model",
    "kind",
    "input_tokens",
    "cache_read",
    "cache_write",
    "output_tokens",
    "tavily_credits",
    "usd",
    "estimated",
    "note",
)


class BudgetExceeded(Exception):
    """A call or run was refused before anything was spent."""

    def __init__(self, reason: str, message: str = "") -> None:
        if reason not in STOP_REASONS:
            raise ValueError(f"unknown budget stop reason: {reason}")
        self.reason = reason
        super().__init__(message or reason)


class LedgerMissing(Exception):
    """The ledger file does not exist, or holds no ledger, and creating one was not asked for."""


@dataclass
class Reservation:
    """An open hold on run and build budget, settled by charge or release."""

    id: int
    run_id: str
    mode: str
    node: str
    model: str | None
    provider: str
    usd: float
    input_tokens_est: int = 0
    max_tokens: int = 0
    credits: int = 0
    thread_id: str | None = None
    settled: bool = False


# ---------------------------------------------------------------------------
# Pricing and estimates
# ---------------------------------------------------------------------------


def _prices_for(model_or_prices: str | dict[str, float]) -> dict[str, float]:
    if isinstance(model_or_prices, dict):
        return model_or_prices
    return config.PRICES_PER_MTOK[model_or_prices]


def _cache_tokens(usage_metadata: dict[str, Any]) -> tuple[int, int, int]:
    """Return (cache_read, write_5m, write_1h) from LangChain usage metadata.

    langchain-anthropic reports input_tokens including cache reads and writes,
    with input_token_details carrying cache_read, cache_creation and, when the
    API breaks writes down by TTL, ephemeral_5m/1h_input_tokens (in which case
    it sets cache_creation to 0 so nothing is counted twice). A bare
    cache_creation is priced as a 5 minute write, the API's default TTL.
    """
    details = usage_metadata.get("input_token_details") or {}
    read = int(details.get("cache_read") or 0)
    write_5m = int(details.get("ephemeral_5m_input_tokens") or 0)
    write_1h = int(details.get("ephemeral_1h_input_tokens") or 0)
    if write_5m + write_1h == 0:
        write_5m = int(details.get("cache_creation") or 0)
    return read, write_5m, write_1h


def price_usage(model_or_prices: str | dict[str, float], usage_metadata: dict[str, Any]) -> float:
    """Actual cost in dollars of one call's usage_metadata."""
    prices = _prices_for(model_or_prices)
    input_price = prices["input"] / 1_000_000
    output_price = prices["output"] / 1_000_000
    read, write_5m, write_1h = _cache_tokens(usage_metadata)
    total_input = int(usage_metadata.get("input_tokens") or 0)
    base_input = max(total_input - read - write_5m - write_1h, 0)
    input_cost = input_price * (
        base_input
        + read * config.CACHE_READ_MULTIPLIER
        + write_5m * config.CACHE_WRITE_5M_MULTIPLIER
        + write_1h * config.CACHE_WRITE_1H_MULTIPLIER
    )
    output_cost = int(usage_metadata.get("output_tokens") or 0) * output_price
    return input_cost + output_cost


def estimate_input_tokens(
    messages: Sequence[Any],
    *,
    model: str,
    tools: Sequence[Any] | None = None,
    images: int = 0,
) -> int:
    """Conservative input token estimate for a call's reservation."""
    tokens: float = count_tokens_approximately(messages, tools=list(tools) if tools else None)
    if tools:
        tokens += config.TOOL_PROMPT_TOKENS.get(model, max(config.TOOL_PROMPT_TOKENS.values()))
    tokens *= config.ESTIMATE_MARGIN.get(model, max(config.ESTIMATE_MARGIN.values()))
    tokens += images * config.IMAGE_TOKENS_ESTIMATE
    return math.ceil(tokens)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class Ledger:
    """Reserve, charge and report spend for runs and the whole build."""

    def __init__(self, path: Path, *, create: bool = False) -> None:
        self.path = Path(path)
        if not self.path.exists():
            if not create:
                # Decision 52: a missing ledger is never silently recreated.
                raise LedgerMissing(f"no ledger file at {self.path}")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # sqlite takes an empty or truncated file for an empty database, so
            # an existing file counts as a ledger only if it holds the entries
            # table; otherwise a cleared ledger would start a fresh build cap.
            if not create and not self._has_entries(conn):
                raise LedgerMissing(
                    f"the ledger at {self.path} has no entries table"
                )
            conn.executescript(_SCHEMA)

    @staticmethod
    def _has_entries(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
        ).fetchone()
        return row is not None

    # connections ------------------------------------------------------------

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode = WAL")
            if write:
                # IMMEDIATE takes the write lock up front, so the cap check and
                # the insert that follows it cannot interleave with another
                # thread's.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            else:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _insert(conn: sqlite3.Connection, **values: Any) -> int:
        row = {col: values.get(col) for col in _COLUMNS}
        row["ts"] = _now()
        for col in ("input_tokens", "cache_read", "cache_write", "output_tokens",
                    "tavily_credits", "usd", "estimated"):
            row[col] = row[col] or 0
        row["note"] = row["note"] or ""
        placeholders = ", ".join("?" for _ in _COLUMNS)
        cur = conn.execute(
            f"INSERT INTO entries ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            [row[c] for c in _COLUMNS],
        )
        return int(cur.lastrowid)

    # sums ---------------------------------------------------------------------

    @staticmethod
    def _run_sums(conn: sqlite3.Connection, run_id: str) -> tuple[float, float, int, int]:
        """(spent usd, open reserved usd, spent credits, open reserved credits)."""
        rows = conn.execute(
            "SELECT kind, COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(tavily_credits), 0) AS cr "
            "FROM entries WHERE run_id = ? GROUP BY kind",
            (run_id,),
        ).fetchall()
        by_kind = {r["kind"]: (float(r["usd"]), int(r["cr"])) for r in rows}
        spent, credits = by_kind.get("charge", (0.0, 0))
        reserved, reserved_cr = by_kind.get("reserve", (0.0, 0))
        released, released_cr = by_kind.get("release", (0.0, 0))
        return spent, reserved - released, credits, reserved_cr - released_cr

    @staticmethod
    def _build_sums(conn: sqlite3.Connection) -> tuple[float, int]:
        """Live spend and credits only: replay rows never count (section 8.9)."""
        marks = ", ".join("?" for _ in config.LIVE_MODES)
        row = conn.execute(
            "SELECT COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(tavily_credits), 0) AS cr "
            f"FROM entries WHERE kind = 'charge' AND mode IN ({marks})",
            config.LIVE_MODES,
        ).fetchone()
        return float(row["usd"]), int(row["cr"])

    @staticmethod
    def _build_held(conn: sqlite3.Connection) -> tuple[float, int]:
        """Open live holds of every run: reserved less released (usd, credits).

        Two live commands at once each check the build cap inside BEGIN
        IMMEDIATE, so counting the other's open holds keeps them together
        under it.
        """
        marks = ", ".join("?" for _ in config.LIVE_MODES)
        rows = conn.execute(
            "SELECT kind, COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(tavily_credits), 0) AS cr "
            f"FROM entries WHERE kind IN ('reserve', 'release') AND mode IN ({marks}) GROUP BY kind",
            config.LIVE_MODES,
        ).fetchall()
        by_kind = {r["kind"]: (float(r["usd"]), int(r["cr"])) for r in rows}
        reserved, reserved_cr = by_kind.get("reserve", (0.0, 0))
        released, released_cr = by_kind.get("release", (0.0, 0))
        return max(reserved - released, 0.0), max(reserved_cr - released_cr, 0)

    def _build_used(self, conn: sqlite3.Connection) -> tuple[float, int]:
        """Live charges plus open live holds, in usd and credits."""
        spent, credits = self._build_sums(conn)
        held, held_cr = self._build_held(conn)
        return spent + held, credits + held_cr

    # caps ---------------------------------------------------------------------

    @staticmethod
    def _check_mode(mode: str) -> None:
        if mode not in config.MODES:
            raise ValueError(f"unknown mode: {mode}")

    @staticmethod
    def run_cap(mode: str, run_cap_usd: float | None = None) -> float:
        """The dollar cap for one run in this mode.

        A cassette's caps block may set its own cap in replay; in live modes an
        override can only lower the configured cap.
        """
        if mode == "replay":
            return config.REPLAY_RUN_CAP_USD if run_cap_usd is None else run_cap_usd
        cap = config.RUN_CAP_USD[mode]
        return cap if run_cap_usd is None else min(cap, run_cap_usd)

    @staticmethod
    def run_credit_cap(mode: str, run_credit_cap: int | None = None) -> int:
        """The Tavily credit cap for one run, overridden the same way as run_cap."""
        if run_credit_cap is None:
            return config.RUN_CREDIT_CAP
        if mode == "replay":
            return run_credit_cap
        return min(config.RUN_CREDIT_CAP, run_credit_cap)

    def _refuse(self, conn: sqlite3.Connection, run_id: str, mode: str, node: str,
                reason: str, message: str, thread_id: str | None = None) -> BudgetExceeded:
        self._insert(conn, run_id=run_id, thread_id=thread_id, mode=mode, node=node,
                     kind="stop", note=f"{reason}: {message}")
        return BudgetExceeded(reason, message)

    # model calls --------------------------------------------------------------

    def reserve(
        self,
        run_id: str,
        *,
        mode: str,
        node: str,
        model: str,
        input_tokens_est: int,
        max_tokens: int,
        thread_id: str | None = None,
        run_cap_usd: float | None = None,
    ) -> Reservation:
        """Hold budget for one model call, or raise BudgetExceeded before it is made."""
        self._check_mode(mode)
        prices = config.REPLAY_PRICES if mode == "replay" else _prices_for(model)
        usd = price_usage(prices, {"input_tokens": input_tokens_est, "output_tokens": max_tokens})
        cap = self.run_cap(mode, run_cap_usd)
        error: BudgetExceeded | None = None
        with self._connect(write=True) as conn:
            spent, held, _, _ = self._run_sums(conn, run_id)
            if spent + held + usd > cap + _EPSILON:
                error = self._refuse(
                    conn, run_id, mode, node, "run_cap",
                    f"spent {spent:.6f} + reserved {held:.6f} + this call {usd:.6f} > run cap {cap:.6f}",
                    thread_id,
                )
            elif mode in config.LIVE_MODES:
                build_used, _ = self._build_used(conn)
                if build_used + usd > config.BUILD_CAP_USD + _EPSILON:
                    error = self._refuse(
                        conn, run_id, mode, node, "build_cap",
                        f"build spent and reserved {build_used:.6f} + this call {usd:.6f} > build cap "
                        f"{config.BUILD_CAP_USD:.2f}",
                        thread_id,
                    )
            if error is None:
                rid = self._insert(
                    conn, run_id=run_id, thread_id=thread_id, mode=mode, node=node,
                    provider="anthropic", model=model, kind="reserve",
                    input_tokens=input_tokens_est, output_tokens=max_tokens, usd=usd,
                )
        if error is not None:
            raise error
        return Reservation(
            id=rid, run_id=run_id, mode=mode, node=node, model=model, provider="anthropic",
            usd=usd, input_tokens_est=input_tokens_est, max_tokens=max_tokens,
            thread_id=thread_id,
        )

    def _settle(self, conn: sqlite3.Connection, reservation: Reservation) -> None:
        if reservation.settled:
            raise ValueError(f"reservation {reservation.id} is already settled")
        self._insert(
            conn, run_id=reservation.run_id, thread_id=reservation.thread_id,
            mode=reservation.mode, node=reservation.node, provider=reservation.provider,
            model=reservation.model, kind="release", usd=reservation.usd,
            tavily_credits=reservation.credits, note=f"reservation {reservation.id}",
        )
        reservation.settled = True

    def charge(self, reservation: Reservation, usage_metadata: dict[str, Any]) -> float:
        """Record a call's full actual cost, even above its reservation, and release it."""
        prices = config.REPLAY_PRICES if reservation.mode == "replay" else _prices_for(reservation.model or "")
        usd = price_usage(prices, usage_metadata)
        read, write_5m, write_1h = _cache_tokens(usage_metadata)
        note = f"reservation {reservation.id}"
        if usd > reservation.usd + _EPSILON:
            note += f"; above reservation by {usd - reservation.usd:.6f} usd"
        with self._connect(write=True) as conn:
            self._insert(
                conn, run_id=reservation.run_id, thread_id=reservation.thread_id,
                mode=reservation.mode, node=reservation.node, provider=reservation.provider,
                model=reservation.model, kind="charge",
                input_tokens=int(usage_metadata.get("input_tokens") or 0),
                cache_read=read, cache_write=write_5m + write_1h,
                output_tokens=int(usage_metadata.get("output_tokens") or 0),
                usd=usd, note=note,
            )
            self._settle(conn, reservation)
        return usd

    def charge_timeout(self, reservation: Reservation) -> float:
        """Charge a call that timed out after it was sent at its full reservation.

        Whether Anthropic bills an abandoned request is unverified, so the row is
        marked estimated and reconciled against the console at each LIVE gate.
        """
        return self.charge_estimated(reservation, "timed out, charged at reservation")

    def charge_estimated(self, reservation: Reservation, why: str) -> float:
        """Charge a sent call whose usage is unknown at its full reservation, marked estimated."""
        with self._connect(write=True) as conn:
            self._insert(
                conn, run_id=reservation.run_id, thread_id=reservation.thread_id,
                mode=reservation.mode, node=reservation.node, provider=reservation.provider,
                model=reservation.model, kind="charge",
                input_tokens=reservation.input_tokens_est, output_tokens=reservation.max_tokens,
                tavily_credits=reservation.credits, usd=reservation.usd, estimated=1,
                note=f"reservation {reservation.id}; {why}",
            )
            self._settle(conn, reservation)
        return reservation.usd

    def release(self, reservation: Reservation) -> None:
        """Release a reservation for a call that was never sent."""
        with self._connect(write=True) as conn:
            self._settle(conn, reservation)

    # Tavily credits -------------------------------------------------------------

    def reserve_credits(self, run_id: str, *, mode: str, node: str, credits: int,
                        thread_id: str | None = None, run_cap_usd: float | None = None,
                        run_credit_cap: int | None = None) -> Reservation:
        """Hold Tavily credits for one call, or raise BudgetExceeded before it is made.

        run_cap_usd and run_credit_cap come from a cassette's caps block. In
        replay they replace the configured caps; in live modes they can only
        lower them.
        """
        self._check_mode(mode)
        usd = credits * config.tavily_credit_usd()
        credit_cap = self.run_credit_cap(mode, run_credit_cap)
        cap = self.run_cap(mode, run_cap_usd)
        error: BudgetExceeded | None = None
        with self._connect(write=True) as conn:
            spent, held, run_cr, held_cr = self._run_sums(conn, run_id)
            if run_cr + held_cr + credits > credit_cap:
                error = self._refuse(
                    conn, run_id, mode, node, "run_credit_cap",
                    f"run credits {run_cr} + reserved {held_cr} + {credits} > {credit_cap}",
                    thread_id,
                )
            elif usd and spent + held + usd > cap + _EPSILON:
                error = self._refuse(conn, run_id, mode, node, "run_cap",
                                     f"credits at {usd:.6f} usd do not fit the run cap", thread_id)
            elif mode in config.LIVE_MODES:
                build_spent, build_cr = self._build_used(conn)
                if build_cr + credits > config.BUILD_CREDIT_CAP:
                    error = self._refuse(
                        conn, run_id, mode, node, "build_credit_cap",
                        f"build credits used and reserved {build_cr} + {credits} > {config.BUILD_CREDIT_CAP}",
                        thread_id,
                    )
                elif usd and build_spent + usd > config.BUILD_CAP_USD + _EPSILON:
                    error = self._refuse(conn, run_id, mode, node, "build_cap",
                                         f"credits at {usd:.6f} usd do not fit the build cap",
                                         thread_id)
            if error is None:
                rid = self._insert(conn, run_id=run_id, thread_id=thread_id, mode=mode,
                                   node=node, provider="tavily", kind="reserve",
                                   tavily_credits=credits, usd=usd)
        if error is not None:
            raise error
        return Reservation(id=rid, run_id=run_id, mode=mode, node=node, model=None,
                           provider="tavily", usd=usd, credits=credits, thread_id=thread_id)

    def charge_credits(self, run_id: str, *, mode: str, node: str, credits: int,
                       note: str = "", reservation: Reservation | None = None,
                       thread_id: str | None = None) -> float:
        """Record the credits Tavily reported, priced per decision 16, and release any hold."""
        self._check_mode(mode)
        usd = credits * config.tavily_credit_usd()
        with self._connect(write=True) as conn:
            self._insert(conn, run_id=run_id, thread_id=thread_id, mode=mode, node=node,
                         provider="tavily", kind="charge", tavily_credits=credits, usd=usd,
                         note=note)
            if reservation is not None:
                self._settle(conn, reservation)
        return usd

    # stops and reports ---------------------------------------------------------

    def stop(self, run_id: str, *, mode: str, reason: str, node: str = "",
             thread_id: str | None = None) -> None:
        """Record that a run ended on a budget or plan limit."""
        if reason not in STOP_REASONS:
            raise ValueError(f"unknown budget stop reason: {reason}")
        with self._connect(write=True) as conn:
            self._insert(conn, run_id=run_id, thread_id=thread_id, mode=mode, node=node,
                         kind="stop", note=reason)

    def run_total(self, run_id: str) -> float:
        with self._connect() as conn:
            return self._run_sums(conn, run_id)[0]

    def run_credits(self, run_id: str) -> int:
        with self._connect() as conn:
            return self._run_sums(conn, run_id)[2]

    def build_total(self) -> float:
        with self._connect() as conn:
            return self._build_sums(conn)[0]

    def build_credits(self) -> int:
        with self._connect() as conn:
            return self._build_sums(conn)[1]

    def assert_can_start_live_run(self, mode: str, *, need_usd: float | None = None,
                                  need_credits: int | None = None) -> None:
        """Refuse a live run the remaining build budget or credits could not cover.

        A run needs its per run cap and credit cap, unless the caller names a
        smaller worst case (the plate runs make one read_plate call and no
        search). Open holds of other runs count as spent.
        """
        if mode not in config.LIVE_MODES:
            raise ValueError(f"not a live mode: {mode}")
        need = config.RUN_CAP_USD[mode] if need_usd is None else need_usd
        need_cr = config.RUN_CREDIT_CAP if need_credits is None else need_credits
        with self._connect() as conn:
            spent, credits = self._build_used(conn)
        remaining = config.BUILD_CAP_USD - spent
        if remaining + _EPSILON < need:
            raise BudgetExceeded(
                "build_cap",
                f"remaining build budget {remaining:.4f} is below the {mode} run's worst case {need:.2f}",
            )
        if credits + need_cr > config.BUILD_CREDIT_CAP:
            raise BudgetExceeded(
                "build_credit_cap",
                f"build credits {credits} + a run's {need_cr} would pass {config.BUILD_CREDIT_CAP}",
            )

    def rows(self, run_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if run_id is None:
                cur = conn.execute("SELECT * FROM entries ORDER BY id")
            else:
                cur = conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (run_id,))
            return [dict(r) for r in cur.fetchall()]
