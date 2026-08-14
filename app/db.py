"""Database access.

PostgreSQL is the system of record, following the same infrastructure pattern as
the RDX Daily Intelligence Engine. The two databases stay logically separate:
opportunity intelligence and marketing intelligence connect through controlled
events (``marketing_events``), never through cross-table reads.

SQLite is supported so tests and local development run without a server.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

from .models import metadata

DEFAULT_SQLITE_URL = "sqlite+pysqlite:///./rdx_marketing.db"


def database_url(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    return os.environ.get("RDX_MARKETING_DATABASE_URL", DEFAULT_SQLITE_URL)


def make_engine(url: Optional[str] = None, echo: bool = False) -> Engine:
    resolved = database_url(url)
    engine = create_engine(resolved, echo=echo, future=True)
    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    return engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_all(engine: Engine) -> None:
    metadata.create_all(engine)


@contextmanager
def transaction(engine: Engine) -> Iterator[Connection]:
    """A single committed unit of work."""
    with engine.begin() as conn:
        yield conn


def render_postgres_ddl() -> str:
    """Emit the canonical PostgreSQL DDL for ``migrations/``."""
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialect = __import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    chunks = []
    for table in metadata.sorted_tables:
        chunks.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            chunks.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(chunks) + "\n"
