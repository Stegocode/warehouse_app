# Owns: orchestrate model metadata fetch → classify → upsert → location-field update.
# Must not: contain SQL; call config.load() directly.
# May import: warehouse_app.adapters.source.ports,
#             warehouse_app.adapters.db.catalog,
#             warehouse_app.core.classify,
#             psycopg, logging.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import psycopg

from warehouse_app.adapters.db import catalog
from warehouse_app.core.classify import classify_model

if TYPE_CHECKING:
    from warehouse_app.adapters.source.ports import SourcePort

logger = logging.getLogger(__name__)

_FLOOR_ROWS = frozenset({"C", "13"})


def _extract(raw: dict) -> dict | None:
    """Convert a single raw model dict from the source API into a catalog row.

    Returns None if model_number is empty.
    """
    model_number = (raw.get("ModelNumber") or "").strip()
    if not model_number:
        return None
    category     = (raw.get("category") or {}).get("Name") or ""
    product_type = (raw.get("type") or {}).get("Name") or ""
    manufacturer = (raw.get("manufacturer") or {}).get("Name") or None
    description  = (raw.get("ShortDescription") or None)
    is_part      = bool(raw.get("IsPart"))
    product_class, width_in, floor_only = classify_model(
        model_number, category, product_type, primary_row=""
    )
    return {
        "model_number":      model_number,
        "manufacturer":      manufacturer,
        "description":       description,
        "category":          category,
        "type":              product_type,
        "is_part":           is_part,
        "product_class":     product_class,
        "width_in":          width_in,
        "floor_only":        floor_only,
        "source_product_id": raw.get("ProductId_FK"),
    }


# DEBT: reclassify floor-row models after primary_row update (DEBT-SYNC-001).
# After batch_update_primary_rows(), models whose primary_row moved into a floor
# row (C or 13) may need product_class promoted from SLIDEIN_RANGE → LARGE_RANGE.
# Reclassification requires category + type from the DB, which are not present in
# the stats list.  Deferred until catalog.py exposes a SQL-level reclassify call.
def _reclassify(stats: list[dict]) -> list[dict]:
    """Return stats entries for models now in floor rows (reclassification candidates).

    Full reclassification is deferred — see DEBT-SYNC-001.  This stub surfaces
    which models would need attention so callers can log a warning.
    """
    return [
        s for s in stats
        if (s.get("primary_row") or "").upper() in _FLOOR_ROWS
    ]


def run(
    source: "SourcePort",
    conn: psycopg.Connection,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Sync product model metadata from the source system into the local database.

    Pipeline:
      1. source.login()                           — authenticate with source system
      2. source.fetch_models(limit=limit)         — paginated model fetch
      3. [_extract(r) for r in raw] → filter Nones
      4. catalog.upsert_models(conn, rows)        — upsert to DB
      5. catalog.fetch_model_location_stats(conn) — get primary_row + serial_count
      6. catalog.batch_update_primary_rows(conn, stats)
      7. catalog.batch_update_serial_counts(conn, stats)

    Returns {fetched: int, upserted: int, location_updated: int}.

    Assumptions: single-writer; no auth layer; single machine.
    Out-of-scope: reclassifying floor-row models after primary_row update
      (DEBT-SYNC-001).
    """
    logger.info("model_sync.run: authenticating with source system")
    source.login()

    logger.info("model_sync.run: fetching models (limit=%s)", limit)
    raw_records = source.fetch_models(limit=limit)
    fetched = len(raw_records)
    logger.info("model_sync.run: fetched %d raw records", fetched)

    rows = [r for raw in raw_records if (r := _extract(raw)) is not None]
    skipped = fetched - len(rows)
    if skipped:
        logger.warning(
            "model_sync.run: skipped %d records with empty model_number", skipped
        )
    logger.info("model_sync.run: extracted %d valid rows", len(rows))

    if dry_run:
        logger.info("model_sync.run: dry_run=True — no writes performed")
        return {"fetched": fetched, "upserted": 0, "location_updated": 0}

    upserted = catalog.upsert_models(conn, rows)
    logger.info("model_sync.run: upserted %d model rows", upserted)

    stats = catalog.fetch_model_location_stats(conn)
    logger.info("model_sync.run: location stats returned for %d models", len(stats))

    location_updated = catalog.batch_update_primary_rows(conn, stats)
    logger.info("model_sync.run: updated primary_row for %d models", location_updated)

    catalog.batch_update_serial_counts(conn, stats)

    floor_candidates = _reclassify(stats)
    if floor_candidates:
        logger.warning(
            "model_sync.run: %d model(s) are in floor rows and may need"
            " reclassification — deferred (DEBT-SYNC-001)",
            len(floor_candidates),
        )

    return {"fetched": fetched, "upserted": upserted, "location_updated": location_updated}
