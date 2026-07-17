# Owns: orchestrate the scanner-API read into a pick queue for a delivery date —
#        fetch orders, resolve each serialized line to its allocated units, tier by
#        delivery priority, detect Qty-vs-allocated shortfalls, delegate ordering to core,
#        then persist by reusing the refresh-safe stop + pick-queue write path.
# Must not: contain SQL; contain domain ordering logic (that is core.pick_order).
# May import: warehouse_app.core (domain, pick_order), warehouse_app.adapters.db
#             (neon, scanner_pick_db), psycopg, concurrent.futures, dataclasses, datetime,
#             logging.
#
# This replaces the PDF-route-sheet + inventory-allocation path (stop_sync + the allocation
# branch of pick_queue_builder). The scanner API names the exact units on each delivery, so
# the queue is built from the delivery itself rather than guessed from what an order has
# allocated in the warehouse. Serialized-only: sum(Qty) over Serialized==1 lines is the
# validated piece count; parts/kits/MEMO notes (Serialized != 1) are not warehouse picks.

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

import psycopg

from warehouse_app.adapters.db import neon, scanner_pick_db
from warehouse_app.core.domain import (
    PRIORITY_MORNING_FIRST,
    PRIORITY_NORMAL,
    SOURCE_STATUS_MAP,
    DeliveryStop,
    PickRow,
    PickShortfall,
    ScannerPickUnit,
)
from warehouse_app.core.pick_order import build_scanner_pick_rows

logger = logging.getLogger(__name__)


class ScannerBuildError(RuntimeError):
    """A scanner pick build cannot be persisted safely — typed so the caller can report it."""

# DeliveryPickupTypeId values that are picked first thing in the morning, before the
# delivery trucks roll: 1=Pickup (Will Call), 4=3rd Party, 5=Drop Ship. Everything else
# (2=Delivery, 8=Bend Transfer, ...) is the normal delivery tier. Keyed on the integer id,
# never the Name string, so a label spelling change cannot silently reshuffle the pick order.
_MORNING_FIRST_TYPE_IDS = frozenset({1, 4, 5})

# Bucket label for a morning-first line that arrives with no TruckName (a customer pickup
# has no truck). Kept distinct from the will_call_db interrupt label so a management view
# never confuses a scheduled pickup (routed, is_will_call FALSE) with a live walk-in
# interrupt (is_will_call TRUE, office-injected).
_DELIVERY_TYPE_BUCKET = {1: "PICKUP", 4: "3RD PARTY", 5: "DROP SHIP"}


def _fetch_allocated_units(reader, order_item_id: int) -> list[dict]:
    """All units allocated to one order line — a single call suffices.

    The scanner API's ``allocated[]`` is location-independent: one request at any
    PickLocationId returns every allocated unit regardless of its physical location
    (verified live 2026-07-16 — a PickLocationId=1 call returned units at locations 1, 2
    and 4). PickLocationId only filters ``available[]`` (open stock), which the pick build
    does not read. Query location 1 (WAREHO, always valid; ids 0/10/11 return HTTP 422).
    """
    payload = reader.fetch_order_item_units(order_item_id, 1)
    return list(payload.get("allocated") or [])


