# warehouse_app

The warehouse-domain library behind the WMS: pure pick, routing, sync, and ERP-write logic,
shared by the UI app-modules (picking, and later receiving and will-call) and the CLI sync
scripts. It owns the warehouse tables in the shared Neon database and exposes a service API
over them, so that logic lives in exactly one place.

## Architecture

Hexagonal — dependencies point **inward only**:

```
core/       pure domain logic — pick order, void finder, scan verification,
            classification, dimensions, delivery calendar, domain types. No I/O.
services/   orchestration — pick_claim, inventory_sync, scanner_pick_builder,
            putaway, will_call, model/dim sync.
adapters/   the edges — db (Neon via psycopg), source (the upstream ERP:
            HTTP reads + the scanner read/write API).
```

The core never imports outward; every consumer passes I/O in. Direction is enforced
mechanically by `gate.py`, not by convention.

## What it does

- **Pick serving** — atomic `claim → confirm → release`, `progress`, with `FOR UPDATE SKIP
  LOCKED` so several pickers share one list without collisions, and will-call interrupts
  that jump the queue. (`services/pick_claim`)
- **Pick queue build** — the ERP scanner API's day manifest → `delivery_stops` → `pick_queue`,
  tiered morning-first then owned-fleet-first, in an order that survives the round-trip to
  SQL. Scheduled-but-unallocated pieces are flagged, not dropped. (`services/scanner_pick_builder`)
- **Inventory & catalog sync** — the upstream ERP's serialized inventory and model
  dimensions into Neon.
- **Routing** — a putaway void finder: the open bin closest to the next pick by real travel
  distance over the warehouse path graph. (`core/void_finder`)
- **ERP write** — a stateless, per-operator scanner-write adapter (mark-in-transit,
  receive). The capability exists; wiring it into a flow is deferred (see `DEBT.md`).

## Using it as a library

```python
from warehouse_app.services import pick_claim

assignment = pick_claim.claim_next(conn, delivery_date, picker)   # see CONTRACT.md
```

Consumers pass a live `psycopg` (v3) connection; the library commits its own writes. The
pick-serving path needs only a database connection — no other config.

## Command-line sync

```
python -m warehouse_app.scripts.sync_inventory        --env-file <path>
python -m warehouse_app.scripts.build_scanner_queue   --env-file <path> [--date YYYY-MM-DD]
python -m warehouse_app.scripts.migrate               --env-file <path>
```

`build_scanner_queue` refreshes inventory then builds the pick queue from the scanner API in
one pass (stops + queue + shortfall flags). With no `--date` it targets the next delivery day,
skipping weekends. `--dry-run` reports counts without writing.

## Configuration

All configuration is environment variables, read in exactly one place (`config.py`) and
validated fail-fast at load. Nothing machine-specific or secret lives in code. The full list
— and which vars are required for the pick screen vs. the sync/Refresh path — is in
**[CONTRACT.md](CONTRACT.md)**.

## Database & migrations

warehouse_app shares one Neon database with the Dolly platform and owns the warehouse-domain
tables. It migrates them with forward-only, idempotent SQL in `schema/migrations/`, applied
by `scripts.migrate` and tracked in the `schema_version` ledger (separate from Dolly's
Alembic, which is fenced off from these tables). The full table inventory and ownership map
is in **[SCHEMA.md](SCHEMA.md)**.

## Conformance gate

```
python gate.py        # must pass 5/5 before any commit
```

Checks: no client/vendor names in `.py`, `os.getenv` confined to `config.py`, `core/` imports
inward only, `DEBT.md` non-empty, and a boundary header on every module.

## Documentation

- **[CONTRACT.md](CONTRACT.md)** — the pick-serving API and env-var reference for consumers
- **[SCHEMA.md](SCHEMA.md)** — shared-Neon table inventory, attributed by owner
- **[DEBT.md](DEBT.md)** — deferred-decisions ledger
- **docs/ADR-0001-hexagonal-architecture.md** — the architecture decision record

## Tests

```
python -m pytest tests/                       # pure suite — runs anywhere, incl. CI
python -m pytest tests/ --env-file <path>     # + integration tests (need a real Postgres)
```
