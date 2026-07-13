# Owns: SQL for claiming, confirming, releasing, and reporting on pick_queue rows.
# Must not: contain domain logic; call config.load() directly.
# May import: warehouse_app.core.domain (types), psycopg, standard library.
#
# Split out of neon.py, which owns the sync/build path. This module owns the *serving*
# path — the queries a picker's phone hits. Same table, different lifecycle: neon.py
# writes the queue, pick_db.py hands it out.
#
# Concurrency contract: several pickers share one list. The claim is a SINGLE statement.
# FOR UPDATE SKIP LOCKED makes Postgres hand each concurrent claimer a *different* row
# rather than blocking them behind one lock. There is no application-level locking here,
# and there must never be: any read-then-write round trip in Python reopens the race.
#
# ORDER BY uses truck_sort_order, never truck_id. Which trucks are owned is configuration
# (OWNED_FLEET_TRUCKS), not something a truck's label encodes, so no ORDER BY over truck_id
# can express owned-fleet-first: it would only ever be right by accident of spelling.
# NULLS LAST keeps any row written before migration 0002 (no rank) from jumping the queue.

from __future__ import annotations

import logging
from datetime import date

import psycopg

from warehouse_app.core.domain import PickAssignment, PickProgress

logger = logging.getLogger(__name__)

# pick_queue is the work list, not the product catalogue: manufacturer, serial, and photo
# live on inventory_items, and the customer on delivery_stops. The picker needs all three,
# so every assignment read joins them.
_ASSIGNMENT_COLUMNS = """
    q.pick_id, q.delivery_date, q.truck_id, q.stop_order, q.piece_order,
    q.model_number, q.whse_location, q.status, q.assigned_to,
    i.manufacturer, i.short_description, i.image_url, i.serial_number,
    ds.customer_name,
    (SELECT count(*) FROM pick_queue p2 WHERE p2.stop_id = q.stop_id) AS pieces_at_stop
"""

_ASSIGNMENT_JOINS = """
    LEFT JOIN inventory_items i ON i.source_inventory_id = q.source_inventory_id
    LEFT JOIN delivery_stops ds ON ds.stop_id = q.stop_id
"""

_CLAIM_NEXT_SQL = f"""
    WITH claimed AS (
        UPDATE pick_queue
        SET status      = 'assigned',
            assigned_to = %(picker)s,
            assigned_at = now(),
            updated_at  = now()
        WHERE pick_id = (
            SELECT pick_id FROM pick_queue
            WHERE delivery_date = %(d)s
              AND status = 'queued'
            ORDER BY truck_sort_order NULLS LAST, stop_order, piece_order
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
    )
    SELECT {_ASSIGNMENT_COLUMNS}
    FROM claimed q
    {_ASSIGNMENT_JOINS}
"""

_FETCH_ASSIGNMENT_SQL = f"""
    SELECT {_ASSIGNMENT_COLUMNS}
    FROM pick_queue q
    {_ASSIGNMENT_JOINS}
    WHERE q.pick_id = %(pick_id)s
"""

_OPEN_ASSIGNMENT_SQL = f"""
    SELECT {_ASSIGNMENT_COLUMNS}
    FROM pick_queue q
    {_ASSIGNMENT_JOINS}
    WHERE q.delivery_date = %(d)s
      AND q.status        = 'assigned'
      AND q.assigned_to   = %(picker)s
    ORDER BY q.assigned_at
    LIMIT 1
"""

# Guarded on (status, assigned_to): a picker may only act on a pick they still hold.
# A stale phone, a double-tap, or a pick released elsewhere all fail this WHERE and
# update zero rows — which the caller surfaces rather than swallowing.
_CONFIRM_PICK_SQL = """
    UPDATE pick_queue
    SET status        = 'picked',
        picked_at     = now(),
        erp_confirmed = FALSE,
        updated_at    = now()
    WHERE pick_id     = %(pick_id)s
      AND status      = 'assigned'
      AND assigned_to = %(picker)s
    RETURNING pick_id
"""

