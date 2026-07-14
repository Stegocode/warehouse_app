"""Tests for services/inventory_sync._extract — field mapping from raw ERP response."""
from warehouse_app.services.inventory_sync import _extract


def _raw(**overrides) -> dict:
    """Minimal valid raw inventory record with optional field overrides.

    Keys here mirror the real source payload. The previous fixture invented
    "Manufacturer" and "SerialNumber" — keys the source never returns — and asserted
    nothing about either, so the suite stayed green while production wrote NULL to
    both columns for all 14,505 rows. Fixtures must not invent the schema.
    """
    base = {
        "InventoryId": 12345,
        "ModelId_FK": 99,
        "ModelNumber": "MODEL-X ",   # trailing space — should be stripped
        "manufacturer": {"MID": 27, "Name": "Acme", "GID_FK": 2},   # nested, not top-level
        "MFGSerialNumber": "SN001",
        "ScannedMFGSerialNumber": None,
        "MobileImageURL": "https://cdn.example/img.jpg",
        "ShortDescription": "MODEL-X 24in Dishwasher",
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
        # The ERP's real InventoryStatus meanings — verified against its own UI.
        # This previously asserted 2=in_transit and 3=missing, which was the bug:
        # 2 is SOLD and 3 is IN-TRANSIT.
        assert _extract(_raw(InventoryStatus=0))["status"] == "on_order"
        assert _extract(_raw(InventoryStatus=1))["status"] == "in_warehouse"
        assert _extract(_raw(InventoryStatus=2))["status"] == "sold"
        assert _extract(_raw(InventoryStatus=3))["status"] == "in_transit"
        assert _extract(_raw(InventoryStatus=7))["status"] == "missing"

    def test_all_erp_statuses_mapped(self):
        expected = {
            0: "on_order", 1: "in_warehouse", 2: "sold", 3: "in_transit",
            4: "vendor_return_pending", 5: "vendor_returned", 6: "order_returned",
            7: "missing", 8: "transfer", 9: "container",
        }
        for code, label in expected.items():
            assert _extract(_raw(InventoryStatus=code))["status"] == label

    def test_unknown_status_falls_back_and_is_reportable(self):
        # A code outside 0-9 is unexpected; it falls back rather than crashing, but
        # _report_unmapped_statuses logs it (tested via the map, not the label).
        from warehouse_app.core.domain import SOURCE_STATUS_MAP
        assert 99 not in SOURCE_STATUS_MAP
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


class TestExtractManufacturer:
    def test_manufacturer_from_nested_object(self):
        """manufacturer must come from manufacturer.Name — there is no top-level key."""
        row = _extract(_raw(manufacturer={"MID": 27, "Name": "Bosch"}))
        assert row["manufacturer"] == "Bosch"

    def test_manufacturer_null_when_object_absent(self):
        assert _extract(_raw(manufacturer=None))["manufacturer"] is None

    def test_top_level_manufacturer_key_is_not_read(self):
        """Guards the original bug: the source has no top-level 'Manufacturer' key."""
        raw = _raw(manufacturer=None)
        raw["Manufacturer"] = "ShouldBeIgnored"
        assert _extract(raw)["manufacturer"] is None


class TestExtractSerialNumber:
    def test_real_serial_is_kept(self):
        assert _extract(_raw(MFGSerialNumber="R000041858"))["serial_number"] == "R000041858"

    def test_scanned_serial_preferred_over_mfg(self):
        row = _extract(_raw(MFGSerialNumber="R001", ScannedMFGSerialNumber="SCANNED-9"))
        assert row["serial_number"] == "SCANNED-9"

    def test_placeholder_equal_to_inventory_id_is_rejected(self):
        """The source auto-fills MFGSerialNumber with the InventoryId (~26% of stock).

        Such a value is not a scannable label and must not masquerade as one.
        """
        row = _extract(_raw(InventoryId=127937, MFGSerialNumber="127937"))
        assert row["serial_number"] is None

    def test_null_serial_stays_null(self):
        row = _extract(_raw(MFGSerialNumber=None, ScannedMFGSerialNumber=None))
        assert row["serial_number"] is None

    def test_falls_back_to_mfg_when_scanned_is_placeholder(self):
        row = _extract(_raw(
            InventoryId=500, ScannedMFGSerialNumber="500", MFGSerialNumber="REAL-1",
        ))
        assert row["serial_number"] == "REAL-1"


class TestExtractDisplayFields:
    def test_image_and_description_captured(self):
        row = _extract(_raw())
        assert row["image_url"] == "https://cdn.example/img.jpg"
        assert row["short_description"] == "MODEL-X 24in Dishwasher"

    def test_falls_back_to_imgurl_when_no_mobile_image(self):
        raw = _raw(MobileImageURL=None)
        raw["ImgURL"] = "https://cdn.example/desktop.jpg"
        assert _extract(raw)["image_url"] == "https://cdn.example/desktop.jpg"

    def test_blank_description_becomes_null(self):
        assert _extract(_raw(ShortDescription="  "))["short_description"] is None
