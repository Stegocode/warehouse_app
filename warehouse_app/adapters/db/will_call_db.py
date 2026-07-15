# Owns: inserting will-call interrupt rows into pick_queue.
# Must not: contain domain/ordering logic; call config.load().
# May import: psycopg, warehouse_app.core.domain (types), standard library.
#
# Kept separate from neon.py (the delivery-build write path) and pick_db.py (the serving
# path): will-call is its own small write concern, and neon.py is already at the size cap.

from __future__ import annotations

import logging
from datetime import date

import psycopg

from warehouse_app.core.domain import InventoryItem

logger = logging.getLogger(__name__)

# A will-call row has no delivery stop (stop_id NULL) and no truck ordering. It carries a
# drop_point, is flagged is_will_call, and takes a monotonic will_call_seq so multiple
# will-calls are picked FIFO. truck_id is a fixed label since the column is NOT NULL and a
# will-call belongs to no real truck. ON CONFLICT DO NOTHING against the will-call unique
# guard makes re-adding the same order idempotent (a unit is not double-queued).
_INSERT_WILL_CALL_SQL = """
    INSERT INTO pick_queue (
        stop_id, source_inventory_id, source_order_item_id,
        delivery_date, truck_id, truck_sort_order, stop_order, piece_order,
        model_number, whse_location,
        status, is_will_call, will_call_seq, drop_point,
        created_at, updated_at
    ) VALUES (
        NULL, %(source_inventory_id)s, %(source_order_item_id)s,
        %(delivery_date)s, %(truck_label)s, NULL, 0, %(piece_order)s,
        %(model_number)s, %(whse_location)s,
        'queued', TRUE, nextval('pick_queue_will_call_seq'), %(drop_point)s,
        now(), now()
    )
    ON CONFLICT (source_inventory_id) WHERE is_will_call AND source_inventory_id IS NOT NULL
    DO NOTHING
"""

_WILL_CALL_TRUCK_LABEL = "WILL CALL"


def insert_will_call_rows(
    conn: psycopg.Connection,
    items: list[InventoryItem],
    delivery_date: date,
    drop_point: str,
) -> int:
    """Insert one will-call pick row per item. Returns the number newly queued.

    Idempotent: a unit already on an open will-call is skipped (the unique guard), so
    re-adding an order does not duplicate its pieces. Pieces are numbered in a stable order
    so the picker sees a consistent sequence.
    """
    ordered = sorted(items, key=lambda it: (it.model_number, it.source_inventory_id or 0))
    inserted = 0
    with conn.cursor() as cur:
        for piece_order, item in enumerate(ordered, start=1):
            cur.execute(_INSERT_WILL_CALL_SQL, {
                "source_inventory_id":  item.source_inventory_id,
                "source_order_item_id": item.source_order_item_id,
                "delivery_date":        delivery_date,
                "truck_label":          _WILL_CALL_TRUCK_LABEL,
                "piece_order":          piece_order,
                "model_number":         item.model_number,
                "whse_location":        item.source_whse_location,
                "drop_point":           drop_point,
            })
            inserted += cur.rowcount
    conn.commit()
    logger.info(
        "insert_will_call_rows: %d of %d piece(s) queued as will-call for %s (drop=%s)",
        inserted, len(ordered), delivery_date, drop_point,
    )
    return inserted
