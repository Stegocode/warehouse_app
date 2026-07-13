"""Service: the pick-serving API — claim, confirm, release, report.

# ── Boundary ──────────────────────────────────────────────────────────────────
# Owns: orchestrating a picker's interaction with the queue.
# Owns     : orchestrate a picker's interaction with the queue; enforce that a pick
#            may only be confirmed by the picker holding it and only with a valid scan
# Must not : contain SQL; contain scan-matching logic (that is core.pick_verify)
# May use  : warehouse_app.core.pick_verify, warehouse_app.core.domain
#            warehouse_app.adapters.db.pick_db
#            psycopg, datetime, logging
# Out of scope: authentication (the caller supplies an authenticated picker identity),
#               the ERP write (deferred — see DEBT-ERP-001)
# ─────────────────────────────────────────────────────────────────────────────

This is the module a UI imports. It is the only supported entry point for the pick
flow: warehouse_app owns pick_queue, so nothing outside this package should be writing
SQL against it.
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg

from warehouse_app.adapters.db import pick_db
from warehouse_app.core.domain import PickAssignment, PickProgress
from warehouse_app.core.pick_verify import ScanResult, verify_scan

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────────
# Typed and actionable: a caller can tell "you lost the claim" from "wrong item in your
# hands" and show the picker something useful, rather than a generic failure.


class PickError(Exception):
    """Base for every refusal in the pick flow."""


class PickNotHeld(PickError):
    """The pick is not currently assigned to this picker.

    Raised on a double-tap, a stale phone, or a pick reassigned elsewhere. Never
    silently ignored: the picker must be told the item is no longer theirs.
    """


class ScanRejected(PickError):
    """The scan did not verify the unit in the picker's hands."""

    def __init__(self, result: ScanResult) -> None:
        super().__init__(result.reason)
        self.result = result


# ── Public API ────────────────────────────────────────────────────────────────


def claim_next(
    conn: psycopg.Connection,
    delivery_date: date,
    picker: str,
) -> PickAssignment | None:
    """Hand this picker the next item. None when the queue is empty for the date.

    Idempotent by intent: if the picker already holds a pick, that same pick is
    returned rather than a second one claimed. A phone that reloads, loses signal, or
    gets double-tapped must not quietly take two items off the queue.
    """
    open_pick = pick_db.fetch_open_assignment(conn, delivery_date, picker)
    if open_pick is not None:
        logger.info(
            "claim_next: picker=%s already holds pick_id=%s — returning it",
            picker, open_pick.pick_id,
        )
        return open_pick
    return pick_db.claim_next_pick(conn, delivery_date, picker)


def confirm(
    conn: psycopg.Connection,
    pick_id: str,
    picker: str,
    scanned_serial: str | None = None,
) -> ScanResult:
    """Confirm a pick as physically done. Raises rather than half-succeeding.

    Sets status='picked' and erp_confirmed=FALSE. It does NOT write to the ERP and does
    NOT set 'in_transit' — that is the deferred async ERP writer's job. A 'picked' row
    with erp_confirmed=FALSE is precisely the pending-write queue.

    Fails closed on the scan: if the unit has a serial on record, a matching scan is
    required. Units with no serial (~3% of pick rows) are accepted without one, but the
    outcome says so explicitly so it can never be mistaken for a verified pick.
    """
    assignment = pick_db.fetch_assignment(conn, pick_id)
    if assignment is None:
        raise PickNotHeld(f"Pick {pick_id!r} does not exist.")
    if assignment.status != "assigned" or assignment.assigned_to != picker:
        raise PickNotHeld(
            f"Pick {pick_id!r} is not assigned to you (status={assignment.status!r}, "
            f"held by {assignment.assigned_to!r}). Request the next pick."
        )

    result = verify_scan(assignment.serial_number, scanned_serial)
    if not result.accepted:
        logger.warning(
            "confirm: REJECTED picker=%s pick_id=%s outcome=%s model=%s",
            picker, pick_id, result.outcome.value, assignment.model_number,
        )
        raise ScanRejected(result)

    # The guarded UPDATE is the real arbiter: if the claim evaporated between the read
    # above and this write, it matches zero rows and we refuse rather than pretend.
    if not pick_db.confirm_pick(conn, pick_id, picker):
        raise PickNotHeld(
            f"Pick {pick_id!r} was no longer yours when the confirmation landed. "
            "Request the next pick."
        )

    logger.info(
        "confirm: picker=%s pick_id=%s model=%s outcome=%s -> picked (erp_confirmed=FALSE)",
        picker, pick_id, assignment.model_number, result.outcome.value,
    )
    return result


def release(conn: psycopg.Connection, pick_id: str, picker: str) -> None:
    """Return a claimed pick to the queue — the picker cannot find or cannot lift it."""
    if not pick_db.release_pick(conn, pick_id, picker):
        raise PickNotHeld(f"Pick {pick_id!r} is not assigned to you.")
    logger.info("release: picker=%s returned pick_id=%s to the queue", picker, pick_id)


def current(
    conn: psycopg.Connection,
    delivery_date: date,
    picker: str,
) -> PickAssignment | None:
    """The pick this picker is holding right now, if any."""
    return pick_db.fetch_open_assignment(conn, delivery_date, picker)


def progress(conn: psycopg.Connection, delivery_date: date) -> PickProgress:
    """Shared queue state for the date, visible to every picker."""
    return pick_db.pick_progress(conn, delivery_date)
