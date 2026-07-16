"""Tests for adapters/source/scanner_read.py — request shapes and the fake, no I/O.

The live HttpScannerReader's HTTP round-trip is not unit-tested (that needs a server);
what IS testable without one is the request shape it builds and its fail-closed behaviour,
plus the fake that lets the builder run offline.
"""
import pytest

from warehouse_app.adapters.source.scanner_read import (
    DEFAULT_APP_VERSION,
    FakeScannerReader,
    HttpScannerReader,
    ScannerReadError,
    build_order_item_inventory_request,
    build_orders_request,
)


class TestRequestShape:
    def test_orders_request_path_and_params(self):
        req = build_orders_request("2026-07-16")
        assert req.path == "/api/scanner/delivery/orders"
        assert req.params["DeliveryDate"] == "2026-07-16"
        assert req.params["version"] == DEFAULT_APP_VERSION

    def test_order_item_inventory_request_params(self):
        req = build_order_item_inventory_request(152081, 1)
        assert req.path == "/api/scanner/delivery/serial/order-item/inventory"
        assert req.params["OrderItemId"] == 152081
        assert req.params["PickLocationId"] == 1

    def test_url_encodes_space_as_percent20(self):
        # The scanner app sends version "2.31 - 143" as %20, not '+'; match it exactly.
        req = build_orders_request("2026-07-16")
        url = req.url("https://erp.example.com/")
        assert url.startswith("https://erp.example.com/api/scanner/delivery/orders?")
        assert "version=2.31%20-%20143" in url
        assert "+" not in url


class TestFakeScannerReader:
    def test_returns_seeded_orders_and_records_call(self):
        orders = {"2026-07-16": [{"OrderId": 18808, "items": []}]}
        reader = FakeScannerReader(orders=orders)
        assert reader.fetch_delivery_orders("2026-07-16")[0]["OrderId"] == 18808
        assert reader.calls == [("fetch_delivery_orders", "2026-07-16")]

    def test_unknown_date_returns_empty_list(self):
        assert FakeScannerReader().fetch_delivery_orders("2026-01-01") == []

    def test_returns_seeded_units_and_defaults_empty(self):
        units = {(152081, 1): {"allocated": [{"InventoryId": 131422}], "available": []}}
        reader = FakeScannerReader(units=units)
        assert reader.fetch_order_item_units(152081, 1)["allocated"][0]["InventoryId"] == 131422
        # unseeded (order_item, location) yields the empty shape, never a KeyError
        assert reader.fetch_order_item_units(999, 9) == {"allocated": [], "available": []}


class TestHttpScannerReaderFailsClosed:
    def test_missing_credentials_raises_before_any_request(self):
        reader = HttpScannerReader("https://erp.example.com", username="", password="")
        with pytest.raises(ScannerReadError):
            reader.fetch_delivery_orders("2026-07-16")
