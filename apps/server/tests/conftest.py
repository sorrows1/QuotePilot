from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from quotepilot_api.database import database_engine, database_url
from quotepilot_api.migrate import migrate


@pytest.fixture
def empty_database() -> Iterator[Engine]:
    """Never downgrade an existing database: create and destroy only our unique test DB."""
    url = database_url()
    name = "qt002_test_" + uuid4().hex
    admin = database_engine(url)
    engine = database_engine(url.set(database=name))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


@pytest.fixture
def database(empty_database: Engine) -> Engine:
    migrate(empty_database)
    return empty_database
