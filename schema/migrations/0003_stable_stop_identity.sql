-- 0003 — Give a delivery stop a stable identity across a refresh.
--
-- Idempotent and forward-only. Safe to re-run.
--
-- The problem this solves:
--   sync_delivery_stops re-runs whenever the route changes (trucks stay fluid until
--   ~2PM the day before delivery). It used to DELETE every stop for the date and
--   re-INSERT them, which regenerated stop_id (a gen_random_uuid PK) on every run. It
--   also deleted every pick_queue row for the date first, with no status filter, to get
--   around the foreign key — so a refresh at 10am destroyed the morning's picks.
--
--   The stop rows already had a natural key and an ON CONFLICT clause to match. That
--   branch was dead code: the DELETE above it guaranteed it never fired.
--
-- Why re-key on (delivery_date, source_order_id) rather than
-- (delivery_date, truck_id, source_order_id):
--   Including truck_id in the identity means a re-routed order is not the same stop
--   any more — the old stop vanishes and a new one is born with a new stop_id, orphaning
--   any pick already claimed against it. Keying on the order alone makes a truck change
--   an UPDATE of the existing row: stop_id holds, and pick rows stay attached to work a
--   picker has already done.
--
--   Verified safe against live data: all 32 stop rows are unique on
--   (delivery_date, source_order_id) — no order is split across two trucks.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_stops_date_order
    ON delivery_stops (delivery_date, source_order_id)
    WHERE source_order_id IS NOT NULL;

-- Superseded: truck_id is no longer part of a stop's identity.
DROP INDEX IF EXISTS uq_stops_order;

COMMIT;
