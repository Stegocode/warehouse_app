"""Tests for core/void_finder.py — pure putaway routing, no I/O."""
import math

from warehouse_app.core.void_finder import find_void

NODES = [
    {"node_id": "A", "x": 0.0, "y": 0.0},
    {"node_id": "B", "x": 10.0, "y": 0.0},
    {"node_id": "C", "x": 10.0, "y": 10.0},
]
EDGES = [
    {"node_a": "A", "node_b": "B", "distance_m": 10.0},
    {"node_a": "B", "node_b": "C", "distance_m": 10.0},
]
PICK_BIN = {"whse_location": "P-01-01", "x": 9.5, "y": 0.5}   # snaps to B
VOIDS = [
    {"whse_location": "V-01-01", "x": 0.5, "y": 0.5, "height_m": 2.5, "rack_type": "STD"},   # near A
    {"whse_location": "V-02-01", "x": 10.5, "y": 9.5, "height_m": 2.5, "rack_type": "STD"},  # near C
]
DIMS = {"carton_w_in": 30, "carton_h_in": 72, "carton_d_in": 30}


class TestRanking:
    def test_returns_all_reachable_fitting_voids(self):
        result = find_void(DIMS, PICK_BIN, VOIDS, set(), NODES, EDGES)
        assert len(result) == 2
        assert {r["whse_location"] for r in result} == {"V-01-01", "V-02-01"}

    def test_score_is_snap_plus_graph_plus_snap(self):
        # Both voids sit one 10 m graph hop from B (the pick node), each ~sqrt(0.5) off-node.
        result = find_void(DIMS, PICK_BIN, VOIDS, set(), NODES, EDGES)
        snap = math.sqrt(0.5)
        expected = snap + 10.0 + snap
        for row in result:
            assert math.isclose(row["score"], expected, rel_tol=1e-6)

    def test_closest_first(self):
        # Move the pick bin right on top of node A: V-01-01 (near A) must outrank V-02-01.
        pick_at_a = {"whse_location": "P", "x": 0.0, "y": 0.0}
        result = find_void(DIMS, pick_at_a, VOIDS, set(), NODES, EDGES)
        assert result[0]["whse_location"] == "V-01-01"
        assert result[0]["score"] < result[1]["score"]

    def test_limit_caps_the_result(self):
        many = [
            {"whse_location": f"V-{i:02d}", "x": float(i), "y": 0.0,
             "height_m": 2.5, "rack_type": "STD"}
            for i in range(10)
        ]
        result = find_void(DIMS, PICK_BIN, many, set(), NODES, EDGES, limit=3)
        assert len(result) == 3


class TestFilters:
    def test_occupied_bins_excluded(self):
        result = find_void(DIMS, PICK_BIN, VOIDS, {"V-01-01"}, NODES, EDGES)
        assert [r["whse_location"] for r in result] == ["V-02-01"]

    def test_too_tall_carton_filtered_out(self):
        # 200 in >> 2.5 m (~98 in): nothing fits.
        tall = {"carton_w_in": 30, "carton_h_in": 200, "carton_d_in": 30}
        assert find_void(tall, PICK_BIN, VOIDS, set(), NODES, EDGES) == []

    def test_too_wide_carton_filtered_by_rack_width(self):
        # STD rack limit is 36 in; a 48 in carton does not fit a STD slot.
        wide = {"carton_w_in": 48, "carton_h_in": 72, "carton_d_in": 30}
        assert find_void(wide, PICK_BIN, VOIDS, set(), NODES, EDGES) == []

    def test_bulk_rack_ignores_width(self):
        bulk_void = [{"whse_location": "BULK-1", "x": 0.5, "y": 0.5,
                      "height_m": 3.0, "rack_type": "BULK"}]
        wide = {"carton_w_in": 90, "carton_h_in": 72, "carton_d_in": 30}
        result = find_void(wide, PICK_BIN, bulk_void, set(), NODES, EDGES)
        assert [r["whse_location"] for r in result] == ["BULK-1"]

    def test_unknown_dims_fit_everything(self):
        no_dims = {"carton_w_in": None, "carton_h_in": None, "carton_d_in": None}
        assert len(find_void(no_dims, PICK_BIN, VOIDS, set(), NODES, EDGES)) == 2


class TestEdgeCases:
    def test_no_candidates_returns_empty(self):
        assert find_void(DIMS, PICK_BIN, [], set(), NODES, EDGES) == []

    def test_no_nodes_returns_empty(self):
        assert find_void(DIMS, PICK_BIN, VOIDS, set(), [], EDGES) == []

    def test_unreachable_void_is_dropped(self):
        # An island node D with no edges: a void nearest it is unreachable from the pick.
        nodes = NODES + [{"node_id": "D", "x": 100.0, "y": 100.0}]
        island_void = [{"whse_location": "ISLE", "x": 100.0, "y": 100.0,
                        "height_m": 2.5, "rack_type": "STD"}]
        assert find_void(DIMS, PICK_BIN, island_void, set(), nodes, EDGES) == []
