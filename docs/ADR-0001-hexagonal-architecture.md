# ADR-0001: Hexagonal (Ports & Adapters) Architecture

**Status:** Accepted — **superseded in part by [ADR-0002](ADR-0002-retire-sink-scanner-source.md) (2026-07)**  
**Date:** 2026-07-10

> **Superseded in part (2026-07).** The `sink/` port (`SinkPort`, `make_sink`, `null_sink`,
> `graphql_sink`) and the `SINK_*` config described below were **retired** when the pick path
> moved to the ERP scanner API — see ADR-0002. The three-layer hexagonal shape and the
> inward-only dependency rule still hold; the system is now **source-only** (there is no sink).
> Read the sink references below as historical.

## Context

The warehouse management system integrates two external systems: a source system
(the ERP/inventory platform) and a sink system (the notification/tracking board).
Both are vendor-managed SaaS products with APIs that will change over time. The
domain logic (pick ordering, bin routing, stop syncing) is independent of which
specific products are in use.

## Decision

Adopt a three-layer hexagonal architecture:

```
core/          — pure domain logic and types (no I/O)
services/      — application services (orchestrate core + adapters)
adapters/      — concrete I/O implementations
  source/      — ERP/inventory adapter (implements SourcePort)
  sink/        — board/notification adapter (implements SinkPort)
  db/          — database adapter (Neon PostgreSQL)
```

**Dependency rule:** imports point inward only. `adapters` may import from
`core`; `core` never imports from `adapters`. Services receive adapters via
constructor injection — they reference only the port protocols, never concrete
types.

**Factory pattern:** `warehouse_app.make_source(cfg)` and `make_sink(cfg)`
select the concrete adapter based on `SOURCE_TYPE` / `SINK_TYPE` config. Setting
`SOURCE_TYPE=fake` and `SINK_TYPE=null` enables fully offline development and
unit testing without any external service.

**Domain vocabulary:** all identifiers use generic domain terms (`source_*`,
`sink_*`). No vendor names appear in code, module names, or column names. See
`.conformance-banned` and `gate.py`.

## Consequences

- Swapping to a different ERP or board requires only a new adapter file — no
  core or service changes.
- The `fake_source` and `null_sink` adapters make unit testing straightforward
  without mocking.
- Config is validated once at startup (fail-closed); no hidden `os.getenv` calls
  spread through the codebase.
- The conformance gate (`gate.py`) enforces these boundaries mechanically.

## Rejected alternatives

- **Single-file scripts:** Simple, but untestable and un-swappable. The existing
  `model_catalog_module/scripts/` will be retired after migration.
- **Django/FastAPI ORM approach:** Too heavy for a background-sync daemon; the
  application layer is thin and benefits from explicit SQL over an ORM.
