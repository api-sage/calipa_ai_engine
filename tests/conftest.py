"""Test fixtures. Database tests run against a throwaway Postgres database created
for the session and dropped afterwards. Set CALIPA_TEST_ADMIN_URL to point at a
different server; tests that need Postgres are skipped if it is unreachable."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from calipa.api.app import create_app
from calipa.audit_trail.db import connect, migrate

ADMIN_URL = os.environ.get("CALIPA_TEST_ADMIN_URL", "postgresql:///postgres")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable at {ADMIN_URL}: {exc}")
    name = f"calipa_test_{uuid4().hex[:12]}"
    admin.execute(f'create database "{name}"')  # type: ignore[arg-type]
    base = ADMIN_URL.rsplit("/", 1)[0]
    try:
        yield f"{base}/{name}"
    finally:
        admin.execute(f'drop database "{name}" with (force)')  # type: ignore[arg-type]
        admin.close()


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> str:
    with connect(database_url) as conn:
        migrate(conn)
    return database_url


@pytest.fixture
def conn(migrated_url: str) -> Iterator[psycopg.Connection]:
    with connect(migrated_url) as connection:
        yield connection


@pytest.fixture
def api(migrated_url: str) -> Iterator[TestClient]:
    """The HTTP API over the test database, with development sign-in."""
    from tests.test_api import deps

    with TestClient(create_app(deps(migrated_url))) as client:
        yield client


@pytest.fixture
def empty_url(database_url: str) -> Iterator[str]:
    """A database of the test's own, with no migrations: for tests that need to count
    everything in it, or that tamper with it."""
    name = f"calipa_own_{uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')  # type: ignore[arg-type]
        try:
            yield f"{ADMIN_URL.rsplit('/', 1)[0]}/{name}"
        finally:
            admin.execute(f'drop database "{name}" with (force)')  # type: ignore[arg-type]


@pytest.fixture
def own(empty_url: str) -> Iterator[psycopg.Connection]:
    """A migrated database of the test's own."""
    with connect(empty_url) as conn:
        migrate(conn)
        yield conn
