# Owns: CLI entry point for the inventory sync pipeline.
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app (make_source),
#             warehouse_app.services.inventory_sync, psycopg.

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

import psycopg

from warehouse_app import config, make_source
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import inventory_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync inventory from source into local DB.")
    parser.add_argument("--env-file", default=None, help="Path to .env file")
    parser.add_argument("--limit", type=int, default=None, help="Cap records fetched (dev/test)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not write to DB")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)
    sync_start = datetime.now(timezone.utc)

    source = make_source(cfg)
    source.login()

    with psycopg.connect(cfg.database_url) as conn:
        upserted, dim_updated, pruned = inventory_sync.run(
            source=source,
            conn=conn,
            sync_start=sync_start,
            limit=args.limit,
            dry_run=args.dry_run,
        )

    print(f"upserted={upserted}  dim_updated={dim_updated}  pruned={pruned}")


if __name__ == "__main__":
    main()
