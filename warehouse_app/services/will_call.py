"""Service: inject a will-call order so its pieces are picked next.

# ── Boundary ──────────────────────────────────────────────────────────────────
# Owns: look up an order's pickable pieces and queue them as a will-call interrupt.
# Owns     : resolve the order to its allocated, pickable pieces; queue them will-call
# Must not : contain SQL; contain the claim/ordering logic (that is pick_db)
# May use  : warehouse_app.adapters.db.neon (fetch_allocated_by_order),
#            warehouse_app.adapters.db.will_call_db, psycopg, datetime, logging
# Out of scope: the office UI; choosing the drop point (the caller supplies it);
#               the ERP write (a will-call pick still flows through the normal
#               picked -> in_transit lifecycle)
# ─────────────────────────────────────────────────────────────────────────────

The desktop office calls this when a customer arrives: given the order number and a drop
point, its pieces jump to the front of the pick queue. A will-call piece is otherwise a
normal pick — claimed, confirmed, and (later) ERP-written exactly like any other.
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg

from warehouse_app.adapters.db import neon, will_call_db

logger = logging.getLogger(__name__)


class WillCallError(Exception):
    """A will-call order could not be queued — typed so the caller can tell the operator."""


def add_will_call_order(
    conn: psycopg.Connection,
    source_order_id: int,
    drop_point: str,
    delivery_date: date,
) -> int:
    """Queue an order's pickable pieces as a will-call interrupt. Returns pieces queued.

    Fails closed: an order with no allocated, pickable pieces raises rather than silently
    queuing nothing — the office needs to know the order is not ready to pick (unreceived,
    already picked, missing a location) rather than assume a picker is on the way.
    """
    drop = (drop_point or "").strip()
    if not drop:
        raise WillCallError("A drop point is required for a will-call order.")

    by_order = neon.fetch_allocated_by_order(conn, [source_order_id])
    items = by_order.get(source_order_id, [])
    if not items:
        raise WillCallError(
            f"Order {source_order_id} has no allocated, pickable pieces in the warehouse "
            "(they may be unreceived, already picked/in-transit, or missing a location)."
        )

    queued = will_call_db.insert_will_call_rows(conn, items, delivery_date, drop)
    if queued == 0:
        logger.info(
            "add_will_call_order: order %s — all %d piece(s) already on an open will-call",
            source_order_id, len(items),
        )
    else:
        logger.info(
            "add_will_call_order: order %s -> %d of %d piece(s) queued will-call (drop=%s)",
            source_order_id, queued, len(items), drop,
        )
    return queued
