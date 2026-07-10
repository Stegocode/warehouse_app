# Owns: structured logging configuration.
# Must not: import from core, services, or adapters.
# May import: standard library.

from __future__ import annotations

import logging


def configure(level: str = "INFO") -> None:
    """Configure root logger with a standard format. Call once at process start."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
