# Test fakes — import FakeSource / NullSink from adapters directly.
# They live in warehouse_app.adapters.source.fake_source and
# warehouse_app.adapters.sink.null_sink to keep them importable in production
# (offline dev mode) without a test dependency.
