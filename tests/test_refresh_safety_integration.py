"""Integration tests for refresh safety — real PostgreSQL required.

The refresh (sync_delivery_stops -> build_pick_queue) re-runs whenever the route changes,
and trucks stay fluid until ~2PM the day before delivery. It therefore runs WHILE people
are picking. These tests pin the invariant that makes that safe:

    a refresh must never destroy, detach, or forget work a human has already done.

Before this, upsert_stops deleted every pick_queue row for the date with no status filter
and regenerated every stop_id — so a refresh at 10am erased the morning.

Run:  pytest tests/test_refresh_safety_integration.py --env-file "C:\\...\\.env"
"""
from __future__ import annotations

from datetime import date

import psycopg
import pytest

from warehouse_app.adapters.db import neon, pick_db
from warehouse_app.services import pick_claim

TEST_DATE = date(2099, 2, 2)
ORDER_KEPT = 990001
ORDER_GONE = 990002


def _stop_row(order_id: int, truck: str, stop_order: int) -> dict:
    return {
        "delivery_date":    TEST_DATE,
        "truck_id":         truck,
        "stop_order":       stop_order,
        "source_order_id":  order_id,
        "sink_item_id":     None,
        "sink_board_id":    "pytest-board",
        "sink_status":      None,
        "customer_name":    f"Customer {order_id}",
        "delivery_address": None,
        "delivery_notes":   None,
    }


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM pick_queue WHERE stop_id IN
               (SELECT stop_id FROM delivery_stops WHERE delivery_date = %s)""",
            (TEST_DATE,),
        )
        cur.execute("DELETE FROM delivery_stops WHERE delivery_date = %s", (TEST_DATE,))
    conn.commit()


def _stop_id_for(conn: psycopg.Connection, order_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id FROM delivery_stops WHERE delivery_date=%s AND source_order_id=%s",
            (TEST_DATE, order_id),
        )
        row = cur.fetchone()
    return None if row is None else row[0]


def _add_picks(conn: psycopg.Connection, stop_id: str, truck: str, n: int) -> None:
    with conn.cursor() as cur:
        for piece in range(1, n + 1):
            cur.execute(
                """INSERT INTO pick_queue (
                       stop_id, source_order_item_id, delivery_date, truck_id,
                       truck_sort_order, stop_order, piece_order, model_number, status
                   ) VALUES (%s, 0, %s, %s, 0, 1, %s, %s, 'queued')""",
                (stop_id, TEST_DATE, truck, piece, f"MODEL-{piece}"),
            )
    conn.commit()


@pytest.fixture()
def routed(conn: psycopg.Connection):
    """Two stops on the route sheet, each with picks queued against them."""
    _cleanup(conn)
    neon.upsert_stops(conn, [
        _stop_row(ORDER_KEPT, "FLEET", 1),
        _stop_row(ORDER_GONE, "FLEET", 2),
    ])
    _add_picks(conn, _stop_id_for(conn, ORDER_KEPT), "FLEET", 3)
    _add_picks(conn, _stop_id_for(conn, ORDER_GONE), "FLEET", 2)
    yield conn
    _cleanup(conn)


class TestStopIdentityIsStable:
    def test_stop_id_survives_a_refresh(self, routed):
        before = _stop_id_for(routed, ORDER_KEPT)
        neon.upsert_stops(routed, [_stop_row(ORDER_KEPT, "FLEET", 1),
                                   _stop_row(ORDER_GONE, "FLEET", 2)])
        assert _stop_id_for(routed, ORDER_KEPT) == before

    def test_stop_id_survives_a_truck_change(self, routed):
        """Trucks are fluid until the afternoon before delivery. Moving an order to
        another truck must not orphan picks already claimed against it."""
        before = _stop_id_for(routed, ORDER_KEPT)

        neon.upsert_stops(routed, [_stop_row(ORDER_KEPT, "ACME", 1),   # re-routed
                                   _stop_row(ORDER_GONE, "FLEET", 2)])

        assert _stop_id_for(routed, ORDER_KEPT) == before, "re-route regenerated stop_id"
        with routed.cursor() as cur:
            cur.execute(
                "SELECT truck_id FROM delivery_stops WHERE stop_id = %s", (before,)
            )
            assert cur.fetchone()[0] == "ACME", "truck change was not applied"


class TestInProgressWorkSurvives:
    def test_assigned_and_picked_rows_survive_a_refresh(self, routed):
        """The invariant. A refresh mid-shift must not erase the morning's work."""
        claimed = pick_claim.claim_next(routed, TEST_DATE, "picker-1")
        pick_claim.confirm(routed, claimed.pick_id, "picker-1")

        held = pick_claim.claim_next(routed, TEST_DATE, "picker-2")   # left assigned

        neon.upsert_stops(routed, [_stop_row(ORDER_KEPT, "FLEET", 1),
                                   _stop_row(ORDER_GONE, "FLEET", 2)])

        after_picked = pick_db.fetch_assignment(routed, claimed.pick_id)
        assert after_picked is not None, "a PICKED row was deleted by the refresh"
        assert after_picked.status == "picked"

        after_held = pick_db.fetch_assignment(routed, held.pick_id)
        assert after_held is not None, "an ASSIGNED row was deleted by the refresh"
        assert after_held.status == "assigned"
        assert after_held.assigned_to == "picker-2", "the claim was lost"

    def test_picked_at_and_assigned_to_are_not_wiped(self, routed):
        claimed = pick_claim.claim_next(routed, TEST_DATE, "picker-1")
        pick_claim.confirm(routed, claimed.pick_id, "picker-1")

        neon.upsert_stops(routed, [_stop_row(ORDER_KEPT, "FLEET", 1),
                                   _stop_row(ORDER_GONE, "FLEET", 2)])

        with routed.cursor() as cur:
            cur.execute(
                "SELECT assigned_to, picked_at FROM pick_queue WHERE pick_id = %s",
                (claimed.pick_id,),
            )
            assigned_to, picked_at = cur.fetchone()
        assert assigned_to == "picker-1"
        assert picked_at is not None


