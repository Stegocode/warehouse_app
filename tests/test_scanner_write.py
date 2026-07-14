"""Tests for adapters/source/scanner_write.py.

The request builders are pure and carry the shapes captured from the live scanner app —
getting a field name or the method wrong is exactly the bug a live server would surface
late, so they are asserted here. The HTTP send is exercised through an injected fake
transport (no network): shape out, response in. FakeScannerWriter covers what services
inject.
"""
from __future__ import annotations

import pytest

from warehouse_app.adapters.source import scanner_write as sw
from warehouse_app.adapters.source.ports import ScannerWritePort


class TestBuildIntransitRequest:
    def test_matches_the_captured_shape(self):
        req = sw.build_intransit_request(
            inventory_id=133854, order_item_id=141551, scanned_at="2026-07-14 14:07:21"
        )
        assert req.method == "POST"
        assert req.path == "/api/scanner/inventory/intransit/serial"
        assert req.body == {
            "scanned_at": "2026-07-14 14:07:21",
            "InventoryId": 133854,
            "OrderItemId": 141551,
        }

    def test_url_carries_the_version_query(self):
        req = sw.build_intransit_request(1, 2, "t", version="9.9 - 1")
        url = req.url("https://erp.example.com/")
        assert url == (
            "https://erp.example.com/api/scanner/inventory/intransit/serial?version=9.9+-+1"
        )


class TestBuildReceiveRequest:
    def test_matches_the_captured_shape(self):
        req = sw.build_receive_request(
            inventory_id=137877, serial="137877", location_id=1, whse_location_id=964
        )
        assert req.method == "PUT"
        # The InventoryId is in the PATH, not the body — that is how the live API addresses it.
        assert req.path == "/api/scanner/inventory/receiveserial/137877"
        assert req.body == {
            "serial": "137877",
            "serial_status": None,
            "location": 1,
            "WHSELocation": 964,
        }

    def test_serial_status_is_passed_through(self):
        req = sw.build_receive_request(1, "SN", 1, 2, serial_status="ok")
        assert req.body["serial_status"] == "ok"


class TestHttpScannerWriterSend:
    def _writer_with_transport(self, monkeypatch, captured, response):
        """Patch requests.request so _send runs without a network, capturing the call."""
        class _Resp:
            status_code = response.get("status", 200)
            text = response.get("text", "{}")

            @staticmethod
            def json():
                return response.get("json", {})

        def _fake_request(method, url, **kwargs):
            captured.update({"method": method, "url": url, **kwargs})
            return _Resp()

        monkeypatch.setattr(sw.requests, "request", _fake_request)
        return sw.HttpScannerWriter("https://erp.example.com")

    def test_mark_in_transit_sends_basic_auth_and_body(self, monkeypatch):
        captured: dict = {}
        writer = self._writer_with_transport(
            monkeypatch, captured, {"json": {"InventoryStatus": 3}}
        )
        result = writer.mark_in_transit("picker@x", "pw", 133854, 141551, "2026-07-14 14:07:21")

        assert captured["method"] == "POST"
        assert captured["auth"] == ("picker@x", "pw")   # per-operator, HTTP Basic
        assert captured["json"]["InventoryId"] == 133854
        assert result["InventoryStatus"] == 3

    def test_receive_serial_uses_put_and_path_id(self, monkeypatch):
        captured: dict = {}
        writer = self._writer_with_transport(
            monkeypatch, captured, {"json": {"InventoryStatus": 1}}
        )
        writer.receive_serial("op@x", "pw", 137877, "SN-9", 1, 964)

        assert captured["method"] == "PUT"
        assert captured["url"].startswith(
            "https://erp.example.com/api/scanner/inventory/receiveserial/137877"
        )

    def test_missing_credentials_fails_closed_before_any_request(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(
            sw.requests, "request",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        writer = sw.HttpScannerWriter("https://erp.example.com")
        with pytest.raises(sw.ScannerWriteError):
            writer.mark_in_transit("", "", 1, 2, "t")
        assert called["n"] == 0, "must not hit the network without operator credentials"

    def test_non_200_raises_with_status(self, monkeypatch):
        captured: dict = {}
        writer = self._writer_with_transport(
            monkeypatch, captured, {"status": 409, "text": "conflict"}
        )
        with pytest.raises(sw.ScannerWriteError) as exc:
            writer.mark_in_transit("op@x", "pw", 1, 2, "t")
        assert exc.value.status == 409


class TestFakeScannerWriter:
    def test_satisfies_the_port(self):
        assert isinstance(sw.FakeScannerWriter(), ScannerWritePort)
        assert isinstance(sw.HttpScannerWriter("https://x"), ScannerWritePort)

    def test_records_calls(self):
        fake = sw.FakeScannerWriter()
        fake.mark_in_transit("op", "pw", 10, 20, "t")
        fake.receive_serial("op", "pw", 30, "SN", 1, 964)
        assert fake.calls[0][0] == "mark_in_transit"
        assert fake.calls[1][0] == "receive_serial"
        assert fake.calls[1][2] == 30

    def test_can_be_told_to_raise(self):
        fake = sw.FakeScannerWriter(raise_on="receive_serial")
        fake.mark_in_transit("op", "pw", 1, 2, "t")  # unaffected
        with pytest.raises(sw.ScannerWriteError):
            fake.receive_serial("op", "pw", 1, "SN", 1, 2)
