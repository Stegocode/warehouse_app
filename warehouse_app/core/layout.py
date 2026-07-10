# Owns: pure parsing of warehouse layout JSON into bin, node, and edge records.
# Must not: open files, call I/O, import from adapters/services/infrastructure.
# May import: warehouse_app.core.classify (for tier_from_bin_height_m), standard library.

from __future__ import annotations

from warehouse_app.core.classify import tier_from_bin_height_m


def build_bin_height_lookup(layout: dict) -> dict[str, tuple[str, float]]:
    """Return {whse_location -> (row_token, level_height_m)} from a layout dict.

    Applies bayLevelOverrides when a bay has non-default levelHeights.
    The layout dict must be the full JSON export from warehouse_layout_editor
    (schemaVersion 5 db-connect format), containing 'racks' and 'bins' arrays.
    """
    # Index racks by both their id (e.g. 'ROW-C') and rowToken (e.g. 'C') so the
    # lookup tolerates whichever form the bin's 'row' field carries.
    rack_index: dict[str, dict] = {}
    for rack in layout.get("racks", []):
        if rack.get("id"):
            rack_index[rack["id"].upper()] = rack
        if rack.get("rowToken"):
            rack_index[rack["rowToken"].upper()] = rack

    lookup: dict[str, tuple[str, float]] = {}
    for bin_entry in layout.get("bins", []):
        loc = bin_entry.get("whse_location", "")
        raw_row = str(bin_entry.get("row", "")).upper()
        bay = int(bin_entry.get("bay", 0))
        level = int(bin_entry.get("level", 0))

        if not loc or not raw_row or bay == 0 or level == 0:
            continue
        rack = rack_index.get(raw_row)
        if rack is None:
            continue

        # Normalise to rowToken for consistent output (strip 'ROW-' prefix if needed)
        row_token = rack.get("rowToken", raw_row).upper()

        overrides = rack.get("bayLevelOverrides") or {}
        bay_override = overrides.get(str(bay))
        if bay_override and "levelHeights" in bay_override:
            heights = bay_override["levelHeights"]
        else:
            heights = rack.get("levelHeights", [])

        idx = level - 1
        if idx < 0 or idx >= len(heights):
            continue

        lookup[loc] = (row_token, float(heights[idx]))

    return lookup


def extract_bins(layout: dict) -> list[dict]:
    """Return one dict per bin with columns for the warehouse_bins table.

    Calls build_bin_height_lookup() internally to resolve each bin's slot
    height, then derives size_tier via tier_from_bin_height_m().

    Output keys per row: whse_location, row_token, bay, level, height_m, size_tier.
    Bins absent from the height lookup (missing rack, out-of-range level, etc.)
    are silently skipped — they cannot be routed without a known slot height.
    """
    height_lookup = build_bin_height_lookup(layout)

    rows: list[dict] = []
    for bin_entry in layout.get("bins", []):
        loc = bin_entry.get("whse_location", "")
        if loc not in height_lookup:
            continue

        row_token, height_m = height_lookup[loc]
        rows.append({
            "whse_location": loc,
            "row_token":     row_token,
            "bay":           int(bin_entry.get("bay", 0)),
            "level":         int(bin_entry.get("level", 0)),
            "height_m":      height_m,
            "size_tier":     tier_from_bin_height_m(height_m),
        })

    return rows


def extract_nodes(layout: dict) -> list[dict]:
    """Return one dict per graph node with columns for the graph_nodes table.

    Output keys per row: node_id, kind, x, y, zone.
    """
    rows: list[dict] = []
    for node in layout.get("nodes", []):
        rows.append({
            "node_id": node["id"],
            "kind":    node["kind"],
            "x":       float(node["x"]),
            "y":       float(node["y"]),
            "zone":    node.get("zone") or None,
        })
    return rows


def extract_edges(layout: dict) -> list[dict]:
    """Return deduplicated edge dicts with node_a = min(a, b), node_b = max(a, b).

    Both (A, B) and (B, A) in the source JSON collapse to a single row with
    node_a < node_b, so the caller can upsert on (node_a, node_b) safely.

    Output keys per row: node_a, node_b, distance_m, ramp.
    """
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for edge in layout.get("edges", []):
        node_a = min(edge["a"], edge["b"])
        node_b = max(edge["a"], edge["b"])
        key = (node_a, node_b)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "node_a":      node_a,
            "node_b":      node_b,
            "distance_m":  float(edge["distance_m"]),
            "ramp":        bool(edge.get("ramp", False)),
        })
    return rows
