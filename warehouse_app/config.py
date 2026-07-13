# Owns: loading and validating ALL environment variables for this application.
# Must not: import from adapters, services, or infrastructure.
# May import: standard library, python-dotenv (load_dotenv only).
#
# This is the only file permitted to call os.getenv (enforced by gate.py).

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SourceType = Literal["portal", "fake"]
SinkType   = Literal["graphql", "null"]


@dataclass(frozen=True)
class Config:
    database_url:        str
    source_username:     str
    source_password:     str
    source_base_url:     str
    source_type:         SourceType
    sink_type:           SinkType
    sink_api_token:      str | None
    sink_api_url:        str | None
    sink_board_id:       str | None
    sink_delivery_col:   str | None
    owned_fleet_trucks:      frozenset[str] = frozenset()
    dim_feed_url_template:   str | None = None   # URL with {model} placeholder
    layout_json_path:        str | None = None   # path to warehouse layout JSON
    source_dim_concurrency:  int = 12            # async fetch parallelism


def load(env_file: str | Path | None = None) -> Config:
    """Load and validate all config from environment. Call once at startup."""
    if env_file:
        from dotenv import load_dotenv
        load_dotenv(Path(env_file))

    def _require(name: str) -> str:
        val = os.getenv(name)
        if not val:
            raise RuntimeError(f"Required env var {name!r} is not set")
        return val

    source_type: SourceType = os.getenv("SOURCE_TYPE", "portal")  # type: ignore[assignment]
    sink_type:   SinkType   = os.getenv("SINK_TYPE", "graphql")   # type: ignore[assignment]

    if source_type not in ("portal", "fake"):
        raise RuntimeError(f"SOURCE_TYPE must be 'portal' or 'fake', got {source_type!r}")
    if sink_type not in ("graphql", "null"):
        raise RuntimeError(f"SINK_TYPE must be 'graphql' or 'null', got {sink_type!r}")

    # Fail closed: an unset OWNED_FLEET_TRUCKS would classify EVERY truck as third-party,
    # silently inverting the pick order with no error and no warning. An empty fleet is
    # never a legitimate configuration, so refuse to start rather than pick the wrong item.
    raw_trucks = _require("OWNED_FLEET_TRUCKS")
    owned = frozenset(t.strip().upper() for t in raw_trucks.split(",") if t.strip())
    if not owned:
        raise RuntimeError("OWNED_FLEET_TRUCKS is set but contains no truck labels")

    # The graphql sink cannot function without a URL and a token. Surfacing that here,
    # at boot, beats a None turning up deep inside an HTTP call mid-sync.
    if sink_type == "graphql":
        _require("SINK_API_URL")
        _require("SINK_API_TOKEN")

    return Config(
        database_url=_require("DATABASE_URL"),
        source_username=_require("SOURCE_USERNAME") if source_type != "fake" else "",
        source_password=_require("SOURCE_PASSWORD") if source_type != "fake" else "",
        source_base_url=_require("SOURCE_BASE_URL") if source_type != "fake" else "",
        source_type=source_type,
        sink_type=sink_type,
        sink_api_token=os.getenv("SINK_API_TOKEN"),
        sink_api_url=os.getenv("SINK_API_URL"),
        sink_board_id=os.getenv("SINK_BOARD_ID"),
        sink_delivery_col=os.getenv("SINK_DELIVERY_COL"),
        owned_fleet_trucks=owned,
        dim_feed_url_template=os.getenv("DIM_FEED_URL_TEMPLATE"),
        layout_json_path=os.getenv("LAYOUT_JSON_PATH"),
        source_dim_concurrency=int(os.getenv("SOURCE_DIM_CONCURRENCY", "12")),
    )
