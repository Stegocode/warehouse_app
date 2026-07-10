# Owns: SQL reads and writes for the model_size_catalog table.
# Must not: contain domain classification logic; call config.load().
# May import: warehouse_app.core.domain, psycopg, standard library.
#
# Convention: one function per logical operation; all SQL is a module-level
# constant named _<VERB>_<ENTITY>_SQL to keep functions readable.

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)


# ── Model upsert ──────────────────────────────────────────────────────────────

_UPSERT_MODELS_SQL = """
    INSERT INTO model_size_catalog (
        model_number, manufacturer, description,
        category, type, is_part,
        product_class, width_in, floor_only, source_product_id
    ) VALUES (
        %(model_number)s, %(manufacturer)s, %(description)s,
        %(category)s, %(type)s, %(is_part)s,
        %(product_class)s, %(width_in)s, %(floor_only)s, %(source_product_id)s
    )
    ON CONFLICT (model_number) DO UPDATE SET
        manufacturer      = COALESCE(EXCLUDED.manufacturer,  model_size_catalog.manufacturer),
        description       = COALESCE(EXCLUDED.description,   model_size_catalog.description),
        category          = CASE WHEN EXCLUDED.category != '' THEN EXCLUDED.category
                                 ELSE model_size_catalog.category END,
        type              = CASE WHEN EXCLUDED.type != '' THEN EXCLUDED.type
                                 ELSE model_size_catalog.type END,
        is_part           = EXCLUDED.is_part,
        product_class     = EXCLUDED.product_class,
        width_in          = COALESCE(EXCLUDED.width_in, model_size_catalog.width_in),
        floor_only        = EXCLUDED.floor_only,
        source_product_id = COALESCE(EXCLUDED.source_product_id, model_size_catalog.source_product_id),
        updated_at        = now()
"""


# ── Physical and carton dimensions ────────────────────────────────────────────

_UPDATE_DIMS_SQL = """
    UPDATE model_size_catalog
    SET
        width_in        = COALESCE(%(width_in)s,        width_in),
        height_in       = COALESCE(%(height_in)s,       height_in),
        depth_in        = COALESCE(%(depth_in)s,        depth_in),
        carton_w_in     = COALESCE(%(carton_w_in)s,     carton_w_in),
        carton_h_in     = COALESCE(%(carton_h_in)s,     carton_h_in),
        carton_d_in     = COALESCE(%(carton_d_in)s,     carton_d_in),
        gross_weight_lb = COALESCE(%(gross_weight_lb)s, gross_weight_lb),
        updated_at      = now()
    WHERE model_number = %(model_number)s
"""


# ── Dim-fetch candidates ──────────────────────────────────────────────────────

_FETCH_DIM_CANDIDATES_SQL = """
    SELECT model_number, source_product_id
    FROM model_size_catalog
    WHERE height_in IS NULL
      AND source_product_id IS NOT NULL
    ORDER BY model_number
"""

_FETCH_DIM_CANDIDATES_ALL_SQL = """
    SELECT model_number, source_product_id
    FROM model_size_catalog
    WHERE source_product_id IS NOT NULL
    ORDER BY model_number
"""


# ── Model-number listing ──────────────────────────────────────────────────────

_FETCH_ALL_MODEL_NUMBERS_SQL = """
    SELECT model_number
    FROM model_size_catalog
    ORDER BY model_number
"""


# ── Location stats (ranked window-function query) ─────────────────────────────
#
# loc_prefixed: for each (model, row_prefix) pair, count how many inventory
#   items sit there. Row prefix = characters before the first digit
#   e.g. 'A' from 'A12-3', 'BS' from 'BS4'.
# ranked: pick the single most-frequent prefix per model (rn = 1).
# totals: total serial count per model across all locations.

