"""Connections and migrations."""

from __future__ import annotations

from importlib import resources

import psycopg
from psycopg_pool import ConnectionPool

from calipa.canonical import sha256_hex

_MIGRATIONS_PACKAGE = "calipa.audit_trail.migrations"


class MigrationError(RuntimeError):
    pass


def connect(database_url: str) -> psycopg.Connection:
    """Autocommit connection: a single insert is durable as soon as it returns, and
    `conn.transaction()` blocks are real transactions rather than savepoints inside
    an implicit one that might never be committed."""
    return psycopg.connect(database_url, autocommit=True)


def open_pool(database_url: str, *, size: int, open_now: bool = True) -> ConnectionPool:
    """A pool of autocommit connections, each checked before it is handed out so a
    connection dropped by the server is replaced rather than failing a request."""
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=size,
        kwargs={"autocommit": True},
        check=ConnectionPool.check_connection,
        open=open_now,
        name="calipa",
    )


def _migration_files() -> list[tuple[str, str]]:
    files = resources.files(_MIGRATIONS_PACKAGE)
    names = sorted(f.name for f in files.iterdir() if f.name.endswith(".sql"))
    return [(name.removesuffix(".sql"), files.joinpath(name).read_text("utf-8")) for name in names]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations in order, each in its own transaction.

    Applied migrations are recorded with their SHA-256. If an applied file has since
    changed, migration stops: schema history must not be rewritten.
    """
    with conn.transaction():
        conn.execute(
            """
            create table if not exists schema_migrations (
                version    text        primary key,
                sha256     char(64)    not null,
                applied_at timestamptz not null default now()
            )
            """
        )
    applied: dict[str, str] = dict(
        conn.execute("select version, sha256 from schema_migrations").fetchall()
    )

    newly_applied: list[str] = []
    for version, sql in _migration_files():
        digest = sha256_hex(sql.encode("utf-8"))
        if version in applied:
            if applied[version] != digest:
                raise MigrationError(f"migration {version} changed after it was applied")
            continue
        with conn.transaction():
            conn.execute(sql.encode("utf-8"))  # trusted, packaged SQL file
            conn.execute(
                "insert into schema_migrations (version, sha256) values (%s, %s)",
                (version, digest),
            )
        newly_applied.append(version)
    return newly_applied


def pending_migrations(conn: psycopg.Connection) -> list[str]:
    """Migrations not yet applied, without changing anything: what a service running
    as calipa_app (which cannot create tables) checks at start-up."""
    exists = conn.execute("select to_regclass('schema_migrations') is not null").fetchone()
    applied: dict[str, str] = (
        dict(conn.execute("select version, sha256 from schema_migrations").fetchall())
        if exists and exists[0]
        else {}
    )
    pending: list[str] = []
    for version, sql in _migration_files():
        if version not in applied:
            pending.append(version)
        elif applied[version] != sha256_hex(sql.encode("utf-8")):
            raise MigrationError(f"migration {version} changed after it was applied")
    return pending


def require_current_schema(conn: psycopg.Connection) -> None:
    pending = pending_migrations(conn)
    if pending:
        raise MigrationError(
            f"database is missing migrations {pending}; run "
            "`python -m calipa.audit_trail.db migrate` as calipa_migrator"
        )


def main() -> None:
    """uv run python -m calipa.audit_trail.db {migrate,check}"""
    import argparse

    from calipa.config import get_settings

    parser = argparse.ArgumentParser(description="Database migrations.")
    parser.add_argument("action", choices=["migrate", "check"])
    args = parser.parse_args()
    with connect(get_settings().database_url) as conn:
        if args.action == "migrate":
            print(f"applied: {migrate(conn) or 'nothing (up to date)'}")
            return
        pending = pending_migrations(conn)
    print(f"pending: {pending or 'none'}")
    raise SystemExit(1 if pending else 0)


if __name__ == "__main__":
    main()
