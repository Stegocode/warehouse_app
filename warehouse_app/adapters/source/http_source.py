# Owns: real HTTP source adapter (ported from model_catalog_module/scripts).
# Must not: contain domain logic — transform raw dicts only.
# May import: warehouse_app.adapters.source.ports, warehouse_app.config,
#             requests, bs4, standard library.
#
# DEBT-ARCH-002: HS-specific URL paths live here as private constants (not in
#   config) because they are part of the adapter's protocol knowledge, not
#   operator configuration. The base URL (SOURCE_BASE_URL) is injected via cfg.

from __future__ import annotations

import json
import logging
import urllib.parse

import requests
from bs4 import BeautifulSoup

from warehouse_app.config import Config

logger = logging.getLogger(__name__)

_PAGE_SIZE = 500
_INVENTORY_FILTER = {
    "logic": "and",
    "filters": [
        {"field": "InventoryStatus", "operator": "neq", "value": 2},
        # NOTE: the source API treats any `deleted` filter as a scope toggle that
        # shows ONLY soft-deleted records. The default scope already excludes them,
        # so we do NOT add a deleted filter here.
    ],
}
_MODEL_FILTER: dict = {}  # no special filters; pagination shell only


class HttpSource:
    """Live source adapter — authenticates via web session, fetches via HTTP."""

    def __init__(self, cfg: Config) -> None:
        self._base = cfg.source_base_url.rstrip("/")
        self._username = cfg.source_username
        self._password = cfg.source_password
        self._session: requests.Session | None = None

    def login(self) -> None:
        session = requests.Session()
        page = session.get(self._base + "/login", timeout=20)
        soup = BeautifulSoup(page.text, "html.parser")
        token_el = soup.find("input", {"name": "_token"})
        if not token_el:
            raise RuntimeError("No CSRF token on source login page")
        resp = session.post(
            self._base + "/login",
            data={"email": self._username, "password": self._password,
                  "_token": token_el["value"]},
            timeout=20,
            allow_redirects=True,
        )
        if "/login" in resp.url:
            raise RuntimeError(f"Source login failed — still at {resp.url}")
        self._session = session
        logger.info("source login OK → %s", resp.url)

    def _require_session(self) -> requests.Session:
        if self._session is None:
            raise RuntimeError("Call login() before fetching data")
        return self._session

    def fetch_inventory(self, limit: int | None = None) -> list[dict]:
        session = self._require_session()
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "referer": self._base + "/inventory/serial",
            "x-requested-with": "XMLHttpRequest",
        }

        first = self._inventory_page(session, headers, skip=0, take=1)
        total = min(first.get("total", 0), limit) if limit else first.get("total", 0)
        logger.info("source reports %d active inventory records", total)

        records: list[dict] = []
        skip = 0
        while skip < total:
            take = min(_PAGE_SIZE, total - skip)
            page = self._inventory_page(session, headers, skip=skip, take=take)
            batch = page.get("data", [])
            records.extend(batch)
            skip += len(batch)
            logger.info("  fetched %d / %d", skip, total)
            if not batch:
                break
        return records

    def _inventory_page(
        self,
        session: requests.Session,
        headers: dict,
        skip: int,
        take: int,
    ) -> dict:
        filter_obj = {
            "take": take, "skip": skip,
            "page": (skip // take) + 1, "pageSize": take,
            "filter": _INVENTORY_FILTER,
        }
        encoded = urllib.parse.quote(json.dumps(filter_obj), safe="")
        r = session.get(
            self._base + "/inventory/serial/showAll?" + encoded,
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def fetch_models(self, limit: int | None = None) -> list[dict]:
        session = self._require_session()
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "referer": self._base + "/inventory/model",
            "x-requested-with": "XMLHttpRequest",
        }

        first = self._model_page(session, headers, skip=0, take=1)
        total = min(first.get("total", 0), limit) if limit else first.get("total", 0)
        logger.info("source reports %d model records", total)

        records: list[dict] = []
        skip = 0
        while skip < total:
            take = min(_PAGE_SIZE, total - skip)
            page = self._model_page(session, headers, skip=skip, take=take)
            batch = page.get("data", [])
            records.extend(batch)
            skip += len(batch)
            logger.info("  fetched %d / %d", skip, total)
            if not batch:
                break
        return records

    def _model_page(
        self,
        session: requests.Session,
        headers: dict,
        skip: int,
        take: int,
    ) -> dict:
        filter_obj = {
            "take": take, "skip": skip,
            "page": (skip // take) + 1, "pageSize": take,
        }
        encoded = urllib.parse.quote(json.dumps(filter_obj), safe="")
        r = session.get(
            self._base + "/inventory/model/showAll?" + encoded,
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def get_session_cookies(self) -> dict[str, str]:
        """Return current session cookies as a plain dict for use with httpx clients."""
        session = self._require_session()
        return dict(session.cookies)
