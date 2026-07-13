# Owns: pure pick-order logic — truck grouping and piece sequencing.
# Must not: import from adapters, services, infrastructure, or config.
# May import: warehouse_app.core.domain, standard library only.

from __future__ import annotations

from datetime import date

from warehouse_app.core.domain import DeliveryStop, InventoryItem, PickRow

def truck_sort_key(truck_id: str, owned_trucks: frozenset[str]) -> tuple[int, str, int]:
    """Owned fleet trucks sort before third-party carriers.

    Within each group trucks sort by their label (numeric labels zero-padded).
    """
    if truck_id in owned_trucks:
        return (0, truck_id.zfill(4), 0)
    return (1, truck_id, 0)


def assign_truck_ranks(
    truck_ids: list[str],
    owned_trucks: frozenset[str],
) -> dict[str, int]:
    """Map each truck to a dense rank (0 = picked first), using truck_sort_key.

    This is the sort order made durable. build_pick_order applies truck_sort_key in
    memory, but nothing about that survives the write, and a SQL table has no inherent
    row order — so the claim query must re-derive the order from a column.

    It cannot do that from truck_id. Which trucks are owned is *configuration*
    (OWNED_FLEET_TRUCKS), not something the label spells out, so no ORDER BY over
    truck_id can express the rule. Any correct-looking result from sorting on truck_id
    is a coincidence of how the current labels happen to sort, and it fails as soon as
    a third-party label sorts before an owned one ('ACME' before 'FLEET') or numeric
    labels of unequal length are compared ('10' before '5'). Persisting the rank makes
    the order explicit and independent of how anyone spells a truck.
    """
    distinct = sorted(set(truck_ids), key=lambda t: truck_sort_key(t, owned_trucks))
    return {truck_id: rank for rank, truck_id in enumerate(distinct)}


def build_pick_order(
    delivery_date: date,
    stops: list[DeliveryStop],
    inventory_by_order: dict[int, list[InventoryItem]],
    owned_trucks: frozenset[str],
) -> list[PickRow]:
    """Pure function: order stops, assign piece numbers, return PickRow list.

    Pick order:
      1. Owned fleet trucks — by stop_order ascending (stop 1 staged first).
      2. Third-party carrier trucks — by truck_id then stop_order.

    Within each stop, items are ordered by model_number then source_inventory_id
    (stable across re-runs; easy to override once routing determines optimal sequence).
    """
    def _stop_key(s: DeliveryStop) -> tuple:
        base = truck_sort_key(s.truck_id, owned_trucks)
        return (base[0], base[1], s.stop_order if s.stop_order is not None else 9999)

    ranks = assign_truck_ranks([s.truck_id for s in stops], owned_trucks)

    rows: list[PickRow] = []
    for stop in sorted(stops, key=_stop_key):
        if stop.source_order_id is None:
            continue
        items = sorted(
            inventory_by_order.get(stop.source_order_id, []),
            key=lambda i: (i.model_number, i.source_inventory_id or 0),
        )
        for piece_idx, item in enumerate(items, start=1):
            rows.append(PickRow(
                stop_id=stop.stop_id,
                source_inventory_id=item.source_inventory_id,
                source_order_item_id=item.source_order_item_id,
                delivery_date=delivery_date,
                truck_id=stop.truck_id,
                truck_sort_order=ranks[stop.truck_id],
                stop_order=stop.stop_order,
                piece_order=piece_idx,
                model_number=item.model_number,
                whse_location=item.source_whse_location,
            ))
    return rows