_FETCH_LOCATION_STATS_SQL = """
    WITH loc_prefixed AS (
        SELECT
            model_number,
            regexp_replace(source_whse_location, '\\d.*', '') AS row_prefix,
            COUNT(*) AS cnt
        FROM inventory_items
        WHERE source_whse_location IS NOT NULL
        GROUP BY model_number, row_prefix
    ),
    ranked AS (
        SELECT
            model_number,
            row_prefix,
            ROW_NUMBER() OVER (
                PARTITION BY model_number
                ORDER BY cnt DESC
            ) AS rn
        FROM loc_prefixed
    ),
    totals AS (
        SELECT model_number, COUNT(*) AS serial_count
        FROM inventory_items
        GROUP BY model_number
    )
    SELECT
        r.model_number,
        r.row_prefix                AS primary_row,
        COALESCE(t.serial_count, 0) AS serial_count
    FROM ranked r
    LEFT JOIN totals t ON t.model_number = r.model_number
    WHERE r.rn = 1
    ORDER BY r.model_number
"""


# ── Primary-row update ────────────────────────────────────────────────────────

_UPDATE_PRIMARY_ROW_SQL = """
    UPDATE model_size_catalog
    SET
        primary_row = %(primary_row)s,
        updated_at  = now()
    WHERE model_number = %(model_number)s
"""


# ── Serial-count update ───────────────────────────────────────────────────────

_UPDATE_SERIAL_COUNT_SQL = """
    UPDATE model_size_catalog
    SET
        serial_count = %(serial_count)s,
        updated_at   = now()
    WHERE model_number = %(model_number)s
"""


# ── Product-class update ──────────────────────────────────────────────────────

_UPDATE_PRODUCT_CLASS_SQL = """
    UPDATE model_size_catalog
    SET
        product_class = %(product_class)s,
        floor_only    = %(floor_only)s,
        updated_at    = now()
    WHERE model_number = %(model_number)s
"""


# ── Size-tier ─────────────────────────────────────────────────────────────────

_FETCH_TIER_CANDIDATES_SQL = """
    SELECT model_number, height_in
    FROM model_size_catalog
    WHERE height_in IS NOT NULL
    ORDER BY model_number
"""

_UPDATE_SIZE_TIER_SQL = """
    UPDATE model_size_catalog
    SET
        size_tier  = %(size_tier)s,
        updated_at = now()
    WHERE model_number = %(model_number)s
"""

_REPORT_TIER_COVERAGE_SQL = """
    SELECT
        COUNT(*)         AS total,
        COUNT(size_tier) AS with_tier,
        ROUND(
            100.0 * COUNT(size_tier) / NULLIF(COUNT(*), 0),
            1
        ) AS pct
    FROM model_size_catalog
"""


# ── Functions ─────────────────────────────────────────────────────────────────

def upsert_models(conn: psycopg.Connection, rows: list[dict]) -> int:
    """Upsert rows into model_size_catalog. Returns count inserted/updated."""
    if not rows:
        return 0
    batch = 500
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            cur.executemany(_UPSERT_MODELS_SQL, rows[i : i + batch])
    conn.commit()
    logger.debug("upserted %d model rows", len(rows))
    return len(rows)


def update_dims(conn: psycopg.Connection, rows: list[dict]) -> int:
    """Update physical + carton dimensions for given model_numbers.
    Each row: {model_number, width_in, height_in, depth_in,
               carton_w_in, carton_h_in, carton_d_in, gross_weight_lb}
    Only updates non-None values (COALESCE preserves the existing DB value
    when the incoming value is NULL)."""
    if not rows:
        return 0
    batch = 500
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            cur.executemany(_UPDATE_DIMS_SQL, rows[i : i + batch])
    conn.commit()
    logger.debug("updated dims for %d models", len(rows))
    return len(rows)


