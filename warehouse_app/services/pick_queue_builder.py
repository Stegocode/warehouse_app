"""Service: orchestrate fetch-stops → fetch-inventory → build-pick-order → write-queue.

# ── Boundary ──────────────────────────────────────────────────────────────────
# Owns: coordinate DB reads and the pick-order core function into a single
#       transactional write of the pick queue for a delivery date.
# Owns     : coordinate DB reads and the pick-order core function into a single
#            transactional write of the pick queue for a delivery date
# Must not : contain SQL; contain domain ordering logic
# May use  : warehouse_app.core.pick_order (build_pick_order)
#            warehouse_app.adapters.db.neon
#            psycopg, datetime, logging
# Out of scope: authentication, config reads, truck-alias resolution
# ─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg

from warehouse_app.adapters.db import neon
from warehouse_app.core.pick_order import build_pick_order

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────


def run(
    conn: psycopg.Connection,
    delivery_date: date,
    owned_trucks: frozenset[str],  # from cfg.owned_fleet_trucks, injected by caller
    dry_run: bool = False,
) -> int:
    """Orchestrate fetch-stops → fetch-inventory → build-pick-order → write-queue.

    Returns the count of pick rows written (0 when dry_run=True).
    """
    date_iso = delivery_date.isoformat()
    logger.info("[pick_queue_builder] Building pick queue for %s", date_iso)

    # Step 1: load delivery stops for the date.
    stops = neon.fetch_stops(conn, delivery_date)
    logger.info("[pick_queue_builder] Fetched %d delivery stops", len(stops))

    if not stops:
        logger.warning("[pick_queue_builder] No stops found for %s — nothing to queue", date_iso)
        return 0

    # Step 2: collect order IDs that have a routable source order.
    order_ids = [s.source_order_id for s in stops if s.source_order_id is not None]
    logger.info("[pick_queue_builder] %d stops carry a source_order_id", len(order_ids))

    # Step 3: fetch allocated inventory keyed by order.
    inventory_by_order = neon.fetch_allocated_by_order(conn, order_ids)
    covered = sum(1 for oid in order_ids if oid in inventory_by_order)
    logger.info(
        "[pick_queue_builder] Inventory fetched: %d of %d orders have allocations",
        covered, len(order_ids),
    )

    # Step 4: delegate ordering logic entirely to the core function.
    rows = build_pick_order(delivery_date, stops, inventory_by_order, owned_trucks)
    logger.info("[pick_queue_builder] Core produced %d pick rows", len(rows))

    if dry_run:
        for r in rows:
            logger.info("  [dry] %s", r)
        return 0

    # Step 5: persist the pick queue, replacing any prior queue for this date.
    neon.write_pick_queue(conn, rows, delivery_date)
    logger.info("[pick_queue_builder] Wrote %d pick rows for %s", len(rows), date_iso)
    return len(rows)
