# Owns: CLI entry point for model metadata sync (fetch → classify → upsert → location update).
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app (make_source),
#             warehouse_app.services.model_sync, psycopg.

from __future__ import annotations

import argparse
import logging

import psycopg

from warehouse_app import config, make_source
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import model_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync model metadata from source into local DB.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--limit", type=int, default=None, help="Cap models fetched (dev/test)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    source = make_source(cfg)

    with psycopg.connect(cfg.database_url) as conn:
        result = model_sync.run(
            source=source,
            conn=conn,
            limit=args.limit,
            dry_run=args.dry_run,
        )

    print(f"fetched={result['fetched']}  upserted={result['upserted']}  "
          f"location_updated={result['location_updated']}")


if __name__ == "__main__":
    main()
