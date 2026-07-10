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
