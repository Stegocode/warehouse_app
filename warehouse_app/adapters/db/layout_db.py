# Owns: SQL reads and writes for warehouse_bins, graph_nodes, graph_edges.
# Must not: contain layout parsing logic; call config.load().
# May import: psycopg, standard library.
#
# Convention: one function per logical operation; all SQL is a module-level
# constant named _<VERB>_<ENTITY>_SQL to keep functions readable.

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)

_BATCH = 500


# ── Warehouse bins ─────────────────────────────────────────────────────────────

_UPSERT_BINS_SQL = """
    INSERT INTO warehouse_bins
        (whse_location, row_token, bay, level, height_m, size_tier)
    VALUES
        (%(whse_location)s, %(row_token)s, %(bay)s, %(level)s,
         %(height_m)s, %(size_tier)s)
    ON CONFLICT (whse_location) DO UPDATE SET
        row_token  = EXCLUDED.row_token,
        bay        = EXCLUDED.bay,
        level      = EXCLUDED.level,
        height_m   = EXCLUDED.height_m,
        size_tier  = EXCLUDED.size_tier,
        updated_at = now()
"""


def upsert_bins(conn: psycopg.Connection, rows: list[dict]) -> int:
    """Upsert rows into warehouse_bins. Returns count.

    Each row: {whse_location, row_token, bay, level, height_m, size_tier}
    """
    if not rows:
        return 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), _BATCH):
            cur.executemany(_UPSERT_BINS_SQL, rows[i : i + _BATCH])
    conn.commit()
    logger.debug("upserted %d warehouse_bins rows", len(rows))
    return len(rows)


# ── Graph nodes ────────────────────────────────────────────────────────────────

_UPSERT_NODES_SQL = """
    INSERT INTO graph_nodes
        (node_id, x, y, z, node_type)
    VALUES
        (%(node_id)s, %(x)s, %(y)s, %(z)s, %(node_type)s)
    ON CONFLICT (node_id) DO UPDATE SET
        x          = EXCLUDED.x,
        y          = EXCLUDED.y,
        z          = EXCLUDED.z,
        node_type  = EXCLUDED.node_type,
        updated_at = now()
"""


# ── Graph edges ────────────────────────────────────────────────────────────────

_UPSERT_EDGES_SQL = """
    INSERT INTO graph_edges
        (node_a, node_b, weight)
    VALUES
        (%(node_a)s, %(node_b)s, %(weight)s)
    ON CONFLICT (node_a, node_b) DO UPDATE SET
        weight     = EXCLUDED.weight,
        updated_at = now()
"""


def upsert_graph(
    conn: psycopg.Connection,
    nodes: list[dict],
    edges: list[dict],
) -> tuple[int, int]:
    """Upsert nodes then edges. Returns (node_count, edge_count).

    Node dict: {node_id, x, y, z, node_type}
    Edge dict: {node_a, node_b, weight}
    """
    with conn.cursor() as cur:
        for i in range(0, len(nodes), _BATCH):
            cur.executemany(_UPSERT_NODES_SQL, nodes[i : i + _BATCH])
        for i in range(0, len(edges), _BATCH):
            cur.executemany(_UPSERT_EDGES_SQL, edges[i : i + _BATCH])
    conn.commit()
    logger.debug(
        "upserted %d graph_nodes rows, %d graph_edges rows",
        len(nodes),
        len(edges),
    )
    return len(nodes), len(edges)
