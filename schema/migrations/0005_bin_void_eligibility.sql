-- 0005 — Mark which bins can receive new inbound product (void-finder targets).
--
-- Idempotent and forward-only. Safe to re-run.
--
-- The void finder ranks OPEN, ELIGIBLE bins by travel distance to the next pick. Bins
-- are ineligible when the rack is being decommissioned (row 13, rotating to row T) or is
-- bulk/floor storage (BULK), which is not a slotted void target. Default TRUE so a newly
-- mapped bin is eligible unless explicitly excluded.
--
-- Per the warehouse layout as of 2026-07: row_token='13' and rack_type='BULK' are the
-- exclusions (~21 bins ineligible, ~699 eligible). The two UPDATEs are data-seeding, not
-- schema, and are safe to re-run.

BEGIN;

ALTER TABLE warehouse_bins ADD COLUMN IF NOT EXISTS eligible_for_void BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE warehouse_bins SET eligible_for_void = FALSE WHERE row_token = '13';
UPDATE warehouse_bins SET eligible_for_void = FALSE WHERE rack_type = 'BULK';

CREATE INDEX IF NOT EXISTS idx_bins_void ON warehouse_bins (eligible_for_void)
    WHERE eligible_for_void;

COMMIT;
