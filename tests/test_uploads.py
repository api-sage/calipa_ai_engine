"""Runs from an uploaded workbook: the upload round-trip, the template, and a run
from upload to reconciled breaks without touching the bank or the model."""

from __future__ import annotations

from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient
from openpyxl.workbook.workbook import Workbook  # type: ignore[import-untyped]

from calipa.audit_trail.artifacts import require_artifact
from calipa.config import Settings
from calipa.engine.contract import ExceptionType, ReconciliationResult
from calipa.ingest.excel import parse_workbook
from calipa.orchestrator.lifecycle import RunState as S
from calipa.orchestrator.runner import Orchestrator
from calipa.orchestrator.steps import (
    APPROVED_SCOPE,
    ENGINE_RESULT,
    GL_SNAPSHOT,
    REQUESTED_SCOPE,
    SOURCE_UPLOAD,
    ApprovedScope,
    RequestedScope,
    SnapshotArtifact,
    SourceUpload,
    in_scope_exceptions,
)
from calipa.reference import load_control_catalog
from calipa.worker import build_handlers
from tests.interpreter_helpers import NOW
from tests.test_api import as_

SETTINGS = Settings(_env_file=None, bank_timezone="Africa/Lagos")  # type: ignore[call-arg]

GL_HEADERS = [
    "id",
    "transferId",
    "externalReference",
    "debitedAccount",
    "creditedAccount",
    "entryType",
    "currency",
    "amount",
    "createdAt",
]
STMT_HEADERS = [
    "statementId",
    "externalReference",
    "debitedAccount",
    "creditedAccount",
    "entryType",
    "currency",
    "amount",
    "valueDate",
]


