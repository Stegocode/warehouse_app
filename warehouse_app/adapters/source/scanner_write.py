# Owns: the upstream ERP scanner-API write client (in-transit, receive) + its fake.
# Must not: import services or core; read env vars directly; hold operator credentials.
# May import: warehouse_app.adapters.source.ports, requests, dataclasses, urllib, logging.
#
# The ERP's mobile scanner app performs item-state writes over a small HTTP+JSON API
# ("/api/scanner/*"), authenticated with HTTP Basic using the operator's own ERP login.
# This replaces browser automation entirely: each write is one request. Because the ERP
# stamps every movement with the authenticating user, credentials are the ACTING
# operator's and are injected per call — never stored on the adapter — so the ERP's own
# audit trail stays accurate (who picked, who received).
#
# Request shapes were captured from the live scanner app and verified end-to-end:
#   in-transit:  POST /api/scanner/inventory/intransit/serial?version=<v>
#                {scanned_at, InventoryId, OrderItemId}
#   receive:     PUT  /api/scanner/inventory/receiveserial/<InventoryId>?version=<v>
#                {serial, serial_status, location, WHSELocation}

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# App-version query string the scanner app sends. Not a secret and not machine-specific;
# the server appears to use it only for telemetry. Overridable per adapter instance.
DEFAULT_APP_VERSION = "2.31 - 143"

_INTRANSIT_PATH = "/api/scanner/inventory/intransit/serial"
_RECEIVE_PATH = "/api/scanner/inventory/receiveserial/{inventory_id}"


class ScannerWriteError(RuntimeError):
    """A scanner-API write did not succeed. Carries the HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ScannerRequest:
    """A fully-formed scanner-API request — pure data, no I/O.

    Split out so the request shape (method, path, query, body) is unit-testable without a
    live server: the part that is easy to get subtly wrong is verified in tests, and the
    send is a thin wrapper over it.
    """

    method: str
    path: str
    version: str
    body: dict = field(default_factory=dict)

    def url(self, base_url: str) -> str:
        query = urllib.parse.urlencode({"version": self.version})
        return f"{base_url.rstrip('/')}{self.path}?{query}"


def build_intransit_request(
    inventory_id: int,
    order_item_id: int,
    scanned_at: str,
    version: str = DEFAULT_APP_VERSION,
) -> ScannerRequest:
    return ScannerRequest(
        method="POST",
        path=_INTRANSIT_PATH,
        version=version,
        body={
            "scanned_at": scanned_at,
            "InventoryId": inventory_id,
            "OrderItemId": order_item_id,
        },
    )


def build_receive_request(
    inventory_id: int,
    serial: str,
    location_id: int,
    whse_location_id: int,
    serial_status: str | None = None,
    version: str = DEFAULT_APP_VERSION,
) -> ScannerRequest:
    return ScannerRequest(
        method="PUT",
        path=_RECEIVE_PATH.format(inventory_id=inventory_id),
        version=version,
        body={
            "serial": serial,
            "serial_status": serial_status,
            "location": location_id,
            "WHSELocation": whse_location_id,
        },
    )


class HttpScannerWriter:
    """Live scanner-API write adapter. Stateless: creds are passed to each method."""

    def __init__(
        self,
        base_url: str,
        version: str = DEFAULT_APP_VERSION,
        timeout: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._version = version
        self._timeout = timeout

    def _send(self, req: ScannerRequest, username: str, password: str) -> dict:
        if not username or not password:
            # Fail closed: an unauthenticated write would either be rejected or, worse,
            # succeed under the wrong identity and corrupt the ERP's operator audit trail.
            raise ScannerWriteError("Operator ERP credentials are required for a scanner write.")
        url = req.url(self._base_url)
        try:
            resp = requests.request(
                req.method,
                url,
                json=req.body,
                auth=(username, password),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ScannerWriteError(f"Scanner write failed to reach the ERP: {exc!r}") from exc

        if resp.status_code != 200:
            raise ScannerWriteError(
                f"Scanner write {req.method} {req.path} returned HTTP {resp.status_code}: "
                f"{resp.text[:300]}",
                status=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ScannerWriteError(
                f"Scanner write {req.method} {req.path} returned non-JSON on 200."
            ) from exc

    def mark_in_transit(
        self,
        username: str,
        password: str,
        inventory_id: int,
        order_item_id: int,
        scanned_at: str,
    ) -> dict:
        req = build_intransit_request(inventory_id, order_item_id, scanned_at, self._version)
        result = self._send(req, username, password)
        logger.info(
            "scanner.mark_in_transit: inventory_id=%s order_item_id=%s -> status=%s",
            inventory_id, order_item_id, result.get("InventoryStatus"),
        )
        return result

    def receive_serial(
        self,
        username: str,
        password: str,
        inventory_id: int,
        serial: str,
        location_id: int,
        whse_location_id: int,
        serial_status: str | None = None,
    ) -> dict:
        req = build_receive_request(
            inventory_id, serial, location_id, whse_location_id, serial_status, self._version
        )
        result = self._send(req, username, password)
        logger.info(
            "scanner.receive_serial: inventory_id=%s bin=%s -> status=%s received_date=%s",
            inventory_id, whse_location_id,
            result.get("InventoryStatus"), result.get("ReceivedDate"),
        )
        return result


class FakeScannerWriter:
    """In-memory scanner writer for tests and offline dev.

    Records every call so a test can assert what would have been sent to the ERP, and can
    be told to raise so failure paths are exercised without a live server.
    """

    def __init__(self, raise_on: str | None = None) -> None:
        self.calls: list[tuple] = []
        self._raise_on = raise_on  # "mark_in_transit" | "receive_serial" | None

    def mark_in_transit(
        self,
        username: str,
        password: str,
        inventory_id: int,
        order_item_id: int,
        scanned_at: str,
    ) -> dict:
        self.calls.append(
            ("mark_in_transit", username, inventory_id, order_item_id, scanned_at)
        )
        if self._raise_on == "mark_in_transit":
            raise ScannerWriteError("FakeScannerWriter configured to raise on mark_in_transit")
        return {"InventoryId": inventory_id, "InventoryStatus": 3}

    def receive_serial(
        self,
        username: str,
        password: str,
        inventory_id: int,
        serial: str,
        location_id: int,
        whse_location_id: int,
        serial_status: str | None = None,
    ) -> dict:
        self.calls.append(
            ("receive_serial", username, inventory_id, serial, location_id, whse_location_id)
        )
        if self._raise_on == "receive_serial":
            raise ScannerWriteError("FakeScannerWriter configured to raise on receive_serial")
        return {
            "InventoryId": inventory_id,
            "InventoryStatus": 1,
            "WHSELocationId_FK": whse_location_id,
        }
