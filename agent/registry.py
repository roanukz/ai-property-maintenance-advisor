"""Property registry: appliances, service history and lookups in SQLite.

Every seeded row is synthetic (PLAN section 8.12), and every row this module
returns carries a boolean ``synthetic`` so callers can label what they show.

Citation ids (see registry_schema.sql): ``appliance:<id>`` for install date,
purchase date and warranty terms (decision 29), ``service:<id>`` for
``happened_before`` (SC8), ``maintenance:<id>`` for maintenance entries.

Each operation opens and closes its own connection, so one ``Registry`` can be
shared across threads. WAL lets readers run while a writer commits.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import DEFAULT_SEED_PATH, REGISTRY_BUSY_TIMEOUT_MS, REGISTRY_PATH

SCHEMA_PATH = Path(__file__).with_name("registry_schema.sql")

APPLIANCE_PREFIX = "appliance:"
SERVICE_PREFIX = "service:"
MAINTENANCE_PREFIX = "maintenance:"

_BOOL_COLUMNS = ("synthetic", "pending_owner_choice")


class SeedError(ValueError):
    """The seed file is malformed or holds a row not marked synthetic."""


def appliance_record_id(appliance_id: str) -> str:
    return APPLIANCE_PREFIX + appliance_id


def service_record_id(service_id: str) -> str:
    return SERVICE_PREFIX + service_id


def maintenance_record_id(entry_id: str) -> str:
    return MAINTENANCE_PREFIX + entry_id


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in _BOOL_COLUMNS:
        if key in out:
            out[key] = bool(out[key])
    return out


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


class Registry:
    """A registry database at ``path``, created with the schema if missing."""

    def __init__(self, path: Path | str = REGISTRY_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.path,
            timeout=REGISTRY_BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={int(REGISTRY_BUSY_TIMEOUT_MS)}")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ seed

    def load_seed(self, path: Path | str | None = None) -> dict[str, int]:
        """Upsert the seed file's rows; returns row counts per table.

        Rejects the whole file if any row is not ``synthetic: true``. Upsert
        rather than replace, so lookups that reference appliances survive a
        reload.
        """
        seed_path = Path(path) if path is not None else DEFAULT_SEED_PATH
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        tables = ("properties", "appliances", "service_records", "maintenance_log")
        for table in tables:
            rows = data.get(table)
            if not isinstance(rows, list):
                raise SeedError(f"seed is missing the {table} list")
            for row in rows:
                if row.get("synthetic") is not True:
                    raise SeedError(f"{table} row {row.get('id')!r} is not marked synthetic")

        with self._connect() as conn:
            for row in data["properties"]:
                conn.execute(
                    "INSERT INTO properties (id, label, synthetic) VALUES (?, ?, 1) "
                    "ON CONFLICT(id) DO UPDATE SET label=excluded.label, synthetic=1",
                    (row["id"], row["label"]),
                )
            for row in data["appliances"]:
                conn.execute(
                    """
                    INSERT INTO appliances (id, record_id, property_id, category,
                        manufacturer, model, serial, purchase_date, install_date,
                        warranty_terms, warranty_source, status, notes,
                        pending_owner_choice, synthetic)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        property_id=excluded.property_id, category=excluded.category,
                        manufacturer=excluded.manufacturer, model=excluded.model,
                        serial=excluded.serial, purchase_date=excluded.purchase_date,
                        install_date=excluded.install_date,
                        warranty_terms=excluded.warranty_terms,
                        warranty_source=excluded.warranty_source,
                        status=excluded.status, notes=excluded.notes,
                        pending_owner_choice=excluded.pending_owner_choice,
                        synthetic=1
                    """,
                    (
                        row["id"],
                        appliance_record_id(row["id"]),
                        row["property_id"],
                        row["category"],
                        row["manufacturer"],
                        row["model"],
                        row.get("serial"),
                        row.get("purchase_date"),
                        row.get("install_date"),
                        row.get("warranty_terms"),
                        row.get("warranty_source"),
                        row.get("status", "active"),
                        row.get("notes"),
                        int(bool(row.get("pending_owner_choice", False))),
                    ),
                )
            for row in data["service_records"]:
                conn.execute(
                    """
                    INSERT INTO service_records (id, record_id, appliance_id, date,
                        symptom, observed_code, work_done, performed_by, synthetic)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        appliance_id=excluded.appliance_id, date=excluded.date,
                        symptom=excluded.symptom, observed_code=excluded.observed_code,
                        work_done=excluded.work_done, performed_by=excluded.performed_by,
                        synthetic=1
                    """,
                    (
                        row["id"],
                        service_record_id(row["id"]),
                        row["appliance_id"],
                        row["date"],
                        row["symptom"],
                        row.get("observed_code"),
                        row.get("work_done"),
                        row.get("performed_by"),
                    ),
                )
            for row in data["maintenance_log"]:
                conn.execute(
                    """
                    INSERT INTO maintenance_log (id, appliance_id, task, done_on,
                        record_id, synthetic)
                    VALUES (?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        appliance_id=excluded.appliance_id, task=excluded.task,
                        done_on=excluded.done_on, synthetic=1
                    """,
                    (
                        row["id"],
                        row["appliance_id"],
                        row["task"],
                        row["done_on"],
                        maintenance_record_id(row["id"]),
                    ),
                )
        return {table: len(data[table]) for table in tables}

    # --------------------------------------------------------------- queries

    def list_properties(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM properties ORDER BY id")

    def get_appliance(self, appliance_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM appliances WHERE id = ?", (appliance_id,))
        return rows[0] if rows else None

    def list_appliances(self, property_id: str | None = None) -> list[dict[str, Any]]:
        """Appliances at one property, or every appliance when property_id is None."""
        if property_id is None:
            return self._all("SELECT * FROM appliances ORDER BY id")
        return self._all(
            "SELECT * FROM appliances WHERE property_id = ? ORDER BY id", (property_id,)
        )

    def find_by_model(self, manufacturer: str | None, model: str) -> list[dict[str, Any]]:
        """Appliances whose model (and manufacturer, when given) match.

        Case and runs of whitespace are ignored; otherwise the match is exact,
        so a model that is not registered returns nothing.
        """
        model_key = _normalize(model)
        if manufacturer is None:
            return self._all(
                "SELECT * FROM appliances WHERE lower(trim(model)) = ? ORDER BY id",
                (model_key,),
            )
        return self._all(
            "SELECT * FROM appliances "
            "WHERE lower(trim(model)) = ? AND lower(trim(manufacturer)) = ? ORDER BY id",
            (model_key, _normalize(manufacturer)),
        )

    def service_history(self, appliance_id: str) -> list[dict[str, Any]]:
        """Service records for one appliance, newest first, each with its record_id."""
        return self._all(
            "SELECT * FROM service_records WHERE appliance_id = ? ORDER BY date DESC, id",
            (appliance_id,),
        )

    def get_service_record(self, record_id: str) -> dict[str, Any] | None:
        """Look up a service record by its citation id (``service:<id>``)."""
        rows = self._all("SELECT * FROM service_records WHERE record_id = ?", (record_id,))
        return rows[0] if rows else None

    def maintenance_log(self, appliance_id: str) -> list[dict[str, Any]]:
        """Maintenance entries for one appliance, newest first."""
        return self._all(
            "SELECT * FROM maintenance_log WHERE appliance_id = ? ORDER BY done_on DESC, id",
            (appliance_id,),
        )

    # --------------------------------------------------------------- lookups

    def record_lookup(
        self,
        run_id: str,
        *,
        status: str,
        thread_id: str | None = None,
        appliance_id: str | None = None,
        route: str | None = None,
        cost_usd: float = 0.0,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Store one lookup row and return it as stored.

        ``synthetic`` is copied from the appliance; a lookup with no appliance
        is not registry data, so it is stored as not synthetic.
        """
        stamp = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            synthetic = 0
            if appliance_id is not None:
                found = conn.execute(
                    "SELECT synthetic FROM appliances WHERE id = ?", (appliance_id,)
                ).fetchone()
                if found is None:
                    raise KeyError(f"unknown appliance {appliance_id!r}")
                synthetic = int(found["synthetic"])
            cur = conn.execute(
                """
                INSERT INTO lookups (run_id, thread_id, appliance_id, status, route,
                    cost_usd, created_at, synthetic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, thread_id, appliance_id, status, route, float(cost_usd), stamp, synthetic),
            )
            row = conn.execute("SELECT * FROM lookups WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_dict(row)

    def lookups(
        self, *, run_id: str | None = None, appliance_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lookup rows in insertion order, filtered by run and/or appliance."""
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if appliance_id is not None:
            clauses.append("appliance_id = ?")
            params.append(appliance_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._all(f"SELECT * FROM lookups{where} ORDER BY id", tuple(params))


def open_registry(
    path: Path | str = REGISTRY_PATH, *, seed: Path | str | None | bool = True
) -> Registry:
    """Create or open the registry; loads the default seed unless seed is False."""
    registry = Registry(path)
    if seed is not False:
        registry.load_seed(None if seed is True else seed)
    return registry
