"""Tests for services/inventory_sync._extract — field mapping from raw ERP response."""
from warehouse_app.services.inventory_sync import _extract


def _raw(**overrides) -> dict:
    """Minimal valid raw inventory record with optional field overrides."""
    base = {
        "InventoryId": 12345,
        "ModelId_FK": 99,
        "ModelNumber": "MODEL-X ",   # trailing space — should be stripped
        "Manufacturer": "Acme",
        "SerialNumber": "SN001",
        "LocationId_FK": 1,
        "WHSELocationId_FK": 513,
        "InventoryStatus": 1,
        "OrderItemId_FK": None,
        "IsNonSellable": 0,
        "IsDeleted": 0,
        "Cost": 199.99,
        "ReceivedDate": "2026-01-01",
        "InvoicedDate": None,
        "order_item": None,
        "whse_location": None,
    }
    base.update(overrides)
    return base


class TestExtractReturnsNoneOnMissingId:
    def test_missing_inventory_id(self):
        assert _extract({"ModelNumber": "X"}) is None

    def test_zero_inventory_id_treated_as_missing(self):
        assert _extract(_raw(InventoryId=0)) is None


class TestExtractFieldMapping:
    def test_basic_fields(self):
        row = _extract(_raw())
        assert row["source_inventory_id"] == 12345
        assert row["model_number"] == "MODEL-X"   # stripped
        assert row["source_location_id"] == 1

    def test_status_mapped(self):
        assert _extract(_raw(InventoryStatus=0))["status"] == "on_order"
        assert _extract(_raw(InventoryStatus=1))["status"] == "in_warehouse"
        assert _extract(_raw(InventoryStatus=2))["status"] == "in_transit"
        assert _extract(_raw(InventoryStatus=3))["status"] == "missing"

    def test_unknown_status_defaults_to_in_warehouse(self):
        assert _extract(_raw(InventoryStatus=99))["status"] == "in_warehouse"

    def test_boolean_flags(self):
        row = _extract(_raw(IsNonSellable=1, IsDeleted=0))
        assert row["is_non_sellable"] is True
        assert row["is_deleted"] is False


class TestExtractOrderId:
    def test_order_id_from_nested_order_item(self):
        """source_order_id must come from order_item.OrderFK, not a top-level key."""
        raw = _raw(
            OrderItemId_FK=147802,
            order_item={"OrderFK": 28879, "OrderItemId": 147802},
        )
        row = _extract(raw)
        assert row["source_order_id"] == 28879
        assert row["source_order_item_id"] == 147802

    def test_order_id_null_when_no_order_item(self):
        row = _extract(_raw(order_item=None))
        assert row["source_order_id"] is None

    def test_order_id_null_when_order_item_missing_key(self):
        row = _extract(_raw(order_item={"SomethingElse": 1}))
        assert row["source_order_id"] is None


class TestExtractBinLocation:
    def test_bin_label_from_nested_whse_location(self):
        """source_whse_location must come from whse_location.Name, not WhseLocation."""
        raw = _raw(whse_location={"WHSELocationId": 513, "Name": "14-02-01"})
        row = _extract(raw)
        assert row["source_whse_location"] == "14-02-01"

    def test_bin_location_null_when_whse_location_absent(self):
        row = _extract(_raw(whse_location=None))
        assert row["source_whse_location"] is None

    def test_bin_location_null_when_name_empty(self):
        row = _extract(_raw(whse_location={"Name": ""}))
        assert row["source_whse_location"] is None
