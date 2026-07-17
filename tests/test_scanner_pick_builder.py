"""Tests for services/scanner_pick_builder — build_units + run(dry_run), no live server.

Exercises the read-to-units orchestration with FakeScannerReader: one call per line,
delivery-type -> morning-first priority, Qty-vs-allocated shortfall detection, the dry-run
(stops, rows, shortfalls) return, and the pure write-path helpers (split-order guard,
synthetic->DB stop_id remap).
"""
from datetime import date

import pytest

from warehouse_app.core.domain import (
    PRIORITY_MORNING_FIRST,
    PRIORITY_NORMAL,
    DeliveryStop,
    PickRow,
)
from warehouse_app.adapters.source.scanner_read import FakeScannerReader
from warehouse_app.services import scanner_pick_builder
from warehouse_app.services.scanner_pick_builder import (
    ScannerBuildError,
    _guard_no_split_orders,
    _remap_rows,
)

DATE = date(2026, 7, 17)
DATE_ISO = DATE.isoformat()
OWNED = frozenset({"56", "HECTOR"})


def _line(oii, model, qty, type_id, type_name, truck=None, edd=DATE_ISO):
    return {
        "OrderItemId": oii, "Model": model, "Qty": qty, "Serialized": 1,
        "TruckName": truck, "EstimatedDeliveryDate": edd,
        "delivery_pickup_type": {"DeliveryPickupTypeId": type_id, "Name": type_name},
    }


def _order(order_id, items, cust="ACME"):
    return {"OrderId": order_id, "IsCanceled": False,
            "ShippingCustomerName": cust, "items": items}


def _unit(inv, status=1, serial=None, bin_id=964):
    return {"InventoryId": inv, "InventoryStatus": status,
            "MFGSerialNumber": serial, "WHSELocationId_FK": bin_id}


def _reader(orders_items, units):
    return FakeScannerReader(orders={DATE_ISO: orders_items}, units=units)


def test_single_call_per_line_at_location_1():
    """allocated[] is location-independent — the builder must query each line exactly once,
    at location 1, not loop the pickable set."""
    orders = [_order(100, [_line(10, "M1", 2, 2, "Delivery", truck="56")])]
    units = {(10, 1): {"allocated": [_unit(1), _unit(2)], "available": []}}
    reader = _reader(orders, units)

    scanner_pick_builder.build_units(reader, DATE)

    unit_calls = [c for c in reader.calls if c[0] == "fetch_order_item_units"]
    assert unit_calls == [("fetch_order_item_units", 10, 1)]


def test_delivery_type_sets_morning_first_priority():
    orders = [_order(100, [
        _line(10, "M1", 1, 2, "Delivery", truck="56"),     # normal
        _line(11, "M2", 1, 1, "Pickup"),                   # morning-first (id 1)
        _line(12, "M3", 1, 4, "3rd Party"),                # morning-first (id 4)
        _line(13, "M4", 1, 5, "Drop Ship"),                # morning-first (id 5)
        _line(14, "M5", 1, 8, "Bend Transfer", truck="BEND"),  # normal
    ])]
    units = {
        (10, 1): {"allocated": [_unit(1)]},
        (11, 1): {"allocated": [_unit(2)]},
        (12, 1): {"allocated": [_unit(3)]},
        (13, 1): {"allocated": [_unit(4)]},
        (14, 1): {"allocated": [_unit(5)]},
    }
    resolved, _ = scanner_pick_builder.build_units(_reader(orders, units), DATE)
    prio = {u.source_order_item_id: u.priority_group for u in resolved}
    assert prio[10] == PRIORITY_NORMAL
    assert prio[14] == PRIORITY_NORMAL
    assert prio[11] == prio[12] == prio[13] == PRIORITY_MORNING_FIRST


def test_pickup_with_no_truck_gets_bucket_label():
    orders = [_order(100, [_line(11, "M2", 1, 1, "Pickup")])]  # no TruckName
    units = {(11, 1): {"allocated": [_unit(2)]}}
    resolved, _ = scanner_pick_builder.build_units(_reader(orders, units), DATE)
    assert resolved[0].truck_id == "PICKUP"


def test_shortfall_when_allocated_less_than_qty():
    orders = [_order(18808, [_line(150836, "JD630STSS", 3, 2, "Delivery", truck="HUB 01")],
                     cust="Hollywood Hub")]
    units = {(150836, 1): {"allocated": [_unit(89509)]}}  # 1 allocated, Qty 3
    resolved, shortfalls = scanner_pick_builder.build_units(_reader(orders, units), DATE)

    assert len(resolved) == 1                     # the one allocated unit is queued
    assert len(shortfalls) == 1
    s = shortfalls[0]
    assert (s.source_order_id, s.source_order_item_id) == (18808, 150836)
    assert s.scheduled_qty == 3 and s.allocated_count == 1 and s.missing == 2


