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

from warehouse_app.core.domain import (
    PICKABLE_SOURCE_STATUS,
    SOURCE_STATUS_MAP,
    DeliveryStop,
    InventoryItem,
    PickRow,
)

logger = logging.getLogger(__name__)


# ── Inventory ─────────────────────────────────────────────────────────────────

_UPSERT_INVENTORY_SQL = """
    INSERT INTO inventory_items (
        source_inventory_id, source_model_id, model_number, manufacturer, serial_number,
        image_url, short_description,
        source_location_id, source_whse_location,
        source_status, status,
        source_order_item_id, source_order_id,
        is_non_sellable, is_deleted,
        cost, received_date, invoiced_date,
        source_synced_at, updated_at
    ) VALUES (
        %(source_inventory_id)s, %(source_model_id)s, %(model_number)s,
        %(manufacturer)s, %(serial_number)s,
        %(image_url)s, %(short_description)s,
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
        image_url             = EXCLUDED.image_url,
        short_description     = EXCLUDED.short_description,
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

# Prune inventory the ERP no longer returns (not seen in this sync). Never delete a row
# still referenced by pick_queue: it is on someone's work list (perhaps already picked),
# the FK would block the delete and roll back the whole sync, and an item on a pick list
# is by definition not gone. Such rows are simply kept until the pick queue that holds
# them is itself cleared.
_PRUNE_INVENTORY_SQL = """
    DELETE FROM inventory_items i
    WHERE i.source_synced_at < %(sync_start)s
      AND NOT EXISTS (
          SELECT 1 FROM pick_queue pq
          WHERE pq.source_inventory_id = i.source_inventory_id
      )
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
    ON CONFLICT (delivery_date, source_order_id)
    WHERE source_order_id IS NOT NULL
    DO UPDATE SET
        -- truck_id is now updatable rather than part of the identity: a re-routed order
        -- stays the SAME stop (same stop_id), so picks already claimed against it survive.
        truck_id       = EXCLUDED.truck_id,
        sink_item_id   = COALESCE(EXCLUDED.sink_item_id,  delivery_stops.sink_item_id),
        sink_status    = COALESCE(EXCLUDED.sink_status,   delivery_stops.sink_status),
        stop_order     = COALESCE(EXCLUDED.stop_order,    delivery_stops.stop_order),
        customer_name  = COALESCE(EXCLUDED.customer_name, delivery_stops.customer_name),
        delivery_notes = COALESCE(EXCLUDED.delivery_notes, delivery_stops.delivery_notes),
        synced_at      = now(),
        updated_at     = now()
"""

# Stops that were on a previous route sheet for this date but are not on this one.
# in_progress counts pick rows a human has already acted on — those must never be
# deleted out from under them.
_VANISHED_STOPS_SQL = """
    SELECT ds.stop_id,
           ds.source_order_id,
           ds.truck_id,
           (SELECT count(*) FROM pick_queue pq
             WHERE pq.stop_id = ds.stop_id AND pq.status <> 'queued') AS in_progress
    FROM delivery_stops ds
    WHERE ds.delivery_date = %(d)s
      AND ds.source_order_id <> ALL(%(orders)s)
"""

_DELETE_QUEUED_FOR_STOPS_SQL = """
    DELETE FROM pick_queue
    WHERE stop_id = ANY(%(stop_ids)s)
      AND status = 'queued'
