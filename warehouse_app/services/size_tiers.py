# Owns: compute and write size_tier for all models that have height_in data.
# Must not: contain SQL; call config.load() directly.
# May import: warehouse_app.adapters.db.catalog,
#             warehouse_app.core.classify (tier_from_product_height_in),
#             psycopg, logging.

from __future__ import annotations

import logging

import psycopg

from warehouse_app.adapters.db import catalog
from warehouse_app.core.classify import tier_from_product_height_in

logger = logging.getLogger(__name__)


def run(
    conn: psycopg.Connection,
    dry_run: bool = False,
) -> int:
    """Compute and write size_tier for all models with height_in data.

    Returns count of rows updated.
    """
    candidates: list[tuple[str, float]] = catalog.fetch_tier_candidates(conn)
    logger.info(
        "size_tiers: %d candidate models with height_in but no tier (before)",
        len(candidates),
    )

    rows = [
        {
            "model_number": model_number,
            "size_tier":    tier_from_product_height_in(height_in),
        }
        for model_number, height_in in candidates
    ]

    if dry_run:
        logger.info("size_tiers dry_run: would update %d rows", len(rows))
        return len(rows)

    updated = catalog.update_size_tiers(conn, rows)
    logger.info(
        "size_tiers: updated %d / %d models with size_tier (after)",
        updated, len(candidates),
    )
    return updated
