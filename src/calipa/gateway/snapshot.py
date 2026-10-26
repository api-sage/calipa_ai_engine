"""Building a GL snapshot that can be trusted and reproduced.

The API pages by offset, newest first, sorted by a one-second timestamp with no
tiebreaker, and gives no way to read "as of" a moment. So:

- Only closed periods are audited (end of period in the bank's timezone, plus a
  settling delay, must be in the past).
- Days are fetched one at a time, with one extra day on each side, because the
  timezone of the API's day filter is unknown. Entries are then trimmed by their
  exact timestamp in the bank's timezone.
- Within a day: every page is read, duplicates are removed by id, the count must
  equal the API's total, and page 1 and the total are read again at the end. Any
  change means the data moved while we read it; the day is retried, and after the
  last attempt the snapshot is marked partial with a limitation. It is never
  silently incomplete.
- The same id with different content is an integrity failure and stops the run.
- Every page is stored raw; the manifest lists their hashes and the snapshot hash
  covers the manifest.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from calipa.canonical import hash_value
from calipa.gateway.fcy import MAX_PAGE_SIZE, FcyClient, GLEntry, GLPage
from calipa.gateway.http import GatewayError

MAX_PERIOD_DAYS = 92
MAX_PAGES_PER_DAY = 1_000

Recorder = Callable[[str, dict[str, Any]], None]


class PeriodNotClosed(ValueError):
    pass


class SnapshotIntegrityError(GatewayError):
    pass


@dataclass(frozen=True, slots=True)
class PageRecord:
    day: date
    attempt: int
    page: int
    total: int
    total_pages: int
    item_ids: tuple[str, ...]
    blob_sha256: str
    api_call_id: UUID


@dataclass(frozen=True, slots=True)
class GLSnapshot:
    period_start: date
    period_end: date
    timezone: str
    entries: tuple[GLEntry, ...]
    status: Literal["complete", "partial"]
    limitations: tuple[str, ...]
    pages: tuple[PageRecord, ...] = field(repr=False)

    def manifest(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "timezone": self.timezone,
            "status": self.status,
            "limitations": list(self.limitations),
            "pages": [
                {
                    "day": p.day,
                    "attempt": p.attempt,
                    "page": p.page,
                    "total": p.total,
                    "blob_sha256": p.blob_sha256,
                }
                for p in self.pages
            ],
            "entries": [entry.model_dump(mode="python") for entry in self.entries],
        }

    @property
    def sha256(self) -> str:
        return hash_value(self.manifest())


def assert_period_closed(
    period_end: date, *, tz: ZoneInfo, now: datetime, settle_lag: timedelta
) -> None:
    closes_at = datetime.combine(period_end + timedelta(days=1), time(0), tz) + settle_lag
    if now < closes_at:
        raise PeriodNotClosed(
            f"period ending {period_end} is not closed until {closes_at.isoformat()}"
        )


def extract_gl_snapshot(
    fcy: FcyClient,
    *,
    period_start: date,
    period_end: date,
    tz: ZoneInfo,
    now: datetime,
    settle_lag: timedelta = timedelta(hours=6),
    max_day_attempts: int = 3,
    page_size: int = MAX_PAGE_SIZE,
    run_id: UUID | None = None,
    record: Recorder | None = None,
) -> GLSnapshot:
    if period_start > period_end:
        raise ValueError("period_start is after period_end")
    if (period_end - period_start).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f"period longer than {MAX_PERIOD_DAYS} days")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    assert_period_closed(period_end, tz=tz, now=now, settle_lag=settle_lag)

    by_id: dict[UUID, GLEntry] = {}
    pages: list[PageRecord] = []
    limitations: list[str] = []
    day = period_start - timedelta(days=1)
    while day <= period_end + timedelta(days=1):
        for attempt in range(1, max_day_attempts + 1):
            entries, day_pages, problem = _fetch_day(
                fcy, day, attempt=attempt, page_size=page_size, run_id=run_id
            )
            pages.extend(day_pages)
            if record is not None:
                record(
                    "extraction_checkpoint",
                    {
                        "day": day,
                        "attempt": attempt,
                        "pages": [p.blob_sha256 for p in day_pages],
                        "entries": len(entries),
                        "consistent": problem is None,
                        "problem": problem,
                    },
                )
            if problem is None:
                break
        else:
            limitations.append(
                f"Entries for API day {day.isoformat()} changed while being read "
                f"({problem}) in all {max_day_attempts} attempts; that day may be incomplete."
            )
        for entry in entries:
            existing = by_id.get(entry.id)
            if existing is not None and existing != entry:
                raise SnapshotIntegrityError(
                    f"entry {entry.id} returned with different content in one snapshot"
                )
            by_id[entry.id] = entry
        day += timedelta(days=1)

    window_start = datetime.combine(period_start, time(0), tz)
    window_end = datetime.combine(period_end + timedelta(days=1), time(0), tz)
    in_period = sorted(
        (e for e in by_id.values() if window_start <= e.created_at < window_end),
        key=lambda e: (e.created_at, str(e.id)),
    )
    return GLSnapshot(
        period_start=period_start,
        period_end=period_end,
        timezone=tz.key,
        entries=tuple(in_period),
        status="partial" if limitations else "complete",
        limitations=tuple(limitations),
        pages=tuple(pages),
    )


def _fetch_day(
    fcy: FcyClient, day: date, *, attempt: int, page_size: int, run_id: UUID | None
) -> tuple[list[GLEntry], list[PageRecord], str | None]:
    """Read one API day. Returns the distinct entries read, the pages read, and a
    description of the inconsistency if the day changed while being read (None if
    consistent). On an inconsistency the entries are what could be read, kept so a
    partial snapshot still contains them."""
    records: list[PageRecord] = []

    def read(page_number: int) -> GLPage:
        fetched = fcy.gl_page(
            start=day, end=day, page=page_number, page_size=page_size, run_id=run_id
        )
        page = fetched.data
        records.append(
            PageRecord(
                day=day,
                attempt=attempt,
                page=page_number,
                total=page.total,
                total_pages=page.total_pages,
                item_ids=tuple(str(item.id) for item in page.items),
                blob_sha256=fetched.response.blob_sha256,
                api_call_id=fetched.response.call_id,
            )
        )
        return page

    first = read(1)
    total = first.total
    expected_pages = math.ceil(total / page_size)
    if first.total_pages != expected_pages:
        return [], records, f"totalPages {first.total_pages} does not fit total {total}"
    if expected_pages > MAX_PAGES_PER_DAY:
        raise SnapshotIntegrityError(f"{day}: {expected_pages} pages exceeds the safety limit")

    unique: dict[UUID, GLEntry] = {}
    for page_number in range(1, expected_pages + 1):
        page = first if page_number == 1 else read(page_number)
        if page.total != total:
            return list(unique.values()), records, f"total changed from {total} to {page.total}"
        for item in page.items:
            existing = unique.get(item.id)
            if existing is not None and existing != item:
                raise SnapshotIntegrityError(
                    f"entry {item.id} returned twice with different content on {day}"
                )
            unique[item.id] = item

    entries = list(unique.values())
    if len(unique) != total:
        return entries, records, f"read {len(unique)} distinct entries but total is {total}"

    recheck = read(1)
    for item in recheck.items:
        existing = unique.get(item.id)
        if existing is not None and existing != item:
            raise SnapshotIntegrityError(
                f"entry {item.id} changed content during the read of {day}"
            )
    if recheck.total != total:
        return entries, records, f"total changed from {total} to {recheck.total} during the read"
    # Compare as sets: entries sharing a timestamp may come back in a different order
    # without the data having changed.
    if {i.id for i in recheck.items} != {i.id for i in first.items}:
        return entries, records, "first page changed during the read"
    return entries, records, None
