# Owns: immutable domain entity definitions and type aliases.
# Must not: import from adapters, services, infrastructure, or config.
# May import: standard library only.

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

# ── Status vocabularies ───────────────────────────────────────────────────────

ItemStatus = Literal["on_order", "in_warehouse", "in_transit", "missing"]

# Pick lifecycle: queued -> assigned -> picked -> in_transit
#   picked      the human physically moved the box (set immediately on confirm)
#   in_transit  the ERP agrees (set only once the ERP write lands; erp_confirmed=TRUE)
# A 'picked' row with erp_confirmed = FALSE is the pending-ERP-write queue.
PickStatus = Literal[
    "queued", "assigned", "picked", "in_transit", "staged", "on_truck", "discrepancy",
]
TicketAction = Literal["pick", "stage", "move", "receive", "relocate"]
TicketStatus = Literal["open", "in_progress", "confirmed", "cancelled"]
ProductClass = Literal[
    "BULK", "SMALL", "FREESTANDING_REFER", "BUILTIN_REFER",
    "LARGE_RANGE", "SLIDEIN_RANGE", "LAUNDRY", "DISHWASHER", "OTHER",
]


# ── Core entities ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelRecord:
    model_number:     str
    manufacturer:     str | None
    description:      str | None
    category:         str | None
    product_class:    ProductClass | None
    width_in:         float | None
    height_in:        float | None
    depth_in:         float | None
    carton_w_in:      int | None
    carton_h_in:      int | None
    carton_d_in:      int | None
    gross_weight_lb:  int | None
    size_tier:        int | None
    source_product_id: int | None = None


@dataclass(frozen=True)
class InventoryItem:
    source_inventory_id:  int
    model_number:         str
    status:               ItemStatus
    source_location_id:   int
    source_whse_location: str | None
    source_order_id:      int | None
    source_order_item_id: int | None
    is_allocated:         bool
    serial_number:        str | None = None


@dataclass(frozen=True)
class WarehouseBin:
    whse_location: str
    row_token:     str
    bay:           int
    level:         int
    height_m:      float


@dataclass(frozen=True)
class DeliveryStop:
    stop_id:         str
    delivery_date:   date
    truck_id:        str
    stop_order:      int | None
    source_order_id: int | None
    customer_name:   str | None
    sink_item_id:    str | None
    sink_board_id:   str | None = None
    sink_status:     str | None = None
    delivery_notes:  str | None = None


@dataclass(frozen=True)
class PickAssignment:
    """One claimed pick, with everything the picker needs on screen.

    Assembled by joining pick_queue to inventory_items (manufacturer, serial, photo) and
    delivery_stops (customer). pick_queue itself stores none of that — it is the work
    list, not the product catalogue.
    """

    pick_id:             str
    delivery_date:       date
    truck_id:            str
    stop_order:          int | None
    piece_order:         int
    pieces_at_stop:      int
    model_number:        str
    whse_location:       str | None
    status:              PickStatus
    assigned_to:         str | None
    manufacturer:        str | None = None
    short_description:   str | None = None
    image_url:           str | None = None
    serial_number:       str | None = None
    customer_name:       str | None = None


@dataclass(frozen=True)
class PickProgress:
    """Queue state for a delivery date — shared by every picker on the floor."""

    delivery_date: date
    queued:        int
    assigned:      int
    picked:        int
    in_transit:    int
    other:         int

    @property
    def total(self) -> int:
        return self.queued + self.assigned + self.picked + self.in_transit + self.other

    @property
    def done(self) -> int:
        return self.picked + self.in_transit


@dataclass(frozen=True)
class PickRow:
    stop_id:              str
    source_inventory_id:  int | None
    source_order_item_id: int | None
    delivery_date:        date
    truck_id:             str
    stop_order:           int | None
    piece_order:          int
    model_number:         str
    whse_location:        str | None
    # Dense rank of the truck within the pick order (0 = picked first). Persisted because
    # owned-fleet-first is a config rule (OWNED_FLEET_TRUCKS), not something a truck label
    # encodes — so no ORDER BY over truck_id can reproduce it except by coincidence.
    truck_sort_order:     int | None = None
    carton_w_in:          int | None = None
    carton_h_in:          int | None = None
    carton_d_in:          int | None = None
    gross_weight_lb:      int | None = None
    status:               PickStatus = "queued"