def sample_workbook() -> bytes:
    """The bank's test data in miniature: one amount mismatch, one leg missing from
    the statement, one duplicated statement line, one line missing from the GL."""
    wb = Workbook()
    gl = wb.active
    assert gl is not None
    gl.title = "GL Transactions"
    gl.append(GL_HEADERS)
    gl.append(
        ["", "", "REF-2", "0123456890", "0125548976", "CREDIT", "USD", 1.02, "2026-10-25T20:36:35Z"]
    )
    gl.append(
        ["", "", "REF-2", "0123456890", "0125548976", "DEBIT", "GBP", 1.47, "2026-10-25T20:36:35Z"]
    )
    gl.append(
        ["", "", "REF-1", "0123456890", "6307882400", "DEBIT", "USD", 13.58, "2026-10-25T20:36:13Z"]
    )
    stmt = wb.create_sheet("Statement")
    stmt.append(STMT_HEADERS)
    stmt.append(
        [
            "STMT-0003",
            "REF-2",
            "0123456890",
            "0125548976",
            "CREDIT",
            "USD",
            1.20,
            "2026-10-25T20:36:35Z",
        ]
    )
    stmt.append(
        [
            "STMT-0007",
            "REF-1",
            "0123456890",
            "6307882400",
            "DEBIT",
            "USD",
            13.58,
            "2026-10-25T20:36:13Z",
        ]
    )
    stmt.append(
        [
            "STMT-0008",
            "REF-1",
            "0123456890",
            "6307882400",
            "DEBIT",
            "USD",
            13.58,
            "2026-10-25T20:36:13Z",
        ]
    )
    stmt.append(
        [
            "STMT-0009",
            "REF-3",
            "0123456890",
            "6307882400",
            "DEBIT",
            "USD",
            5,
            "2026-10-25T20:41:02Z",
        ]
    )
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def upload(api: TestClient, content: bytes, filename: str = "recon.xlsx") -> Any:
    return api.post(
        "/runs/uploads",
        files={
            "file": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=as_("auditor@bank"),
    )


def start_upload_run(api: TestClient, preview: dict[str, Any], **scope: Any) -> Any:
    body = {
        "request_text": f"Reconcile {preview['filename']} for 25 October 2026",
        "idempotency_key": f"k-{uuid4()}",
        "upload_sha256": preview["upload_sha256"],
        "upload_filename": preview["filename"],
        "scope": {
            "objective": "statement_reconciliation",
            "period_start": "2026-10-25",
            "period_end": "2026-10-25",
            **scope,
        },
    }
    created = api.post("/runs", json=body, headers=as_("auditor@bank"))
    assert created.status_code == 202, created.text
    return created.json()


def advance_to_analysis(conn: psycopg.Connection, run_id: UUID) -> S:
    handlers = build_handlers(fcy=None, llm=None, settings=SETTINGS)  # type: ignore[arg-type]
    wanted = {S.INTERPRETING, S.EXTRACTING, S.EXTRACTION_PARTIAL, S.RECONCILING}
    orchestrator = Orchestrator(
        conn,
        {state: handler for state, handler in handlers.items() if state in wanted},
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    return orchestrator.advance(run_id)


def test_upload_previews_what_the_file_holds(api: TestClient) -> None:
    response = upload(api, sample_workbook())
    assert response.status_code == 200, response.text
    preview = response.json()
    assert (preview["gl_entries"], preview["statement_lines"], preview["has_statement"]) == (
        3,
        4,
        True,
    )
    assert (preview["period_start"], preview["period_end"]) == ("2026-10-25", "2026-10-25")
    assert preview["currencies"] == ["GBP", "USD"]
    assert preview["upload_sha256"] and preview["filename"] == "recon.xlsx"


def test_upload_refuses_bad_rows_with_places(api: TestClient) -> None:
    wb = Workbook()
    gl = wb.active
    assert gl is not None
    gl.title = "GL Transactions"
    gl.append(GL_HEADERS)
    gl.append(["", "", "REF-1", "nope", "0125548976", "DEBIT", "GBP", 1, "2026-10-25T10:00:00Z"])
    out = BytesIO()
    wb.save(out)
    response = upload(api, out.getvalue())
    assert response.status_code == 422
    rejected = response.json()
    assert rejected["total_issues"] == 1
    (issue,) = rejected["issues"]
    assert (issue["sheet"], issue["row"]) == ("GL Transactions", 2)


def test_upload_refuses_non_workbooks(api: TestClient) -> None:
    assert upload(api, b"{}", filename="recon.json").status_code == 422
    assert upload(api, b"not a workbook", filename="recon.xlsx").status_code == 422


def test_upload_needs_an_initiator(api: TestClient) -> None:
    response = api.post(
        "/runs/uploads",
        files={"file": ("recon.xlsx", sample_workbook())},
        headers=as_("reviewer@bank"),
    )
    assert response.status_code == 403


def test_template_downloads_and_parses(api: TestClient) -> None:
    response = api.get("/runs/uploads/template", headers=as_("auditor@bank"))
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    parsed = parse_workbook(response.content, filename="template.xlsx")
    assert len(parsed.gl_entries) == 2 and len(parsed.statement_lines) == 1


def test_upload_without_scope_is_rejected(api: TestClient) -> None:
    preview = upload(api, sample_workbook()).json()
    body = {
        "request_text": "Reconcile the upload",
        "idempotency_key": f"k-{uuid4()}",
        "upload_sha256": preview["upload_sha256"],
    }
    assert api.post("/runs", json=body, headers=as_("auditor@bank")).status_code == 422


def test_unknown_upload_is_rejected(api: TestClient) -> None:
    body = {
        "request_text": "Reconcile the upload",
        "idempotency_key": f"k-{uuid4()}",
        "upload_sha256": "0" * 64,
        "scope": {
            "objective": "statement_reconciliation",
            "period_start": "2026-10-25",
            "period_end": "2026-10-25",
        },
    }
    assert api.post("/runs", json=body, headers=as_("auditor@bank")).status_code == 422


def test_upload_run_finds_the_four_breaks(api: TestClient, conn: psycopg.Connection) -> None:
    preview = upload(api, sample_workbook()).json()
    run = start_upload_run(api, preview)
    run_id = UUID(run["run_id"])

    _, source = require_artifact(conn, run_id, SOURCE_UPLOAD, SourceUpload)
    assert source.filename == "recon.xlsx"
    _, requested = require_artifact(conn, run_id, REQUESTED_SCOPE, RequestedScope)
    assert requested.objective.value == "statement_reconciliation"

    assert advance_to_analysis(conn, run_id) is S.ANALYSING

    _, snap = require_artifact(conn, run_id, GL_SNAPSHOT, SnapshotArtifact)
    assert snap.source == "excel_upload"
    assert len(snap.entries) == 3 and len(snap.statements) == 4
    _, result = require_artifact(conn, run_id, ENGINE_RESULT, ReconciliationResult)
    statements = {e.type for e in result.exceptions if e.rule_id.startswith("R-STMT")}
    assert statements == {
        ExceptionType.STATEMENT_AMOUNT_MISMATCH,
        ExceptionType.MISSING_FROM_STATEMENT,
        ExceptionType.DUPLICATE_ON_STATEMENT,
        ExceptionType.MISSING_FROM_GL,
    }
    in_scope = in_scope_exceptions(
        result.exceptions, result_scope(conn, run_id), load_control_catalog()
    )
    assert {e.type for e in in_scope} == statements


def result_scope(conn: psycopg.Connection, run_id: UUID) -> ApprovedScope:
    return require_artifact(conn, run_id, APPROVED_SCOPE, ApprovedScope)[1]


def test_upload_run_asks_about_an_open_period_without_the_model(
    api: TestClient, conn: psycopg.Connection
) -> None:
    preview = upload(api, sample_workbook()).json()
    run = start_upload_run(api, preview, period_start="2026-10-26", period_end="2026-10-26")
    handlers = build_handlers(fcy=None, llm=None, settings=SETTINGS)  # type: ignore[arg-type]
    orchestrator = Orchestrator(
        conn, {S.INTERPRETING: handlers[S.INTERPRETING]}, clock=lambda: NOW, sleep=lambda _: None
    )
    assert orchestrator.advance(UUID(run["run_id"])) is S.CLARIFICATION_REQUESTED
