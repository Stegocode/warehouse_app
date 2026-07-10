# Owns: database adapter — upserts and queries against the WMS schema.
# Must not: contain domain logic; call config.load() directly.
# May import: warehouse_app.core (types), warehouse_app.config, psycopg.
