"""A fresh database for the web app's browser tests (web/e2e), seeded with runs in the
states those tests need. Every step runs on the scripted model from the unit tests, so
seeding costs nothing and calls no bank.

    uv run python -m tests.e2e_seed <database name> <ids.json>

Drops and recreates the database on the server at CALIPA_TEST_ADMIN_URL, migrates it,
and writes the seeded run ids to <ids.json>.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg

from calipa.audit_trail.db import connect, migrate
from calipa.audit_trail.store import create_run
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.lifecycle import start, transition
from tests.test_report_step import GOOD, WITH_DIGIT, run_to_review

ADMIN_URL = os.environ.get("CALIPA_TEST_ADMIN_URL", "postgresql:///postgres")


def seed(url: str) -> dict[str, str]:
    with connect(url) as conn:
        migrate(conn)
        pending, _, _ = run_to_review(conn, GOOD)
        approve, _, _ = run_to_review(conn, GOOD)
        send_back, _, _ = run_to_review(conn, GOOD)
        flagged, _, _ = run_to_review(conn, WITH_DIGIT, WITH_DIGIT)

        question = create_run(
            conn,
            idempotency_key=f"e2e-{uuid4()}",
            initiated_by="auditor@bank",
            request_text="Duplicates for all of 2025, every account",
        ).run_id
        start(conn, question, actor="auditor@bank")
        transition(conn, question, S.INTERPRETING, actor="e2e")
        transition(
            conn,
            question,
            S.CLARIFICATION_REQUESTED,
            actor="e2e",
            payload={"question": "The period is 365 days; which 92 days should I audit?"},
        )
    ids = {
        "pending": pending,
        "approve": approve,
        "send_back": send_back,
        "flagged": flagged,
        "question": question,
    }
    return {k: str(v) for k, v in ids.items()}


def main(name: str, out: Path) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.execute(f'create database "{name}"')
    ids = seed(f"{ADMIN_URL.rsplit('/', 1)[0]}/{name}")
    out.write_text(json.dumps(ids, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
