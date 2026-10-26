"""Turn P1's description of a period into dates. Claude never computes dates.

`today` is the current date in the bank's timezone. Weeks are ISO weeks (Monday to
Sunday). Quarters and years need to know whether they are calendar or fiscal; fiscal
periods need the fiscal year's first month, which Finance has not confirmed yet, so
until it is configured a fiscal period is sent back to the auditor as a question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from calipa.interpreter.schema import PeriodSpec

MAX_RELATIVE_COUNT = 24


class PeriodUnresolved(ValueError):
    """The period cannot be turned into dates without asking the auditor. The message
    is written for the auditor."""


@dataclass(frozen=True, slots=True)
class ResolvedPeriod:
    start: date
    end: date
    rule: str  # how the dates were worked out, in words the auditor can check

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_period(
    spec: PeriodSpec, *, today: date, fiscal_year_start_month: int | None = None
) -> ResolvedPeriod:
    if spec.kind == "absolute":
        return _absolute(spec)
    return _relative(spec, today=today, fiscal_start=fiscal_year_start_month)


def _absolute(spec: PeriodSpec) -> ResolvedPeriod:
    if spec.start is None:
        raise PeriodUnresolved("Which date should the audit start from?")
    end = spec.end or spec.start
    if end < spec.start:
        raise PeriodUnresolved(
            f"The end date {end.isoformat()} is before the start date "
            f"{spec.start.isoformat()}. Which period did you mean?"
        )
    rule = "as stated" if spec.end else "single day as stated"
    return ResolvedPeriod(spec.start, end, rule)


def _relative(spec: PeriodSpec, *, today: date, fiscal_start: int | None) -> ResolvedPeriod:
    unit, offset = spec.relative_unit, spec.relative_offset
    if unit is None or offset is None:
        raise PeriodUnresolved(f"Which period does {spec.source_text!r} refer to?")
    count = spec.relative_count or 1
    if offset > 0:
        raise PeriodUnresolved("Audits can only cover past periods. Which period did you mean?")
    if not 1 <= count <= MAX_RELATIVE_COUNT:
        raise PeriodUnresolved(f"Which period does {spec.source_text!r} refer to?")

    first_month = 1
    if unit in ("quarter", "year"):
        if spec.calendar == "unspecified":
            raise PeriodUnresolved(
                f"Does {spec.source_text!r} mean the calendar {unit} or the fiscal {unit}?"
            )
        if spec.calendar == "fiscal":
            if fiscal_start is None:
                raise PeriodUnresolved(
                    "The fiscal calendar is not set up in Calipa yet. Please give the "
                    "start and end dates of the period."
                )
            first_month = fiscal_start

    _, last_end = _unit_bounds(unit, today, offset, first_month)
    first_start, _ = _unit_bounds(unit, today, offset - count + 1, first_month)
    kind = f"{spec.calendar} " if unit in ("quarter", "year") else ""
    which = {0: f"current {kind}{unit}", -1: f"previous {kind}{unit}"}.get(
        offset, f"{kind}{unit} {-offset} back"
    )
    rule = which if count == 1 else f"{count} {kind}{unit}s ending with the {which}"
    rule += f", relative to {today.isoformat()}"
    return ResolvedPeriod(first_start, last_end, rule)


def _unit_bounds(unit: str, today: date, offset: int, first_month: int) -> tuple[date, date]:
    if unit == "day":
        day = today + timedelta(days=offset)
        return day, day
    if unit == "week":
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        return monday, monday + timedelta(days=6)
    months = {"month": 1, "quarter": 3, "year": 12}[unit]
    # Months since the period grid's origin, then snap to the start of the unit.
    index = today.year * 12 + today.month - 1 - (first_month - 1)
    start_index = (index // months + offset) * months + (first_month - 1)
    start = date(start_index // 12, start_index % 12 + 1, 1)
    end_index = start_index + months
    end = date(end_index // 12, end_index % 12 + 1, 1) - timedelta(days=1)
    return start, end
