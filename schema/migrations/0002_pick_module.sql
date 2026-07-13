-- 0002 — Pick module: claimable queue, ERP handoff flag, display fields.
--
-- Idempotent and forward-only. Safe to re-run.
--
-- Context:
--   * assigned_to / assigned_at / picked_at already exist (0001_init.sql) — not re-added.
--   * 'in_transit' was NOT a legal status; the CHECK constraint forbade it, so the
--     documented queued -> assigned -> in_transit progression would raise a check
--     violation. Added here. Nothing writes it yet: the ERP write path is deferred,
--     and 'picked' + erp_confirmed = FALSE is the pending-ERP-write queue.
--   * truck_sort_order persists the owned-fleet-first ordering that core.pick_order
--     computes in memory and previously threw away. Without it no ORDER BY can
--     reproduce the pick order, because which trucks are owned is configuration
--     (OWNED_FLEET_TRUCKS), not something a truck label encodes. Sorting on truck_id
--     is right only by accident of spelling, and breaks whenever a carrier label sorts
--     before an owned one, or numeric labels of unequal length are compared.

BEGIN;

-- ── pick_queue ────────────────────────────────────────────────────────────────

ALTER TABLE pick_queue ADD COLUMN IF NOT EXISTS erp_confirmed    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pick_queue ADD COLUMN IF NOT EXISTS truck_sort_order INTEGER;

-- Allow 'in_transit'. Drop-then-add because Postgres has no ALTER CONSTRAINT for CHECK.
ALTER TABLE pick_queue DROP CONSTRAINT IF EXISTS pick_queue_status_check;
ALTER TABLE pick_queue ADD  CONSTRAINT pick_queue_status_check
    CHECK (status IN ('queued','assigned','picked','in_transit',
                      'staged','on_truck','discrepancy'));

-- Serves the atomic claim: WHERE delivery_date=? AND status='queued'
--                          ORDER BY truck_sort_order, stop_order, piece_order
CREATE INDEX IF NOT EXISTS idx_pq_claim ON pick_queue
    (delivery_date, status, truck_sort_order, stop_order, piece_order);

-- Rows written before this migration have no rank. Leave them NULL: the next
-- build_pick_queue run backfills them, and NULLS LAST in the claim keeps unranked
-- rows from jumping the queue in the meantime.

-- ── inventory_items ───────────────────────────────────────────────────────────
-- Display fields for the pick screen. A photo of the appliance is worth more to a
-- picker than a serial they cannot scan.

ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS image_url         TEXT;
ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS short_description TEXT;

-- ── Retire the runtime DDL hack ───────────────────────────────────────────────
-- neon.write_pick_queue used to execute these two statements on EVERY build, as a
-- self-healing migration off an older conflict key. That is a migration, so it
-- belongs here, and the write path no longer performs DDL.

DROP INDEX IF EXISTS uq_pq_stop_order_item;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pq_stop_inventory
    ON pick_queue (stop_id, source_inventory_id)
    WHERE source_inventory_id IS NOT NULL;

COMMIT;
