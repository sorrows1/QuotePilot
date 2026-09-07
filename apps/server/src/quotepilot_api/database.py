"""Explicit PostgreSQL configuration shared by application code and migrations."""

import os
from urllib.parse import unquote

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def database_url() -> URL:
    raw = os.environ.get("DATABASE_URL")
    try:
        if raw:
            url = make_url(raw)
            # SQLAlchemy decodes credentials but leaves the URL path encoded.
            url = url.set(database=unquote(url.database or ""))
        else:
            # Compose supplies all parts explicitly; no production credential defaults.
            parts = [
                os.environ.get(key)
                for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DATABASE_HOST")
            ]
            if not all(parts):
                raise ValueError
            url = URL.create(
                "postgresql+psycopg",
                username=parts[0],
                password=parts[1],
                database=parts[2],
                host=parts[3],
                port=5432,
            )
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError
        if not url.host or not url.database or not url.username:
            raise ValueError
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError
    except (ArgumentError, ValueError, TypeError):
        raise ValueError(
            "Set a valid PostgreSQL DATABASE_URL; use root commands for local dev."
        ) from None
    return url.set(drivername="postgresql+psycopg")


def database_engine(url: URL | None = None) -> Engine:
    return create_engine(
        url if url is not None else database_url(),
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
        pool_pre_ping=True,
        hide_parameters=True,
    )
