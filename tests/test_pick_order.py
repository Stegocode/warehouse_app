"""Tests for core/pick_order.py — pure pick sequencing logic, no I/O."""
from datetime import date

import pytest

from warehouse_app.core.domain import DeliveryStop, InventoryItem, PickRow
from warehouse_app.core.pick_order import (
    assign_truck_ranks,
    build_pick_order,
    truck_sort_key,
)

DELIVERY_DATE = date(2026, 1, 15)
OWNED = frozenset({"OWN-01", "OWN-02"})

# A fleet shaped like a real one: numeric internal trucks plus one named internal truck,
# and a third-party carrier whose label sorts BEFORE the named internal truck.
# Lexicographically:  '68' < '72' < 'ACME' < 'FLEET'
# so ORDER BY truck_id would hand out third-party 'ACME' ahead of owned 'FLEET'.
# Which trucks are owned is configuration, not something the label encodes — which is
# exactly why the rank has to be persisted rather than re-derived from truck_id.
FLEET_OWNED = frozenset({"56", "58", "62", "64", "68", "72", "FLEET"})


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


# ── assign_truck_ranks ────────────────────────────────────────────────────────
# The rank is what makes the pick order survive the round-trip to SQL. A table has no
# inherent row order, so if the rank is wrong the claim query hands out the wrong item.

class TestAssignTruckRanks:
    def test_owned_ranked_before_third_party(self):
        ranks = assign_truck_ranks(["3PL-01", "OWN-02", "OWN-01"], OWNED)
        assert ranks["OWN-01"] < ranks["OWN-02"] < ranks["3PL-01"]

    def test_ranks_are_dense_and_zero_based(self):
        ranks = assign_truck_ranks(["3PL-01", "OWN-02", "OWN-01"], OWNED)
        assert sorted(ranks.values()) == [0, 1, 2]

    def test_duplicate_trucks_collapse_to_one_rank(self):
        ranks = assign_truck_ranks(["OWN-01"] * 5 + ["3PL-01"] * 3, OWNED)
        assert ranks == {"OWN-01": 0, "3PL-01": 1}

    def test_stable_across_input_order(self):
        a = assign_truck_ranks(["3PL-01", "OWN-01", "OWN-02"], OWNED)
        b = assign_truck_ranks(["OWN-02", "3PL-01", "OWN-01"], OWNED)
        assert a == b

    def test_third_party_cannot_jump_ahead_of_owned_fleet(self):
        """The bug this column exists to prevent.

        'ACME' sorts before 'FLEET', so ORDER BY truck_id would hand a picker the
        third-party carrier's item ahead of the owned fleet's. Ownership is config, not
        spelling, and only the rank can express that.
        """
        trucks = ["ACME", "68", "72", "FLEET", "STORE"]
        assert sorted(trucks) == ["68", "72", "ACME", "FLEET", "STORE"]  # the trap

        ranks = assign_truck_ranks(trucks, FLEET_OWNED)
        assert ranks["FLEET"] < ranks["ACME"], "third-party jumped ahead of owned fleet"
        assert ranks["68"] < ranks["ACME"]
        assert ranks["72"] < ranks["ACME"]
        # Neither ACME nor STORE is owned; among third parties, label order stands.
        assert ranks["ACME"] < ranks["STORE"]

    def test_owned_numeric_trucks_sort_numerically_not_lexically(self):
        """Lexicographically '10' < '5'. Zero-padding in truck_sort_key fixes that, and
        the rank inherits it."""
        ranks = assign_truck_ranks(["10", "5"], frozenset({"5", "10"}))
        assert ranks["5"] < ranks["10"]

        ranks = assign_truck_ranks(["72", "56", "64"], FLEET_OWNED)
        assert ranks["56"] < ranks["64"] < ranks["72"]

    def test_empty_owned_set_makes_everything_third_party(self):
        """Guards the silent-config hazard: an unset OWNED_FLEET_TRUCKS must not
        quietly reorder the fleet. Ranks stay well-formed; config.load() is what
        refuses to boot."""
        ranks = assign_truck_ranks(["68", "HUB 01"], frozenset())
        assert sorted(ranks.values()) == [0, 1]


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

    def test_truck_sort_order_is_set_on_every_row(self):
        stops = [_stop("3PL-01", 1, 2001), _stop("OWN-01", 1, 1001)]
        inv = {
            1001: [_item(1, "MODEL-A", 1001)],
            2001: [_item(2, "MODEL-B", 2001)],
        }
        rows = build_pick_order(DELIVERY_DATE, stops, inv, OWNED)
        assert all(r.truck_sort_order is not None for r in rows)

    def test_sql_order_by_reproduces_in_memory_order(self):
        """The contract the pick_queue claim depends on.

        build_pick_order sorts in memory; the claim query re-derives that order in SQL
        with ORDER BY truck_sort_order, stop_order, piece_order. If the two ever
        disagree, pickers are handed the wrong item.
        """
        stops = [
            _stop("ACME", 2, 3002),
            _stop("68", 1, 1001),
            _stop("ACME", 1, 3001),
            _stop("FLEET", 1, 2001),
        ]
        inv = {
            1001: [_item(10, "A", 1001), _item(11, "B", 1001)],
            2001: [_item(20, "C", 2001)],
            3001: [_item(30, "D", 3001)],
            3002: [_item(40, "E", 3002)],
        }
        rows = build_pick_order(DELIVERY_DATE, stops, inv, FLEET_OWNED)

        # Exactly what the claim's ORDER BY does, applied to the persisted columns.
        as_sql_would_sort = sorted(
            rows, key=lambda r: (r.truck_sort_order, r.stop_order, r.piece_order)
        )
        assert as_sql_would_sort == rows

        # Owned fleet first — the order ORDER BY truck_id would have got wrong.
        assert [r.truck_id for r in rows] == ["68", "68", "FLEET", "ACME", "ACME"]

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
