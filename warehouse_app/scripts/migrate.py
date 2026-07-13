# Owns: CLI entry point for applying schema migrations.
# Must not: contain domain logic or SQL.
# May import: warehouse_app.config, warehouse_app.adapters.db.migrate, psycopg, argparse.

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

from warehouse_app import config
from warehouse_app.adapters.db import migrate
from warehouse_app.infrastructure.logging import configure

# Repo layout: <root>/warehouse_app/scripts/migrate.py  ->  <root>/schema/migrations
_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "schema" / "migrations"


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply pending schema migrations.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument(
        "--migrations-dir",
        default=str(_DEFAULT_DIR),
        help="Directory of numbered .sql migrations (default: <repo>/schema/migrations)",
    )
    parser.add_argument("--dry-run", action="store_true", help="List pending, apply nothing")
    parser.add_argument(
        "--restamp",
        action="store_true",
        help="Recompute stored checksums for applied migrations. Runs no SQL. Use ONLY "
             "when the checksum algorithm changed — never to silence a real drift warning.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure(level="DEBUG" if args.verbose else "INFO")
    cfg = config.load(env_file=args.env_file)

    with psycopg.connect(cfg.database_url) as conn:
        if args.restamp:
            changed = migrate.restamp(conn, Path(args.migrations_dir))
            if changed:
                for version, old, new in changed:
                    print(f"restamped {version}: {old} -> {new}")
            else:
                print("restamp: all checksums already current")
            return

        applied = migrate.run_migrations(
            conn,
            Path(args.migrations_dir),
            dry_run=args.dry_run,
        )

    verb = "pending" if args.dry_run else "applied"
    if applied:
        print(f"migrations {verb}: {', '.join(applied)}")
    else:
        print("migrations: schema up to date")


if __name__ == "__main__":
    main()
