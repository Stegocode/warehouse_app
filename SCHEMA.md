# Shared Neon — Table Inventory

warehouse_app and the Dolly platform share one Neon PostgreSQL database. This is the full
list of tables in it: names, purpose, and **who owns each**. Ownership is the important
column — it says which repo's migrations may alter a table.

- **warehouse_app** owns the warehouse-domain tables. Its migrations live in
  `schema/migrations/` and are applied by `python -m warehouse_app.scripts.migrate`.
- **Dolly** owns the platform tables. Its Alembic migrations are deliberately fenced off
  from warehouse_app's tables (`migrations/env.py` `include_object`), so the two migration
  systems never touch each other's tables.

Row counts are a snapshot (2026-07-15) for orientation, not a contract.

---

## warehouse_app — warehouse domain (owned here)

| Table | Rows | Purpose |
|---|---|---|
| `pick_queue` | 96 | The pick work list — one row per piece to pick for a delivery date. Lifecycle `queued → assigned → picked → in_transit`; carries the persisted pick order (`truck_sort_order`) and will-call interrupt rows (`is_will_call`, `drop_point`). |
| `delivery_stops` | 39 | Delivery route stops for a date — truck, stop order, customer. Built from the scanner API (`build_scanner_queue`); keyed on `(delivery_date, source_order_id)` so a re-route keeps the same `stop_id`. The `sink_*` columns are vestigial (the Monday sink is retired) and now nullable. |
| `inventory_items` | 14,658 | Serialized ERP inventory, synced from the source portal — model, serial, bin location, `source_status`, allocation. The occupancy and allocation source of truth. |
| `model_size_catalog` | 16,376 | Model → dimensions, size tier, manufacturer, carton dims. Joined to pick rows for the pick screen and to candidate bins for putaway fit checks. |
| `source_locations` | 7 | ERP location IDs → short names (WAREHO, OUTLET, …) and which are pickable. |
| `location_aliases` | 0 | Legacy ERP location strings → a canonical `warehouse_bins` location. |
| `warehouse_bins` | 720 | Physical bin slots — location code, row/bay/level, slot height, rack type, x/y, `eligible_for_void`. From the warehouse layout; the putaway candidate set. |
| `graph_nodes` | 43 | Warehouse path-graph nodes (junctions/doors/docks) with x/y — the routing graph. |
| `graph_edges` | 72 | Path-graph edges with travel distance (and a ramp flag) — the routing weights. |
| `assignment_tickets` | 0 | WMS-era design: one active ticket per employee. Currently unused. |
| `flags` | 0 | WMS-era design: pieces that couldn't be located, for management resolution. Currently unused. |
| `schema_version` | 5 | warehouse_app's forward-only migration ledger (which `schema/migrations/` files are applied). Separate from Dolly's `alembic_version`. |

---

## Dolly — platform (owned by Dolly)

Listed for the full shared-DB picture; warehouse_app never writes these.

| Table | Rows | Purpose |
|---|---|---|
| `user` | 1 | Platform user accounts — login, role tier, job profile. |
| `app` | 2 | App registry — one row per registered app-module (`picking`, `sample_dolly_app_module`). |
| `app_permission` | 1 | Legacy permission model, superseded by job profiles. |
| `integration` | 1 | External systems the credential vault supports. |
| `app_integration` | 0 | Which apps require which integrations. |
| `user_credential` | 0 | Per-user encrypted credentials (Fernet vault). |
| `job_profile` | 2 | Role/access profiles (`Administrator`, `Picker`). |
| `job_profile_app` | 3 | Which apps each job profile grants access to. |
| `user_app_override` | 0 | Per-user app-access grants/revokes on top of the profile. |
| `invitation` | 7 | Single-use invite links for onboarding. |
| `audit_log` | 27 | Platform audit trail — auth, invites, access and credential changes. |
| `alembic_version` | 1 | Dolly's Alembic migration version. |

### Dolly reference app

| Table | Rows | Purpose |
|---|---|---|
| `sample_dolly_app_module_customer` | 0 | The reference app's demo customer table. |
| `sample_dolly_app_module_audit_event` | 0 | The reference app's demo audit table. |

---

## Orphaned — safe to drop (Dolly's call)

Neither repo's live code touches these. They are leftovers from an early iteration of the
sample app, before it was renamed to the `sample_dolly_app_module_*` tables above.

| Table | Rows | What it is |
|---|---|---|
| `customer` | 6 | Pre-rename sample-app customer table — demo/seed data (fictional customers). Superseded by `sample_dolly_app_module_customer`. |
| `audit_event` | 16 | Pre-rename sample-app audit table — admin login events from 2026-06-23. Superseded by `sample_dolly_app_module_audit_event`. |

Dropping them is a Dolly-owned change (they predate warehouse_app), and only after
confirming nothing in Dolly still references them.

---

## Adding tables

- A **warehouse_app** table change ships as a numbered file in `schema/migrations/`, applied
  by the migrate runner, recorded in `schema_version`.
- A **new module** (picking, receiving, will-call) that needs its own storage owns
  **slug-prefixed** tables and registers them through Dolly's mechanism — not here.
- Whoever adds tables to the shared DB sends the other the name + purpose list first, so the
  shared surface stays something both have looked at rather than assumed about.