"""

_DELETE_STOPS_SQL = """
    DELETE FROM delivery_stops
    WHERE stop_id = ANY(%(stop_ids)s)
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
    """Upsert the route sheet for a date without destroying work already in progress.

    Safe to re-run mid-shift. Stops are matched on their natural key
    (delivery_date, source_order_id), so stop_id is stable and pick rows stay attached
    even when an order is moved to a different truck.

    A stop that has dropped off the route sheet has its still-'queued' picks removed and
    the stop deleted. If a picker has already claimed or picked against that stop, the
    stop is LEFT ALONE and a warning is raised — an order leaving the route does not
    un-happen the fact that someone physically moved the appliance. Deleting it would
    make the database lie about the floor.
    """
    if not rows:
        return 0

    delivery_date = rows[0]["delivery_date"]
    orders = [r["source_order_id"] for r in rows if r.get("source_order_id") is not None]
    if len(orders) != len(rows):
        raise RuntimeError(
            "upsert_stops: every stop row must carry a source_order_id — it is the "
            "natural key. Refusing to write rows that cannot be matched on a refresh."
        )

    with conn.cursor() as cur:
        cur.execute(_VANISHED_STOPS_SQL, {"d": delivery_date, "orders": orders})
        vanished = cur.fetchall()

        droppable = [v for v in vanished if v[3] == 0]
        in_progress = [v for v in vanished if v[3] > 0]

        if in_progress:
            for stop_id, order_id, truck_id, n in in_progress:
                logger.warning(
                    "upsert_stops: order %s (truck %s) left the route sheet for %s but has "
                    "%d pick row(s) already assigned or picked — KEEPING the stop. "
                    "Resolve by hand: the goods may already be staged.",
                    order_id, truck_id, delivery_date, n,
                )

        if droppable:
            stop_ids = [v[0] for v in droppable]
            cur.execute(_DELETE_QUEUED_FOR_STOPS_SQL, {"stop_ids": stop_ids})
            dropped_picks = cur.rowcount
            cur.execute(_DELETE_STOPS_SQL, {"stop_ids": stop_ids})
            logger.info(
                "upsert_stops: %d stop(s) left the route sheet for %s — removed with "
                "%d queued pick row(s)",
                len(droppable), delivery_date, dropped_picks,
            )

        cur.executemany(_UPSERT_STOP_SQL, rows)

    conn.commit()
    logger.info(
        "upsert_stops: upserted %d stop(s) for %s (stop_id preserved; in-progress picks intact)",
        len(rows), delivery_date,
    )
    return len(rows)


def fetch_stops(conn: psycopg.Connection, delivery_date: date) -> list[DeliveryStop]:
    with conn.cursor() as cur:
        cur.execute(_FETCH_STOPS_SQL, {"delivery_date": delivery_date})
        cols = [d.name for d in cur.description]
        return [DeliveryStop(**dict(zip(cols, row))) for row in cur.fetchall()]


# ── Pick queue ────────────────────────────────────────────────────────────────

# Only 'queued' rows are rebuildable. A row that a picker has already claimed
# ('assigned') or physically moved ('picked' and beyond) is a record of something that
# happened in the real world — a refresh must never delete it. This previously also
# cleared 'assigned', which silently destroyed in-flight claims on every rebuild.
_CLEAR_PQ_DATE_SQL = """
    DELETE FROM pick_queue
    WHERE delivery_date = %(d)s
      AND status = 'queued'
"""

# Rows surviving the clear are in-progress or complete. For an 'assigned' row we
# refresh routing (the truck or stop may have moved) but never touch status,
# assigned_to, or assigned_at — the claim stands. Anything further along is left
# entirely alone.
_UPSERT_PQ_SQL = """
    INSERT INTO pick_queue (
        stop_id, source_inventory_id, source_order_item_id,
        delivery_date, truck_id, truck_sort_order, stop_order, piece_order,
        model_number, whse_location,
        carton_w_in, carton_h_in, carton_d_in, gross_weight_lb,
        status, created_at, updated_at
    ) VALUES (
        %(stop_id)s, %(source_inventory_id)s, %(source_order_item_id)s,
        %(delivery_date)s, %(truck_id)s, %(truck_sort_order)s, %(stop_order)s, %(piece_order)s,
        %(model_number)s, %(whse_location)s,
        %(carton_w_in)s, %(carton_h_in)s, %(carton_d_in)s, %(gross_weight_lb)s,
        'queued', now(), now()
    )
    ON CONFLICT (stop_id, source_inventory_id)
    WHERE source_inventory_id IS NOT NULL
    DO UPDATE SET
        source_order_item_id = EXCLUDED.source_order_item_id,
        truck_id             = EXCLUDED.truck_id,
        truck_sort_order     = EXCLUDED.truck_sort_order,
        stop_order           = EXCLUDED.stop_order,
        piece_order          = EXCLUDED.piece_order,
        whse_location        = EXCLUDED.whse_location,
        carton_w_in          = EXCLUDED.carton_w_in,
        carton_h_in          = EXCLUDED.carton_h_in,
        carton_d_in          = EXCLUDED.carton_d_in,
        gross_weight_lb      = EXCLUDED.gross_weight_lb,
        updated_at           = now()
    WHERE pick_queue.status = 'assigned'
"""

