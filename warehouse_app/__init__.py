# Owns: package-level factory functions that wire config → concrete adapters.
# Must not: contain domain logic or database calls.
# May import: warehouse_app.config, warehouse_app.adapters.*.

from __future__ import annotations

from warehouse_app.config import Config


def make_source(cfg: Config):
    """Return the source adapter matching SOURCE_TYPE."""
    if cfg.source_type == "fake":
        from warehouse_app.adapters.source.fake_source import FakeSource
        return FakeSource()
    from warehouse_app.adapters.source.http_source import HttpSource
    return HttpSource(cfg)


def make_scanner_writer(cfg: Config):
    """Return the ERP scanner-write adapter matching SOURCE_TYPE.

    Follows SOURCE_TYPE (the scanner API is part of the same upstream ERP): a 'fake'
    source yields a FakeScannerWriter so the full write path runs offline. Operator
    credentials are NOT taken here — they are injected per call by the caller.
    """
    if cfg.source_type == "fake":
        from warehouse_app.adapters.source.scanner_write import FakeScannerWriter
        return FakeScannerWriter()
    from warehouse_app.adapters.source.scanner_write import HttpScannerWriter
    return HttpScannerWriter(cfg.source_base_url)
