# ---------------------------------------------------------------------------
# BOUNDARY: warehouse_app.services.dim_feed_fetch
# Owns: async-fetch carton dimensions from an external product content feed.
#
# Owns   : Async-fetch carton dimensions from an external product content feed.
# Must not: Contain SQL; hardcode feed URL or any vendor names.
# May import: warehouse_app.adapters.db.catalog,
#             warehouse_app.core.dims (parse_catalog_dims),
#             httpx, asyncio, psycopg, logging.
# Out of scope: Authentication against the feed, schema negotiation,
#               retry/back-off policies.
# ---------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
import psycopg

from warehouse_app.adapters.db import catalog
from warehouse_app.core.dims import parse_catalog_dims

log = logging.getLogger(__name__)

_DIM_KEYS = ("carton_w_in", "carton_h_in", "height_in")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    conn: psycopg.Connection,
    url_template: str,
    concurrency: int = 12,
    limit: Optional[int] = None,
    offset: int = 0,
    dry_run: bool = False,
) -> int:
    """Fetch carton dimensions from an external product content feed.

    Parameters
    ----------
    conn:
        Open psycopg connection; caller owns the transaction boundary.
    url_template:
        URL pattern containing ``{model}`` as the placeholder for the model
        number, e.g. ``"https://feed.example.com/products/{model}"``.
        Read from ``cfg.dim_feed_url_template`` by the caller — never
        hardcoded here.
    concurrency:
        Maximum simultaneous HTTP requests.
    limit:
        Cap on the number of model numbers processed (None = all).
    offset:
        Skip this many model numbers before beginning (for resumable runs).
    dry_run:
        When True, fetches and parses dims but skips all DB writes.

    Returns
    -------
    int — count of models whose dimension record was updated.
    """
    log.info(
        "dim_feed_fetch.run started concurrency=%d limit=%s offset=%d dry_run=%s",
        concurrency, limit, offset, dry_run,
    )

    all_model_numbers: list[str] = catalog.fetch_all_model_numbers(conn)

    # Apply offset and limit.
    model_numbers = all_model_numbers[offset:]
    if limit is not None:
        model_numbers = model_numbers[:limit]

    log.info(
        "dim_feed_fetch total=%d after_offset=%d processing=%d",
        len(all_model_numbers), len(all_model_numbers) - offset, len(model_numbers),
    )

    if not model_numbers:
        log.info("dim_feed_fetch no model numbers to process — returning 0")
        return 0

    results = asyncio.run(_async_fetch_all(url_template, model_numbers, concurrency))
    log.info("dim_feed_fetch fetched results=%d", len(results))

    if dry_run:
        log.info("dim_feed_fetch dry_run=True — DB writes skipped")
        return len(results)

    catalog.update_dims(conn, results)
    log.info("dim_feed_fetch updated dims count=%d", len(results))
    return len(results)


# ---------------------------------------------------------------------------
# Async internals
# ---------------------------------------------------------------------------

async def _async_fetch_all(
    url_template: str,
    model_numbers: list[str],
    concurrency: int,
) -> list[dict]:
    """Concurrently fetch all model numbers from the feed.

    Returns a list of dim dicts (each includes ``model_number``) where at
    least one of ``carton_w_in``, ``carton_h_in``, or ``height_in`` is
    non-None.
    """
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            _fetch_one(client, sem, url_template, mn)
            for mn in model_numbers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if isinstance(r, dict)]


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url_template: str,
    model_number: str,
) -> Optional[dict]:
    """Fetch and parse one model from the feed.

    Returns a dim dict (with ``model_number`` included) or None when the
    response is non-200 or no useful dimensions are present.
    """
    url = url_template.format(model=model_number)
    async with sem:
        resp = await client.get(url)

    if resp.status_code != 200:
        log.debug(
            "dim_feed_fetch status=%d model=%s url=%s",
            resp.status_code, model_number, url,
        )
        return None

    dims = parse_catalog_dims(resp.json())

    if not any(dims.get(k) for k in _DIM_KEYS):
        return None

    return {"model_number": model_number, **dims}
