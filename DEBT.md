# Debt Ledger

Every deferred fix gets an entry here before it ships. Format:
  DEBT-<ID>  <YYYY-MM-DD>  Rule <N>  <one-line description>
  Why deferred: ...
  Fix path: ...

---

## Open

DEBT-ERP-001  2026-07-13  Rule 13  No write path to the source ERP exists. A confirmed
  pick is recorded as status='picked' with erp_confirmed=FALSE; nothing sets 'in_transit'.
  Why deferred: the write-to-ERP side has not been started for any application in the
    estate, and the owner wants a single async, fail-loud write mechanism rather than one
    per app. Blocking picking on it would have blocked the pick screen behind an entire
    unstarted workstream.
  Scope not covered: pick_queue rows accumulate as picked + erp_confirmed=FALSE. Nothing
    reconciles them against the ERP. The ERP therefore still shows these units as open
    inventory until a human acts. This is a KNOWN, ACCEPTED gap, not an oversight.
  Fix path: An async writer drains WHERE status='picked' AND erp_confirmed=FALSE, sets
    the unit in-transit in the ERP, then sets erp_confirmed=TRUE and status='in_transit'.
    Prefer an XHR endpoint over browser automation — the source is Laravel + Kendo, the
    inventory grid is already a JSON XHR endpoint, HttpSource already holds an
    authenticated session, and get_session_cookies() exists to hand it to another client.
    The unit carries a first-class IsDeliveryInTransit boolean, distinct from
    InventoryStatus. Capture the request from DevTools before assuming Playwright.

DEBT-SYNC-002  2026-07-13  Rule 4  inventory_sync._STATUS_MAP does not cover source
  status codes 6 and 7 (199 of 14,627 records on 2026-07-13). They fall back to
  'in_warehouse' — a guess.
  Why deferred: the meaning of codes 6 and 7 is unknown, and inventing a mapping would be
    a confident wrong answer. The fallback is now reported loudly on every sync rather
    than applied silently, so the guess is at least visible.
  Fix path: Identify what 6 and 7 mean in the source system, add them to _STATUS_MAP, and
    decide whether either should be excluded from pickable inventory.

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

DEBT-GATE-001  2026-07-10  Rule 15  Conformance gate (gate.py) is not yet
  wired into CI; runs only locally.
  Resolved 2026-07-13: .github/workflows/gate.yml runs `pytest tests/` then
  `python gate.py` on every push and PR to main, with BANNED_TOKENS injected
  from repository secrets. Branch protection requires it to pass.
