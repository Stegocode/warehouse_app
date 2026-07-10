# Owns: orchestrate inventory fetch → extract → upsert pipeline.
# Must not: contain SQL; call config.load() directly.
# May import: warehouse_app.adapters.source.ports (SourcePort),
#             warehouse_app.adapters.db.neon, psycopg, datetime, logging.

from __future__ import annotations

import logging
from datetime import datetime

import psycopg

from warehouse_app.adapters.db import neon
from warehouse_app.adapters.source.ports import SourcePort

logger = logging.getLogger(__name__)

_STATUS_MAP: dict[int, str] = {
    0: "on_order",
    1: "in_warehouse",
    2: "in_transit",
    3: "missing",
}


def _extract(raw: dict) -> dict | None:
    """Return a normalised inventory row, or None if the record lacks a valid ID."""
    if not raw.get("InventoryId"):
        return None
    raw_status = raw.get("InventoryStatus")
    order_item = raw.get("order_item") or {}
    whse_loc   = raw.get("whse_location") or {}
    return {
        "source_inventory_id":  raw.get("InventoryId"),
        "source_model_id":      raw.get("ModelId_FK"),
        "model_number":         (raw.get("ModelNumber") or "").strip(),
        "manufacturer":         raw.get("Manufacturer"),
        "serial_number":        raw.get("SerialNumber"),
        "source_location_id":   raw.get("LocationId_FK"),
        "source_whse_location": whse_loc.get("Name") or None,
        "source_status":        raw_status,
        "status":               _STATUS_MAP.get(raw_status, "in_warehouse"),
        "source_order_item_id": raw.get("OrderItemId_FK"),
        "source_order_id":      order_item.get("OrderFK"),
        "is_non_sellable":      bool(raw.get("IsNonSellable")),
        "is_deleted":           bool(raw.get("IsDeleted")),
        "cost":                 raw.get("Cost"),
        "received_date":        raw.get("ReceivedDate"),
        "invoiced_date":        raw.get("InvoicedDate"),
    }


def run(
    source: SourcePort,
    conn: psycopg.Connection,
    sync_start: datetime,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Fetch, extract, and upsert inventory records.

    Returns (upserted, dim_updated, pruned).
    """
    raw_records = source.fetch_inventory(limit=limit)
    logger.info("inventory_sync: fetched %d raw records from source", len(raw_records))

    rows: list[dict] = []
    skipped = 0
    for raw in raw_records:
        row = _extract(raw)
        if row is None:
            skipped += 1
        else:
            rows.append(row)

    if skipped:
        logger.warning(
            "inventory_sync: skipped %d records missing InventoryId", skipped
        )

    if dry_run:
        logger.info(
            "inventory_sync dry_run: would upsert %d rows, skip %d invalid",
            len(rows), skipped,
        )
        return len(rows), 0, 0

    upserted, dim_updated, pruned = neon.upsert_inventory(conn, rows, sync_start)
    logger.info(
        "inventory_sync: upserted=%d  dim_updated=%d  pruned=%d",
        upserted, dim_updated, pruned,
    )
    return upserted, dim_updated, pruned
