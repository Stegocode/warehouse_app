# Owns: CLI entry point for pick queue builder (stops + inventory → ordered pick list).
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app.services.pick_queue_builder, psycopg.

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import psycopg

from warehouse_app import config
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import pick_queue_builder


def main() -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    parser = argparse.ArgumentParser(description="Build pick queue for a delivery date.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--date", default=tomorrow, help="Delivery date ISO (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    with psycopg.connect(cfg.database_url) as conn:
        count = pick_queue_builder.run(
            conn=conn,
            delivery_date=date.fromisoformat(args.date),
            owned_trucks=cfg.owned_fleet_trucks,
            dry_run=args.dry_run,
        )

    print(f"pick rows written: {count}")


if __name__ == "__main__":
    main()
