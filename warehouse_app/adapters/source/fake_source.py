# Owns: in-memory source adapter for offline development and unit tests.
# Must not: make network calls or read files.
# May import: warehouse_app.adapters.source.ports, standard library.

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FakeSource:
    """Satisfies SourcePort with canned empty data — enables full offline dev."""

    def login(self) -> None:
        logger.info("[fake-source] login (no-op)")

    def fetch_inventory(self, limit: int | None = None) -> list[dict]:
        logger.info("[fake-source] fetch_inventory → []")
        return []

    def fetch_models(self, limit: int | None = None) -> list[dict]:
        logger.info("[fake-source] fetch_models → []")
        return []
