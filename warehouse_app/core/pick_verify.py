# Owns: pure scan-verification logic for a pick confirmation.
# Must not: import from adapters, services, infrastructure, or config.
# May import: warehouse_app.core.domain, standard library only.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ScanOutcome(str, Enum):
    """Why a confirmation was accepted or refused."""

    MATCH = "match"                    # scanned serial equals the expected serial
    MISMATCH = "mismatch"              # scanned a different unit — refuse
    MISSING_SCAN = "missing_scan"      # a serial is on record but none was scanned — refuse
    NO_SERIAL_ON_RECORD = "no_serial"  # nothing to scan against — accept, but record why


@dataclass(frozen=True)
class ScanResult:
    outcome: ScanOutcome
    accepted: bool
    reason: str


def normalise_serial(serial: str | None) -> str | None:
    """Trim and upper-case a serial for comparison.

    Scanners and the source disagree on case (the source stores both 'E3717702171' and
    'f0629301058'), and hand-typed entries carry whitespace. Comparison must not care.
    """
    if serial is None:
        return None
    cleaned = serial.strip().upper()
    return cleaned or None


def verify_scan(expected: str | None, scanned: str | None) -> ScanResult:
    """Decide whether a pick confirmation may proceed.

    Fails closed: when the record carries a serial, a matching scan is required. The one
    permitted exception is a unit with no serial on record at all (~3% of pick rows) —
    there is physically nothing to scan, so the confirmation is accepted but explicitly
    labelled, never silently waved through.
    """
    want = normalise_serial(expected)
    got = normalise_serial(scanned)

    if want is None:
        return ScanResult(
            outcome=ScanOutcome.NO_SERIAL_ON_RECORD,
            accepted=True,
            reason="No serial on record for this unit — confirmed without a scan.",
        )

    if got is None:
        return ScanResult(
            outcome=ScanOutcome.MISSING_SCAN,
            accepted=False,
            reason="This unit has a serial on record. Scan it to confirm the pick.",
        )

    if got != want:
        return ScanResult(
            outcome=ScanOutcome.MISMATCH,
            accepted=False,
            reason=(
                "Scanned serial does not match this pick. You may be holding the wrong "
                "unit — check the model and bin, or release the pick."
            ),
        )

    return ScanResult(
        outcome=ScanOutcome.MATCH,
        accepted=True,
        reason="Serial matches.",
    )
