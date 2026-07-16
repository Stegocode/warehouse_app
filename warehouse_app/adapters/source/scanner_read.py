# Owns: the upstream ERP scanner-API READ client (delivery orders + per-line units) + its fake.
# Must not: import services or core; contain domain logic; read env vars directly.
# May import: requests, dataclasses, urllib, logging, standard library.
#
# The ERP's mobile scanner app reads the day's pick work over the same "/api/scanner/*" HTTP
# API it writes to, authenticated with HTTP Basic. Two reads compose the pick list:
#   day manifest:  GET /api/scanner/delivery/orders?DeliveryDate=YYYY-MM-DD&version=<v>
#                  -> [ {OrderId, ShippingCustomerName, items:[ {OrderItemId, Model, Qty,
#                        Serialized, TruckName, delivery_pickup_type, EstimatedDeliveryDate,
#                        ItemsPicked, ItemsInTransit, ItemsMissing, ...} ]} ]
#   line units:    GET /api/scanner/delivery/serial/order-item/inventory
#                        ?OrderItemId=<id>&PickLocationId=<loc>&version=<v>
#                  -> { allocated:[ {InventoryId, MFGSerialNumber, WHSELocationId_FK,
#                       InventoryStatus, ReceivedDate, ...} ], available:[...] }
#
# This is a plain data client: it returns raw dicts unchanged. Selecting serialized lines,
# computing pick-vs-done, and flagging redelivery are domain decisions and live in services/core.

from __future__ import annotations

import logging
import time
import urllib.parse
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# App-version query string the scanner app sends; server uses it for telemetry only.
DEFAULT_APP_VERSION = "2.31 - 143"

_ORDERS_PATH = "/api/scanner/delivery/orders"
_ORDER_ITEM_INVENTORY_PATH = "/api/scanner/delivery/serial/order-item/inventory"

# Location ids that hold physically pickable inventory (mirrors neon._PICKABLE_LOCATION_IDS):
# 1=WAREHO 2=OUTLET 3=BANGY 4=DAVIS 7=WILLCA 9=BEND. A line's units may sit in more than one,
# so a full pick build queries each and de-dupes by InventoryId.
PICKABLE_LOCATION_IDS = (1, 2, 3, 4, 7, 9)


class ScannerReadError(RuntimeError):
    """A scanner-API read did not succeed. Carries the HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ScannerReadRequest:
    """A fully-formed scanner-API GET — pure data, no I/O.

    Split out so the request shape (path + query) is unit-testable without a live server;
    the send is a thin wrapper over it.
    """

    path: str
    params: dict = field(default_factory=dict)

    def url(self, base_url: str) -> str:
        # quote (not quote_plus) so a space encodes as %20, matching the scanner app exactly.
        query = urllib.parse.urlencode(self.params, quote_via=urllib.parse.quote)
        return f"{base_url.rstrip('/')}{self.path}?{query}"


def build_orders_request(
    delivery_date: str, version: str = DEFAULT_APP_VERSION
) -> ScannerReadRequest:
    """delivery_date is ISO YYYY-MM-DD (the endpoint accepts it verbatim)."""
    return ScannerReadRequest(
        path=_ORDERS_PATH,
        params={"DeliveryDate": delivery_date, "version": version},
    )


def build_order_item_inventory_request(
    order_item_id: int, pick_location_id: int, version: str = DEFAULT_APP_VERSION
) -> ScannerReadRequest:
    return ScannerReadRequest(
        path=_ORDER_ITEM_INVENTORY_PATH,
        params={
            "OrderItemId": order_item_id,
            "PickLocationId": pick_location_id,
            "version": version,
        },
    )


class HttpScannerReader:
    """Live scanner-API read adapter. HTTP Basic with the configured source credentials.

    Unlike the write adapter (which injects each *operator's* credentials per call so the
    ERP audit trail records who picked), a read is a system operation and uses the single
    configured source login, injected once at construction.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        version: str = DEFAULT_APP_VERSION,
        timeout: int = 60,
        max_retries: int = 4,
        backoff: float = 0.75,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._version = version
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff

    def _get(self, req: ScannerReadRequest) -> object:
        if not self._username or not self._password:
            # Fail closed: an unauthenticated read returns nothing useful and would let an
            # empty queue look like "no work today" rather than "not configured".
            raise ScannerReadError("Source ERP credentials are required for a scanner read.")
        url = req.url(self._base_url)
        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.get(
                    url,
                    auth=(self._username, self._password),
                    headers={"Accept": "application/json", "User-Agent": "PocketScan/143"},
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise ScannerReadError(f"Scanner read failed to reach the ERP: {exc!r}") from exc

            # The ERP rate-limits bursts (429). Back off and retry rather than dropping the
            # unit — a dropped unit would silently shrink the pick list, which is exactly the
            # kind of quiet miss Rule 4 forbids.
            if resp.status_code == 429 and attempt < self._max_retries:
                time.sleep(self._backoff * (2 ** attempt))
                continue

            if resp.status_code != 200:
                raise ScannerReadError(
                    f"Scanner read {req.path} returned HTTP {resp.status_code}: {resp.text[:300]}",
                    status=resp.status_code,
                )
            try:
                return resp.json()
            except ValueError as exc:
                raise ScannerReadError(
                    f"Scanner read {req.path} returned non-JSON on 200."
                ) from exc
        raise ScannerReadError(
            f"Scanner read {req.path} still rate-limited (429) after {self._max_retries} retries.",
            status=429,
        )

    def fetch_delivery_orders(self, delivery_date: str) -> list[dict]:
        """The day's orders, each with an ``items`` list. Raw dicts, unfiltered."""
        data = self._get(build_orders_request(delivery_date, self._version))
        if not isinstance(data, list):
            raise ScannerReadError(
                f"delivery/orders expected a JSON array, got {type(data).__name__}"
            )
        logger.info(
            "scanner.fetch_delivery_orders(%s) -> %d order(s)", delivery_date, len(data)
        )
        return data

    def fetch_order_item_units(self, order_item_id: int, pick_location_id: int) -> dict:
        """Units for one order line at one pick location: ``{allocated:[...], available:[...]}``."""
        data = self._get(
            build_order_item_inventory_request(order_item_id, pick_location_id, self._version)
        )
        if not isinstance(data, dict):
            raise ScannerReadError(
                f"order-item/inventory expected a JSON object, got {type(data).__name__}"
            )
        return data


class FakeScannerReader:
    """In-memory scanner reader for tests and offline dev.

    Seed it with a per-date order list and per-(order_item_id, pick_location_id) unit
    payloads; it returns them verbatim and records every call, so the builder can be
    exercised end-to-end without a live server.
    """

    def __init__(
        self,
        orders: dict[str, list[dict]] | None = None,
        units: dict[tuple[int, int], dict] | None = None,
    ) -> None:
        self._orders = orders or {}
        self._units = units or {}
        self.calls: list[tuple] = []

    def fetch_delivery_orders(self, delivery_date: str) -> list[dict]:
        self.calls.append(("fetch_delivery_orders", delivery_date))
        return self._orders.get(delivery_date, [])

    def fetch_order_item_units(self, order_item_id: int, pick_location_id: int) -> dict:
        self.calls.append(("fetch_order_item_units", order_item_id, pick_location_id))
        return self._units.get(
            (order_item_id, pick_location_id), {"allocated": [], "available": []}
        )
