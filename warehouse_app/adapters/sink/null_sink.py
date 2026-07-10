# Owns: no-op sink adapter — used when SINK_TYPE=null or sink is unavailable.
# Must not: make network calls.
# May import: warehouse_app.adapters.sink.ports, standard library.

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class NullSink:
    """Satisfies SinkPort with no-ops — useful for local dev without board access."""

    def fetch_board_items(self, delivery_date: str) -> dict[str, dict]:
        logger.info("[null-sink] fetch_board_items(%s) → {}", delivery_date)
        return {}

    def update_item_status(self, sink_item_id: str, status: str) -> None:
        logger.info("[null-sink] update_item_status(%s, %s) (no-op)", sink_item_id, status)
