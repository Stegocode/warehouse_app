# Owns: GraphQL sink adapter (ported from model_catalog_module/scripts).
# Must not: contain domain logic.
# May import: warehouse_app.adapters.sink.ports, warehouse_app.config,
#             requests, standard library.

from __future__ import annotations

import logging
import re

import requests

from warehouse_app.config import Config

logger = logging.getLogger(__name__)

_API_URL = "https://api.monday.com/v2"
_ORDER_RE = re.compile(r"^\s*([A-Z]?-?\d{3,6}[A-Z0-9-]*)\b", re.IGNORECASE)

_COLUMNS_QUERY = """
query ($boardId: [ID!]) {
  boards(ids: $boardId) {
    columns { id title }
  }
}
"""

_ITEMS_QUERY = """
query ($boardId: [ID!], $cursor: String, $columnIds: [String!]) {
  boards(ids: $boardId) {
    items_page(limit: 500, cursor: $cursor) {
      cursor
      items {
        id
        name
        column_values(ids: $columnIds) { id text }
      }
    }
  }
}
"""

_COLUMN_TITLES = {
    "time_window":   "Preferred Time Window if No Installer (SUBJECT TO CHANGE)",
    "special_notes": "SPECIAL NOTES",
    "delivery_type": "HOW ARE WE DELIVERING THIS?",
    "haul_away":     "HAUL AWAY?",
    "rma_pickup":    "WILL WE NEED TO PICK UP AN RMA THIS TRIP?",
    "installs":      "PLEASE SELECT ALL ITEMS FOR INSTALL ON THIS DELIVERY",
}


class GraphqlSink:
    """Live sink adapter — reads/writes the notification board via GraphQL."""

    def __init__(self, cfg: Config) -> None:
        if not cfg.sink_api_token:
            raise RuntimeError("SINK_API_TOKEN is required for sink_type=graphql")
        if not cfg.sink_board_id:
            raise RuntimeError("SINK_BOARD_ID is required for sink_type=graphql")
        if not cfg.sink_delivery_col:
            raise RuntimeError("SINK_DELIVERY_COL is required for sink_type=graphql")
        self._token = cfg.sink_api_token
        self._board_id = cfg.sink_board_id
        self._delivery_col = cfg.sink_delivery_col
        self._col_ids: dict[str, str] | None = None

    def _graphql(self, query: str, variables: dict) -> dict:
        resp = requests.post(
            _API_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": self._token, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _ensure_col_ids(self) -> dict[str, str]:
        if self._col_ids is not None:
            return self._col_ids
        resp = self._graphql(_COLUMNS_QUERY, {"boardId": str(self._board_id)})
        columns = resp["data"]["boards"][0]["columns"]
        by_title = {c["title"]: c["id"] for c in columns}
        missing = [t for t in _COLUMN_TITLES.values() if t not in by_title]
        if missing:
            raise ValueError(f"Sink board missing columns: {missing}")
        self._col_ids = {key: by_title[title] for key, title in _COLUMN_TITLES.items()}
        return self._col_ids

    def fetch_board_items(self, delivery_date: str) -> dict[str, dict]:
        col_ids = self._ensure_col_ids()
        all_ids = list({self._delivery_col, *col_ids.values()})
        out: dict[str, dict] = {}
        cursor: str | None = None

        while True:
            resp = self._graphql(_ITEMS_QUERY, {
                "boardId": str(self._board_id),
                "cursor": cursor,
                "columnIds": all_ids,
            })
            page = resp["data"]["boards"][0]["items_page"]
            for item in page["items"]:
                by_id = {cv["id"]: (cv.get("text") or "").strip()
                         for cv in item["column_values"]}
                if by_id.get(self._delivery_col) != delivery_date:
                    continue
                m = _ORDER_RE.match(item.get("name") or "")
                if not m:
                    continue
                order_num = m.group(1).upper()
                name = (item.get("name") or "").strip()
                customer = re.sub(r"^\s*[A-Z]?-?\d+[A-Z0-9-]*\s*", "", name).strip()
                out[order_num] = {
                    "sink_item_id":      str(item["id"]),
                    "sink_board_id":     self._board_id,
                    "sink_status":       None,
                    "customer_name":     customer or None,
                    "delivery_notes":    by_id.get(col_ids.get("special_notes", "")) or None,
                    "sink_time_window":  by_id.get(col_ids.get("time_window", "")) or None,
                    "sink_delivery_type": by_id.get(col_ids.get("delivery_type", "")) or None,
                }
            cursor = page.get("cursor")
            if not cursor:
                break

        logger.info("sink: %d board items for %s", len(out), delivery_date)
        return out

    def update_item_status(self, sink_item_id: str, status: str) -> None:
        raise NotImplementedError("status write-back not yet implemented")
