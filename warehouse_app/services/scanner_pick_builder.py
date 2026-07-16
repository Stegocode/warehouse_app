# Owns: orchestrate the scanner-API read into a pick queue for a delivery date —
#        fetch orders, resolve each serialized line to its units, delegate ordering to core.
# Must not: contain SQL; contain domain ordering logic (that is core.pick_order).
# May import: warehouse_app.core (domain, pick_order), warehouse_app.adapters.source.scanner_read,
#             concurrent.futures, datetime, logging.
#
# This replaces the PDF-route-sheet + inventory-allocation path (stop_sync + the allocation
# branch of pick_queue_builder). The scanner API names the exact units on each delivery, so
# the queue is built from the delivery itself rather than guessed from what an order has
# allocated in the warehouse. Serialized-only: sum(Qty) over Serialized==1 lines is the
# validated piece count; parts/kits (Serialized 0) are not warehouse picks.

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from warehouse_app.core.domain import SOURCE_STATUS_MAP, ScannerPickUnit
from warehouse_app.core.pick_order import build_scanner_pick_rows
from warehouse_app.adapters.source.scanner_read import PICKABLE_LOCATION_IDS

logger = logging.getLogger(__name__)


def _fetch_item_units(
    reader, order_item_id: int, pick_locations, expected_qty: int | None = None
) -> list[dict]:
    """All allocated units for one order line, de-duped by InventoryId across locations.

    Stops early once ``expected_qty`` units are found — most units sit in the warehouse
    (location 1), so this keeps a day's build to roughly one call per line instead of one
    per (line x location), which the ERP rate-limits.
    """
    seen: dict[int, dict] = {}
    for loc in pick_locations:
        payload = reader.fetch_order_item_units(order_item_id, loc)
        for a in (payload.get("allocated") or []):
            inv = a.get("InventoryId")
            if inv is not None and inv not in seen:
                seen[inv] = a
        if expected_qty is not None and len(seen) >= expected_qty:
            break
    return list(seen.values())


def build_units(
    reader,
    delivery_date: date,
    *,
    concurrency: int = 4,
    pick_locations=PICKABLE_LOCATION_IDS,
) -> list[ScannerPickUnit]:
    """Read the day's orders and resolve every serialized line to its physical units."""
    date_iso = delivery_date.isoformat()
    orders = reader.fetch_delivery_orders(date_iso)

    # Serialized lines scheduled for THIS date. (An order returned for a date can carry lines
    # for other dates; EstimatedDeliveryDate is per line.)
    lines: list[dict] = []
    for o in orders:
        if o.get("IsCanceled"):
            continue
        order_id = o.get("OrderId")
        customer = o.get("ShippingCustomerName") or o.get("BillingCustomerName")
        for it in o.get("items", []):
            if it.get("Serialized") != 1:
                continue  # parts/kits are not warehouse picks
            if it.get("EstimatedDeliveryDate") != date_iso:
                continue
            dtype = (it.get("delivery_pickup_type") or {}).get("Name")
            lines.append({
                "order_id":      order_id,
                "customer":      customer,
                "order_item_id": it.get("OrderItemId"),
                "model":         it.get("Model"),
                "truck":         it.get("TruckName"),
                "is_will_call":  dtype == "Pickup",
                "qty":           it.get("Qty"),
            })

    def _work(line: dict):
        return line, _fetch_item_units(
            reader, line["order_item_id"], pick_locations, line.get("qty")
        )

    resolved: list[tuple[dict, list[dict]]] = []
    if lines:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            resolved = list(ex.map(_work, lines))

    units: list[ScannerPickUnit] = []
    for line, raw_units in resolved:
        truck = line["truck"] or ("WILL CALL" if line["is_will_call"] else "UNROUTED")
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
                truck_id=truck,
                model_number=line["model"],
                source_inventory_id=a.get("InventoryId"),
                source_order_item_id=line["order_item_id"],
                erp_status=label,
                customer_name=line["customer"],
                serial_number=a.get("MFGSerialNumber"),
                whse_location=None,  # bin label resolved from inventory_items at write time
                is_will_call=line["is_will_call"],
            ))
    return units


def run(
    reader,
    delivery_date: date,
    owned_trucks: frozenset[str],
    *,
    concurrency: int = 4,
    dry_run: bool = False,
):
    """Build stops + pick rows for a date from the scanner API.

    dry_run=True returns ``(stops, rows)`` and writes nothing — used to validate counts
    against the ERP scheduler before persisting. The DB write is stage 2b.
    """
    units = build_units(reader, delivery_date, concurrency=concurrency)
    stops, rows = build_scanner_pick_rows(delivery_date, units, owned_trucks)
    logger.info(
        "[scanner_pick_builder] %s: %d unit(s) -> %d stop(s), %d pick row(s)",
        delivery_date.isoformat(), len(units), len(stops), len(rows),
    )
    if dry_run:
        return stops, rows
    raise NotImplementedError(
        "scanner_pick_builder DB write is stage 2b; call with dry_run=True for now"
    )
