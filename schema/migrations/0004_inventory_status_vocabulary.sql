-- 0004 — Expand the inventory_items.status vocabulary to the ERP's real states.
--
-- Idempotent and forward-only. Safe to re-run.
--
-- The old CHECK allowed only ('on_order','in_warehouse','in_transit','missing') — a
-- vocabulary that did not match the ERP's actual InventoryStatus values. The sync's map
-- had been quietly wrong (2 stored as in_transit though it means SOLD; 3 stored as
-- missing though it means IN-TRANSIT; 7/MISSING unmapped and filed as in_warehouse),
-- and the CHECK enforced that wrongness — writing the correct label 'sold' or
-- 'order_returned' would have failed the constraint.
--
-- The ERP's own numbering (read from its UI 2026-07-14):
--   0 TO BE RECEIVED  1 OPEN  2 SOLD  3 IN-TRANSIT  4 VENDOR RETURN PENDING
--   5 VENDOR RETURNED 6 ORDER RETURNED  7 MISSING  8 TRANSFER  9 CONTAINER
--
-- Note: 3 IN-TRANSIT is what this business uses to mean "picked".

BEGIN;

ALTER TABLE inventory_items DROP CONSTRAINT IF EXISTS inventory_items_status_check;
ALTER TABLE inventory_items ADD  CONSTRAINT inventory_items_status_check
    CHECK (status IN (
        'on_order', 'in_warehouse', 'sold', 'in_transit', 'vendor_return_pending',
        'vendor_returned', 'order_returned', 'missing', 'transfer', 'container'
    ));

COMMIT;
