"""Tests for core/route_sheet.py — pure parsing logic, no I/O."""
from warehouse_app.core.route_sheet import norm_truck, parse_route_pages


# ── norm_truck ────────────────────────────────────────────────────────────────

class TestNormTruck:
    def test_hub_number_normalised(self):
        assert norm_truck("HUB 1") == "HUB 01"
        assert norm_truck("HUB 01") == "HUB 01"
        assert norm_truck("hub#3") == "HUB 03"
        assert norm_truck("HUB  12") == "HUB 12"

    def test_plain_number_uppercased(self):
        assert norm_truck("68") == "68"
        assert norm_truck("basco") == "BASCO"

    def test_alias_takes_priority(self):
        aliases = {"Truck A": "OWN-01", "HUB 1": "OWN-02"}
        assert norm_truck("Truck A", aliases=aliases) == "OWN-01"
        assert norm_truck("HUB 1", aliases=aliases) == "OWN-02"

    def test_empty_string(self):
        assert norm_truck("") == ""
        assert norm_truck(None) == ""  # type: ignore[arg-type]

    def test_whitespace_stripped(self):
        assert norm_truck("  68  ") == "68"


# ── parse_route_pages ─────────────────────────────────────────────────────────

def _make_page(truck_label: str, stops: list[tuple[int, str]], page=1, total=1) -> list[str]:
    """Build a minimal route-sheet page as a list of text lines."""
    lines = [
        truck_label,
        "# Order    Customer              Address",
    ]
    for stop_num, order_id in stops:
        lines.append(f"  {stop_num}  {order_id}  A Customer Name   123 Somewhere St")
    lines.append(f"Page {page} of {total}")
    return lines


class TestParseRoutePages:
    def test_single_truck_two_stops(self):
        pages = [_make_page("TRUCK 01", [(1, "11111"), (2, "22222")])]
        result = parse_route_pages(pages)
        assert result == {"TRUCK 01": [(1, "11111"), (2, "22222")]}

    def test_two_trucks_separate_pages(self):
        pages = [
            _make_page("TRUCK 01", [(1, "10001")]),
            _make_page("TRUCK 02", [(1, "20001"), (2, "20002")]),
        ]
        result = parse_route_pages(pages)
        assert result["TRUCK 01"] == [(1, "10001")]
        assert result["TRUCK 02"] == [(1, "20001"), (2, "20002")]

    def test_hub_label_normalised_in_output(self):
        pages = [_make_page("HUB 1", [(1, "99001")])]
        result = parse_route_pages(pages)
        assert "HUB 01" in result

    def test_multipage_truck_continuation(self):
        page1 = _make_page("TRUCK 01", [(1, "11111"), (2, "22222")], page=1, total=2)
        page2 = _make_page("TRUCK 01", [(3, "33333")], page=2, total=2)
        result = parse_route_pages([page1, page2])
        assert result["TRUCK 01"] == [(1, "11111"), (2, "22222"), (3, "33333")]

    def test_empty_pages_ignored(self):
        pages = [[], _make_page("TRUCK 01", [(1, "11111")])]
        result = parse_route_pages(pages)
        assert "TRUCK 01" in result

    def test_aliases_forwarded(self):
        # alias keys match the raw string exactly (case-sensitive, pre-normalisation)
        pages = [_make_page("Raw Label", [(1, "55555")])]
        result = parse_route_pages(pages, aliases={"Raw Label": "FLEET-A"})
        assert "FLEET-A" in result
