import calendar
from datetime import date, datetime, timedelta
from typing import List, Tuple


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def shift_months(base: date, delta_months: int) -> date:
    month_index = (base.month - 1) + delta_months
    year = base.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def meta_safe_date_from(date_from: str) -> str:
    # Meta rejects ranges where start date is older than ~37 months.
    start = parse_iso_date(date_from)
    min_allowed = shift_months(date.today(), -37)
    if start < min_allowed:
        return min_allowed.isoformat()
    return date_from


def date_chunks(date_from: str, date_to: str, max_days: int) -> List[Tuple[str, str]]:
    start = parse_iso_date(date_from)
    end = parse_iso_date(date_to)
    if start > end:
        return []

    chunks: List[Tuple[str, str]] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        chunks.append((cur.isoformat(), chunk_end.isoformat()))
        cur = chunk_end + timedelta(days=1)
    return chunks
