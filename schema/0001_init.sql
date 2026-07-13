-- warehouse_app canonical schema (v1) — BASELINE. Do not edit.
--
-- Applies to a fresh database. Reflects all migrations from model_catalog_module
-- 0001 + 0002 (constraint relaxation) + 0003 (column renames to domain vocabulary).
--
-- This file is the frozen starting point, not the current schema. Every change since
-- lives as a numbered, idempotent, forward-only file in schema/migrations/, applied by:
--
--     python -m warehouse_app.scripts.migrate --env-file <path>
--
-- Bootstrapping a fresh database:
--     1. psql < schema/0001_init.sql        (this file, once)
--     2. python -m warehouse_app.scripts.migrate   (everything since)
--
-- Editing this file would desync it from the databases already built on it. Add a
-- migration instead — the runner rejects a migration whose contents changed after it
-- was applied, precisely so the repo and the database cannot silently diverge.
--
-- Naming conventions:
--   source_*  — fields from the upstream ERP / inventory system
--   sink_*    — fields from the downstream notification / tracking board

BEGIN;


-- ── SOURCE LOCATIONS ─────────────────────────────────────────────────────────
-- Maps source system location IDs to human-readable names.
-- source_location_id=1 is the primary warehouse (WAREHO).

CREATE TABLE source_locations (
    source_location_id  INTEGER     PRIMARY KEY,
    name                TEXT        NOT NULL,
    description         TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO source_locations (source_location_id, name, description) VALUES
    (1, 'WAREHO',  'Primary warehouse'),
    (2, 'OUTLET',  'Outlet / scratch-and-dent floor'),
    (3, 'DISPLAY', 'Showroom displays'),
    (4, 'TRANSIT', 'In transit between locations'),
    (5, 'VENDOR',  'At vendor for service'),
    (6, 'OTHER',   'Other / unknown'),
    (7, 'SERVICE', 'Service department');


-- ── MODEL SIZE CATALOG ───────────────────────────────────────────────────────
-- One row per model number. Populated by source model sync + dim scrapers.

CREATE TABLE model_size_catalog (
    model_number    TEXT        PRIMARY KEY,
    manufacturer    TEXT,
    description     TEXT,
    category        TEXT,
    type            TEXT,
    is_part         BOOLEAN     NOT NULL DEFAULT FALSE,
    product_class   TEXT,
    width_in        REAL,
    height_in       REAL,
    depth_in        REAL,
    carton_w_in     INTEGER,
    carton_h_in     INTEGER,
    carton_d_in     INTEGER,
    gross_weight_lb INTEGER,
    size_tier       INTEGER,
    floor_only      BOOLEAN     NOT NULL DEFAULT FALSE,
    primary_row     TEXT,
    serial_count    INTEGER,
    source_product_id INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_msc_class ON model_size_catalog (product_class);


-- ── INVENTORY ITEMS ───────────────────────────────────────────────────────────
-- One row per physical serialized unit. Synced from the source system.

CREATE TABLE inventory_items (
    source_inventory_id INTEGER     PRIMARY KEY,
    source_model_id     INTEGER,
    model_number        TEXT        NOT NULL
                            REFERENCES model_size_catalog (model_number)
                            ON UPDATE CASCADE,
    manufacturer        TEXT,
    serial_number       TEXT,

    -- Location
    source_location_id  INTEGER     NOT NULL
                            REFERENCES source_locations (source_location_id),
    source_whse_location TEXT,

    -- Status
    source_status       INTEGER,
    status              TEXT        NOT NULL DEFAULT 'in_warehouse'
                            CHECK (status IN ('on_order','in_warehouse','in_transit','missing')),
    is_non_sellable     BOOLEAN     NOT NULL DEFAULT FALSE,
    is_deleted          BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Order linkage
    source_order_item_id INTEGER,
    source_order_id     INTEGER,

    -- Derived (gate: re-run after schema changes that touch source_order_item_id)
    is_allocated        BOOLEAN     NOT NULL
                            GENERATED ALWAYS AS (source_order_item_id IS NOT NULL) STORED,

    -- Financials
    cost                REAL,
    received_date       TEXT,
    invoiced_date       TEXT,

    -- Denormalized dims (joined from model_size_catalog at sync time)
    carton_w_in         INTEGER,
    carton_h_in         INTEGER,
    carton_d_in         INTEGER,
    gross_weight_lb     INTEGER,
    product_class       TEXT,

    source_synced_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_inv_location   ON inventory_items (source_location_id, status);
CREATE INDEX idx_inv_status     ON inventory_items (status);
CREATE INDEX idx_inv_order_item ON inventory_items (source_order_item_id)
    WHERE source_order_item_id IS NOT NULL;
CREATE INDEX idx_inv_model      ON inventory_items (model_number);


-- ── WAREHOUSE BINS ────────────────────────────────────────────────────────────
-- WAREHO sub-locations only. Populated by the layout JSON import.

CREATE TABLE warehouse_bins (
    whse_location   TEXT        PRIMARY KEY,
    row_token       TEXT        NOT NULL,
    bay             INTEGER     NOT NULL,
    level           INTEGER     NOT NULL,
    height_m        REAL        NOT NULL,
    x               REAL,
    y               REAL,
    rack_type       TEXT,
    section         TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bins_row ON warehouse_bins (row_token);


-- ── LOCATION ALIASES ──────────────────────────────────────────────────────────
-- Normalises source system bin name variants to the canonical warehouse_bins key.

CREATE TABLE location_aliases (
    raw_location    TEXT        PRIMARY KEY,
    canonical_bin   TEXT        NOT NULL REFERENCES warehouse_bins (whse_location),
    note            TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── ROUTING GRAPH ─────────────────────────────────────────────────────────────

CREATE TABLE graph_nodes (
    node_id     TEXT        PRIMARY KEY,
    kind        TEXT        NOT NULL,
    x           REAL        NOT NULL,
    y           REAL        NOT NULL,
    zone        TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE graph_edges (
    node_a      TEXT        NOT NULL REFERENCES graph_nodes (node_id),
    node_b      TEXT        NOT NULL REFERENCES graph_nodes (node_id),
    distance_m  REAL        NOT NULL DEFAULT 1.0,
    ramp        BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node_a, node_b)
);


-- ── DELIVERY STOPS ────────────────────────────────────────────────────────────
-- One row per confirmed delivery stop per date.
-- Route sheet drives the full stop list; the sink board enriches flagged stops.

CREATE TABLE delivery_stops (
    stop_id             TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,

    -- Sink (notification board) identifiers — NULL for route-sheet-only stops
    sink_item_id        TEXT,
    sink_board_id       TEXT,
    sink_status         TEXT,

    -- Schedule
    delivery_date       DATE        NOT NULL,
    truck_id            TEXT        NOT NULL,
    stop_order          INTEGER,

    -- Order / customer
    source_order_id     INTEGER,
    customer_name       TEXT,
    delivery_address    TEXT,
    delivery_notes      TEXT,

    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_stops_date ON delivery_stops (delivery_date, truck_id, stop_order);
CREATE INDEX idx_stops_order ON delivery_stops (source_order_id)
    WHERE source_order_id IS NOT NULL;

-- Conflict key for upsert: one stop per (date, truck, order).
CREATE UNIQUE INDEX uq_stops_order ON delivery_stops (delivery_date, truck_id, source_order_id)
    WHERE source_order_id IS NOT NULL;

-- Allow multiple stops per sink item (one item may cover multiple serials).
CREATE UNIQUE INDEX uq_stops_sink_item ON delivery_stops (sink_item_id)
    WHERE sink_item_id IS NOT NULL;


-- ── PICK QUEUE ────────────────────────────────────────────────────────────────
-- One row per serialized unit to be picked for a confirmed stop.

CREATE TABLE pick_queue (
    pick_id             TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,

    stop_id             TEXT        NOT NULL REFERENCES delivery_stops (stop_id),
    source_inventory_id INTEGER     REFERENCES inventory_items (source_inventory_id),
    source_order_item_id INTEGER    NOT NULL,

    delivery_date       DATE        NOT NULL,
    truck_id            TEXT        NOT NULL,
    stop_order          INTEGER     NOT NULL,
    piece_order         INTEGER     NOT NULL,
    model_number        TEXT        NOT NULL,
    whse_location       TEXT,

    carton_w_in         INTEGER,
    carton_h_in         INTEGER,
    carton_d_in         INTEGER,
    gross_weight_lb     INTEGER,

    has_discrepancy     BOOLEAN     NOT NULL DEFAULT FALSE,
    discrepancy_reason  TEXT,

    status              TEXT        NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued','assigned','picked',
                                              'staged','on_truck','discrepancy')),
    assigned_to         TEXT,
    assigned_at         TIMESTAMPTZ,
    picked_at           TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_pq_date_status ON pick_queue
    (delivery_date, status, truck_id, stop_order, piece_order);
CREATE INDEX idx_pq_stop      ON pick_queue (stop_id);
CREATE INDEX idx_pq_inventory ON pick_queue (source_inventory_id)
    WHERE source_inventory_id IS NOT NULL;

-- Conflict key: one row per physical unit per stop.
CREATE UNIQUE INDEX uq_pq_stop_inventory ON pick_queue (stop_id, source_inventory_id)
    WHERE source_inventory_id IS NOT NULL;


-- ── ASSIGNMENT TICKETS ────────────────────────────────────────────────────────
-- Tasks dispatched to warehouse employees.

CREATE TABLE assignment_tickets (
    ticket_id           TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    employee_id         TEXT        NOT NULL,
    action              TEXT        NOT NULL
                            CHECK (action IN ('pick','stage','move','receive','relocate')),
    pick_id             TEXT        REFERENCES pick_queue (pick_id),
    source_inventory_id INTEGER,
    model_number        TEXT,
    from_location       TEXT,
    to_location         TEXT,
    status              TEXT        NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open','in_progress','confirmed','cancelled')),
    confirmed_at        TIMESTAMPTZ,
    adjusted_to         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tickets_employee ON assignment_tickets (employee_id, status);
CREATE INDEX idx_tickets_pick     ON assignment_tickets (pick_id)
    WHERE pick_id IS NOT NULL;


-- ── FLAGS ─────────────────────────────────────────────────────────────────────
-- Discrepancies surfaced by sync scripts. Resolved manually or by re-sync.

CREATE TABLE flags (
    flag_id             TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    source_inventory_id INTEGER,
    model_number        TEXT,
    source_order_id     INTEGER,
    stop_id             TEXT        REFERENCES delivery_stops (stop_id),
    pick_id             TEXT        REFERENCES pick_queue (pick_id),
    sink_item_id        TEXT,
    reason              TEXT        NOT NULL,
    detail              TEXT,
    flagged_by          TEXT        NOT NULL DEFAULT 'system',
    resolved_at         TIMESTAMPTZ,
    resolution_note     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_flags_stop       ON flags (stop_id) WHERE stop_id IS NOT NULL;
CREATE INDEX idx_flags_unresolved ON flags (created_at) WHERE resolved_at IS NULL;


COMMIT;
