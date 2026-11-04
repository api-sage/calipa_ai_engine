"""The uploaded workbook: sheets, headers, values and every way a file is refused."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from openpyxl.workbook.workbook import Workbook  # type: ignore[import-untyped]

from calipa.ingest import excel
from calipa.ingest.excel import (
    MAX_ROWS_PER_SHEET,
    WorkbookError,
    build_template,
    parse_workbook,
)

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

T1 = "828972ad-6b3d-4f16-b230-4ef2f4875b33"
E1 = "295acb22-8b4b-4b33-85de-02103c494399"


def workbook(
    gl_rows: list[list[Any]] | None = None,
    stmt_rows: list[list[Any]] | None = None,
    *,
    gl_headers: list[str] = GL_HEADERS,
    stmt_headers: list[str] = STMT_HEADERS,
    gl_title: str = "GL Transactions",
    extra_sheets: dict[str, list[list[Any]]] | None = None,
) -> bytes:
    wb = Workbook()
    gl = wb.active
    assert gl is not None
    gl.title = gl_title
    gl.append(gl_headers)
    for row in gl_rows if gl_rows is not None else [default_gl_row()]:
        gl.append(row)
    if stmt_rows is not None:
        stmt = wb.create_sheet("Statement")
        stmt.append(stmt_headers)
        for row in stmt_rows:
            stmt.append(row)
    for title, rows in (extra_sheets or {}).items():
        sheet = wb.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def default_gl_row() -> list[Any]:
    return [
        E1,
        T1,
        "REF-1",
        "0123456890",
        "0125548976",
        "DEBIT",
        "GBP",
        0.75,
        "2026-10-25T20:36:37Z",
    ]


def default_stmt_row() -> list[Any]:
    return ["STMT-0001", "REF-1", "0123456890", "0125548976", "DEBIT", "GBP", 0.75, "2026-10-25"]


def test_template_parses_cleanly() -> None:
    parsed = parse_workbook(build_template(), filename="template.xlsx")
    assert len(parsed.gl_entries) == 2 and len(parsed.statement_lines) == 1
    assert parsed.has_statement
    assert parsed.period_start == parsed.period_end == date(2026, 10, 25)
    assert parsed.currencies == ("GBP", "USD")
    assert any("How to fill this in" in w for w in parsed.warnings)


def test_amounts_survive_excel_floats_exactly() -> None:
    content = workbook(
        gl_rows=[
            [
                E1,
                T1,
                "REF-1",
                "0123456890",
                "0125548976",
                "DEBIT",
                "GBP",
                1.02,
                "2026-10-25T20:36:37Z",
            ],
        ]
    )
    (entry,) = parse_workbook(content).gl_entries
    assert entry.amount == Decimal("1.02")


def test_ids_come_from_the_file_when_present() -> None:
    (entry,) = parse_workbook(workbook()).gl_entries
    assert (str(entry.id), str(entry.transfer_id)) == (E1, T1)


def test_missing_ids_are_derived_and_shared_per_reference() -> None:
    rows = [
        ["", "", "REF-1", "0123456890", "0125548976", "DEBIT", "GBP", 1, "2026-10-25T10:00:00Z"],
        ["", "", "REF-1", "0123456890", "6307882400", "DEBIT", "USD", 2, "2026-10-25T10:00:00Z"],
        ["", "", "REF-2", "0123456890", "6307882400", "DEBIT", "USD", 3, "2026-10-25T10:00:00Z"],
    ]
    first = parse_workbook(workbook(gl_rows=rows)).gl_entries
    second = parse_workbook(workbook(gl_rows=rows)).gl_entries
    assert [e.id for e in first] == [e.id for e in second]
    assert first[0].transfer_id == first[1].transfer_id != first[2].transfer_id
    assert len({e.id for e in first}) == 3


def test_snake_case_headers_dr_cr_and_numeric_accounts() -> None:
    content = workbook(
        gl_rows=[
            ["", "", "REF-1", 123456890, 125548976, "dr", "gbp", 1, "2026-10-25T10:00:00Z"],
            ["", "", "REF-1", "0123456890", "6307882400", "CR", "USD", 2, "2026-10-25T10:00:00Z"],
        ],
        gl_headers=[
            "id",
            "transfer_id",
            "external_reference",
            "debited_account",
            "credited_account",
            "entry_type",
            "currency",
            "amount",
            "created_at",
        ],
    )
    first, second = parse_workbook(content).gl_entries
    assert (first.debited_account, first.credited_account) == ("0123456890", "0125548976")
    assert (first.entry_type, second.entry_type) == ("DEBIT", "CREDIT")
    assert first.currency.value == "GBP"


def test_naive_dates_are_read_in_the_bank_timezone() -> None:
    content = workbook(
        gl_rows=[
            [
                E1,
                T1,
                "REF-1",
                "0123456890",
                "0125548976",
                "DEBIT",
                "GBP",
                1,
                datetime(2026, 10, 25, 10, 0),
            ],
            [
                E1.replace("2", "3"),
                T1,
                "REF-1",
                "0123456890",
                "0125548976",
                "DEBIT",
                "GBP",
                1,
                date(2026, 10, 25),
            ],
        ]
    )
    first, second = parse_workbook(content).gl_entries
    lagos = ZoneInfo("Africa/Lagos")
    assert first.created_at == datetime(2026, 10, 25, 10, 0, tzinfo=lagos)
    assert second.created_at == datetime(2026, 10, 25, 0, 0, tzinfo=lagos)


def test_statement_sheet_is_optional() -> None:
    parsed = parse_workbook(workbook())
    assert parsed.statement_lines == () and not parsed.has_statement


def test_statement_rows_are_validated_like_gl_rows() -> None:
    content = workbook(stmt_rows=[default_stmt_row()])
    (line,) = parse_workbook(content).statement_lines
    assert (line.statement_id, line.amount) == ("STMT-0001", Decimal("0.75"))
    assert line.value_date.tzinfo is not None


def test_unknown_sheets_are_ignored_with_a_warning() -> None:
    content = workbook(extra_sheets={"Expected Breaks": [["breakType"], ["x"]]})
    parsed = parse_workbook(content)
    assert parsed.warnings == ("Sheet 'Expected Breaks' is not used and was ignored.",)


def test_not_a_workbook_is_rejected() -> None:
    with pytest.raises(WorkbookError, match=r"not a readable \.xlsx file"):
        parse_workbook(b"not a workbook")


def test_missing_gl_sheet_lists_what_was_seen() -> None:
    wb = Workbook()
    sheet = wb.active
    assert sheet is not None
    sheet.title = "Something else"
    sheet.append(["a"])
    out = BytesIO()
    wb.save(out)
    with pytest.raises(WorkbookError, match="Something else"):
        parse_workbook(out.getvalue())


def test_missing_column_names_what_to_add() -> None:
    content = workbook(gl_headers=[h for h in GL_HEADERS if h != "amount"])
    with pytest.raises(WorkbookError) as exc_info:
        parse_workbook(content)
    assert any("amount" in i.message for i in exc_info.value.issues)


def test_every_bad_row_is_reported_with_its_place() -> None:
    content = workbook(
        gl_rows=[
            [E1, T1, "REF-1", "nope", "0125548976", "DEBIT", "GBP", 1, "2026-10-25T10:00:00Z"],
            [E1, T1, "REF-1", "0123456890", "0125548976", "SIDEWAYS", "XX", "lots", "yesterday"],
        ],
        stmt_rows=[
            ["STMT-0001", "REF-1", "0123456890", "0125548976", "DEBIT", "GBP", 1, "2026-10-25"],
            ["STMT-0001", "REF-1", "0123456890", "0125548976", "DEBIT", "GBP", 1, "2026-10-25"],
        ],
    )
    with pytest.raises(WorkbookError) as exc_info:
        parse_workbook(content)
    by_row = {(i.sheet, i.row) for i in exc_info.value.issues}
    assert ("GL Transactions", 2) in by_row
    assert ("GL Transactions", 3) in by_row
    assert ("Statement", 3) in by_row  # duplicate statementId
    assert exc_info.value.total == len(exc_info.value.issues)


def test_duplicate_gl_ids_are_rejected() -> None:
    content = workbook(gl_rows=[default_gl_row(), default_gl_row()])
    with pytest.raises(WorkbookError, match="duplicate id"):
        parse_workbook(content)


def test_empty_gl_sheet_is_rejected() -> None:
    with pytest.raises(WorkbookError, match="no entries"):
        parse_workbook(workbook(gl_rows=[]))


def test_row_cap_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(excel, "MAX_ROWS_PER_SHEET", 1)
    assert MAX_ROWS_PER_SHEET > 1  # the real cap is untouched
    with pytest.raises(WorkbookError, match="more than 1 rows"):
        parse_workbook(workbook(gl_rows=[default_gl_row(), default_gl_row()]))
