# Owns: application services that orchestrate core logic + adapters.
# Must not: import from adapters directly — receive them via constructor injection.
# May import: warehouse_app.core, warehouse_app.adapters.*.ports (protocols only).
