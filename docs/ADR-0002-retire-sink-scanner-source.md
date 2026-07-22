# ADR-0002: Retire the sink adapter; the ERP scanner API is the pick source

**Status:** Accepted
**Date:** 2026-07

Supersedes, in part, [ADR-0001](ADR-0001-hexagonal-architecture.md).

## Context

ADR-0001 described a two-port system: a **source** (ERP/inventory) and a **sink**
(notification/tracking board). The sink was a Monday.com GraphQL board, used to mirror
delivery/pick state for visual verification.

The pick queue was then rebuilt to source directly from the ERP's **scanner API**
(`/api/scanner/*`). The scanner manifest names the exact units on each delivery, so one
pass builds both the delivery stops and the pick queue — replacing the route-sheet PDF
parser (`stop_sync` + `core/route_sheet`) and the allocation-guessing builder
(`pick_queue_builder`). With the pick path no longer feeding a board, the sink had no
remaining consumer on this path.

## Decision

Remove the sink port entirely:

- Delete `adapters/sink/` (`SinkPort`, `graphql_sink`, `null_sink`, `ports`), the
  `make_sink` factory, and all `SINK_*` config plus its validation.
- The architecture is now **source-only**: `core → services → adapters`, where the edge
  adapters are the ERP **scanner source** and the Neon **db**. The inward-only dependency
  rule and the `SOURCE_TYPE=fake` offline path (via `fake_source`) are unchanged; there is
  no `null_sink` counterpart because there is no sink to null.
- The `delivery_stops.sink_*` columns are kept **forward-only** (vestigial) and were relaxed
  to nullable by migration `0008` — dropping them is a separate, later migration, not this
  decision.

## Consequences

- One fewer external dependency and credential set on the pick path; the queue build no
  longer needs the board or `pdfplumber`.
- Swapping ERPs is still a single new source adapter (ADR-0001's core benefit holds).
- ADR-0001's `SinkPort` / `make_sink` / `SINK_*` references are historical; new code must
  not reintroduce a sink port. Any future outbound integration is a new adapter with its own
  ADR, not a revival of this one.
- The retired board was only ever visual verification for the Playwright receiving flow; the
  scanner write adapter (`adapters/source/scanner_write.py`) is the real state-write path.

## Rejected alternatives

- **Keep the sink as a `null_sink` no-op.** Retaining a dead port invites code to depend on
  it again and keeps `SINK_*` in the config surface. Deleting it is the honest state.
- **Drop the `delivery_stops.sink_*` columns now.** Forward-only migrations are safer;
  relaxing NOT NULL (0008) is enough to unblock the scanner build. A column drop can follow
  once nothing reads them.