def build_units(
    reader,
    delivery_date: date,
    *,
    concurrency: int = 4,
) -> tuple[list[ScannerPickUnit], list[PickShortfall]]:
    """Read the day's orders and resolve every serialized line to its allocated units.

    Returns the pickable units plus a shortfall per line whose scheduled ``Qty`` exceeds
    its allocated-unit count — those pieces are scheduled but not yet allocated, so they
    never appear in ``allocated[]`` and cannot be queued, yet still must be picked. They
    are surfaced as shortfalls (flagged), never silently dropped (Rule 4).
    """
    date_iso = delivery_date.isoformat()
    orders = reader.fetch_delivery_orders(date_iso)

    # Serialized lines scheduled for THIS date. (An order returned for a date can carry
    # lines for other dates; EstimatedDeliveryDate is per line.)
    lines: list[dict] = []
    for o in orders:
        if o.get("IsCanceled"):
            continue
        order_id = o.get("OrderId")
        customer = o.get("ShippingCustomerName") or o.get("BillingCustomerName")
        for it in o.get("items", []):
            if it.get("Serialized") != 1:
                continue  # parts/kits/MEMO notes are not warehouse picks
            if it.get("EstimatedDeliveryDate") != date_iso:
                continue
            type_id = (it.get("delivery_pickup_type") or {}).get("DeliveryPickupTypeId")
            morning = type_id in _MORNING_FIRST_TYPE_IDS
            truck = it.get("TruckName") or _DELIVERY_TYPE_BUCKET.get(type_id, "UNROUTED")
            lines.append({
                "order_id":       order_id,
                "customer":       customer,
                "order_item_id":  it.get("OrderItemId"),
                "model":          it.get("Model"),
                "truck":          truck,
                "priority_group": PRIORITY_MORNING_FIRST if morning else PRIORITY_NORMAL,
                "qty":            it.get("Qty") or 0,
            })

    def _work(line: dict):
        return line, _fetch_allocated_units(reader, line["order_item_id"])

    resolved: list[tuple[dict, list[dict]]] = []
    if lines:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            resolved = list(ex.map(_work, lines))

    units: list[ScannerPickUnit] = []
    shortfalls: list[PickShortfall] = []
    for line, raw_units in resolved:
        # Shortfall counts ALL allocated units (any status — an already-picked unit is
        # still accounted for); it is the pieces with no allocated unit at all that are
        # missing. Compute before status filtering below.
        allocated_count = len(raw_units)
        if allocated_count < line["qty"]:
            shortfalls.append(PickShortfall(
                source_order_id=line["order_id"],
                source_order_item_id=line["order_item_id"],
                model_number=line["model"],
                scheduled_qty=line["qty"],
                allocated_count=allocated_count,
                customer_name=line["customer"],
            ))

        for a in raw_units:
            inv_status = a.get("InventoryStatus")
            label = SOURCE_STATUS_MAP.get(inv_status)
            if label is None:
                # Fail closed and loud: an ERP status we do not know how to classify is
                # never silently queued (Rule 4).
                logger.warning(
                    "scanner unit %s (line %s) has unmapped InventoryStatus %r — skipped",
                    a.get("InventoryId"), line["order_item_id"], inv_status,
                )
                continue
            units.append(ScannerPickUnit(
                order_id=line["order_id"],
                truck_id=line["truck"],
                model_number=line["model"],
                source_inventory_id=a.get("InventoryId"),
                source_order_item_id=line["order_item_id"],
                erp_status=label,
                customer_name=line["customer"],
                serial_number=a.get("MFGSerialNumber"),
                whse_location=None,  # bin label resolved from inventory_items at write time
                priority_group=line["priority_group"],
            ))
    return units, shortfalls


def _guard_no_split_orders(stops: list[DeliveryStop]) -> None:
    """Refuse a build where one order is spread across more than one truck.

    The core makes a stop per (order, truck), but delivery_stops holds ONE stop per
    (delivery_date, source_order_id) — truck is not part of its identity (migration 0003).
    Upserting two same-order stops would collapse them onto one DB stop and mis-route the
    loser's picks. Verified never to happen live; fail closed rather than write a lie (Rule 4).
    """
    trucks_by_order: dict[int, set[str]] = {}
    for s in stops:
        trucks_by_order.setdefault(s.source_order_id, set()).add(s.truck_id)
    split = {o: sorted(t) for o, t in trucks_by_order.items() if len(t) > 1}
    if split:
        raise ScannerBuildError(
            f"{len(split)} order(s) split across multiple trucks {split}: the DB holds one "
            "stop per (date, order), so persisting this would collapse them and mis-route "
            "picks. Resolve the split in the source before building."
        )


def _stop_upsert_row(s: DeliveryStop) -> dict:
    """A core stop -> an upsert_stops row dict. Drops the synthetic stop_id (the DB keeps its
    own, matched on the natural key) and carries no sink fields (the sink is retired)."""
    return {
        "delivery_date":    s.delivery_date,
        "truck_id":         s.truck_id,
        "stop_order":       s.stop_order,
        "source_order_id":  s.source_order_id,
        "sink_item_id":     None,
        "sink_board_id":    None,
        "sink_status":      None,
        "customer_name":    s.customer_name,
        "delivery_address": None,
        "delivery_notes":   None,
    }


