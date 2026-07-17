-- 0008 — Scanner-built stops carry no sink (Monday board) fields.
--
-- Idempotent and forward-only. Safe to re-run: DROP NOT NULL on an already-nullable
-- column is a no-op.
--
-- The pick queue now builds from the scanner API, and a scanner stop has no Monday board
-- item — so it is written with sink_item_id / sink_board_id / sink_status all NULL. The
-- live delivery_stops.sink_board_id still carried a NOT NULL from the pre-runner
-- model_catalog_module schema (never captured in a repo migration: 0001_init.sql shows it
-- nullable, but the live column had diverged). Relax the sink columns to nullable so a
-- sink-less stop can persist. This is the first step of retiring the sink entirely
-- (stage 3); the columns stay for now (forward-only — no drop) but accept NULL.

BEGIN;

ALTER TABLE delivery_stops ALTER COLUMN sink_board_id DROP NOT NULL;
ALTER TABLE delivery_stops ALTER COLUMN sink_status   DROP NOT NULL;
ALTER TABLE delivery_stops ALTER COLUMN sink_item_id  DROP NOT NULL;

COMMIT;
