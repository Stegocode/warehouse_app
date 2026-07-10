# Owns: CLI entry point for warehouse layout sync (bins + graph from layout JSON → DB).
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app.services.layout_sync, psycopg.

from __future__ import annotations

import argparse
import logging
import sys

import psycopg

from warehouse_app import config
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import layout_sync


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upsert warehouse bins and graph from layout JSON."
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--layout-path", default=None,
                        help="Path to warehouse_layout.json "
                             "(overrides LAYOUT_JSON_PATH env var)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    layout_path = args.layout_path or cfg.layout_json_path
    if not layout_path:
        print("ERROR: provide --layout-path or set LAYOUT_JSON_PATH in environment.",
              file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(cfg.database_url) as conn:
        result = layout_sync.run(conn=conn, layout_path=layout_path, dry_run=args.dry_run)

    print(f"bins={result['bins']}  nodes={result['nodes']}  edges={result['edges']}")


if __name__ == "__main__":
    main()
