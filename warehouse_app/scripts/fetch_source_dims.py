# Owns: CLI entry point for async dim fetch from source product pages.
# Must not: contain scraping logic or SQL.
# May import: warehouse_app.config, warehouse_app (make_source),
#             warehouse_app.services.source_dim_fetch, psycopg.

from __future__ import annotations

import argparse
import logging

import psycopg

from warehouse_app import config, make_source
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import source_dim_fetch


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Async-fetch product dimensions from source product pages."
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="Max models to process (dev/test)")
    parser.add_argument("--refetch", action="store_true",
                        help="Refetch even if dims already exist")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    source = make_source(cfg)

    with psycopg.connect(cfg.database_url) as conn:
        updated = source_dim_fetch.run(
            source=source,
            conn=conn,
            concurrency=args.concurrency,
            limit=args.limit,
            refetch=args.refetch,
            dry_run=args.dry_run,
        )

    print(f"models updated: {updated}")


if __name__ == "__main__":
    main()
