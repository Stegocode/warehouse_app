-- 0007 — Per-date Monday-item uniqueness: a delivery may be rescheduled to a new date.
--
-- Idempotent and forward-only. Safe to re-run.
--
-- The original guard made sink_item_id globally unique across delivery_stops — a Monday
-- board item could exist on exactly one delivery_date, ever. But a delivery legitimately
-- moves dates (rescheduled, redelivered) while keeping its Monday item id, so the same
-- sink_item_id must be allowed on more than one date. The stop upsert already keys a stop
-- on (delivery_date, source_order_id); this aligns the sink-item guard with that per-date
-- identity: one Monday item PER DATE, not one ever. Without it, syncing a date that reuses
-- a prior date's Monday item aborts the whole write (ON CONFLICT covers only one target).
--
-- The prior date's row is intentionally left in place: a rescheduled order keeps its old,
-- now-historical stop. Redelivery handling — flagging the new pick as "redelivery from
-- {date}" and carrying an in-progress claim forward — is a separate, later change.

BEGIN;

-- Drop the global guard under either name it has carried (live: uq_stops_monday_item;
-- schema baseline 0001_init.sql: uq_stops_sink_item).
DROP INDEX IF EXISTS uq_stops_monday_item;
DROP INDEX IF EXISTS uq_stops_sink_item;

-- Re-add it scoped to the delivery date.
CREATE UNIQUE INDEX IF NOT EXISTS uq_stops_sink_item_per_date
    ON delivery_stops (delivery_date, sink_item_id)
    WHERE sink_item_id IS NOT NULL;

COMMIT;
