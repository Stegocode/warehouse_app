# Owns: CLI entry-point scripts. Each script loads config, wires adapters, calls one service.
# Must not: contain domain logic, SQL, or adapter implementation.
# May import: warehouse_app.config, warehouse_app (factories), warehouse_app.services.*.
