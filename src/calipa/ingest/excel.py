"""Reading the workbook the bank uploads.

The workbook has one sheet of GL entries and, optionally, one sheet of statement
lines to reconcile them against:

- "GL Transactions": one row per entry, with the same fields as the FCY API's
  GL entries (id, transferId, externalReference, debitedAccount, creditedAccount,
  entryType, currency, amount, createdAt). Headers are matched case-insensitively
  and may use snake_case; `id` and `transferId` may be omitted, in which case
  deterministic ids are derived (entries sharing a reference share a transfer).
- "Statement": one row per statement line (statementId plus the same movement
  fields, with valueDate for the date).

Any other sheet is ignored with a warning. A workbook with no usable GL sheet, or
with any invalid row, is rejected outright with every problem listed: a half-read
file must never become a half-audited run.

Excel stores amounts as floats and dates as serials. Amounts are converted with
Decimal(str(value)) so 0.75 stays 0.75, and naive dates are read in the bank's
timezone; both conversions are documented on the parsed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.workbook.workbook import Workbook  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
)

from calipa.canonical import sha256_hex
from calipa.gateway.fcy import AccountNumber, GLEntry
from calipa.money import Currency, MoneyError, to_decimal

MAX_ROWS_PER_SHEET = 20_000
MAX_ISSUES = 200
MAX_UPLOAD_BYTES = 10_000_000

GL_SHEET_NAMES = frozenset({"gltransactions", "glentries", "gl", "generalledger"})
STATEMENT_SHEET_NAMES = frozenset({"statement", "statements", "bankstatement", "statementlines"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class StatementLine(_Strict):
    """One line of the bank statement, shaped like a GL entry so the two can be
    compared field by field. `id` is derived from the file and the statementId, so
    re-reading the same file gives the same line the same id."""

    id: UUID
    statement_id: str = Field(alias="statementId", max_length=64)
    external_reference: str = Field(alias="externalReference", max_length=64)
    debited_account: AccountNumber = Field(alias="debitedAccount")
    credited_account: AccountNumber = Field(alias="creditedAccount")
    entry_type: Literal["DEBIT", "CREDIT"] = Field(alias="entryType")
    currency: Currency
    amount: Annotated[Decimal, BeforeValidator(to_decimal)]
    value_date: AwareDatetime = Field(alias="valueDate")


@dataclass(frozen=True, slots=True)
class RowIssue:
    sheet: str
    row: int  # 1-based spreadsheet row; 1 for header problems, 0 for file problems
    column: str | None
    message: str


class WorkbookError(ValueError):
    """The workbook was rejected. `issues` holds the first problems found (at most
    MAX_ISSUES); `total` is how many rows had problems."""

    def __init__(self, issues: list[RowIssue], total: int) -> None:
        self.issues = issues
        self.total = total
        shown = "; ".join(
            f"{i.sheet} row {i.row}" + (f" ({i.column})" if i.column else "") + f": {i.message}"
            for i in issues[:5]
        )
        super().__init__(f"{total} row(s) need attention: {shown}")


@dataclass(frozen=True, slots=True)
class ParsedWorkbook:
    filename: str
    file_sha256: str
    gl_entries: tuple[GLEntry, ...]
    statement_lines: tuple[StatementLine, ...]
    warnings: tuple[str, ...]
    period_start: date
    period_end: date
    currencies: tuple[str, ...]

    @property
    def has_statement(self) -> bool:
        return bool(self.statement_lines)


def _normalize_header(value: Any) -> str:
    return "".join(str(value).split()).replace("_", "").lower()


# Normalized header -> canonical field. The first name in each group is the one the
# template uses.
_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "entryid", "glid"),
    "transfer_id": ("transferid", "transfer", "transferreference"),
    "external_reference": (
        "externalreference",
        "reference",
        "transactionreference",
        "txnreference",
        "txnref",
        "ref",
    ),
    "debited_account": (
        "debitedaccount",
        "debitaccount",
        "debited",
        "debit",
        "fromaccount",
        "from",
    ),
    "credited_account": (
        "creditedaccount",
        "creditaccount",
        "credited",
        "credit",
        "toaccount",
        "to",
    ),
    "entry_type": ("entrytype", "type", "direction", "drcr", "debitcredit"),
    "currency": ("currency", "ccy", "curr"),
    "amount": ("amount", "value"),
    "created_at": (
        "createdat",
        "date",
        "postingdate",
        "valuedate",
        "bookingdate",
        "transactiondate",
        "entrydate",
    ),
    "statement_id": ("statementid", "stmtid", "lineid", "id"),
    "value_date": (
        "valuedate",
        "date",
        "postingdate",
        "createdat",
        "bookingdate",
        "transactiondate",
        "entrydate",
    ),
}

_REQUIRED_GL = (
    "external_reference",
    "debited_account",
    "credited_account",
    "entry_type",
    "currency",
    "amount",
    "created_at",
)
_REQUIRED_STMT = (
    "statement_id",
    "external_reference",
    "debited_account",
    "credited_account",
    "entry_type",
    "currency",
    "amount",
    "value_date",
)


def _lookup(headers: list[str]) -> dict[str, int]:
    """Normalized header names, in column order, for a sheet's first row."""
    return {name: index for index, name in enumerate(headers)}