def test_no_shortfall_when_fully_allocated_even_if_already_picked():
    """8 allocated units, all already picked (status 3) — accounted for, not a shortfall."""
    orders = [_order(100, [_line(10, "M1", 8, 2, "Delivery", truck="56")])]
    units = {(10, 1): {"allocated": [_unit(i, status=3) for i in range(8)]}}
    _, shortfalls = scanner_pick_builder.build_units(_reader(orders, units), DATE)
    assert shortfalls == []


def test_non_serialized_and_memo_lines_excluded():
    orders = [_order(19743, [
        {"OrderItemId": 152291, "Model": "MEMO", "Qty": 1, "Serialized": None,
         "TruckName": "HUB 06", "EstimatedDeliveryDate": DATE_ISO,
         "delivery_pickup_type": {"DeliveryPickupTypeId": 2, "Name": "Delivery"}},
    ])]
    resolved, shortfalls = scanner_pick_builder.build_units(_reader(orders, {}), DATE)
    assert resolved == [] and shortfalls == []


def test_line_scheduled_for_other_date_excluded():
    orders = [_order(100, [_line(10, "M1", 1, 2, "Delivery", truck="56", edd="2026-07-20")])]
    units = {(10, 1): {"allocated": [_unit(1)]}}
    resolved, _ = scanner_pick_builder.build_units(_reader(orders, units), DATE)
    assert resolved == []


def test_run_dry_returns_stops_rows_shortfalls():
    orders = [_order(100, [
        _line(10, "M1", 1, 2, "Delivery", truck="56"),
        _line(11, "M2", 2, 2, "Delivery", truck="56"),   # short: only 1 allocated
    ])]
    units = {
        (10, 1): {"allocated": [_unit(1)]},
        (11, 1): {"allocated": [_unit(2)]},
    }
    stops, rows, shortfalls = scanner_pick_builder.run(
        _reader(orders, units), DATE, OWNED, dry_run=True
    )
    assert len(stops) == 1                    # one (order, truck) stop
    assert len(rows) == 2                     # two allocated units queued
    assert len(shortfalls) == 1 and shortfalls[0].missing == 1


# ── write-path helpers (pure) ──────────────────────────────────────────────────

def _stop(order_id, truck, stop_id):
    return DeliveryStop(
        stop_id=stop_id, delivery_date=DATE, truck_id=truck, stop_order=1,
        source_order_id=order_id, customer_name="ACME", sink_item_id=None,
    )


def _row(stop_id, inv, order_item=10, status="queued"):
    return PickRow(
        stop_id=stop_id, source_inventory_id=inv, source_order_item_id=order_item,
        delivery_date=DATE, truck_id="56", stop_order=1, piece_order=1,
        model_number="M", whse_location=None, truck_sort_order=0, status=status,
    )


def test_guard_raises_when_order_split_across_trucks():
    stops = [_stop(500, "56", "s-a"), _stop(500, "HECTOR", "s-b")]  # same order, 2 trucks
    with pytest.raises(ScannerBuildError):
        _guard_no_split_orders(stops)


def test_guard_passes_when_no_order_split():
    stops = [_stop(500, "56", "s-a"), _stop(501, "HECTOR", "s-b")]
    _guard_no_split_orders(stops)  # does not raise


def test_remap_rewrites_stop_id_and_attaches_bin():
    rows = [_row("2026-07-20-56-100", inv=1)]
    remapped, dropped = _remap_rows(
        rows,
        order_by_synth_stop={"2026-07-20-56-100": 100},
        db_stop_by_order={100: "db-uuid-1"},
        bins={1: "02-02-04"},
    )
    assert dropped == []
    assert remapped[0].stop_id == "db-uuid-1"
    assert remapped[0].whse_location == "02-02-04"


def test_remap_drops_row_when_order_has_no_db_stop():
    rows = [_row("2026-07-20-UNROUTED-100", inv=1)]
    remapped, dropped = _remap_rows(
        rows,
        order_by_synth_stop={"2026-07-20-UNROUTED-100": 100},
        db_stop_by_order={},          # order 100 produced no fetchable stop
        bins={},
    )
    assert remapped == [] and len(dropped) == 1


def test_remap_preserves_in_transit_status():
    rows = [_row("2026-07-20-56-100", inv=1, status="in_transit")]
    remapped, _ = _remap_rows(
        rows, {"2026-07-20-56-100": 100}, {100: "db-1"}, {1: "A-1"}
    )
    assert remapped[0].status == "in_transit"
