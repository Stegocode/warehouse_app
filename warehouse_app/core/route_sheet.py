"""Route sheet text parser — pure logic layer.

# Owns: pure text parsing of route sheet PDF pages into structured stop data.
# Must not: open files, call pdfplumber, import from adapters/services/infrastructure.
# May import: standard library only.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Fleet-specific raw-label aliases are injected at runtime via config;
# they do not live here.  See norm_truck()'s `aliases` parameter.

# Matches a table-body stop line: "<stop#>  <order#>  ..."
_STOP_RE = re.compile(r"^\s*(\d+)\s+(\d{4,6})\s+.+")

# Matches the page counter that appears in route-sheet page footers.
_PAGE_MARKER_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)


def norm_truck(raw: str, aliases: dict[str, str] | None = None) -> str:
    """Normalise a raw truck label from the route sheet into a clean truck ID.

    Args:
        raw:     The label string extracted from the top of a PDF page.
        aliases: Optional mapping of raw labels to canonical IDs.  Callers
                 supply fleet-specific mappings; this function contains only
                 generic normalisation rules.

    Returns:
        A normalised truck ID string (e.g. ``"HUB 03"``, ``"62"``).
    """
    t = (raw or "").strip()
    if aliases and t in aliases:
        return aliases[t]
    m = re.match(r"^HUB\s*#?\s*(\d+)$", t, re.I)
    if m:
        return "HUB " + m.group(1).zfill(2)
    return t.upper()


def parse_route_pages(
    pages: list[list[str]],
    aliases: dict[str, str] | None = None,
) -> dict[str, list[tuple[int, str]]]:
    """Parse route-sheet PDF pages into a per-truck stop list.

    Args:
        pages:   List of pages.  Each page is a list of stripped text lines
                 (as produced by splitting ``page.extract_text()`` on newlines
                 and filtering blanks).  PDF extraction lives in the service
                 layer; this function only receives the already-extracted strings.
        aliases: Optional fleet-specific truck-label aliases forwarded to
                 :func:`norm_truck`.

    Returns:
        Mapping ``{truck_id: [(stop_order, order_id_str), ...]}``.  The lists
        are in document order, not sorted; callers sort as needed.
    """
    stops: dict[str, list[tuple[int, str]]] = defaultdict(list)
    current_truck: str | None = None
    current_page = 1
    current_total = 1

    for lines in pages:
        if not lines:
            continue

        # Multi-page trucks: if still inside a continued page block, the first
        # line is NOT a new truck header.
        is_continuation = current_truck is not None and current_page < current_total
        if not is_continuation:
            current_truck = norm_truck(lines[0], aliases=aliases)

        # Reset page counter, then scan backwards for the page-footer marker.
        current_page, current_total = 1, 1
        for line in reversed(lines):
            m = _PAGE_MARKER_RE.search(line)
            if m:
                current_page = int(m.group(1))
                current_total = int(m.group(2))
                break

        # Walk the page looking for the column-header row, then collect stops.
        in_table = False
        for line in lines:
            if ("# Order" in line and "Customer" in line) or line.lower().startswith("# order"):
                in_table = True
                continue
            if not in_table:
                continue
            m = _STOP_RE.match(line)
            if not m:
                continue
            stops[current_truck].append((int(m.group(1)), m.group(2)))

    return dict(stops)