def _resolve_columns(
    sheet: str, headers: list[str], required: tuple[str, ...], issues: list[RowIssue]
) -> dict[str, int]:
    by_header = _lookup(headers)
    resolved: dict[str, int] = {}
    for field in required:
        found = [(alias, by_header[alias]) for alias in _ALIASES[field] if alias in by_header]
        if not found:
            issues.append(
                RowIssue(
                    sheet=sheet,
                    row=1,
                    column=None,
                    message=f"missing column for {_ALIASES[field][0]} "
                    f"(looked for: {', '.join(_ALIASES[field])})",
                )
            )
        else:
            resolved[field] = found[0][1]
    # Optional GL columns.
    for field in ("id", "transfer_id"):
        optional = [by_header[a] for a in _ALIASES[field] if a in by_header]
        if optional:
            resolved[field] = optional[0]
    return resolved


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_amount(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise MoneyError(f"not an amount: {value!r}")
    if isinstance(value, float):
        # Excel's storage: str(0.75) is "0.75", exact. Never Decimal(float).
        return Decimal(str(value))
    return to_decimal(value)


def _parse_moment(value: Any, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str) and value.strip():
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"not a date: {value!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment


def _parse_entry_type(value: Any) -> Literal["DEBIT", "CREDIT"]:
    text = (_text(value) or "").lower()
    if text in ("debit", "dr", "d"):
        return "DEBIT"
    if text in ("credit", "cr", "c"):
        return "CREDIT"
    raise ValueError(f"entryType must be DEBIT or CREDIT, got {value!r}")


def _parse_currency(value: Any) -> Currency:
    text = (_text(value) or "").upper()
    try:
        return Currency(text)
    except ValueError:
        raise ValueError(f"currency must be one of USD, EUR, GBP, NGN, got {value!r}") from None


def _parse_account(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError(f"not an account number: {value!r}")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"not an account number: {value!r}")
        value = int(value)
    text = str(value).strip()
    if text.isdigit():
        # A leading zero does not survive a numeric cell; restore the width.
        text = text.zfill(10)
    return text


def _parse_uuid(value: Any) -> UUID:
    text = _text(value)
    if text is None:
        raise ValueError("missing id")
    return UUID(text)


def parse_workbook(
    content: bytes, *, filename: str = "", default_tz: str = "Africa/Lagos"
) -> ParsedWorkbook:
    """Parse and validate an uploaded workbook, or raise WorkbookError listing why."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise WorkbookError(
            [RowIssue(sheet="", row=0, column=None, message="file is larger than 10 MB")], 1
        )
    tz = ZoneInfo(default_tz)
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise WorkbookError(
            [RowIssue(sheet="", row=0, column=None, message=f"not a readable .xlsx file: {exc}")],
            1,
        ) from exc
    try:
        return _parse_open(workbook, content=content, filename=filename, tz=tz)
    finally:
        workbook.close()


def _parse_open(
    workbook: Workbook, *, content: bytes, filename: str, tz: ZoneInfo
) -> ParsedWorkbook:
    file_sha = sha256_hex(content)
    issues: list[RowIssue] = []
    warnings: list[str] = []
    gl_sheet: Worksheet | None = None
    stmt_sheet: Worksheet | None = None
    for sheet in workbook.worksheets:
        normalized = _normalize_header(sheet.title)
        if normalized in GL_SHEET_NAMES and gl_sheet is None:
            gl_sheet = sheet
        elif normalized in STATEMENT_SHEET_NAMES and stmt_sheet is None:
            stmt_sheet = sheet
        else:
            warnings.append(f"Sheet {sheet.title!r} is not used and was ignored.")
    if gl_sheet is None:
        names = ", ".join(s.title for s in workbook.worksheets) or "no sheets"
        raise WorkbookError(
            [
                RowIssue(
                    sheet="",
                    row=0,
                    column=None,
                    message=f"no GL sheet found (looked for 'GL Transactions'; saw: {names})",
                )
            ],
            1,
        )
    entries = _parse_gl(gl_sheet, file_sha=file_sha, tz=tz, issues=issues)
    statements = (
        _parse_statement(stmt_sheet, file_sha=file_sha, tz=tz, issues=issues)
        if stmt_sheet is not None
        else []
    )
    if issues:
        raise WorkbookError(issues[:MAX_ISSUES], len(issues))
    if not entries:
        raise WorkbookError(
            [
                RowIssue(
                    sheet=str(gl_sheet.title),
                    row=0,
                    column=None,
                    message="the GL sheet has no entries",
                )
            ],
            1,
        )
    moments = [e.created_at for e in entries] + [s.value_date for s in statements]
    days = sorted(m.astimezone(tz).date() for m in moments)
    currencies = sorted(
        {e.currency.value for e in entries} | {s.currency.value for s in statements}
    )
    return ParsedWorkbook(
        filename=filename,
        file_sha256=file_sha,
        gl_entries=tuple(entries),
        statement_lines=tuple(statements),
        warnings=tuple(warnings),
        period_start=days[0],
        period_end=days[-1],
        currencies=tuple(currencies),
    )


def _rows(sheet: Worksheet) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in sheet.iter_rows(values_only=True)]


def _parse_gl(
    sheet: Worksheet, *, file_sha: str, tz: ZoneInfo, issues: list[RowIssue]
) -> list[GLEntry]:
    name = str(sheet.title)
    rows = _rows(sheet)
    if not rows:
        issues.append(RowIssue(sheet=name, row=0, column=None, message="the sheet is empty"))
        return []
    headers = [_normalize_header(cell) for cell in rows[0]]
    if len(set(headers)) != len(headers):
        issues.append(
            RowIssue(sheet=name, row=1, column=None, message="two columns share a header name")
        )
        return []
    columns = _resolve_columns(name, headers, _REQUIRED_GL, issues)
    if any(i.row == 1 for i in issues if i.sheet == name):
        return []
    entries: list[GLEntry] = []
    seen_ids: set[UUID] = set()
    data = [
        (number, row)
        for number, row in enumerate(rows[1:], start=2)
        if any(c is not None and str(c).strip() != "" for c in row)
    ]
    if len(data) > MAX_ROWS_PER_SHEET:
        issues.append(
            RowIssue(
                sheet=name,
                row=0,
                column=None,
                message=f"the sheet has more than {MAX_ROWS_PER_SHEET} rows",
            )
        )
        return []
    for number, row in data:
        entry = _gl_row(
            name, number, row, columns, file_sha=file_sha, tz=tz, issues=issues, seen_ids=seen_ids
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _cell(row: tuple[Any, ...], columns: dict[str, int], field: str) -> Any:
    index = columns.get(field)
    return row[index] if index is not None and index < len(row) else None


def _gl_row(
    sheet: str,
    number: int,
    row: tuple[Any, ...],
    columns: dict[str, int],
    *,
    file_sha: str,
    tz: ZoneInfo,
    issues: list[RowIssue],
    seen_ids: set[UUID],
) -> GLEntry | None:
    def fail(column: str, message: str) -> None:
        issues.append(RowIssue(sheet=sheet, row=number, column=column, message=message))

    reference = _text(_cell(row, columns, "external_reference"))
    if reference is None:
        fail("externalReference", "missing reference")
        return None
    try:
        entry_id = (
            _parse_uuid(_cell(row, columns, "id"))
            if _cell(row, columns, "id") is not None
            else uuid5(NAMESPACE_URL, f"calipa:upload:{file_sha}:gl:{number}")
        )
    except ValueError as exc:
        fail("id", str(exc))
        return None
    if entry_id in seen_ids:
        fail("id", f"duplicate id {entry_id}")
        return None
    try:
        transfer_id = (
            _parse_uuid(_cell(row, columns, "transfer_id"))
            if _cell(row, columns, "transfer_id") is not None
            else uuid5(NAMESPACE_URL, f"calipa:upload-transfer:{file_sha}:{reference}")
        )
    except ValueError as exc:
        fail("transferId", str(exc))
        return None
    try:
        entry = GLEntry(
            id=entry_id,
            transferId=transfer_id,
            externalReference=reference,
            debitedAccount=_parse_account(_cell(row, columns, "debited_account")),
            creditedAccount=_parse_account(_cell(row, columns, "credited_account")),
            entryType=_parse_entry_type(_cell(row, columns, "entry_type")),
            currency=_parse_currency(_cell(row, columns, "currency")),
            amount=_parse_amount(_cell(row, columns, "amount")),
            createdAt=_parse_moment(_cell(row, columns, "created_at"), tz),
        )
    except (ValueError, MoneyError, ValidationError) as exc:
        fail("row", _short_error(exc))
        return None
    seen_ids.add(entry_id)
    return entry


def _parse_statement(
    sheet: Worksheet, *, file_sha: str, tz: ZoneInfo, issues: list[RowIssue]
) -> list[StatementLine]:
    name = str(sheet.title)
    rows = _rows(sheet)
    if not rows:
        issues.append(RowIssue(sheet=name, row=0, column=None, message="the sheet is empty"))
        return []
    headers = [_normalize_header(cell) for cell in rows[0]]
    if len(set(headers)) != len(headers):
        issues.append(
            RowIssue(sheet=name, row=1, column=None, message="two columns share a header name")
        )
        return []
    columns = _resolve_columns(name, headers, _REQUIRED_STMT, issues)
    if any(i.row == 1 for i in issues if i.sheet == name):
        return []
    lines: list[StatementLine] = []
    seen: set[str] = set()
    data = [
        (number, row)
        for number, row in enumerate(rows[1:], start=2)
        if any(c is not None and str(c).strip() != "" for c in row)
    ]
    if len(data) > MAX_ROWS_PER_SHEET:
        issues.append(
            RowIssue(
                sheet=name,
                row=0,
                column=None,
                message=f"the sheet has more than {MAX_ROWS_PER_SHEET} rows",
            )
        )
        return []
    for number, row in data:
        line = _statement_row(name, number, row, columns, file_sha=file_sha, tz=tz, issues=issues)
        if line is not None:
            if line.statement_id in seen:
                issues.append(
                    RowIssue(
                        sheet=name,
                        row=number,
                        column="statementId",
                        message=f"duplicate statementId {line.statement_id}",
                    )
                )
                continue
            seen.add(line.statement_id)
            lines.append(line)
    return lines


def _statement_row(
    sheet: str,
    number: int,
    row: tuple[Any, ...],
    columns: dict[str, int],
    *,
    file_sha: str,
    tz: ZoneInfo,
    issues: list[RowIssue],
) -> StatementLine | None:
    statement_id = _text(_cell(row, columns, "statement_id"))
    if statement_id is None:
        issues.append(
            RowIssue(sheet=sheet, row=number, column="statementId", message="missing statementId")
        )
        return None
    try:
        return StatementLine(
            id=uuid5(NAMESPACE_URL, f"calipa:statement:{file_sha}:{statement_id}"),
            statementId=statement_id,
            externalReference=_text(_cell(row, columns, "external_reference")) or "",
            debitedAccount=_parse_account(_cell(row, columns, "debited_account")),
            creditedAccount=_parse_account(_cell(row, columns, "credited_account")),
            entryType=_parse_entry_type(_cell(row, columns, "entry_type")),
            currency=_parse_currency(_cell(row, columns, "currency")),
            amount=_parse_amount(_cell(row, columns, "amount")),
            valueDate=_parse_moment(_cell(row, columns, "value_date"), tz),
        )
    except (ValueError, MoneyError, ValidationError) as exc:
        issues.append(RowIssue(sheet=sheet, row=number, column="row", message=_short_error(exc)))
        return None


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"])
        return f"{field}: {first['msg']}"
    return str(exc)


def build_template() -> bytes:
    """A workbook with the expected sheets, headers and one example transfer, for
    the bank to fill in. The example parses cleanly."""
    workbook = Workbook()
    gl = workbook.active
    assert gl is not None
    gl.title = "GL Transactions"
    gl.append(
        [
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
    )
    gl.append(
        [
            "",
            "",
            "FT-20261025-0001",
            "5648968500",
            "0123456890",
            "CREDIT",
            "GBP",
            12.22,
            "2026-10-25T20:36:13+01:00",
        ]
    )
    gl.append(
        [
            "",
            "",
            "FT-20261025-0001",
            "0123456890",
            "6307882400",
            "DEBIT",
            "USD",
            13.58,
            "2026-10-25T20:36:13+01:00",
        ]
    )
    stmt = workbook.create_sheet("Statement")
    stmt.append(
        [
            "statementId",
            "externalReference",
            "debitedAccount",
            "creditedAccount",
            "entryType",
            "currency",
            "amount",
            "valueDate",
        ]
    )
    stmt.append(
        [
            "STMT-0001",
            "FT-20261025-0001",
            "5648968500",
            "0123456890",
            "CREDIT",
            "GBP",
            12.22,
            "2026-10-25T20:36:13+01:00",
        ]
    )
    guide = workbook.create_sheet("How to fill this in")
    guide.append(["Column", "Meaning"])
    for column, meaning in (
        ("externalReference", "The transfer reference, identical on every leg of a transfer."),
        ("debitedAccount / creditedAccount", "10-digit account numbers."),
        ("entryType", "DEBIT or CREDIT (DR and CR work too)."),
        ("currency", "USD, EUR, GBP or NGN."),
        ("amount", "The leg amount, as a number."),
        ("createdAt / valueDate", "Date and time, e.g. 2026-10-25T20:36:13+01:00."),
        ("id / transferId", "Optional: leave blank and Calipa derives them."),
        ("statementId", "Each statement line's own id, used in the report."),
    ):
        guide.append([column, meaning])
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()
