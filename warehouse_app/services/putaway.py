"""Service: choose where to put away a received unit.

# ── Boundary ──────────────────────────────────────────────────────────────────
# Owns: assemble the void finder's inputs from the database and return ranked bins.
# Owns     : gather graph + candidate bins + occupancy + reference pick + dims,
#            delegate the ranking to core.void_finder, return the result
# Must not : contain routing/scoring logic; contain SQL
# May use  : warehouse_app.core.void_finder, warehouse_app.adapters.db.routing_db,
#            psycopg, datetime, logging
# Out of scope: writing the chosen bin anywhere (that is the receive/assignment step);
#               mapping whse_location -> the ERP's WHSELocationId (a separate concern)
# ─────────────────────────────────────────────────────────────────────────────

This is the entry point a receiving flow calls to answer "where should this go?". It
returns ranked bins; it does not commit a choice. Turning the top bin into an ERP receive
write (which needs the numeric WHSELocationId, not the bin string) is deferred — see
DEBT-ROUTE-001.
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg

from warehouse_app.adapters.db import routing_db
from warehouse_app.core.void_finder import find_void

logger = logging.getLogger(__name__)


def rank_putaway_bins(
    conn: psycopg.Connection,
    model_number: str,
    delivery_date: date,
    limit: int = 5,
) -> list[dict]:
    """Return open bins ranked by travel distance to the date's next pick, closest first.

    Empty when there is no queued pick with a location to reference against (nothing to be
    near yet), or when no eligible bin is open, fits, and is reachable. The caller decides
    what to do with an empty result — this service never guesses a bin.
    """
    reference = routing_db.fetch_next_pick_bin(conn, delivery_date)
    if reference is None:
        logger.info(
            "rank_putaway_bins: no queued pick with a location for %s — no reference point; "
            "returning no ranking", delivery_date,
        )
        return []

    dims = routing_db.fetch_product_dims(conn, model_number)
    voids = routing_db.fetch_candidate_voids(conn)
    occupied = routing_db.fetch_occupied_locations(conn)
    nodes, edges = routing_db.fetch_graph(conn)

    ranked = find_void(dims, reference, voids, occupied, nodes, edges, limit=limit)

    logger.info(
        "rank_putaway_bins: model=%s date=%s reference=%s -> %d candidate bin(s)%s",
        model_number, delivery_date, reference["whse_location"], len(ranked),
        f" (top={ranked[0]['whse_location']})" if ranked else "",
    )
    return ranked
