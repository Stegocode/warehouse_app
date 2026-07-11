"""Conformance gate — run before every commit. Exits 1 on any violation.

Checks (Rule 15: the gate is an artifact, not a promise):
  A. No banned names in source files      (Rule 10 — domain vocabulary)
  B. os.getenv only in warehouse_app/config.py  (Rule 7 — config not hardcode)
  C. core/ has no outward imports         (Rule 1 — architecture spine)
  D. DEBT.md exists and is non-empty      (Rule 12 — track every deferral)
  E. Every .py module has a boundary header  (Rule 14 — declare your boundaries)

Usage:
    python gate.py          — run all checks
    python gate.py --check A B  — run only named checks
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC  = ROOT / "warehouse_app"

FAIL = False


def fail(msg: str) -> None:
    global FAIL
    FAIL = True
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


# ── A: Banned names ───────────────────────────────────────────────────────────

def check_banned() -> None:
    print("\n[A] Banned names (.conformance-banned or BANNED_TOKENS secret)")
    banned_file = ROOT / ".conformance-banned"

    if banned_file.exists():
        raw = banned_file.read_text()
    elif os.environ.get("BANNED_TOKENS"):
        raw = os.environ["BANNED_TOKENS"]
    else:
        print("  skip  no .conformance-banned file and no BANNED_TOKENS env var — "
              "copy .conformance-banned.example to .conformance-banned to enable")
        return

    tokens = [
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not tokens:
        print("  skip  banned-token list is empty")
        return

    exempt = {".env", ".env.example", ".conformance-banned", "gate.py",
              "ADR-0001-hexagonal-architecture.md", "DEBT.md"}

    hits = 0
    for path in ROOT.rglob("*.py"):
        if any(part.startswith(".") for part in path.parts):
            continue
        if path.name in exempt:
            continue
        text_lower = path.read_text(encoding="utf-8", errors="ignore").lower()
        for token in tokens:
            if token in text_lower:
                fail(f"{path.relative_to(ROOT)}  contains banned token {token!r}")
                hits += 1

    if not hits:
        ok(f"no banned tokens found across {len(tokens)} rules")


# ── B: os.getenv only in config.py ───────────────────────────────────────────

def check_getenv() -> None:
    print("\n[B] os.getenv only in warehouse_app/config.py")
    allowed = SRC / "config.py"
    gate   = ROOT / "gate.py"      # meta-file; exempt from application rules
    hits = 0
    for path in ROOT.rglob("*.py"):
        if path in (allowed, gate):
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "os.getenv" in line and not line.strip().startswith("#"):
                fail(f"{path.relative_to(ROOT)}:{lineno}  os.getenv outside config.py")
                hits += 1
    if not hits:
        ok("os.getenv confined to config.py")


# ── C: core has no outward imports ────────────────────────────────────────────

def check_core_imports() -> None:
    print("\n[C] core/ imports nothing from adapters, services, or infrastructure")
    core_dir = SRC / "core"
    banned_imports = re.compile(
        r"^\s*(?:import|from)\s+warehouse_app\.(adapters|services|infrastructure)",
        re.MULTILINE,
    )
    hits = 0
    for path in core_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in banned_imports.finditer(text):
            lineno = text[: m.start()].count("\n") + 1
            fail(f"{path.relative_to(ROOT)}:{lineno}  core imports outward layer")
            hits += 1
    if not hits:
        ok("core/ imports are inward-only")


# ── D: DEBT.md exists and non-empty ──────────────────────────────────────────

def check_debt() -> None:
    print("\n[D] DEBT.md exists and has open entries")
    debt = ROOT / "DEBT.md"
    if not debt.exists():
        fail("DEBT.md not found")
        return
    text = debt.read_text()
    if len(text.strip()) < 50:
        fail("DEBT.md appears empty — add entries or note 'no open debt'")
    else:
        ok("DEBT.md present and non-empty")


# ── E: boundary headers ───────────────────────────────────────────────────────

def check_headers() -> None:
    print("\n[E] Every .py module has # Owns: boundary header")
    missing = []
    for path in SRC.rglob("*.py"):
        if path.name == "__init__.py":
            # __init__.py files are allowed to be empty or just have comments
            text = path.read_text(encoding="utf-8", errors="ignore")
            if text.strip() and "# Owns:" not in text:
                missing.append(path)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "# Owns:" not in text:
                missing.append(path)

    if missing:
        for p in missing:
            fail(f"{p.relative_to(ROOT)}  missing '# Owns:' boundary header")
    else:
        ok("all modules have boundary headers")


# ── Main ──────────────────────────────────────────────────────────────────────

ALL_CHECKS = {"A": check_banned, "B": check_getenv, "C": check_core_imports,
              "D": check_debt, "E": check_headers}


def main() -> None:
    parser = argparse.ArgumentParser(description="warehouse_app conformance gate")
    parser.add_argument("--check", nargs="*", choices=list(ALL_CHECKS),
                        help="Run only the named checks (default: all)")
    args = parser.parse_args()

    to_run = args.check or list(ALL_CHECKS)
    print(f"warehouse_app conformance gate — checks: {' '.join(to_run)}")

    for key in to_run:
        ALL_CHECKS[key]()

    print()
    if FAIL:
        print("GATE FAILED — fix violations above before committing.")
        sys.exit(1)
    else:
        print("GATE PASSED")


if __name__ == "__main__":
    main()
