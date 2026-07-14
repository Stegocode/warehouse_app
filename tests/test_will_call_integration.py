"""Integration tests for the will-call interrupt — real PostgreSQL required.

The point of will-call is claim ORDERING: a will-call piece must be handed out before any
normal pick, from any date, FIFO among will-calls. That ordering is SQL, so it is tested
against a real database on an isolated far-future date.

Run:  pytest tests/test_will_call_integration.py --env-file "C:\\...\\.env"
"""
from __future__ import annotations

from datetime import date

import psycopg
import pytest

from warehouse_app.adapters.db import pick_db, will_call_db
from warehouse_app.core.domain import InventoryItem
from warehouse_app.services import pick_claim, will_call
from warehouse_app.services.will_call import WillCallError

TEST_DATE = date(2099, 3, 3)
OTHER_DATE = date(2099, 4, 4)
STOP = "pytest-wc-stop"

# Synthetic inventory ids used by the will-call tests. pick_queue.source_inventory_id has
# an FK to inventory_items, so these must exist there.
WC_IDS = list(range(90001, 90010))


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pick_queue WHERE delivery_date IN (%s, %s)", (TEST_DATE, OTHER_DATE))
        cur.execute("DELETE FROM delivery_stops WHERE delivery_date = %s", (TEST_DATE,))
        cur.execute("DELETE FROM inventory_items WHERE source_inventory_id = ANY(%s)", (WC_IDS,))
    conn.commit()


def _seed_inventory(conn: psycopg.Connection) -> None:
    """Minimal inventory_items rows so the will-call ids satisfy pick_queue's FK."""
    with conn.cursor() as cur:
        for inv_id in WC_IDS:
            cur.execute(
                """INSERT INTO inventory_items
                       (source_inventory_id, model_number, source_status, status)
                   VALUES (%s, %s, 1, 'in_warehouse')
                   ON CONFLICT (source_inventory_id) DO NOTHING""",
                (inv_id, f"WC-MODEL-{inv_id}"),
            )
    conn.commit()


def _seed_normal(conn, date_, truck_sort, n) -> None:
    """A stop plus n normal queued picks for a date."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO delivery_stops (stop_id, delivery_date, truck_id, stop_order, sink_board_id)
               VALUES (%s, %s, 'FLEET', 1, 'b') ON CONFLICT (stop_id) DO NOTHING""",
            (STOP, date_),
        )
        for piece in range(1, n + 1):
            cur.execute(
                """INSERT INTO pick_queue (stop_id, source_order_item_id, delivery_date,
                       truck_id, truck_sort_order, stop_order, piece_order, model_number, status)
                   VALUES (%s, 0, %s, 'FLEET', %s, 1, %s, %s, 'queued')""",
                (STOP, date_, truck_sort, piece, f"NORMAL-{piece}"),
            )
    conn.commit()


def _wc_item(inv_id: int, model: str) -> InventoryItem:
    return InventoryItem(
        source_inventory_id=inv_id, model_number=model, status="in_warehouse",
        source_location_id=1, source_whse_location="A-01-01",
        source_order_id=None, source_order_item_id=inv_id * 10, is_allocated=True,
    )


@pytest.fixture()
def seeded(conn: psycopg.Connection):
    _cleanup(conn)
    _seed_inventory(conn)
    _seed_normal(conn, TEST_DATE, truck_sort=0, n=3)   # normal owned-fleet queue
    yield conn
    _cleanup(conn)


class TestWillCallIsPickedFirst:
    def test_will_call_beats_normal_picks(self, seeded):
        will_call_db.insert_will_call_rows(seeded, [_wc_item(90001, "WC-A")], TEST_DATE, "Counter 1")
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        assert a.is_will_call is True
        assert a.model_number == "WC-A"
        assert a.drop_point == "Counter 1"

    def test_normal_picks_resume_after_will_calls(self, seeded):
        will_call_db.insert_will_call_rows(seeded, [_wc_item(90001, "WC-A")], TEST_DATE, "Counter 1")
        first = pick_claim.claim_next(seeded, TEST_DATE, "p1")
        second = pick_claim.claim_next(seeded, TEST_DATE, "p2")
        assert first.is_will_call and not second.is_will_call
        assert second.model_number.startswith("NORMAL")


class TestWillCallIsGlobal:
    def test_will_call_from_another_date_still_jumps_the_queue(self, seeded):
        # Picker is working TEST_DATE, but the will-call was entered for OTHER_DATE.
        will_call_db.insert_will_call_rows(seeded, [_wc_item(90002, "WC-B")], OTHER_DATE, "Dock")
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        assert a.is_will_call and a.model_number == "WC-B"


class TestFifoAmongWillCalls:
    def test_earlier_will_call_is_picked_first(self, seeded):
        will_call_db.insert_will_call_rows(seeded, [_wc_item(90003, "FIRST")], TEST_DATE, "C1")
        will_call_db.insert_will_call_rows(seeded, [_wc_item(90004, "SECOND")], TEST_DATE, "C2")
        a = pick_claim.claim_next(seeded, TEST_DATE, "p1")
        b = pick_claim.claim_next(seeded, TEST_DATE, "p2")
        assert a.model_number == "FIRST"
        assert b.model_number == "SECOND"


class TestIdempotentInjection:
    def test_reinjecting_the_same_unit_does_not_duplicate(self, seeded):
        item = _wc_item(90005, "DUP")
        n1 = will_call_db.insert_will_call_rows(seeded, [item], TEST_DATE, "C1")
        n2 = will_call_db.insert_will_call_rows(seeded, [item], TEST_DATE, "C1")
        assert n1 == 1
        assert n2 == 0  # the will-call unique guard skipped it
        prog = pick_claim.progress(seeded, TEST_DATE)
        assert prog.queued == 4  # 3 normal + 1 will-call, not 5


class TestServiceGuards:
    def test_empty_drop_point_is_refused(self, seeded):
        with pytest.raises(WillCallError):
            will_call.add_will_call_order(seeded, 12345, "  ", TEST_DATE)

    def test_order_with_no_pickable_pieces_is_refused(self, seeded):
        # A random order id with no allocated pickable inventory must fail loudly.
        with pytest.raises(WillCallError):
            will_call.add_will_call_order(seeded, 999_000_111, "Counter 1", TEST_DATE)


class TestWillCallLifecycle:
    def test_a_will_call_confirms_like_any_pick(self, seeded):
        will_call_db.insert_will_call_rows(seeded, [_wc_item(90006, "WC-LIFE")], TEST_DATE, "C1")
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        pick_claim.confirm(seeded, a.pick_id, "picker-1", scanned_serial=None)
        after = pick_db.fetch_assignment(seeded, a.pick_id)
        assert after.status == "picked"
