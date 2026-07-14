# Owns: pure void-selection routing — rank open bins by travel distance to a reference pick.
# Must not: import from adapters, services, infrastructure, or config; do any I/O.
# May import: standard library only.
#
# Given a product, the next pick's location, and the warehouse path graph, return the
# open bins closest to that pick by real travel distance (not straight-line): so a
# received unit is put away near where it will next be picked, minimising forklift trips.
# Pure: data in, ranked list out. The graph is tiny (43 nodes, 72 edges) so a textbook
# Dijkstra is ample.

from __future__ import annotations

import heapq
import math

# Rack width limits in inches by rack type, for the carton-width fit check. BULK has no
# defined slot width (open floor), so it is never width-constrained.
_RACK_WIDTH_IN: dict[str, int | None] = {
    "STD": 36,
    "TALL": 36,
    "TYPE2": 36,
    "BULK": None,
}

_METRES_TO_INCHES = 39.3701


def _euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def _snap_to_node(x: float, y: float, nodes: list[dict]) -> tuple[str | None, float]:
    """Return (nearest node_id, straight-line distance) — how far off-graph a point is."""
    best_id: str | None = None
    best_dist = math.inf
    for node in nodes:
        d = _euclidean(x, y, node["x"], node["y"])
        if d < best_dist:
            best_dist = d
            best_id = node["node_id"]
    return best_id, best_dist


def _dijkstra(graph: dict[str, list[tuple[str, float]]], start: str) -> dict[str, float]:
    """Shortest-path distance from start to every reachable node."""
    dist: dict[str, float] = {start: 0.0}
    heap: list[tuple[float, str]] = [(0.0, start)]
    while heap:
        cost, u = heapq.heappop(heap)
        if cost > dist.get(u, math.inf):
            continue
        for v, w in graph.get(u, []):
            nc = cost + w
            if nc < dist.get(v, math.inf):
                dist[v] = nc
                heapq.heappush(heap, (nc, v))
    return dist


def _fits(product_dims: dict, void: dict) -> bool:
    """Whether a carton fits the bin. Unknown dims fit (fail-open on dims only).

    Height and width are independent gates: a missing dimension does not veto a bin, but a
    known dimension that exceeds the slot does. Occupancy is handled by the caller.
    """
    height_in = product_dims.get("carton_h_in")
    if height_in is not None and height_in > void["height_m"] * _METRES_TO_INCHES:
        return False
    width_in = product_dims.get("carton_w_in")
    if width_in is not None:
        limit = _RACK_WIDTH_IN.get(void.get("rack_type"))
        if limit is not None and width_in > limit:
            return False
    return True


def find_void(
    product_dims: dict,
    next_pick_bin: dict,
    candidate_voids: list[dict],
    occupied_locations: set[str],
    graph_nodes: list[dict],
    graph_edges: list[dict],
    limit: int = 5,
) -> list[dict]:
    """Rank open, fitting bins by travel distance to next_pick_bin. Closest first.

    Inputs (all plain dicts so this stays pure and adapter-agnostic):
      product_dims       carton_w_in / carton_h_in / carton_d_in (any may be None)
      next_pick_bin      the reference bin: whse_location, x, y
      candidate_voids    eligible bins: whse_location, x, y, height_m, rack_type
      occupied_locations whse_locations that currently hold inventory (excluded)
      graph_nodes        node_id, x, y
      graph_edges        node_a, node_b, distance_m (undirected)

    Score = (void -> its nearest node) + (that node -> pick's node, via the graph)
          + (pick's node -> pick bin). Returns up to `limit` rows, each with the score
    and its components for transparency. Empty when nothing is open, fits, and is reachable.
    """
    eligible = [
        void
        for void in candidate_voids
        if void["whse_location"] not in occupied_locations and _fits(product_dims, void)
    ]
    if not eligible or not graph_nodes:
        return []

    adjacency: dict[str, list[tuple[str, float]]] = {}
    for edge in graph_edges:
        a, b, d = edge["node_a"], edge["node_b"], edge["distance_m"]
        adjacency.setdefault(a, []).append((b, d))
        adjacency.setdefault(b, []).append((a, d))

    pick_node, snap_dist_pick = _snap_to_node(next_pick_bin["x"], next_pick_bin["y"], graph_nodes)
    if pick_node is None:
        return []
    graph_dist = _dijkstra(adjacency, pick_node)

    scored: list[dict] = []
    for void in eligible:
        void_node, snap_dist_void = _snap_to_node(void["x"], void["y"], graph_nodes)
        via_graph = graph_dist.get(void_node, math.inf)
        score = snap_dist_void + via_graph + snap_dist_pick
        if math.isinf(score):
            continue  # unreachable from the pick node in this graph
        scored.append({
            "whse_location":  void["whse_location"],
            "score":          score,
            "void_node":      void_node,
            "snap_dist_void": snap_dist_void,
            "graph_dist":     via_graph,
            "snap_dist_pick": snap_dist_pick,
        })

    scored.sort(key=lambda row: row["score"])
    return scored[:limit]
