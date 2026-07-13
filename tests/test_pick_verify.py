"""Tests for core/pick_verify.py — pure scan verification, no I/O."""
from warehouse_app.core.pick_verify import (
    ScanOutcome,
    normalise_serial,
    verify_scan,
)


class TestNormaliseSerial:
    def test_trims_and_uppercases(self):
        assert normalise_serial("  r000041858 ") == "R000041858"

    def test_none_stays_none(self):
        assert normalise_serial(None) is None

    def test_blank_becomes_none(self):
        assert normalise_serial("   ") is None


class TestVerifyScanAccepts:
    def test_exact_match(self):
        r = verify_scan("R000041858", "R000041858")
        assert r.accepted
        assert r.outcome is ScanOutcome.MATCH

    def test_match_is_case_insensitive(self):
        """The source stores serials in both cases (e.g. 'f0629301058'); a scanner
        reports whatever is printed. Case must not fail a legitimate pick."""
        r = verify_scan("f0629301058", "F0629301058")
        assert r.accepted
        assert r.outcome is ScanOutcome.MATCH

    def test_match_ignores_surrounding_whitespace(self):
        assert verify_scan("SN-1", "  SN-1 ").accepted

    def test_no_serial_on_record_is_accepted_but_labelled(self):
        """~3% of pick rows have no serial. There is nothing to scan, so the pick is
        allowed — but it must be distinguishable from a verified one, never silently
        waved through."""
        r = verify_scan(None, None)
        assert r.accepted
        assert r.outcome is ScanOutcome.NO_SERIAL_ON_RECORD


class TestVerifyScanRefuses:
    def test_mismatch_is_refused(self):
        r = verify_scan("R000041858", "A0477510")
        assert not r.accepted
        assert r.outcome is ScanOutcome.MISMATCH

    def test_missing_scan_is_refused_when_serial_known(self):
        """Fails closed: a unit with a serial on record cannot be confirmed by taps."""
        r = verify_scan("R000041858", None)
        assert not r.accepted
        assert r.outcome is ScanOutcome.MISSING_SCAN

    def test_blank_scan_is_refused_when_serial_known(self):
        r = verify_scan("R000041858", "   ")
        assert not r.accepted
        assert r.outcome is ScanOutcome.MISSING_SCAN

    def test_refusal_carries_an_actionable_reason(self):
        assert "wrong unit" in verify_scan("A", "B").reason.lower()
