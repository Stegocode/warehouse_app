# Debt Ledger

Every deferred fix gets an entry here before it ships. Format:
  DEBT-<ID>  <YYYY-MM-DD>  Rule <N>  <one-line description>
  Why deferred: ...
  Fix path: ...

---

## Open

DEBT-ERP-001  2026-07-13  Rule 13  ERP write ADAPTER now exists (2026-07-14); the async
  drainer that USES it is still deferred. A confirmed pick is recorded as status='picked'
  with erp_confirmed=FALSE; nothing yet flips it to 'in_transit'.
  Built 2026-07-14: adapters/source/scanner_write.py — HttpScannerWriter / FakeScannerWriter
    behind ScannerWritePort, credential-injected (per-operator), with mark_in_transit and
    receive_serial. Request builders unit-tested against the captured shapes. This is the
    low-level write capability; it is not wired into any flow yet.
  Why the DRAINER is deferred: the owner wants a single async, fail-loud write mechanism for
    the whole estate rather than one per app, and is not ready to build it. Blocking picking
    on it would have blocked the pick screen behind an unstarted workstream.
  Scope not covered: pick_queue rows accumulate as picked + erp_confirmed=FALSE. Nothing
    reconciles them against the ERP yet. KNOWN, ACCEPTED gap, not an oversight.
  Domain note (2026-07-14): the operation uses the ERP's InventoryStatus=3 ("in transit"),
    which this business has repurposed to mean "picked on purpose" — the native pick list
    recorded no what/who/when, so in-transit is their pick signal. So the write fires at
    PICK time, on confirm; the decoupled queue (picked + erp_confirmed=FALSE) is exactly
    its input.

  WRITE RECIPE (discovered + verified read-only 2026-07-14 — the whole spike answer is:
    plain HTTP, no browser, no worker, no queue infra). The mobile scanner app "PocketScan"
    performs it:

      POST {SOURCE_BASE_URL}/api/scanner/inventory/intransit/serial?version=<appver>
      Authorization: Basic base64(<erp_username>:<erp_password>)
      Content-Type: application/json
      body: { "scanned_at": "YYYY-MM-DD HH:MM:SS", "InventoryId": <int>, "OrderItemId": <int> }

    - InventoryId  = pick_queue.source_inventory_id
    - OrderItemId  = pick_queue.source_order_item_id   (both already on every pick row)
    - CREDENTIALS ARE PER-PICKER, not the service account (requirement 2026-07-14). The
      ERP stamps each movement with the authenticating user, and the whole point of this
      integration is accurate operator logging — who picked what. So the Basic-auth creds
      must be the CONFIRMING picker's own ERP login, retrieved from Dolly's per-user
      credential vault (keyed on pick_queue.assigned_to = the Dolly user id), NEVER
      warehouse_app's SOURCE_* service account. This means the warehouse_app write must
      accept injected credentials rather than read config — keep it a stateless adapter
      function: mark_in_transit(base_url, username, password, inventory_id, order_item_id,
      scanned_at).
    - Route confirmed: GET -> 405 Allow: POST, DELETE. DELETE on the same route REVERSES
      in-transit (a clean undo for a cancelled/mis-picked item).
    - Effect: creates an inventory_movement of transaction type 9 ("intransit"),
      flips the unit to InventoryStatus=3 and IsDeliveryInTransit=true, note
      "Scanned out for delivery for Order #<n>".
    - Verified end-to-end on InventoryId 133854/133943: read the before state, the phone
      wrote it, read the after state — status 1 -> 3, new type-9 movement. Not written to
      from here yet; this repo has only READ the effect.
    - NOT /api/v2 (that is a different JWT-guarded API). The scanner API is /api/scanner/*
      with HTTP Basic auth.

  RECEIVE RECIPE (captured + verified live 2026-07-14 on InventoryId 137877 — this one PUT
    replaces receiving_app's entire 8-step Playwright wizard):

      PUT {SOURCE_BASE_URL}/api/scanner/inventory/receiveserial/<InventoryId>?version=<appver>
      Authorization: Basic base64(<erp_username>:<erp_password>)
      body: { "serial": <scanned serial>, "serial_status": null,
              "location": <LocationId>, "WHSELocation": <WHSELocationId> }

    - Flips an on-order unit to InventoryStatus=1 (received), stamps ReceivedDate, and
      assigns WHSELocation (the bin — this is the seam a future putaway/distance engine
      fills: the API already takes the bin as a parameter). Movement note "via the Scanner".
    - InventoryId is in the PATH; the scanned serial is in the body. Open Q: the verified
      capture had a PLACEHOLDER serial (= InventoryId), so body `serial` vs the path id is
      not fully disambiguated for an item with a REAL distinct serial. One capture of
      receiving a real-serial item settles it.
    - Same per-operator Basic auth as in-transit.

  Still unmapped (one phone capture each, same method): transfer-location, photo upload.

  Fix path (remaining): wire the adapter into an async drainer. It processes
    WHERE status='picked' AND erp_confirmed=FALSE, looks up the picker's ERP creds from
    Dolly's vault (keyed on assigned_to), calls mark_in_transit, and on success sets
    erp_confirmed=TRUE, status='in_transit'. Fail loud: leave the row picked +
    erp_confirmed=FALSE on any error so it retries; never mark 'in_transit' unless the ERP
    call returned success. Receiving uses receive_serial the same way.

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

DEBT-SYNC-002  2026-07-13  Rule 4  inventory_sync._STATUS_MAP did not cover all source
  status codes; unmapped ones fell back to 'in_warehouse' on a guess.
  Resolved 2026-07-14: read the ERP's full status list from its own UI (0-9) and put the
  correct map in core.domain.SOURCE_STATUS_MAP. Codes 2 and 3 were also actively wrong
  (2=SOLD stored as in_transit; 3=IN-TRANSIT stored as missing). Migration 0004 widened
  the CHECK to match. A pickability guard (source_status=OPEN only) now keeps
  missing/sold/in-transit items off pick lists, logging every exclusion. Any code outside
  0-9 is still reported loudly by _report_unmapped_statuses.
