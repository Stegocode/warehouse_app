# Owns: CLI entry point for size tier derivation from height_in data.
# Must not: contain tier-classification logic or SQL.
# May import: warehouse_app.config, warehouse_app.services.size_tiers, psycopg.

from __future__ import annotations

import argparse
import logging

import psycopg

from warehouse_app import config
from warehouse_app.infrastructure.logging import configure
from warehouse_app.services import size_tiers


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive size_tier for all models with known height_in."
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    with psycopg.connect(cfg.database_url) as conn:
        updated = size_tiers.run(conn=conn, dry_run=args.dry_run)

    print(f"size_tier updated: {updated}")


if __name__ == "__main__":
    main()
