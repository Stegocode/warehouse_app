"""Integration tests for the pick claim/confirm flow — real PostgreSQL required.

Why real: FOR UPDATE SKIP LOCKED is the whole concurrency guarantee. Faking the database
would test the fake. These run against an isolated far-future delivery_date so they can
never touch live pick data, and clean up after themselves.

Run:  pytest tests/test_pick_claim_integration.py --env-file "C:\\...\\.env"
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import psycopg
import pytest

from warehouse_app.adapters.db import pick_db
from warehouse_app.services import pick_claim
from warehouse_app.services.pick_claim import PickNotHeld, ScanRejected

# Far-future and fictitious: cannot collide with a real delivery date.
TEST_DATE = date(2099, 1, 1)
TEST_STOP = "pytest-stop-0001"

# Ranked exactly as build_pick_order would rank them: owned 'FLEET' before third-party
# 'ACME', even though 'ACME' < 'FLEET' lexicographically — the trap the column defuses.
SEED_ROWS = [
    # (truck_id, truck_sort_order, stop_order, piece_order, model)
    ("FLEET", 0, 1, 1, "MODEL-A"),
    ("FLEET", 0, 1, 2, "MODEL-B"),
    ("FLEET", 0, 2, 1, "MODEL-C"),
    ("ACME",  1, 1, 1, "MODEL-D"),
    ("ACME",  1, 1, 2, "MODEL-E"),
    ("ACME",  1, 2, 1, "MODEL-F"),
]
EXPECTED_ORDER = ["MODEL-A", "MODEL-B", "MODEL-C", "MODEL-D", "MODEL-E", "MODEL-F"]


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pick_queue WHERE delivery_date = %s", (TEST_DATE,))
        cur.execute("DELETE FROM delivery_stops WHERE delivery_date = %s", (TEST_DATE,))
    conn.commit()


@pytest.fixture()
def seeded(conn: psycopg.Connection):
    """A queue of 6 picks on an isolated date. Torn down afterwards, pass or fail."""
    _cleanup(conn)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO delivery_stops
                   (stop_id, delivery_date, truck_id, stop_order, sink_board_id)
               VALUES (%s, %s, 'TEST', 1, 'pytest-board')""",
            (TEST_STOP, TEST_DATE),
        )
        for truck, rank, stop_order, piece, model in SEED_ROWS:
            cur.execute(
                """INSERT INTO pick_queue (
                       stop_id, source_order_item_id, delivery_date, truck_id,
                       truck_sort_order, stop_order, piece_order, model_number, status
                   ) VALUES (%s, 0, %s, %s, %s, %s, %s, %s, 'queued')""",
                (TEST_STOP, TEST_DATE, truck, rank, stop_order, piece, model),
            )
    conn.commit()
    # serial lives on inventory_items, which these synthetic rows do not join to;
    # scan behaviour is covered by the pure suite and by test_confirm_* below.
    yield conn
    _cleanup(conn)


class TestConcurrentClaims:
    def test_four_pickers_never_receive_the_same_pick(self, seeded, database_url):
        """The guarantee the whole design rests on.

        Four pickers hammer the queue simultaneously, each on its own connection (four
        phones = four requests = four connections). SKIP LOCKED must hand each of them a
        DIFFERENT row rather than blocking or double-assigning.
        """
        def drain(picker: str) -> list[str]:
            claimed: list[str] = []
            with psycopg.connect(database_url) as c:
                while True:
                    a = pick_db.claim_next_pick(c, TEST_DATE, picker)
                    if a is None:
                        return claimed
                    claimed.append(a.pick_id)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(drain, [f"picker-{i}" for i in range(4)]))

        all_claimed = [pick_id for batch in results for pick_id in batch]

        # No pick handed to two pickers.
        assert len(all_claimed) == len(set(all_claimed)), "a pick was double-assigned"
        # Every pick handed out exactly once.
        assert len(all_claimed) == len(SEED_ROWS)

    def test_queue_is_empty_afterwards(self, seeded, database_url):
        def drain(picker: str) -> None:
            with psycopg.connect(database_url) as c:
                while pick_db.claim_next_pick(c, TEST_DATE, picker) is not None:
                    pass

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(drain, [f"picker-{i}" for i in range(4)]))

        progress = pick_db.pick_progress(seeded, TEST_DATE)
        assert progress.queued == 0
        assert progress.assigned == len(SEED_ROWS)


