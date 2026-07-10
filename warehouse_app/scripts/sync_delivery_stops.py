# Owns: CLI entry point for delivery stop sync (route sheet PDF + sink board → DB).
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app (make_source, make_sink),
#             warehouse_app.services.stop_sync, psycopg.

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from warehouse_app import config, make_sink, make_source
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import stop_sync


def main() -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    parser = argparse.ArgumentParser(description="Sync delivery stops from route sheet + sink board.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--date", default=tomorrow, help="Delivery date ISO (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    source = make_source(cfg)
    source.login()
    sink   = make_sink(cfg)

    count = stop_sync.run(
        source=source,
        sink=sink,
        database_url=cfg.database_url,
        delivery_date=args.date,
        default_board_id=cfg.sink_board_id,
        dry_run=args.dry_run,
    )

    print(f"stop rows upserted: {count}")


if __name__ == "__main__":
    main()