class TestVanishedStops:
    def test_stop_off_the_route_sheet_is_removed_with_its_queued_picks(self, routed):
        gone_stop = _stop_id_for(routed, ORDER_GONE)

        neon.upsert_stops(routed, [_stop_row(ORDER_KEPT, "FLEET", 1)])   # ORDER_GONE dropped

        assert _stop_id_for(routed, ORDER_GONE) is None, "vanished stop was not removed"
        with routed.cursor() as cur:
            cur.execute("SELECT count(*) FROM pick_queue WHERE stop_id = %s", (gone_stop,))
            assert cur.fetchone()[0] == 0, "its queued picks should have gone with it"

    def test_vanished_stop_with_picked_work_is_KEPT_not_deleted(self, routed, caplog):
        """Fails closed. An order leaving the route does not un-happen the fact that a
        human already carried the appliance to the staging lane. Deleting the row would
        make the database lie about the floor — so we keep it and shout."""
        gone_stop = _stop_id_for(routed, ORDER_GONE)

        # work the ORDER_GONE stop specifically
        with routed.cursor() as cur:
            cur.execute(
                """UPDATE pick_queue SET status='picked', assigned_to='picker-9',
                       picked_at=now()
                   WHERE stop_id = %s AND piece_order = 1""",
                (gone_stop,),
            )
        routed.commit()

        neon.upsert_stops(routed, [_stop_row(ORDER_KEPT, "FLEET", 1)])   # ORDER_GONE dropped

        assert _stop_id_for(routed, ORDER_GONE) == gone_stop, \
            "a stop with picked work was deleted"
        with routed.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pick_queue WHERE stop_id=%s AND status='picked'",
                (gone_stop,),
            )
            assert cur.fetchone()[0] == 1, "the picked row was destroyed"

        assert any(
            "left the route sheet" in r.message and "KEEPING" in r.message
            for r in caplog.records
        ), "the conflict was not reported"


class TestRefreshIsIdempotent:
    def test_running_the_same_refresh_twice_changes_nothing(self, routed):
        rows = [_stop_row(ORDER_KEPT, "FLEET", 1), _stop_row(ORDER_GONE, "FLEET", 2)]

        neon.upsert_stops(routed, rows)
        first = pick_claim.progress(routed, TEST_DATE)
        ids_first = (_stop_id_for(routed, ORDER_KEPT), _stop_id_for(routed, ORDER_GONE))

        neon.upsert_stops(routed, rows)
        second = pick_claim.progress(routed, TEST_DATE)
        ids_second = (_stop_id_for(routed, ORDER_KEPT), _stop_id_for(routed, ORDER_GONE))

        assert ids_first == ids_second
        assert (first.queued, first.assigned, first.picked) == \
               (second.queued, second.assigned, second.picked)