class TestClaimOrder:
    def test_sequential_claims_follow_owned_fleet_first(self, seeded):
        """A single picker draining the queue must see build_pick_order's exact order.

        This is the round-trip: core sorted in memory, the column persisted it, and the
        claim's ORDER BY reproduces it. Owned 'FLEET' must come before third-party
        'ACME' even though 'ACME' sorts first lexicographically.
        """
        seen = []
        while (a := pick_db.claim_next_pick(seeded, TEST_DATE, "solo")) is not None:
            seen.append(a.model_number)
        assert seen == EXPECTED_ORDER


class TestClaimIsIdempotent:
    def test_claiming_twice_returns_the_same_pick(self, seeded):
        """A phone that reloads must not silently take a second item off the queue."""
        first = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        second = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        assert first is not None
        assert second is not None
        assert first.pick_id == second.pick_id

        progress = pick_db.pick_progress(seeded, TEST_DATE)
        assert progress.assigned == 1, "reload claimed a second pick"


class TestConfirm:
    def test_confirm_sets_picked_and_leaves_erp_unconfirmed(self, seeded):
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        pick_claim.confirm(seeded, a.pick_id, "picker-1", scanned_serial=None)

        after = pick_db.fetch_assignment(seeded, a.pick_id)
        assert after.status == "picked"

        with seeded.cursor() as cur:
            cur.execute(
                "SELECT erp_confirmed, picked_at FROM pick_queue WHERE pick_id = %s",
                (a.pick_id,),
            )
            erp_confirmed, picked_at = cur.fetchone()
        assert erp_confirmed is False, "the ERP write is deferred — must not be marked done"
        assert picked_at is not None

    def test_another_picker_cannot_confirm_your_pick(self, seeded):
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        with pytest.raises(PickNotHeld):
            pick_claim.confirm(seeded, a.pick_id, "picker-2", scanned_serial=None)

    def test_confirming_twice_is_refused(self, seeded):
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        pick_claim.confirm(seeded, a.pick_id, "picker-1", scanned_serial=None)
        with pytest.raises(PickNotHeld):
            pick_claim.confirm(seeded, a.pick_id, "picker-1", scanned_serial=None)

    def test_confirm_unknown_pick_is_refused(self, seeded):
        with pytest.raises(PickNotHeld):
            pick_claim.confirm(seeded, "no-such-pick", "picker-1")


class TestRelease:
    def test_release_returns_the_pick_to_the_queue(self, seeded):
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        pick_claim.release(seeded, a.pick_id, "picker-1")

        after = pick_db.fetch_assignment(seeded, a.pick_id)
        assert after.status == "queued"
        assert after.assigned_to is None

        # and it is handed out again — to the next picker who asks
        again = pick_claim.claim_next(seeded, TEST_DATE, "picker-2")
        assert again.pick_id == a.pick_id

    def test_cannot_release_a_pick_you_do_not_hold(self, seeded):
        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        with pytest.raises(PickNotHeld):
            pick_claim.release(seeded, a.pick_id, "picker-2")


class TestProgress:
    def test_counts_move_as_picks_are_worked(self, seeded):
        start = pick_claim.progress(seeded, TEST_DATE)
        assert start.queued == len(SEED_ROWS)
        assert start.total == len(SEED_ROWS)
        assert start.done == 0

        a = pick_claim.claim_next(seeded, TEST_DATE, "picker-1")
        mid = pick_claim.progress(seeded, TEST_DATE)
        assert mid.assigned == 1
        assert mid.queued == len(SEED_ROWS) - 1

        pick_claim.confirm(seeded, a.pick_id, "picker-1", scanned_serial=None)
        end = pick_claim.progress(seeded, TEST_DATE)
        assert end.picked == 1
        assert end.done == 1
        assert end.total == len(SEED_ROWS)
