from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from urllib.parse import urlencode

CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"


def _api_key() -> str:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        raise RuntimeError("FINNHUB_API_KEY environment variable is not set")
    return key


def fetch_calendar(date_str: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Return Finnhub earnings-calendar rows for a single YYYY-MM-DD date.

    Confirmed real fields (2026-07): symbol, date, hour ('bmo'/'amc'/''),
    quarter, year, epsEstimate, epsActual, revenueEstimate, revenueActual.
    Numeric fields are already numbers, not '$'-prefixed strings like NASDAQ's.
    Updates faster and more completely than the NASDAQ calendar endpoint in
    practice, and is the sole source for both EPS and revenue actuals.
    """
    url = f"{CALENDAR_URL}?{urlencode({'from': date_str, 'to': date_str, 'token': _api_key()})}"
    req = urllib.request.Request(url, headers={"user-agent": "stock-earnings-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", errors="ignore"))
    rows = payload.get("earningsCalendar") or []
    return [row for row in rows if isinstance(row, dict)]


def _as_number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def row_symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or "").strip().upper()


def row_period_end(row: dict[str, Any]) -> str | None:
    year = row.get("year")
    quarter = row.get("quarter")
    if year is None or quarter is None:
        return None
    return f"{year}-Q{quarter}"


def row_eps_actual(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("epsActual"))


def row_eps_estimate(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("epsEstimate"))


def row_revenue_actual(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("revenueActual"))


def row_revenue_estimate(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("revenueEstimate"))


def has_reported(row: dict[str, Any]) -> bool:
    """True once Finnhub has posted an actual EPS figure for this row."""
    return row_eps_actual(row) is not None
