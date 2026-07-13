"""Shared pytest fixtures.

Integration tests need a real PostgreSQL. FOR UPDATE SKIP LOCKED is the entire safety
mechanism behind concurrent picking, and it cannot be faked — a fake would only prove
the fake works. So those tests run against a real database, and are skipped unless one
is supplied:

    pytest --env-file "C:\\path\\to\\.env"

Without it the pure suite still runs everywhere, including CI.

The env file is read through config.load(), never by reading the environment directly:
config.py is the only module permitted to do that, and gate.py check B enforces it.
"""
from __future__ import annotations

import psycopg
import pytest

from warehouse_app import config


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--env-file",
        action="store",
        default=None,
        help="Path to a .env supplying DATABASE_URL. Enables the integration tests.",
    )


@pytest.fixture(scope="session")
def database_url(request: pytest.FixtureRequest) -> str:
    env_file = request.config.getoption("--env-file")
    if not env_file:
        pytest.skip("integration test needs a real database — pass --env-file")
    return config.load(env_file=env_file).database_url


@pytest.fixture()
def conn(database_url: str):
    with psycopg.connect(database_url) as connection:
        yield connection
