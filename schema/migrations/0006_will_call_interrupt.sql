-- 0006 — Will-call interrupt: pieces a desktop user injects to be picked next.
--
-- Idempotent and forward-only. Safe to re-run.
--
-- A will-call is a customer standing at the counter: the office adds their order and its
-- pieces must be picked ahead of everything, then dropped at a named point rather than
-- loaded on a truck. Modelled as pick_queue rows flagged is_will_call, which the claim
-- query serves first (globally, regardless of the date a picker is working), FIFO by
-- will_call_seq so the first customer waiting is picked first.
--
-- A will-call piece has no delivery stop, so stop_id becomes nullable and the row carries
-- a drop_point instead. Normal delivery rows are unchanged: is_will_call defaults FALSE,
-- will_call_seq/drop_point NULL, stop_id still populated.

BEGIN;

ALTER TABLE pick_queue ADD COLUMN IF NOT EXISTS is_will_call  BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pick_queue ADD COLUMN IF NOT EXISTS will_call_seq BIGINT;
ALTER TABLE pick_queue ADD COLUMN IF NOT EXISTS drop_point    TEXT;

-- A will-call row belongs to no delivery_stop.
ALTER TABLE pick_queue ALTER COLUMN stop_id DROP NOT NULL;

-- Monotonic FIFO ordering among will-calls (first entered, first picked).
CREATE SEQUENCE IF NOT EXISTS pick_queue_will_call_seq;

-- A physical unit may sit on at most one open will-call. The existing
-- uq_pq_stop_inventory is keyed on (stop_id, source_inventory_id) and does not guard NULL
-- stop_id rows (NULLs never conflict), so will-call needs its own guard.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pq_willcall_inventory
    ON pick_queue (source_inventory_id)
    WHERE is_will_call AND source_inventory_id IS NOT NULL;

-- Serves the claim's new ordering: will-call first, then FIFO, then the normal tiers.
CREATE INDEX IF NOT EXISTS idx_pq_claim_willcall ON pick_queue
    (status, is_will_call, will_call_seq, delivery_date, truck_sort_order, stop_order, piece_order);

COMMIT;
