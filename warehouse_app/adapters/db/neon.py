# Owns: all SQL reads and writes for the warehouse schema.
# Must not: contain domain logic; call config.load() directly.
# May import: warehouse_app.core.domain (types), psycopg, standard library.
#
# Convention: one function per logical operation; all SQL is a module-level
# constant named _<VERB>_<ENTITY>_SQL to keep functions readable.

from __future__ import annotations

import logging
from datetime import date, datetime

import psycopg

from warehouse_app.core.domain import DeliveryStop, InventoryItem, PickRow

logger = logging.getLogger(__name__)


# ── Inventory ─────────────────────────────────────────────────────────────────

_UPSERT_INVENTORY_SQL = """
    INSERT INTO inventory_items (
        source_inventory_id, source_model_id, model_number, manufacturer, serial_number,
        source_location_id, source_whse_location,
        source_status, status,
        source_order_item_id, source_order_id,
        is_non_sellable, is_deleted,
        cost, received_date, invoiced_date,
        source_synced_at, updated_at
    ) VALUES (
        %(source_inventory_id)s, %(source_model_id)s, %(model_number)s,
        %(manufacturer)s, %(serial_number)s,
        %(source_location_id)s, %(source_whse_location)s,
        %(source_status)s, %(status)s,
        %(source_order_item_id)s, %(source_order_id)s,
        %(is_non_sellable)s, %(is_deleted)s,
        %(cost)s, %(received_date)s, %(invoiced_date)s,
        %(source_synced_at)s, %(source_synced_at)s
    )
    ON CONFLICT (source_inventory_id) DO UPDATE SET
        source_model_id       = EXCLUDED.source_model_id,
        model_number          = EXCLUDED.model_number,
        manufacturer          = EXCLUDED.manufacturer,
        serial_number         = EXCLUDED.serial_number,
        source_location_id    = EXCLUDED.source_location_id,
        source_whse_location  = EXCLUDED.source_whse_location,
        source_status         = EXCLUDED.source_status,
        status                = EXCLUDED.status,
        source_order_item_id  = EXCLUDED.source_order_item_id,
        source_order_id       = EXCLUDED.source_order_id,
        is_non_sellable       = EXCLUDED.is_non_sellable,
        is_deleted            = EXCLUDED.is_deleted,
        cost                  = EXCLUDED.cost,
        received_date         = EXCLUDED.received_date,
        invoiced_date         = EXCLUDED.invoiced_date,
        source_synced_at      = EXCLUDED.source_synced_at,
        updated_at            = EXCLUDED.updated_at
"""

_JOIN_DIMS_SQL = """
    UPDATE inventory_items i
    SET
        carton_w_in     = m.carton_w_in,
        carton_h_in     = m.carton_h_in,
        carton_d_in     = m.carton_d_in,
        gross_weight_lb = m.gross_weight_lb,
        product_class   = m.product_class
    FROM model_size_catalog m
    WHERE i.model_number = m.model_number
      AND (m.carton_w_in IS NOT NULL
           OR m.carton_h_in IS NOT NULL
           OR m.product_class IS NOT NULL)
"""

_PRUNE_INVENTORY_SQL = """
    DELETE FROM inventory_items
    WHERE source_synced_at < %(sync_start)s
"""


def upsert_inventory(
    conn: psycopg.Connection,
    rows: list[dict],
    sync_start: datetime,
) -> tuple[int, int, int]:
    """Upsert, join dims, prune stale. Returns (upserted, dim_updated, pruned)."""
    stamped = [{**r, "source_synced_at": sync_start} for r in rows]
    with conn.cursor() as cur:
        batch = 500
        for i in range(0, len(stamped), batch):
            cur.executemany(_UPSERT_INVENTORY_SQL, stamped[i : i + batch])
        cur.execute(_JOIN_DIMS_SQL)
        dim_updated = cur.rowcount
        cur.execute(_PRUNE_INVENTORY_SQL, {"sync_start": sync_start})
        pruned = cur.rowcount
    conn.commit()
    return len(stamped), dim_updated, pruned


