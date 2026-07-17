# Owns: CLI entry point for the scanner-API pick build (stops + pick queue + shortfalls).
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app (make_source), warehouse_app.core
#             (delivery_schedule), warehouse_app.adapters.source.scanner_read,
#             warehouse_app.services (inventory_sync, scanner_pick_builder), psycopg, datetime.
#
# Replaces sync_delivery_stops + build_pick_queue. The scanner API names the exact units on
# each delivery, so ONE pass builds the stops AND the pick queue. Inventory is refreshed
# first so every scanned unit resolves a bin label and satisfies the pick_queue ->
# inventory_items foreign key.

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timezone

import psycopg

from warehouse_app import config, make_source
from warehouse_app.adapters.source.scanner_read import HttpScannerReader
from warehouse_app.core.delivery_schedule import next_delivery_day
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import inventory_sync, scanner_pick_builder

logger = logging.getLogger(__name__)


def main() -> None:
    default_date = next_delivery_day(date.today()).isoformat()
    parser = argparse.ArgumentParser(description="Build the pick queue from the scanner API.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--date", default=default_date,
                        help="Delivery date ISO (YYYY-MM-DD); default = next delivery day")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and report counts, write nothing")
    parser.add_argument("--skip-inventory-sync", action="store_true",
                        help="Skip the inventory refresh (bins + FK rely on it being current)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)
    delivery_date = date.fromisoformat(args.date)

    # 1. Refresh inventory so every scanned unit has a bin label and a valid FK target.
    #    Skipped on a dry-run (no write) or when explicitly told the sync already ran.
    if not args.dry_run and not args.skip_inventory_sync:
        source = make_source(cfg)
        source.login()
        with psycopg.connect(cfg.database_url) as conn:
            inventory_sync.run(source, conn, datetime.now(timezone.utc))

    # 2. Build (and unless dry-run, persist) the pick queue from the scanner API.
    reader = HttpScannerReader(cfg.source_base_url, cfg.source_username, cfg.source_password)
    stops, rows, shortfalls = scanner_pick_builder.run(
        reader,
        delivery_date,
        cfg.owned_fleet_trucks,
        None if args.dry_run else cfg.database_url,
        dry_run=args.dry_run,
    )
    suffix = " (dry-run, nothing written)" if args.dry_run else ""
    print(f"{args.date}: {len(stops)} stop(s), {len(rows)} pick row(s), "
          f"{len(shortfalls)} shortfall(s){suffix}")


if __name__ == "__main__":
    main()