def _remap_rows(
    rows: list[PickRow],
    order_by_synth_stop: dict[str, int],
    db_stop_by_order: dict[int, str],
    bins: dict[int, str | None],
) -> tuple[list[PickRow], list[PickRow]]:
    """Rewrite each row's synthetic stop_id to the DB stop_id and attach the bin label.

    A row whose order produced no fetchable DB stop (e.g. an UNROUTED stop, which
    fetch_stops filters out) cannot be attached to a real stop, so it is returned as
    'dropped' rather than written with a dangling stop_id. Returns (remapped, dropped).
    """
    remapped: list[PickRow] = []
    dropped: list[PickRow] = []
    for r in rows:
        order_id = order_by_synth_stop.get(r.stop_id)
        db_stop_id = db_stop_by_order.get(order_id) if order_id is not None else None
        if db_stop_id is None:
            dropped.append(r)
            continue
        remapped.append(replace(
            r, stop_id=db_stop_id, whse_location=bins.get(r.source_inventory_id)
        ))
    return remapped, dropped


def run(
    reader,
    delivery_date: date,
    owned_trucks: frozenset[str],
    database_url: str | None = None,
    *,
    concurrency: int = 4,
    dry_run: bool = False,
):
    """Build stops + pick rows (+ shortfalls) for a date and, unless dry_run, persist them.

    dry_run=True returns ``(stops, rows, shortfalls)`` and writes nothing — used to validate
    counts against the ERP scheduler before persisting. A write reuses the refresh-safe
    upsert_stops + write_pick_queue path: it maps the core's synthetic stop ids onto the
    DB's real stop ids, resolves bin labels, preserves in-progress claims, and records
    shortfalls as flags. Returns ``(stops, written_rows, shortfalls)`` on a write.
    """
    units, shortfalls = build_units(reader, delivery_date, concurrency=concurrency)
    stops, rows = build_scanner_pick_rows(delivery_date, units, owned_trucks)
    _guard_no_split_orders(stops)

    missing_pieces = sum(s.missing for s in shortfalls)
    logger.info(
        "[scanner_pick_builder] %s: %d unit(s) -> %d stop(s), %d pick row(s); "
        "%d line(s) short, %d scheduled piece(s) unallocated (flagged)",
        delivery_date.isoformat(), len(units), len(stops), len(rows),
        len(shortfalls), missing_pieces,
    )
    for s in shortfalls:
        logger.warning(
            "[scanner_pick_builder] shortfall: order %s line %s %s — scheduled %d, "
            "allocated %d, %d not pickable (unallocated/unreceived)",
            s.source_order_id, s.source_order_item_id, s.model_number,
            s.scheduled_qty, s.allocated_count, s.missing,
        )

    if dry_run:
        return stops, rows, shortfalls

    if not database_url:
        raise ScannerBuildError("database_url is required to persist a scanner pick build.")

    # Open the connection only after all scanner reads are done (a day is 90+ HTTP calls);
    # holding a Neon connection across them risks an idle-timeout drop. Mirrors stop_sync.
    with psycopg.connect(database_url) as conn:
        neon.upsert_stops(conn, [_stop_upsert_row(s) for s in stops])
        db_stops = neon.fetch_stops(conn, delivery_date)
        db_stop_by_order = {s.source_order_id: s.stop_id for s in db_stops}
        order_by_synth_stop = {s.stop_id: s.source_order_id for s in stops}

        inv_ids = [r.source_inventory_id for r in rows if r.source_inventory_id is not None]
        bins = scanner_pick_db.fetch_bin_labels(conn, inv_ids)

        remapped, dropped = _remap_rows(rows, order_by_synth_stop, db_stop_by_order, bins)
        if dropped:
            logger.warning(
                "[scanner_pick_builder] %d pick row(s) dropped — their order produced no "
                "persistable stop (unrouted/filtered): orders %s",
                len(dropped),
                sorted({order_by_synth_stop.get(r.stop_id) for r in dropped}),
            )
        no_bin = [r.source_inventory_id for r in remapped
                  if r.whse_location is None and r.status == "queued"]
        if no_bin:
            logger.warning(
                "[scanner_pick_builder] %d queued unit(s) have no bin label yet "
                "(inventory not synced): %s%s",
                len(no_bin), no_bin[:20], " ..." if len(no_bin) > 20 else "",
            )

        written = neon.write_pick_queue(conn, remapped, delivery_date)
        build_order_ids = list(
            {s.source_order_id for s in stops} | {s.source_order_id for s in shortfalls}
        )
        scanner_pick_db.write_shortfall_flags(
            conn, shortfalls, build_order_ids, db_stop_by_order
        )

    logger.info(
        "[scanner_pick_builder] %s: persisted %d pick row(s) across %d stop(s), "
        "%d shortfall flag(s)",
        delivery_date.isoformat(), written, len(stops), len(shortfalls),
    )
    return stops, remapped, shortfalls