# ── Delivery stops ────────────────────────────────────────────────────────────

_UPSERT_STOP_SQL = """
    INSERT INTO delivery_stops (
        sink_item_id, sink_board_id, sink_status,
        delivery_date, truck_id, stop_order,
        source_order_id, customer_name, delivery_address, delivery_notes,
        synced_at, updated_at
    ) VALUES (
        %(sink_item_id)s, %(sink_board_id)s, %(sink_status)s,
        %(delivery_date)s, %(truck_id)s, %(stop_order)s,
        %(source_order_id)s, %(customer_name)s, %(delivery_address)s, %(delivery_notes)s,
        now(), now()
    )
    ON CONFLICT (delivery_date, truck_id, source_order_id)
    WHERE source_order_id IS NOT NULL
    DO UPDATE SET
        sink_item_id   = COALESCE(EXCLUDED.sink_item_id,  delivery_stops.sink_item_id),
        sink_status    = COALESCE(EXCLUDED.sink_status,   delivery_stops.sink_status),
        stop_order     = COALESCE(EXCLUDED.stop_order,    delivery_stops.stop_order),
        customer_name  = COALESCE(EXCLUDED.customer_name, delivery_stops.customer_name),
        delivery_notes = COALESCE(EXCLUDED.delivery_notes, delivery_stops.delivery_notes),
        synced_at      = now(),
        updated_at     = now()
"""

_FETCH_STOPS_SQL = """
    SELECT stop_id, delivery_date, truck_id, stop_order, source_order_id,
           customer_name, sink_item_id, sink_board_id, sink_status, delivery_notes
    FROM delivery_stops
    WHERE delivery_date = %(delivery_date)s
      AND source_order_id IS NOT NULL
      AND truck_id NOT IN ('STORAGE','UNPAID','ISSUES','RETURN','UNROUTED')
    ORDER BY truck_id, stop_order NULLS LAST
"""


