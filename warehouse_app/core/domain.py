# Owns: immutable domain entity definitions and type aliases.
# Must not: import from adapters, services, infrastructure, or config.
# May import: standard library only.

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

# ── Status vocabularies ───────────────────────────────────────────────────────

ItemStatus = Literal["on_order", "in_warehouse", "in_transit", "missing"]
PickStatus = Literal["queued", "assigned", "picked", "staged", "on_truck", "discrepancy"]
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
    carton_w_in:          int | None = None
    carton_h_in:          int | None = None
    carton_d_in:          int | None = None
    gross_weight_lb:      int | None = None
    status:               PickStatus = "queued"
