-- Property registry (PLAN section 8.12). Every seeded row is synthetic.
--
-- Citation scheme: each registry row a brief can cite has a record_id made of
-- a kind prefix and the row id, enforced by CHECK constraints below:
--   appliance:<appliances.id>        install date, purchase date, warranty terms
--   service:<service_records.id>     happened_before (SC8)
--   maintenance:<maintenance_log.id> maintenance_due.last_done_record_id
--   warranty:<warranty_coverage.id>  one warranty component's coverage (SC13)
-- Record ids are never URLs, so they stay out of the web source list.

CREATE TABLE IF NOT EXISTS properties (
    id        TEXT PRIMARY KEY,
    label     TEXT NOT NULL,
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);

CREATE TABLE IF NOT EXISTS appliances (
    id                   TEXT PRIMARY KEY,
    record_id            TEXT NOT NULL UNIQUE CHECK (record_id = 'appliance:' || id),
    property_id          TEXT NOT NULL REFERENCES properties(id),
    category             TEXT NOT NULL,
    manufacturer         TEXT NOT NULL,
    model                TEXT NOT NULL,
    serial               TEXT,
    purchase_date        TEXT,
    install_date         TEXT,
    warranty_terms       TEXT,
    warranty_source      TEXT,
    status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'discontinued')),
    notes                TEXT,
    pending_owner_choice INTEGER NOT NULL DEFAULT 0 CHECK (pending_owner_choice IN (0, 1)),
    synthetic            INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    -- The exact model number, when the record holds one apart from the model
    -- name (the product recall check compares it exactly). Null when unknown.
    -- It comes last because a file made before it gains it by ALTER TABLE
    -- (agent/registry.py, MIGRATIONS), which adds a column at the end; so a
    -- new file and a migrated one have the same columns in the same order.
    model_number         TEXT
);

CREATE INDEX IF NOT EXISTS appliances_by_property ON appliances(property_id);

CREATE TABLE IF NOT EXISTS service_records (
    id            TEXT PRIMARY KEY,
    record_id     TEXT NOT NULL UNIQUE CHECK (record_id = 'service:' || id),
    appliance_id  TEXT NOT NULL REFERENCES appliances(id),
    date          TEXT NOT NULL,
    symptom       TEXT NOT NULL,
    observed_code TEXT,
    work_done     TEXT,
    performed_by  TEXT,
    synthetic     INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);

CREATE INDEX IF NOT EXISTS service_by_appliance ON service_records(appliance_id);

CREATE TABLE IF NOT EXISTS maintenance_log (
    id           TEXT PRIMARY KEY,
    appliance_id TEXT NOT NULL REFERENCES appliances(id),
    task         TEXT NOT NULL,
    done_on      TEXT NOT NULL,
    record_id    TEXT NOT NULL UNIQUE CHECK (record_id = 'maintenance:' || id),
    synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);

CREATE INDEX IF NOT EXISTS maintenance_by_appliance ON maintenance_log(appliance_id);

-- Structured warranty coverage (SC13): one row per component. The term is
-- stored in months (5 years is 60), and the start date is the appliance's own
-- install or purchase date, named by start_event, so a date is never stored
-- twice. Code computes each end date and status (agent/rules/coverage.py).
-- source_text is the part of the appliance's warranty_terms the row encodes.
CREATE TABLE IF NOT EXISTS warranty_coverage (
    id           TEXT PRIMARY KEY,
    record_id    TEXT NOT NULL UNIQUE CHECK (record_id = 'warranty:' || id),
    appliance_id TEXT NOT NULL REFERENCES appliances(id),
    component    TEXT NOT NULL,
    term_months  INTEGER NOT NULL CHECK (term_months BETWEEN 1 AND 1200),
    start_event  TEXT NOT NULL CHECK (start_event IN ('install', 'purchase')),
    source_text  TEXT NOT NULL,
    synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);

CREATE INDEX IF NOT EXISTS warranty_by_appliance ON warranty_coverage(appliance_id);

-- One row per advisor run that looked up an appliance. synthetic is copied
-- from the appliance, so a lookup on seeded data is labeled synthetic too.
CREATE TABLE IF NOT EXISTS lookups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    thread_id    TEXT,
    appliance_id TEXT REFERENCES appliances(id),
    status       TEXT NOT NULL,
    route        TEXT,
    cost_usd     REAL NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1))
);

CREATE INDEX IF NOT EXISTS lookups_by_run ON lookups(run_id);
CREATE INDEX IF NOT EXISTS lookups_by_appliance ON lookups(appliance_id);