def upsert_stops(conn: psycopg.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    delivery_date = rows[0]["delivery_date"]
    with conn.cursor() as cur:
        # pick_queue has a FK to delivery_stops; clear it first for this date.
        cur.execute(
            "DELETE FROM pick_queue WHERE delivery_date = %s",
            (delivery_date,),
        )
        cur.execute("DELETE FROM delivery_stops WHERE delivery_date = %s", (delivery_date,))
        cur.executemany(_UPSERT_STOP_SQL, rows)
    conn.commit()
    return len(rows)


def fetch_stops(conn: psycopg.Connection, delivery_date: date) -> list[DeliveryStop]:
    with conn.cursor() as cur:
        cur.execute(_FETCH_STOPS_SQL, {"delivery_date": delivery_date})
        cols = [d.name for d in cur.description]
        return [DeliveryStop(**dict(zip(cols, row))) for row in cur.fetchall()]


# ── Pick queue ────────────────────────────────────────────────────────────────

_ENSURE_PQ_INDEX_SQL = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_pq_stop_inventory
        ON pick_queue (stop_id, source_inventory_id)
        WHERE source_inventory_id IS NOT NULL
"""

_CLEAR_PQ_DATE_SQL = """
    DELETE FROM pick_queue
    WHERE delivery_date = %(d)s
      AND status IN ('queued', 'assigned')
"""

_UPSERT_PQ_SQL = """
    INSERT INTO pick_queue (
        stop_id, source_inventory_id, source_order_item_id,
        delivery_date, truck_id, stop_order, piece_order,
        model_number, whse_location,
        carton_w_in, carton_h_in, carton_d_in, gross_weight_lb,
        status, created_at, updated_at
    ) VALUES (
        %(stop_id)s, %(source_inventory_id)s, %(source_order_item_id)s,
        %(delivery_date)s, %(truck_id)s, %(stop_order)s, %(piece_order)s,
        %(model_number)s, %(whse_location)s,
        %(carton_w_in)s, %(carton_h_in)s, %(carton_d_in)s, %(gross_weight_lb)s,
        'queued', now(), now()
    )
    ON CONFLICT (stop_id, source_inventory_id)
    WHERE source_inventory_id IS NOT NULL
    DO UPDATE SET
        source_order_item_id = EXCLUDED.source_order_item_id,
        stop_order           = EXCLUDED.stop_order,
        piece_order          = EXCLUDED.piece_order,
        whse_location        = EXCLUDED.whse_location,
        carton_w_in          = EXCLUDED.carton_w_in,
        carton_h_in          = EXCLUDED.carton_h_in,
        carton_d_in          = EXCLUDED.carton_d_in,
        gross_weight_lb      = EXCLUDED.gross_weight_lb,
        updated_at           = now()
    WHERE pick_queue.status IN ('queued', 'assigned')
"""

# Location IDs that contain physically pickable inventory.
# 1=WAREHO  2=OUTLET  3=BANGY  4=DAVIS  7=WILLCA  9=BEND
_PICKABLE_LOCATION_IDS = (1, 2, 3, 4, 7, 9)

_FETCH_ALLOCATED_SQL = """
    SELECT
        source_order_id, source_inventory_id, source_order_item_id,
        model_number, source_whse_location,
        carton_w_in, carton_h_in, carton_d_in, gross_weight_lb
    FROM inventory_items
    WHERE source_order_id = ANY(%(order_ids)s)
      AND is_allocated = TRUE
      AND source_location_id = ANY(%(pickable_ids)s)
    ORDER BY source_order_id, model_number, source_inventory_id
"""


def fetch_allocated_by_order(
    conn: psycopg.Connection,
    order_ids: list[int],
) -> dict[int, list[InventoryItem]]:
    if not order_ids:
        return {}
    by_order: dict[int, list[InventoryItem]] = {}
    with conn.cursor() as cur:
        cur.execute(_FETCH_ALLOCATED_SQL, {
            "order_ids": order_ids,
            "pickable_ids": list(_PICKABLE_LOCATION_IDS),
        })
        cols = [d.name for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            item = InventoryItem(
                source_inventory_id=rec["source_inventory_id"],
                model_number=rec["model_number"],
                status="in_warehouse",
                source_location_id=1,
                source_whse_location=rec["source_whse_location"],
                source_order_id=rec["source_order_id"],
                source_order_item_id=rec["source_order_item_id"],
                is_allocated=True,
            )
            by_order.setdefault(rec["source_order_id"], []).append(item)
    return by_order


def write_pick_queue(
    conn: psycopg.Connection,
    rows: list[PickRow],
    delivery_date: date,
) -> int:
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS uq_pq_stop_order_item")
        cur.execute(_ENSURE_PQ_INDEX_SQL)
        if rows:
            cur.execute(_CLEAR_PQ_DATE_SQL, {"d": delivery_date})
        row_dicts = [
            {
                "stop_id":              r.stop_id,
                "source_inventory_id":  r.source_inventory_id,
                "source_order_item_id": r.source_order_item_id,
                "delivery_date":        r.delivery_date,
                "truck_id":             r.truck_id,
                "stop_order":           r.stop_order,
                "piece_order":          r.piece_order,
                "model_number":         r.model_number,
                "whse_location":        r.whse_location,
                "carton_w_in":          r.carton_w_in,
                "carton_h_in":          r.carton_h_in,
                "carton_d_in":          r.carton_d_in,
                "gross_weight_lb":      r.gross_weight_lb,
            }
            for r in rows
        ]
        batch = 200
        for i in range(0, len(row_dicts), batch):
            cur.executemany(_UPSERT_PQ_SQL, row_dicts[i : i + batch])
    conn.commit()
    return len(rows)
