"""Tests for core/pick_order.py — pure pick sequencing logic, no I/O."""
from datetime import date

import pytest

from warehouse_app.core.domain import DeliveryStop, InventoryItem, PickRow
from warehouse_app.core.pick_order import build_pick_order, truck_sort_key

DELIVERY_DATE = date(2026, 1, 15)
OWNED = frozenset({"OWN-01", "OWN-02"})


def _stop(truck_id: str, stop_order: int, order_id: int, stop_id: str | None = None) -> DeliveryStop:
    return DeliveryStop(
        stop_id=stop_id or f"{truck_id}-{stop_order}",
        delivery_date=DELIVERY_DATE,
        truck_id=truck_id,
        stop_order=stop_order,
        source_order_id=order_id,
        customer_name=None,
        sink_item_id=None,
    )


def _item(inv_id: int, model: str, order_id: int, bin_loc: str | None = None) -> InventoryItem:
    return InventoryItem(
        source_inventory_id=inv_id,
        model_number=model,
        status="in_warehouse",
        source_location_id=1,
        source_whse_location=bin_loc,
        source_order_id=order_id,
        source_order_item_id=inv_id * 10,
        is_allocated=True,
    )


# ── truck_sort_key ────────────────────────────────────────────────────────────

class TestTruckSortKey:
    def test_owned_sorts_before_third_party(self):
        assert truck_sort_key("OWN-01", OWNED)[0] == 0
        assert truck_sort_key("3PL-99", OWNED)[0] == 1

    def test_owned_trucks_padded_for_numeric_sort(self):
        key2 = truck_sort_key("2", OWNED | frozenset({"2", "10"}))[1]
        key10 = truck_sort_key("10", OWNED | frozenset({"2", "10"}))[1]
        assert key2 < key10  # "0002" < "0010"

    def test_unknown_truck_is_third_party(self):
        assert truck_sort_key("UNKNOWN", OWNED)[0] == 1


# ── build_pick_order ──────────────────────────────────────────────────────────

class TestBuildPickOrder:
    def test_returns_pick_rows(self):
        stops = [_stop("OWN-01", 1, 1001)]
        inv = {1001: [_item(1, "MODEL-A", 1001)]}
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        assert len(rows) == 1
        assert isinstance(rows[0], PickRow)

    def test_owned_trucks_before_third_party(self):
        stops = [
            _stop("3PL-01", 1, 2001),
            _stop("OWN-01", 1, 1001),
        ]
        inv = {
            1001: [_item(1, "MODEL-A", 1001)],
            2001: [_item(2, "MODEL-B", 2001)],
        }
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        assert rows[0].truck_id == "OWN-01"
        assert rows[1].truck_id == "3PL-01"

    def test_stops_ordered_ascending_within_truck(self):
        stops = [
            _stop("OWN-01", 3, 1003),
            _stop("OWN-01", 1, 1001),
            _stop("OWN-01", 2, 1002),
        ]
        inv = {
            1001: [_item(10, "X", 1001)],
            1002: [_item(20, "X", 1002)],
            1003: [_item(30, "X", 1003)],
        }
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        stop_orders = [r.stop_order for r in rows]
        assert stop_orders == [1, 2, 3]

    def test_piece_order_resets_per_stop(self):
        stops = [
            _stop("OWN-01", 1, 1001),
            _stop("OWN-01", 2, 1002),
        ]
        inv = {
            1001: [_item(1, "A", 1001), _item(2, "B", 1001)],
            1002: [_item(3, "C", 1002)],
        }
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        stop1_pieces = [r.piece_order for r in rows if r.stop_order == 1]
        stop2_pieces = [r.piece_order for r in rows if r.stop_order == 2]
        assert stop1_pieces == [1, 2]
        assert stop2_pieces == [1]

    def test_items_sorted_by_model_then_inv_id(self):
        stops = [_stop("OWN-01", 1, 1001)]
        inv = {
            1001: [
                _item(99, "ZZZZ", 1001),
                _item(1,  "AAAA", 1001),
                _item(5,  "AAAA", 1001),
            ]
        }
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        models = [r.model_number for r in rows]
        assert models == ["AAAA", "AAAA", "ZZZZ"]
        inv_ids = [r.source_inventory_id for r in rows if r.model_number == "AAAA"]
        assert inv_ids == [1, 5]

    def test_stop_with_no_order_id_skipped(self):
        stop = DeliveryStop(
            stop_id="s1", delivery_date=DELIVERY_DATE,
            truck_id="OWN-01", stop_order=1,
            source_order_id=None,
            customer_name=None, sink_item_id=None,
        )
        rows = build_pick_order(DELIVERY_DATE, [stop], {}, OWNED)
        assert rows == []

    def test_stop_with_no_inventory_produces_no_rows(self):
        stops = [_stop("OWN-01", 1, 9999)]
        rows = build_pick_order(DELIVERY_DATE, stops, {}, OWNED)
        assert rows == []

    def test_whse_location_propagated(self):
        stops = [_stop("OWN-01", 1, 1001)]
        inv = {1001: [_item(1, "MODEL-A", 1001, bin_loc="14-02-01")]}
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        assert rows[0].whse_location == "14-02-01"

    def test_delivery_date_propagated(self):
        stops = [_stop("OWN-01", 1, 1001)]
        inv = {1001: [_item(1, "MODEL-A", 1001)]}
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        assert rows[0].delivery_date == DELIVERY_DATE
