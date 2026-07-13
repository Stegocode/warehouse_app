# Owns: orchestrate inventory fetch → extract → upsert pipeline.
# Must not: contain SQL; call config.load() directly.
# May import: warehouse_app.adapters.source.ports (SourcePort),
#             warehouse_app.adapters.db.neon, psycopg, datetime, logging.

from __future__ import annotations

import logging
from collections import Counter
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

_UNKNOWN_STATUS_FALLBACK = "in_warehouse"


def _serial_number(raw: dict) -> str | None:
    """Return the real manufacturer serial, or None when the source has none.

    The source auto-fills MFGSerialNumber with the InventoryId whenever no real serial
    was captured, so a value equal to the id carries no information and must not be
    mistaken for a scannable label. ScannedMFGSerialNumber, when set, is the serial
    physically scanned off the carton and is preferred.
    """
    inventory_id = str(raw.get("InventoryId") or "")
    for key in ("ScannedMFGSerialNumber", "MFGSerialNumber"):
        value = raw.get(key)
        if value is None:
            continue
        serial = str(value).strip()
        if serial and serial != inventory_id:
            return serial
    return None


def _extract(raw: dict) -> dict | None:
    """Return a normalised inventory row, or None if the record lacks a valid ID."""
    if not raw.get("InventoryId"):
        return None
    raw_status   = raw.get("InventoryStatus")
    order_item   = raw.get("order_item") or {}
    whse_loc     = raw.get("whse_location") or {}
    manufacturer = raw.get("manufacturer") or {}
    return {
        "source_inventory_id":  raw.get("InventoryId"),
        "source_model_id":      raw.get("ModelId_FK"),
        "model_number":         (raw.get("ModelNumber") or "").strip(),
        "manufacturer":         manufacturer.get("Name") or None,
        "serial_number":        _serial_number(raw),
        "image_url":            raw.get("MobileImageURL") or raw.get("ImgURL") or None,
        "short_description":    (raw.get("ShortDescription") or "").strip() or None,
        "source_location_id":   raw.get("LocationId_FK"),
        "source_whse_location": whse_loc.get("Name") or None,
        "source_status":        raw_status,
        "status":               _STATUS_MAP.get(raw_status, _UNKNOWN_STATUS_FALLBACK),
        "source_order_item_id": raw.get("OrderItemId_FK"),
        "source_order_id":      order_item.get("OrderFK"),
        "is_non_sellable":      bool(raw.get("IsNonSellable")),
        "is_deleted":           bool(raw.get("IsDeleted")),
        "cost":                 raw.get("Cost"),
        "received_date":        raw.get("ReceivedDate"),
        "invoiced_date":        raw.get("InvoicedDate"),
    }


def _report_unmapped_statuses(rows: list[dict]) -> None:
    """Warn once per sync about source status codes absent from _STATUS_MAP.

    The fallback keeps the sync running, but it must never be silent: an unmapped
    code means the source added a state we do not model, and those rows are being
    filed as in_warehouse on a guess. See DEBT-SYNC-002.
    """
    unmapped = Counter(
        r["source_status"] for r in rows if r["source_status"] not in _STATUS_MAP
    )
    if unmapped:
        detail = ", ".join(f"{code!r}={n}" for code, n in sorted(unmapped.items(), key=str))
        logger.warning(
            "inventory_sync: %d record(s) carry a source status not in _STATUS_MAP "
            "(%s) — filed as %r. Identify these codes and map them explicitly.",
            sum(unmapped.values()), detail, _UNKNOWN_STATUS_FALLBACK,
        )


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

    _report_unmapped_statuses(rows)

    with_serial = sum(1 for r in rows if r["serial_number"])
    with_mfr    = sum(1 for r in rows if r["manufacturer"])
    logger.info(
        "inventory_sync: coverage — serial %d/%d (%.0f%%), manufacturer %d/%d (%.0f%%)",
        with_serial, len(rows), 100 * with_serial / max(len(rows), 1),
        with_mfr, len(rows), 100 * with_mfr / max(len(rows), 1),
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