# Location IDs that contain physically pickable inventory.
# 1=WAREHO  2=OUTLET  3=BANGY  4=DAVIS  7=WILLCA  9=BEND
_PICKABLE_LOCATION_IDS = (1, 2, 3, 4, 7, 9)

# An item is only pickable when the ERP has it OPEN (present, available). Filtering here
# is what stops a MISSING item from being sent to a picker, and an already-picked
# (in_transit) item from being re-queued. Fail closed: an item of any other status, or of
# no status at all (NULL), is excluded.
_FETCH_ALLOCATED_SQL = """
    SELECT
        source_order_id, source_inventory_id, source_order_item_id,
        model_number, source_whse_location,
        carton_w_in, carton_h_in, carton_d_in, gross_weight_lb
    FROM inventory_items
    WHERE source_order_id = ANY(%(order_ids)s)
      AND is_allocated = TRUE
      AND source_location_id = ANY(%(pickable_ids)s)
      AND source_status = %(pickable_status)s
    ORDER BY source_order_id, model_number, source_inventory_id
"""

# Diagnostic: allocated, in a pickable location, but NOT pickable status. These are the
# rows the guard above drops — logged so the exclusion is visible, never silent.
_EXCLUDED_FROM_PICK_SQL = """
    SELECT source_status, count(*)
    FROM inventory_items
    WHERE source_order_id = ANY(%(order_ids)s)
      AND is_allocated = TRUE
      AND source_location_id = ANY(%(pickable_ids)s)
      AND source_status IS DISTINCT FROM %(pickable_status)s
    GROUP BY source_status
    ORDER BY source_status
"""


def fetch_allocated_by_order(
    conn: psycopg.Connection,
    order_ids: list[int],
) -> dict[int, list[InventoryItem]]:
    if not order_ids:
        return {}
    params = {
        "order_ids": order_ids,
        "pickable_ids": list(_PICKABLE_LOCATION_IDS),
        "pickable_status": PICKABLE_SOURCE_STATUS,
    }
    by_order: dict[int, list[InventoryItem]] = {}
    with conn.cursor() as cur:
        cur.execute(_EXCLUDED_FROM_PICK_SQL, params)
        excluded = cur.fetchall()
        if excluded:
            detail = ", ".join(
                f"{SOURCE_STATUS_MAP.get(s, '?')}({s})={n}" for s, n in excluded
            )
            logger.warning(
                "fetch_allocated_by_order: %d allocated item(s) excluded from picking "
                "by status — %s. (in_transit = already picked; missing/sold/transfer "
                "cannot be picked.)",
                sum(n for _, n in excluded), detail,
            )
        cur.execute(_FETCH_ALLOCATED_SQL, params)
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
    """Rebuild the queued rows for a date, preserving anything already in progress.

    Safe to run while pickers are working: 'assigned' and 'picked' rows survive, and
    the clear runs unconditionally so an empty build correctly leaves zero queued rows
    rather than silently stranding a stale queue.

    Performs no DDL — the index management this used to do on every call now lives in
    schema/migrations/0002_pick_module.sql.
    """
    with conn.cursor() as cur:
        cur.execute(_CLEAR_PQ_DATE_SQL, {"d": delivery_date})
        cleared = cur.rowcount
        row_dicts = [
            {
                "stop_id":              r.stop_id,
                "source_inventory_id":  r.source_inventory_id,
                "source_order_item_id": r.source_order_item_id,
                "delivery_date":        r.delivery_date,
                "truck_id":             r.truck_id,
                "truck_sort_order":     r.truck_sort_order,
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
    logger.info(
        "write_pick_queue: cleared %d queued row(s), wrote %d for %s "
        "(in-progress rows preserved)",
        cleared, len(rows), delivery_date,
    )
    return len(rows)
