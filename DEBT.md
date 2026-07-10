# Debt Ledger

Every deferred fix gets an entry here before it ships. Format:
  DEBT-<ID>  <YYYY-MM-DD>  Rule <N>  <one-line description>
  Why deferred: ...
  Fix path: ...

---

## Open

DEBT-ARCH-001  2026-07-10  Rule 7  Scripts in model_catalog_module still load
  .env from a hardcoded absolute Windows path.
  Why deferred: migration to warehouse_app in progress; paths will move to
    config injection once scripts are ported (Step 3).
  Fix path: Port each script to call config.load(env_file) with an injected
    path; remove the hardcoded load_dotenv() calls.

DEBT-ARCH-002  2026-07-10  Rule 1  model_catalog_module scripts import from
  `model_catalog.config` — a module outside the warehouse_app boundary.
  Why deferred: model_catalog_module is being retired once warehouse_app is
    confirmed working (Step 4).
  Fix path: Retire model_catalog_module; all imports switch to
    warehouse_app.config.

DEBT-GATE-001  2026-07-10  Rule 15  Conformance gate (gate.py) is not yet
  wired into CI; runs only locally.
  Why deferred: no CI pipeline exists yet for warehouse_app.
  Fix path: Add GitHub Actions or equivalent CI; gate.py is the check command.

DEBT-SYNC-001  2026-07-10  Rule 2  model_sync.run() does not reclassify
  floor-row models after batch_update_primary_rows().
  Why deferred: reclassification requires category + type data from the DB that
    is not present in the stats list returned by fetch_model_location_stats().
    Pulling it in-Python adds a second DB round-trip and duplicates classify
    logic already expressible in SQL.
  Fix path: Add catalog.reclassify_floor_row_models(conn) that executes a
    single SQL UPDATE joining model_size_catalog on primary_row IN ('C','13')
    and product_class = 'SLIDEIN_RANGE', setting product_class = 'LARGE_RANGE'
    and floor_only = TRUE.  Call it in model_sync.run() after
    batch_update_primary_rows().

---

## Closed

<!-- Move resolved entries here with resolution note and date. -->
