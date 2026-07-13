# Owns: forward-only SQL migration runner and the schema_version ledger.
# Must not: contain domain logic; import from services or core.
# May import: psycopg, pathlib, hashlib, logging, standard library.
#
# Migrations are plain .sql files in schema/migrations/, applied in filename order.
# Each file is applied at most once, inside a transaction, and recorded in
# schema_version. Re-running the runner is a no-op (Rule 9 — idempotent setup).
#
# Every migration file must ALSO be internally idempotent (IF NOT EXISTS / IF EXISTS).
# The ledger is the primary guard; file-level idempotency is defence in depth for a
# database that was hand-migrated before this runner existed — which is exactly the
# state this codebase is in.

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

_ENSURE_LEDGER_SQL = """
    CREATE TABLE IF NOT EXISTS schema_version (
        version     TEXT        PRIMARY KEY,
        checksum    TEXT        NOT NULL,
        applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


def _executable_sql(sql: str) -> str:
    """Strip comments and blank lines, leaving only what the database actually executes.

    The checksum is taken over this, not the raw file. The guard exists to catch a
    migration whose *behaviour* changed after it was applied — that is what silently
    diverges a database from its repo. Rewording a comment changes nothing the database
    ever sees, and tripping the guard on it would train people to bypass it, which is
    how a guard stops being a guard.
    """
    lines = []
    for raw_line in sql.splitlines():
        line = raw_line.split("--", 1)[0].strip()
        if line:
            lines.append(" ".join(line.split()))
    return "\n".join(lines)


def _checksum(sql: str) -> str:
    return hashlib.sha256(_executable_sql(sql).encode("utf-8")).hexdigest()[:16]


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(_ENSURE_LEDGER_SQL)
        cur.execute("SELECT version, checksum FROM schema_version")
        return {row[0]: row[1] for row in cur.fetchall()}
    # commit handled by caller


def discover(migrations_dir: Path) -> list[Path]:
    """Return migration files in filename order. Fails closed on a missing directory."""
    if not migrations_dir.is_dir():
        raise RuntimeError(f"Migrations directory not found: {migrations_dir}")
    return sorted(migrations_dir.glob("*.sql"))


def run_migrations(
    conn: psycopg.Connection,
    migrations_dir: Path,
    dry_run: bool = False,
) -> list[str]:
    """Apply every migration not yet in schema_version. Returns the versions applied.

    Raises if a previously-applied migration's contents have changed — an edited
    migration means the database and the repo have silently diverged, and guessing
    which is correct is exactly the confident-wrong-action we refuse to take.
    """
    files = discover(migrations_dir)
    applied = _applied(conn)
    conn.commit()

    pending: list[Path] = []
    for path in files:
        version = path.stem
        sql = path.read_text(encoding="utf-8")
        known = applied.get(version)
        if known is None:
            pending.append(path)
        elif known != _checksum(sql):
            raise RuntimeError(
                f"Migration {version!r} was already applied but its contents have "
                f"changed (recorded {known}, file now {_checksum(sql)}). Migrations are "
                f"immutable once applied — add a new one instead of editing this."
            )

    if not pending:
        logger.info("migrate: schema up to date (%d applied, 0 pending)", len(applied))
        return []

    logger.info("migrate: %d pending — %s", len(pending), ", ".join(p.stem for p in pending))
    if dry_run:
        return [p.stem for p in pending]

    done: list[str] = []
    for path in pending:
        version = path.stem
        sql = path.read_text(encoding="utf-8")
        logger.info("migrate: applying %s", version)
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_version (version, checksum) VALUES (%s, %s)",
                (version, _checksum(sql)),
            )
        conn.commit()
        done.append(version)
        logger.info("migrate: applied %s", version)

    return done


def restamp(conn: psycopg.Connection, migrations_dir: Path) -> list[tuple[str, str, str]]:
    """Recompute stored checksums for already-applied migrations. Runs no SQL.

    This is an escape hatch for exactly one situation: the checksum *algorithm* changed,
    so every stored value is stale even though no migration's behaviour differs. It does
    not re-run anything and it does not touch the schema.

    It is deliberately not a way to silence a real drift warning. Anyone reaching for it
    because a migration "already ran, it's fine" is about to hide a divergence between
    the repo and a live database — write a new migration instead.

    Returns (version, old_checksum, new_checksum) for every row it changed.
    """
    applied = _applied(conn)
    conn.commit()

    changed: list[tuple[str, str, str]] = []
    for path in discover(migrations_dir):
        version = path.stem
        if version not in applied:
            continue
        new = _checksum(path.read_text(encoding="utf-8"))
        old = applied[version]
        if new == old:
            continue
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE schema_version SET checksum = %s WHERE version = %s",
                (new, version),
            )
        conn.commit()
        changed.append((version, old, new))
        logger.warning("migrate: RESTAMPED %s  %s -> %s", version, old, new)

    return changed
