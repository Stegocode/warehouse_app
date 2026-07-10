"""Service: orchestrate route sheet + sink board → upsert delivery stops.

# ── Boundary ──────────────────────────────────────────────────────────────────
# Owns: fetch PDF → parse stops → merge sink annotations → upsert rows.
# Owns     : fetch PDF → parse stops → merge sink annotations → upsert rows
# Must not : contain SQL; hardcode truck labels or customer names
# May use  : warehouse_app.core.route_sheet (parse_route_pages, norm_truck)
#            warehouse_app.adapters.db.neon
#            warehouse_app.adapters.source.ports
#            warehouse_app.adapters.sink.ports
#            pdfplumber, psycopg, logging
# Out of scope: authentication, retry logic, column discovery
# ─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import io
import logging
from datetime import date
from typing import TYPE_CHECKING

import pdfplumber
import psycopg

from warehouse_app.adapters.db import neon
from warehouse_app.core.route_sheet import parse_route_pages

if TYPE_CHECKING:
    from warehouse_app.adapters.sink.ports import SinkPort
    from warehouse_app.adapters.source.ports import SourcePort

logger = logging.getLogger(__name__)

# ── Internal helpers ──────────────────────────────────────────────────────────


def _extract_pages(pdf_bytes: bytes) -> list[list[str]]:
    """Open PDF bytes with pdfplumber and return per-page line lists."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [
            [ln.strip() for ln in (page.extract_text() or "").splitlines() if ln.strip()]
            for page in pdf.pages
        ]


def _build_sink_lookup(sink_items: dict[str, dict]) -> dict[int, dict]:
    """Convert the sink board's str-keyed dict to an int-keyed lookup.

    fetch_board_items() returns {order_number_str: item_dict}; route sheet
    order IDs are integers, so we coerce here once.
    """
    lookup: dict[int, dict] = {}
    for order_str, item in sink_items.items():
        try:
            lookup[int(order_str)] = item
        except (TypeError, ValueError):
            logger.warning("Sink item has non-integer order key %r — skipping", order_str)
    return lookup


def _build_stop_rows(
    target_date: date,
    route_stops: dict[str, list[tuple[int, str]]],
    sink_by_order: dict[int, dict],
    default_board_id: str | None = None,
) -> list[dict]:
    """Merge route-sheet stops with sink annotations into delivery_stops row dicts.

    Stops present on the route sheet but absent from the sink board are included
    with null sink fields.  Sink items that have no matching route-sheet stop are
    appended as UNROUTED stops so nothing on the board is silently dropped.
    """
    rows: list[dict] = []
    routed_order_ids: set[int] = set()

    for truck_id, stops in route_stops.items():
        for stop_order, order_id_str in stops:
            try:
                source_order_id = int(order_id_str)
            except ValueError:
                logger.warning(
                    "Non-integer order# %r on truck %s stop %d — skipping",
                    order_id_str, truck_id, stop_order,
                )
                continue

            routed_order_ids.add(source_order_id)
            sink = sink_by_order.get(source_order_id, {})

            rows.append({
                "delivery_date":    target_date,
                "truck_id":         truck_id,
                "stop_order":       stop_order,
                "source_order_id":  source_order_id,
                "sink_item_id":     sink.get("sink_item_id"),
                "sink_board_id":    sink.get("sink_board_id") or default_board_id,
                "sink_status":      sink.get("sink_status"),
                "customer_name":    sink.get("customer_name"),
                "delivery_address": None,
                "delivery_notes":   sink.get("delivery_notes"),
            })

    # Sink-only stops: on the board but not on the route sheet.
    for order_id, sink in sink_by_order.items():
        if order_id in routed_order_ids:
            continue
        rows.append({
            "delivery_date":    target_date,
            "truck_id":         "UNROUTED",
            "stop_order":       None,
            "source_order_id":  order_id,
            "sink_item_id":     sink.get("sink_item_id"),
            "sink_board_id":    sink.get("sink_board_id"),
            "sink_status":      sink.get("sink_status"),
            "customer_name":    sink.get("customer_name"),
            "delivery_address": None,
            "delivery_notes":   sink.get("delivery_notes"),
        })

    return rows


# ── Public API ────────────────────────────────────────────────────────────────


def run(
    source: "SourcePort",
    sink: "SinkPort",
    database_url: str,
    delivery_date: str,                # ISO: YYYY-MM-DD
    truck_aliases: dict | None = None, # runtime-injected fleet label overrides
    default_board_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """Orchestrate route sheet + sink board → upsert delivery stops.

    Accepts database_url rather than a live connection so the DB connection is
    opened only after all external fetches complete (avoids Neon idle-timeout drops).
    Returns the count of stop rows upserted (0 when dry_run=True).
    """
    target_date = date.fromisoformat(delivery_date)
    aliases = truck_aliases or {}

    # Step 1: fetch and parse the route-sheet PDF.
    logger.info("[stop_sync] Fetching route sheet PDF for %s", delivery_date)
    pdf_bytes = source.fetch_route_sheet_pdf(delivery_date)
    pages = _extract_pages(pdf_bytes)
    route_stops = parse_route_pages(pages, aliases=aliases)

    truck_count = len(route_stops)
    stop_count = sum(len(v) for v in route_stops.values())
    logger.info("[stop_sync] Route sheet parsed: %d trucks, %d stops", truck_count, stop_count)
    for truck_id, stops in sorted(route_stops.items()):
        logger.info("  %s → %d stops", truck_id, len(stops))

    # Step 2: fetch sink board items and build order-keyed lookup.
    logger.info("[stop_sync] Fetching sink board items...")
    sink_items = sink.fetch_board_items(delivery_date)
    sink_by_order = _build_sink_lookup(sink_items)
    logger.info("[stop_sync] Sink items indexed: %d entries", len(sink_by_order))

    # Step 3: merge route sheet + sink annotations into upsert rows.
    rows = _build_stop_rows(target_date, route_stops, sink_by_order, default_board_id)
    sink_matched = sum(1 for r in rows if r.get("sink_item_id"))
    logger.info(
        "[stop_sync] Built %d rows (%d sink-matched, %d route-only)",
        len(rows), sink_matched, len(rows) - sink_matched,
    )

    if dry_run:
        for r in rows:
            logger.info(
                "  [dry] truck=%s stop=%s order=%s sink=%s %s",
                r["truck_id"], r["stop_order"], r["source_order_id"],
                r.get("sink_item_id"), r.get("customer_name") or "",
            )
        return 0

    # Step 4: open a fresh connection and upsert — all external I/O is done by now.
    with psycopg.connect(database_url) as conn:
        neon.upsert_stops(conn, rows)
    logger.info("[stop_sync] Upserted %d rows for %s", len(rows), delivery_date)
    return len(rows)
