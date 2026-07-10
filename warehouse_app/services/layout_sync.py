# ---------------------------------------------------------------------------
# BOUNDARY: warehouse_app.services.layout_sync
# Owns: orchestrate layout JSON → upsert warehouse bins + graph.
#
# Owns   : Orchestrate layout JSON → upsert warehouse bins + graph.
# Accepts: A parsed layout JSON dict (caller reads the file path).
# Must not: Contain SQL; open files directly (accepts a pre-parsed dict via
#           the public API, though run() handles path → dict for convenience).
# May import: warehouse_app.core.layout, warehouse_app.adapters.db.layout_db,
#             psycopg, json, logging, pathlib.
# Out of scope: Schema migration, authentication, graph routing.
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
from pathlib import Path

import psycopg

from warehouse_app.adapters.db import layout_db
from warehouse_app.core import layout as layout_core

log = logging.getLogger(__name__)


def run(
    conn: psycopg.Connection,
    layout_path: str,
    dry_run: bool = False,
) -> dict:
    """Load layout JSON, extract bins/nodes/edges, upsert to DB.

    Parameters
    ----------
    conn:
        Open psycopg connection; the caller owns the transaction boundary.
    layout_path:
        Absolute or relative path to the warehouse_layout.json file.
    dry_run:
        When True, extracts and returns counts but skips all DB writes.

    Returns
    -------
    dict with keys ``bins``, ``nodes``, ``edges`` (int counts).
    """
    log.info("layout_sync.run started path=%s dry_run=%s", layout_path, dry_run)

    layout = json.loads(Path(layout_path).read_text(encoding="utf-8"))

    bins = layout_core.extract_bins(layout)
    nodes = layout_core.extract_nodes(layout)
    edges = layout_core.extract_edges(layout)

    log.info(
        "layout_sync extracted bins=%d nodes=%d edges=%d",
        len(bins),
        len(nodes),
        len(edges),
    )

    if not dry_run:
        layout_db.upsert_bins(conn, bins)
        layout_db.upsert_graph(conn, nodes, edges)
        log.info("layout_sync upserted bins and graph")
    else:
        log.info("layout_sync dry_run=True — DB writes skipped")

    return {"bins": len(bins), "nodes": len(nodes), "edges": len(edges)}
