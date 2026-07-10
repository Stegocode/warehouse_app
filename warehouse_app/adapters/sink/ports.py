# Owns: SinkPort protocol — the contract any notification/board adapter must satisfy.
# Must not: import from services or core.
# May import: standard library, typing.

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SinkPort(Protocol):
    """Port for reading and writing delivery annotations on the notification board."""

    def fetch_board_items(self, delivery_date: str) -> dict[str, dict]:
        """Return {order_number: item_dict} for items whose delivery date matches.

        order_number is a string (from the board item name prefix).
        item_dict keys: sink_item_id, sink_board_id, sink_status,
                        customer_name, delivery_notes, sink_time_window,
                        sink_delivery_type.
        """
        ...

    def update_item_status(self, sink_item_id: str, status: str) -> None:
        """Write a status update back to the sink board."""
        ...
