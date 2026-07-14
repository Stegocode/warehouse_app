# Owns: SQL reads that feed the void finder — graph, candidate bins, occupancy,
#        the reference pick, and product dimensions.
# Must not: contain routing/scoring logic (that is core.void_finder); call config.load().
# May import: psycopg, standard library.
#
# Convention: one function per logical read; SQL in module-level _<VERB>_<ENTITY>_SQL
# constants. Everything here returns the plain dicts/sets that core.void_finder consumes,
# so the pure logic never touches the database.

from __future__ import annotations

import logging
from datetime import date

import psycopg

logger = logging.getLogger(__name__)

_FETCH_NODES_SQL = "SELECT node_id, x, y FROM graph_nodes"

_FETCH_EDGES_SQL = "SELECT node_a, node_b, distance_m FROM graph_edges"

# Eligible, coordinate-bearing bins — the void finder's candidate set before occupancy and
# dimension filtering (both of which it applies itself).
_FETCH_VOIDS_SQL = """
    SELECT whse_location, x, y, height_m, rack_type
    FROM warehouse_bins
    WHERE eligible_for_void = TRUE
      AND x IS NOT NULL AND y IS NOT NULL
"""

# A bin is occupied if any in-warehouse item is currently recorded there. This is the
# occupancy view the finder excludes; inventory_items.source_whse_location is warehouse_app's
# source of truth for where stock physically is.
_FETCH_OCCUPIED_SQL = """
    SELECT DISTINCT source_whse_location
    FROM inventory_items
    WHERE source_whse_location IS NOT NULL
      AND status = 'in_warehouse'
"""

# The reference bin: the next pick still to be worked for the date, in pick order, that has
# a real mapped location. Ordering matches the claim query (owned fleet first). NULLS LAST
# keeps unranked rows from jumping in.
_FETCH_NEXT_PICK_BIN_SQL = """
    SELECT pq.whse_location, b.x, b.y
    FROM pick_queue pq
    JOIN warehouse_bins b ON b.whse_location = pq.whse_location
    WHERE pq.delivery_date = %(d)s
      AND pq.status = 'queued'
      AND pq.whse_location IS NOT NULL
    ORDER BY pq.truck_sort_order NULLS LAST, pq.stop_order, pq.piece_order
    LIMIT 1
"""

_FETCH_DIMS_SQL = """
    SELECT carton_w_in, carton_h_in, carton_d_in
    FROM model_size_catalog
    WHERE model_number = %(model)s
"""


def fetch_graph(conn: psycopg.Connection) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges) for the path graph."""
    with conn.cursor() as cur:
        cur.execute(_FETCH_NODES_SQL)
        nodes = [{"node_id": r[0], "x": r[1], "y": r[2]} for r in cur.fetchall()]
        cur.execute(_FETCH_EDGES_SQL)
        edges = [{"node_a": r[0], "node_b": r[1], "distance_m": r[2]} for r in cur.fetchall()]
    return nodes, edges


def fetch_candidate_voids(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(_FETCH_VOIDS_SQL)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_occupied_locations(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_FETCH_OCCUPIED_SQL)
        return {r[0] for r in cur.fetchall()}


def fetch_next_pick_bin(conn: psycopg.Connection, delivery_date: date) -> dict | None:
    """The reference bin for putaway, or None when nothing is queued with a location."""
    with conn.cursor() as cur:
        cur.execute(_FETCH_NEXT_PICK_BIN_SQL, {"d": delivery_date})
        row = cur.fetchone()
    if row is None:
        return None
    return {"whse_location": row[0], "x": row[1], "y": row[2]}


def fetch_product_dims(conn: psycopg.Connection, model_number: str) -> dict:
    """Carton dims for a model. Missing model or missing dims -> all None (the finder
    treats unknown dims as fitting, so a missing catalog row does not veto putaway)."""
    with conn.cursor() as cur:
        cur.execute(_FETCH_DIMS_SQL, {"model": model_number})
        row = cur.fetchone()
    if row is None:
        return {"carton_w_in": None, "carton_h_in": None, "carton_d_in": None}
    return {"carton_w_in": row[0], "carton_h_in": row[1], "carton_d_in": row[2]}
