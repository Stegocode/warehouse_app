# Owns: SourcePort protocol — the contract any source system adapter must satisfy.
# Must not: import from services or core.
# May import: standard library, typing.

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SourcePort(Protocol):
    """Port for reading inventory and schedule data from the upstream ERP system."""

    def login(self) -> None:
        """Authenticate. Must be called before any fetch method."""
        ...

    def fetch_inventory(self, limit: int | None = None) -> list[dict]:
        """Return raw inventory records (all active serials)."""
        ...

    def fetch_models(self, limit: int | None = None) -> list[dict]:
        """Return raw model catalog records."""
        ...

    def fetch_route_sheet_pdf(self, delivery_date: str) -> bytes:
        """Return the route-sheet PDF for the given date (ISO format YYYY-MM-DD)."""
        ...


@runtime_checkable
class ScannerWritePort(Protocol):
    """Port for writing item-state changes back to the upstream ERP scanner API.

    Separate from SourcePort (which only reads) because the write path uses a different
    auth model: HTTP Basic with the ACTING OPERATOR's own ERP credentials, injected per
    call, so the ERP records who did what. Credentials are never held on the adapter.
    """

    def mark_in_transit(
        self,
        username: str,
        password: str,
        inventory_id: int,
        order_item_id: int,
        scanned_at: str,
    ) -> dict:
        """Mark an allocated unit in-transit (this business's signal for 'picked')."""
        ...

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
        """Receive an on-order unit into a warehouse bin."""
        ...
