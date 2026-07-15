# warehouse_app — Integration Contract

What a consumer (e.g. a Dolly app-module) needs to use this library without reading the
source. Two parts: the **pick-serving API** (`warehouse_app.services.pick_claim`) and the
**environment** `config.load()` requires.

All pick-serving functions take a live `psycopg` (v3) connection as their first argument.
The caller owns the connection; these functions `commit()` their own writes. `delivery_date`
is a `datetime.date`. `picker` is a stable string identity for the operator (in Dolly, use
`str(current_user.id)` — not the username, which can change).

---

## `warehouse_app.services.pick_claim`

### `claim_next(conn, delivery_date, picker) -> PickAssignment | None`
Atomically hand this picker the next pick, or `None` when the queue is empty for the date.
- **Idempotent:** if the picker already holds a pick, that same pick is returned rather
  than a second one claimed — a reloaded phone does not quietly take two items.
- **Concurrency-safe:** `FOR UPDATE SKIP LOCKED`. Several pickers claiming at once each get
  a *different* pick; no double-assignment, no blocking.
- **Ordering:** will-call interrupts first (globally, any date, FIFO), then owned-fleet
  trucks, then third-party, each by stop then piece.

### `current(conn, delivery_date, picker) -> PickAssignment | None`
The pick this picker is currently holding (status `assigned`), or `None`. Read-only; use it
to render the screen on load without claiming.

### `confirm(conn, pick_id, picker, scanned_serial=None) -> ScanResult`
Confirm a pick as physically done: sets `status='picked'`, `picked_at=now()`,
`erp_confirmed=FALSE`. It does **not** write to the ERP and does **not** set `in_transit`
— that is a separate, deferred step. A `picked` row with `erp_confirmed=FALSE` is the
pending-ERP-write queue.
- **Fails closed on the scan:** if the unit has a serial on record, a matching
  `scanned_serial` is required. Units with no serial (~3% of pick rows) are accepted
  without one, but the returned `ScanResult.outcome` says so (`no_serial`) — never a silent
  pass.
- **Raises** (see Errors): `ScanRejected` if the scan does not verify; `PickNotHeld` if the
  pick is not currently assigned to this picker (stale phone, double-tap, released).

### `release(conn, pick_id, picker) -> None`
Return a claimed pick to the queue (picker can't find it / can't lift it). **Raises**
`PickNotHeld` if this picker no longer holds it.

### `progress(conn, delivery_date) -> PickProgress`
Queue counts for the date — the shared status every picker sees. Read-only.

### Errors
- `PickError` — base class for every refusal.
- `PickNotHeld(PickError)` — the pick is not assigned to this picker.
- `ScanRejected(PickError)` — the scan did not verify; carries `.result: ScanResult`.

A refused scan and a lost claim are **normal, expected outcomes** (wrong unit in hand, a
double-tap) — catch them and show the picker `str(exc)`, which is operator-facing.

---

## Domain types (`warehouse_app.core.domain`)

### `PickAssignment` — one claimed pick, everything the screen needs
| field | meaning |
|---|---|
| `pick_id` | stable id; pass to `confirm`/`release` |
| `delivery_date` | the date this pick belongs to |
| `truck_id` | truck label (or `"WILL CALL"` for an interrupt) |
| `stop_order` | stop sequence within the truck (`None` for will-call) |
| `piece_order` | piece sequence within the stop (1-based) |
| `pieces_at_stop` | how many pieces share this stop (1 for a will-call) |
| `model_number` | appliance model |
| `whse_location` | bin to pick from (e.g. `"14-02-01"`); may be `None` |
| `status` | `queued`/`assigned`/`picked`/`in_transit`/… |
| `assigned_to` | the holding picker's identity |
| `manufacturer` | brand, for the screen |
| `short_description` | e.g. `24" Custom Panel Dishwasher` |
| `image_url` | product photo URL (blocked by Dolly's `img-src` CSP today) |
| `serial_number` | real serial when known; `None` for ~3% of rows |
| `customer_name` | delivery stop's customer |
| `is_will_call` | `True` for an interrupt pick |
| `drop_point` | where to take a will-call (replaces truck/stop) |

### `PickProgress` — queue state for a date
Fields: `delivery_date`, `queued`, `assigned`, `picked`, `in_transit`, `other`.
Properties: `.total` (sum of all), `.done` (`picked + in_transit`).

### `ScanResult` (`warehouse_app.core.pick_verify`)
Returned by `confirm`. Fields: `outcome: ScanOutcome`, `accepted: bool`, `reason: str`.
`ScanOutcome` ∈ { `match`, `mismatch`, `missing_scan`, `no_serial` }. `reason` is a
plain-language, operator-facing sentence.

---

## Environment — what `warehouse_app.config.load()` requires

`config.load()` fails fast, listing the first missing required var. The **pick screen**
(claim/confirm/current/progress) needs **none of this** — it uses only a database
connection, which the consumer supplies (Dolly's `DATABASE_URL`). These are needed only
when the consumer calls `config.load()` — i.e. to run the **Refresh / ERP-sync**.

**Always required**
| var | example / note |
|---|---|
| `DATABASE_URL` | plain `postgresql://…` DSN — NOT the SQLAlchemy `postgresql+psycopg://` form |
| `OWNED_FLEET_TRUCKS` | comma list of owned-fleet truck labels, e.g. `56,58,62,64,FLEET` (unset → refuses to start) |

**Required when `SOURCE_TYPE=portal`** (the default — set `SOURCE_TYPE=fake` for offline)
`SOURCE_USERNAME`, `SOURCE_PASSWORD`, `SOURCE_BASE_URL`

**Required when `SINK_TYPE=graphql`** (the default — set `SINK_TYPE=null` to disable)
`SINK_API_URL`, `SINK_API_TOKEN`

**Optional**
`SOURCE_TYPE`, `SINK_TYPE`, `SINK_BOARD_ID`, `SINK_DELIVERY_COL`, `DIM_FEED_URL_TEMPLATE`,
`LAYOUT_JSON_PATH`, `SOURCE_DIM_CONCURRENCY` (default `12`).

Credentials belong in Render env vars / the deployment's `.env`, never committed.