def fetch_source_dim_candidates(
    conn: psycopg.Connection,
    refetch: bool = False,
    limit: int | None = None,
) -> list[tuple[str, int]]:
    """Return (model_number, source_product_id) for models needing dim refresh.
    If refetch=False, only returns rows where height_in IS NULL.
    If refetch=True, returns all with source_product_id IS NOT NULL."""
    sql = _FETCH_DIM_CANDIDATES_ALL_SQL if refetch else _FETCH_DIM_CANDIDATES_SQL
    with conn.cursor() as cur:
        cur.execute(sql)
        raw = cur.fetchmany(limit) if limit is not None else cur.fetchall()
    return [(row[0], row[1]) for row in raw]


def fetch_all_model_numbers(conn: psycopg.Connection) -> list[str]:
    """Return all model_numbers from catalog, ordered alphabetically."""
    with conn.cursor() as cur:
        cur.execute(_FETCH_ALL_MODEL_NUMBERS_SQL)
        return [row[0] for row in cur.fetchall()]


def fetch_model_location_stats(conn: psycopg.Connection) -> list[dict]:
    """Return location stats for all model numbers from inventory.
    Each row: {model_number, primary_row, serial_count}
    primary_row = the most-common source_whse_location row prefix
    (characters before the first digit, e.g. 'A' from 'A12-3').

    Uses a window-function ranked CTE to find the dominant warehouse
    location for each model, ported from the ranked query in sync_model_meta.py."""
    with conn.cursor() as cur:
        cur.execute(_FETCH_LOCATION_STATS_SQL)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def batch_update_primary_rows(conn: psycopg.Connection, updates: list[dict]) -> int:
    """Update primary_row in model_size_catalog.
    Each dict: {model_number: str, primary_row: str}"""
    if not updates:
        return 0
    batch = 500
    with conn.cursor() as cur:
        for i in range(0, len(updates), batch):
            cur.executemany(_UPDATE_PRIMARY_ROW_SQL, updates[i : i + batch])
    conn.commit()
    return len(updates)


def batch_update_serial_counts(conn: psycopg.Connection, updates: list[dict]) -> int:
    """Update serial_count in model_size_catalog.
    Each dict: {model_number: str, serial_count: int}"""
    if not updates:
        return 0
    batch = 500
    with conn.cursor() as cur:
        for i in range(0, len(updates), batch):
            cur.executemany(_UPDATE_SERIAL_COUNT_SQL, updates[i : i + batch])
    conn.commit()
    return len(updates)


def batch_update_product_classes(conn: psycopg.Connection, updates: list[dict]) -> int:
    """Update product_class and floor_only for reclassified models.
    Each dict: {model_number: str, product_class: str, floor_only: bool}"""
    if not updates:
        return 0
    batch = 500
    with conn.cursor() as cur:
        for i in range(0, len(updates), batch):
            cur.executemany(_UPDATE_PRODUCT_CLASS_SQL, updates[i : i + batch])
    conn.commit()
    return len(updates)


def fetch_tier_candidates(conn: psycopg.Connection) -> list[tuple[str, float]]:
    """Return (model_number, height_in) for all models where height_in IS NOT NULL."""
    with conn.cursor() as cur:
        cur.execute(_FETCH_TIER_CANDIDATES_SQL)
        return [(row[0], row[1]) for row in cur.fetchall()]


def update_size_tiers(conn: psycopg.Connection, rows: list[dict]) -> int:
    """Update size_tier. Each dict: {model_number: str, size_tier: int}"""
    if not rows:
        return 0
    batch = 500
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            cur.executemany(_UPDATE_SIZE_TIER_SQL, rows[i : i + batch])
    conn.commit()
    return len(rows)


def report_tier_coverage(conn: psycopg.Connection) -> dict:
    """Return {total: int, with_tier: int, pct: float}"""
    with conn.cursor() as cur:
        cur.execute(_REPORT_TIER_COVERAGE_SQL)
        row = cur.fetchone()
    total, with_tier, pct = row
    return {
        "total":     int(total),
        "with_tier": int(with_tier),
        "pct":       float(pct) if pct is not None else 0.0,
    }
