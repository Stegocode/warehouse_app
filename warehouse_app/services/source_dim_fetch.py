# ---------------------------------------------------------------------------
# BOUNDARY: warehouse_app.services.source_dim_fetch
# Owns: async-fetch physical dimensions from source product pages.
#
# Owns   : Async-fetch physical dimensions from source product pages.
# Must not: Contain SQL; contain dimension parsing logic (lives in core.dims).
# May import: warehouse_app.adapters.db.catalog,
#             warehouse_app.adapters.source.ports (HttpSource),
#             warehouse_app.core.dims (parse_source_page_dims),
#             httpx, asyncio, psycopg, logging.
# Out of scope: Login-credential storage, HTML scraping rules, retry policies.
# ---------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
import psycopg

from warehouse_app.adapters.db import catalog
from warehouse_app.core.dims import parse_source_page_dims

log = logging.getLogger(__name__)

_STREAM_STOP_BYTES = 220_000


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    source,
    conn: psycopg.Connection,
    concurrency: int = 12,
    limit: Optional[int] = None,
    refetch: bool = False,
    dry_run: bool = False,
) -> int:
    """Fetch physical dimensions from source product pages and persist them.

    Parameters
    ----------
    source:
        HttpSource instance.  Must expose ``get_session_cookies() -> dict``
        and ``._base`` (the base URL string).
    conn:
        Open psycopg connection; caller owns the transaction boundary.
    concurrency:
        Maximum simultaneous HTTP requests.
    limit:
        Cap on the number of candidates fetched from the DB (None = all).
    refetch:
        When True, re-fetch models that already have dimensions on record.
    dry_run:
        When True, fetches pages and parses dims but skips all DB writes.

    Returns
    -------
    int — count of models whose dimension record was updated.
    """
    log.info(
        "source_dim_fetch.run started concurrency=%d limit=%s refetch=%s dry_run=%s",
        concurrency, limit, refetch, dry_run,
    )

    # Ensure the source client is authenticated.
    if not getattr(source, "_session", None):
        source.login()

    cookies = source.get_session_cookies()
    base_url: str = source._base

    candidates: list[tuple[str, int]] = catalog.fetch_source_dim_candidates(
        conn, refetch=refetch, limit=limit
    )
    log.info("source_dim_fetch candidates=%d", len(candidates))

    if not candidates:
        log.info("source_dim_fetch no candidates — returning 0")
        return 0

    results = asyncio.run(
        _async_fetch_all(base_url, cookies, candidates, concurrency, _STREAM_STOP_BYTES)
    )
    log.info("source_dim_fetch fetched results=%d", len(results))

    if dry_run:
        log.info("source_dim_fetch dry_run=True — DB writes skipped")
        return len(results)

    catalog.update_dims(conn, results)
    log.info("source_dim_fetch updated dims count=%d", len(results))
    return len(results)


# ---------------------------------------------------------------------------
# Async internals
# ---------------------------------------------------------------------------

async def _async_fetch_all(
    base_url: str,
    cookies: dict,
    candidates: list[tuple[str, int]],
    concurrency: int,
    stop_bytes: int = _STREAM_STOP_BYTES,
) -> list[dict]:
    """Concurrently fetch all candidate product pages.

    Returns a list of dim dicts, each containing ``model_number`` plus
    whatever keys ``parse_source_page_dims`` returns.  Entries where
    ``height_in`` is None are excluded.
    """
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        cookies=cookies, timeout=30.0, follow_redirects=True
    ) as client:
        tasks = [
            _fetch_one(client, sem, base_url, model_number, source_product_id, stop_bytes)
            for model_number, source_product_id in candidates
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if isinstance(r, dict)]


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    model_number: str,
    source_product_id: int,
    stop_bytes: int,
) -> Optional[dict]:
    """Fetch a single product page and parse its physical dimensions.

    Returns a dim dict (with ``model_number`` included) or None when the
    page is unreachable or yields no usable height dimension.
    """
    url = f"{base_url}/inventory/model/{source_product_id}"
    async with sem:
        chunks: list[bytes] = []
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                log.debug(
                    "source_dim_fetch status=%d model=%s url=%s",
                    resp.status_code, model_number, url,
                )
                return None
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                chunks.append(chunk)
                if sum(len(c) for c in chunks) >= stop_bytes:
                    break

    html = b"".join(chunks).decode("utf-8", errors="ignore")
    dims = parse_source_page_dims(html)

    if dims.get("height_in") is None:
        return None

    return {"model_number": model_number, **dims}
