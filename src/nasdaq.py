from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.parse import urlencode

CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "user-agent": "Mozilla/5.0 (compatible; stock-earnings-monitor/1.0)",
    "origin": "https://www.nasdaq.com",
    "referer": "https://www.nasdaq.com/",
}

_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
    "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def fetch_calendar(date_str: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Return raw NASDAQ earnings-calendar rows for a given YYYY-MM-DD date.

    Rows carry no date field of their own -- the date is implicit in the query.
    Confirmed fields (2026-07): symbol, name, marketCap, fiscalQuarterEnding,
    epsForecast, noOfEsts, time, and once reported: eps, surprise. No revenue
    field is provided by this endpoint at all.
    """
    url = f"{CALENDAR_URL}?{urlencode({'date': date_str})}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", errors="ignore"))
    rows = (((payload or {}).get("data") or {}).get("rows") or [])
    return [row for row in rows if isinstance(row, dict)]


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("$", "").strip()
    if not cleaned or cleaned in {"N/A", "--"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def row_symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or "").strip().upper()


def row_period_end(row: dict[str, Any]) -> str | None:
    """Normalize NASDAQ's 'Jun/2026' fiscalQuarterEnding into a sortable 'YYYY-MM'."""
    raw = str(row.get("fiscalQuarterEnding") or "").strip()
    if "/" not in raw:
        return None
    month, _, year = raw.partition("/")
    month_num = _MONTHS.get(month[:3].title())
    if not month_num or not year.isdigit():
        return None
    return f"{year}-{month_num}"


def row_eps_actual(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("eps"))


def row_eps_estimate(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("epsForecast"))


def has_reported(row: dict[str, Any]) -> bool:
    """True once NASDAQ has posted an actual EPS figure for this row."""
    return row_eps_actual(row) is not None
