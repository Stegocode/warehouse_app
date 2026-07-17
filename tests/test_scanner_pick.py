"""Tests for build_scanner_pick_rows — pure scanner-unit -> stops/rows assembly, no I/O."""
from datetime import date

from warehouse_app.core.domain import (
    PRIORITY_MORNING_FIRST,
    PRIORITY_NORMAL,
    ScannerPickUnit,
)
from warehouse_app.core.pick_order import build_scanner_pick_rows

DATE = date(2026, 7, 16)
OWNED = frozenset({"HECTOR", "56"})


def _u(order, truck, model, inv, oii, status="in_warehouse", cust="ACME",
       priority=PRIORITY_NORMAL):
    return ScannerPickUnit(
        order_id=order, truck_id=truck, model_number=model,
        source_inventory_id=inv, source_order_item_id=oii,
        erp_status=status, customer_name=cust, priority_group=priority,
    )


def test_owned_fleet_ranks_before_third_party():
    units = [
        _u(100, "HUB 01", "M1", 1, 10),   # not in OWNED -> ranks after
        _u(200, "HECTOR", "M2", 2, 20),   # owned -> ranks first
    ]
    _, rows = build_scanner_pick_rows(DATE, units, OWNED)
    rank = {r.truck_id: r.truck_sort_order for r in rows}
    assert rank["HECTOR"] < rank["HUB 01"]


def test_stop_per_order_truck_and_piece_sequence():
    units = [
        _u(100, "HUB 01", "BMODEL", 2, 10),
        _u(100, "HUB 01", "AMODEL", 1, 11),
    ]
    stops, rows = build_scanner_pick_rows(DATE, units, OWNED)
    assert len(stops) == 1
    # within a stop, sorted by model then inventory id -> AMODEL first
    assert [r.model_number for r in rows] == ["AMODEL", "BMODEL"]
    assert [r.piece_order for r in rows] == [1, 2]
    assert all(r.stop_id == stops[0].stop_id for r in rows)


def test_status_seeded_from_erp():
    units = [
        _u(1, "HECTOR", "M", 1, 1, status="in_warehouse"),
        _u(1, "HECTOR", "M", 2, 1, status="in_transit"),
    ]
    _, rows = build_scanner_pick_rows(DATE, units, OWNED)
    by_inv = {r.source_inventory_id: r.status for r in rows}
    assert by_inv[1] == "queued"        # open -> to pick
    assert by_inv[2] == "in_transit"    # already picked -> done


def test_non_pickable_status_is_dropped_never_queued():
    units = [
        _u(1, "HECTOR", "M", 1, 1, status="missing"),
        _u(1, "HECTOR", "M", 2, 1, status="sold"),
        _u(1, "HECTOR", "M", 3, 1, status="in_warehouse"),
    ]
    _, rows = build_scanner_pick_rows(DATE, units, OWNED)
    assert [r.source_inventory_id for r in rows] == [3]


def test_same_order_split_across_two_trucks_is_two_stops():
    units = [
        _u(500, "HECTOR", "M", 1, 1),
        _u(500, "56", "M", 2, 2),
    ]
    stops, _ = build_scanner_pick_rows(DATE, units, OWNED)
    assert len(stops) == 2
    assert {s.truck_id for s in stops} == {"HECTOR", "56"}


def test_morning_first_ranks_before_owned_delivery():
    """A scheduled Pickup/3rd-Party/Drop-Ship (morning-first tier) is picked before the
    owned delivery fleet, even though it rides no owned truck."""
    units = [
        _u(1, "HECTOR", "M", 1, 1),                                  # owned delivery, normal
        _u(2, "PICKUP", "M", 2, 2, priority=PRIORITY_MORNING_FIRST),  # scheduled pickup
    ]
    _, rows = build_scanner_pick_rows(DATE, units, OWNED)
    rank = {r.truck_id: r.truck_sort_order for r in rows}
    assert rank["PICKUP"] < rank["HECTOR"]


def test_morning_first_tier_keeps_owned_before_carrier_within_tier():
    """Within the morning-first tier the owned/label order still applies."""
    units = [
        _u(1, "CARRIER", "M", 1, 1, priority=PRIORITY_MORNING_FIRST),  # not owned
        _u(2, "56", "M", 2, 2, priority=PRIORITY_MORNING_FIRST),       # owned
    ]
    _, rows = build_scanner_pick_rows(DATE, units, OWNED)
    rank = {r.truck_id: r.truck_sort_order for r in rows}
    assert rank["56"] < rank["CARRIER"]
