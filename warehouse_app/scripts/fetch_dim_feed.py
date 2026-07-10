# Owns: CLI entry point for async carton-dim fetch from the external catalog feed.
# Must not: contain scraping logic or SQL.
# May import: warehouse_app.config, warehouse_app.services.dim_feed_fetch, psycopg.

from __future__ import annotations

import argparse
import logging
import sys

import psycopg

from warehouse_app import config
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import dim_feed_fetch


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Async-fetch carton dimensions from the external catalog feed."
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    if not cfg.dim_feed_url_template:
        print("ERROR: DIM_FEED_URL_TEMPLATE not set.", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(cfg.database_url) as conn:
        updated = dim_feed_fetch.run(
            conn=conn,
            url_template=cfg.dim_feed_url_template,
            concurrency=args.concurrency,
            limit=args.limit,
            offset=args.offset,
            dry_run=args.dry_run,
        )

    print(f"models updated: {updated}")


if __name__ == "__main__":
    main()
