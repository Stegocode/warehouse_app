# Owns: the scanner-pick-build's own DB reads/writes — bin-label resolution and the
#        Qty-vs-allocated shortfall flags.
# Must not: contain domain/ordering logic; call config.load().
# May import: psycopg, warehouse_app.core.domain (types), standard library.
#
# Kept out of neon.py (already at the module size cap) and separate from will_call_db.py:
# these two operations belong to the scanner build path specifically — resolving the human
# bin label the scanner unit lacks, and recording pieces that are scheduled but not yet
# allocated (so unpickable) for a management module to surface. Never silent (Rule 4).

from __future__ import annotations

import logging

import psycopg

from warehouse_app.core.domain import PickShortfall

logger = logging.getLogger(__name__)


# ── Bin-label resolution ──────────────────────────────────────────────────────

_FETCH_BIN_LABELS_SQL = """
    SELECT source_inventory_id, source_whse_location
    FROM inventory_items
    WHERE source_inventory_id = ANY(%(ids)s)
"""


def fetch_bin_labels(
    conn: psycopg.Connection, inventory_ids: list[int]
) -> dict[int, str | None]:
    """Map each inventory id to its human bin label (inventory_items.source_whse_location).

    A scanner unit carries only the numeric WHSELocationId_FK; the human label ("02-02-04")
    is synced onto inventory_items. A unit not yet synced simply returns no row and is
    absent from the map — the caller keeps the pick with a NULL bin and logs it, never
    dropping a pickable unit for want of a label (Rule 4).
    """
    if not inventory_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(_FETCH_BIN_LABELS_SQL, {"ids": list(inventory_ids)})
        return {row[0]: row[1] for row in cur.fetchall()}


# ── Shortfall flags ───────────────────────────────────────────────────────────

# The reason token a management module keys on. One kind of flag this module writes.
SHORTFALL_REASON = "unallocated_scheduled"

# Idempotent refresh: clear this build's own unresolved shortfall flags for the orders it
# touched, then re-insert the current ones. An order that recovered (units now allocated)
# gets its flag cleared; a resolved flag (someone acted on it) is left alone.
_CLEAR_SHORTFALL_FLAGS_SQL = """
    DELETE FROM flags
    WHERE reason = %(reason)s
      AND resolved_at IS NULL
      AND source_order_id = ANY(%(order_ids)s)
"""

_INSERT_SHORTFALL_FLAG_SQL = """
    INSERT INTO flags (source_order_id, stop_id, model_number, reason, detail, flagged_by)
    VALUES (%(source_order_id)s, %(stop_id)s, %(model_number)s, %(reason)s, %(detail)s, 'system')
"""


def write_shortfall_flags(
    conn: psycopg.Connection,
    shortfalls: list[PickShortfall],
    build_order_ids: list[int],
    stop_id_by_order: dict[int, str],
) -> int:
    """Refresh the shortfall flags for the orders in this build. Returns flags written.

    ``build_order_ids`` is every order the build touched (so a recovered order's stale flag
    is cleared even though it has no current shortfall). Idempotent: safe to run on every
    rebuild without piling up duplicates.
    """
    with conn.cursor() as cur:
        if build_order_ids:
            cur.execute(
                _CLEAR_SHORTFALL_FLAGS_SQL,
                {"reason": SHORTFALL_REASON, "order_ids": list(build_order_ids)},
            )
        for s in shortfalls:
            detail = (
                f"{s.model_number}: scheduled {s.scheduled_qty}, allocated "
                f"{s.allocated_count}, {s.missing} piece(s) scheduled for delivery but not "
                f"allocated — not pickable (unreceived/unallocated)."
            )
            cur.execute(_INSERT_SHORTFALL_FLAG_SQL, {
                "source_order_id": s.source_order_id,
                "stop_id":         stop_id_by_order.get(s.source_order_id),
                "model_number":    s.model_number,
                "reason":          SHORTFALL_REASON,
                "detail":          detail,
            })
    conn.commit()
    logger.info(
        "write_shortfall_flags: %d shortfall flag(s) written across %d touched order(s)",
        len(shortfalls), len(build_order_ids),
    )
    return len(shortfalls)
