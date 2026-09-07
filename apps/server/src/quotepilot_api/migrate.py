"""Single migration runner, serialized across processes by a PostgreSQL transaction lock."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from quotepilot_api.database import database_engine

MIGRATION_LOCK = 762002


def migrate(engine: Engine, direction: str = "upgrade", revision: str = "head") -> None:
    if direction not in {"upgrade", "downgrade"}:
        raise ValueError("Migration direction must be upgrade or downgrade.")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '30s'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        config.attributes["connection"] = connection
        if direction == "upgrade":
            command.upgrade(config, revision)
        else:
            command.downgrade(config, revision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=["upgrade", "downgrade"], default="upgrade", nargs="?")
    parser.add_argument("revision", default="head", nargs="?")
    args = parser.parse_args()
    engine = None
    try:
        engine = database_engine()
        migrate(engine, args.direction, args.revision)
    except (SQLAlchemyError, ValueError, CommandError):
        print(
            "Migration failed. Check database configuration, connectivity and migration state.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