_RELEASE_PICK_SQL = """
    UPDATE pick_queue
    SET status      = 'queued',
        assigned_to = NULL,
        assigned_at = NULL,
        updated_at  = now()
    WHERE pick_id     = %(pick_id)s
      AND status      = 'assigned'
      AND assigned_to = %(picker)s
    RETURNING pick_id
"""

_PROGRESS_SQL = """
    SELECT status, count(*)
    FROM pick_queue
    WHERE delivery_date = %(d)s
    GROUP BY status
"""


def _row_to_assignment(cols: list[str], row: tuple) -> PickAssignment:
    return PickAssignment(**dict(zip(cols, row)))


def claim_next_pick(
    conn: psycopg.Connection,
    delivery_date: date,
    picker: str,
) -> PickAssignment | None:
    """Atomically claim the next pick for a date. None when the queue is empty."""
    with conn.cursor() as cur:
        cur.execute(_CLAIM_NEXT_SQL, {"d": delivery_date, "picker": picker})
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
    conn.commit()
    if row is None:
        logger.info("claim_next_pick: queue empty for %s (picker=%s)", delivery_date, picker)
        return None
    assignment = _row_to_assignment(cols, row)
    logger.info(
        "claim_next_pick: picker=%s claimed pick_id=%s truck=%s stop=%s piece=%s model=%s",
        picker, assignment.pick_id, assignment.truck_id,
        assignment.stop_order, assignment.piece_order, assignment.model_number,
    )
    return assignment


def fetch_assignment(conn: psycopg.Connection, pick_id: str) -> PickAssignment | None:
    with conn.cursor() as cur:
        cur.execute(_FETCH_ASSIGNMENT_SQL, {"pick_id": pick_id})
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
    return None if row is None else _row_to_assignment(cols, row)


def fetch_open_assignment(
    conn: psycopg.Connection,
    delivery_date: date,
    picker: str,
) -> PickAssignment | None:
    """The pick this picker already holds, if any.

    A phone that reloads mid-pick must show the same item, not silently claim a second
    one — otherwise a dropped connection quietly doubles the picker's workload.
    """
    with conn.cursor() as cur:
        cur.execute(_OPEN_ASSIGNMENT_SQL, {"d": delivery_date, "picker": picker})
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
    return None if row is None else _row_to_assignment(cols, row)


def confirm_pick(conn: psycopg.Connection, pick_id: str, picker: str) -> bool:
    """Mark a pick physically done. False if this picker no longer holds it.

    Sets erp_confirmed = FALSE explicitly: the row now sits in the pending-ERP-write
    queue. status becomes 'in_transit' only once that write lands.
    """
    with conn.cursor() as cur:
        cur.execute(_CONFIRM_PICK_SQL, {"pick_id": pick_id, "picker": picker})
        ok = cur.fetchone() is not None
    conn.commit()
    logger.info("confirm_pick: pick_id=%s picker=%s ok=%s", pick_id, picker, ok)
    return ok


def release_pick(conn: psycopg.Connection, pick_id: str, picker: str) -> bool:
    """Return a claimed pick to the queue. False if this picker no longer holds it."""
    with conn.cursor() as cur:
        cur.execute(_RELEASE_PICK_SQL, {"pick_id": pick_id, "picker": picker})
        ok = cur.fetchone() is not None
    conn.commit()
    logger.info("release_pick: pick_id=%s picker=%s ok=%s", pick_id, picker, ok)
    return ok


def pick_progress(conn: psycopg.Connection, delivery_date: date) -> PickProgress:
    """Queue state for a date — the shared progress every picker sees."""
    with conn.cursor() as cur:
        cur.execute(_PROGRESS_SQL, {"d": delivery_date})
        counts = dict(cur.fetchall())
    known = ("queued", "assigned", "picked", "in_transit")
    return PickProgress(
        delivery_date=delivery_date,
        queued=counts.get("queued", 0),
        assigned=counts.get("assigned", 0),
        picked=counts.get("picked", 0),
        in_transit=counts.get("in_transit", 0),
        other=sum(n for status, n in counts.items() if status not in known),
    )
