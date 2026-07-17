"""Tests for core/pick_order.py — pure truck ranking, no I/O.

Covers truck_sort_key and assign_truck_ranks, the ranking the pick queue's claim ORDER BY
re-derives from truck_sort_order. (Stop/piece sequencing is covered by the scanner build in
test_scanner_pick.py, which is the sole consumer of this ranking now.)
"""
from warehouse_app.core.pick_order import assign_truck_ranks, truck_sort_key

OWNED = frozenset({"OWN-01", "OWN-02"})

# A fleet shaped like a real one: numeric internal trucks plus one named internal truck,
# and a third-party carrier whose label sorts BEFORE the named internal truck.
# Lexicographically:  '68' < '72' < 'ACME' < 'FLEET'
# so ORDER BY truck_id would hand out third-party 'ACME' ahead of owned 'FLEET'.
# Which trucks are owned is configuration, not something the label encodes — which is
# exactly why the rank has to be persisted rather than re-derived from truck_id.
FLEET_OWNED = frozenset({"56", "58", "62", "64", "68", "72", "FLEET"})


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

    def test_morning_first_priority_ranks_ahead_of_owned_fleet(self):
        """The delivery-priority tier layered on top of the owned/label order: a
        morning-first truck (priority 0) outranks the owned fleet (priority 1)."""
        ranks = assign_truck_ranks(
            ["FLEET", "PICKUP"], FLEET_OWNED, truck_priority={"PICKUP": 0, "FLEET": 1}
        )
        assert ranks["PICKUP"] < ranks["FLEET"]
