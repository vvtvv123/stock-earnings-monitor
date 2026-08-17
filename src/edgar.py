from __future__ import annotations

import json
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from logging_utils import log_event

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
USER_AGENT = "stock-earnings-monitor contact@example.com"

DEFAULT_CIK_MAP_PATH = Path("data/cik_tickers.json")

# Modern filers use the ASC 606 tag; older/legacy filings use the plain one.
REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
)
EPS_TAGS = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
)

# How far a quarter's end-date may drift from exactly 365 days before the
# current report and still count as "the same quarter last year" -- fiscal
# calendars shift by a few weeks year to year.
YEAR_AGO_TOLERANCE_DAYS = 45


def _get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"user-agent": USER_AGENT, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def refresh_cik_map(path: Path = DEFAULT_CIK_MAP_PATH) -> dict[str, str]:
    """Fetch SEC's official ticker->CIK mapping and cache it locally."""
    data = _get_json(COMPANY_TICKERS_URL)
    mapping = {
        str(v["ticker"]).upper(): str(v["cik_str"]).zfill(10)
        for v in data.values()
        if v.get("ticker") and v.get("cik_str") is not None
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return mapping


def load_cik_map(path: Path = DEFAULT_CIK_MAP_PATH) -> dict[str, str]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        return refresh_cik_map(path)
    except Exception as exc:
        log_event("edgar_cik_map_refresh_failed", error=str(exc))
        return {}


def lookup_cik(ticker: str, cik_map: dict[str, str]) -> str | None:
    return cik_map.get(ticker.upper())


def _is_single_quarter(entry: dict[str, Any]) -> bool:
    try:
        start = date.fromisoformat(entry["start"])
        end = date.fromisoformat(entry["end"])
    except Exception:
        return False
    return 80 <= (end - start).days <= 100


def _closest_quarterly_value(
    gaap: dict[str, Any], tags: tuple[str, ...], target: date, tolerance_days: int
) -> tuple[float, str] | None:
    """(val, end_date_iso) for the single-quarter entry -- across the given
    candidate tags, first tag with any match wins -- whose end date is
    closest to target, within tolerance_days."""
    for tag in tags:
        units = (gaap.get(tag) or {}).get("units") or {}
        entries = [e for unit_list in units.values() for e in unit_list]
        best = None
        best_diff = None
        for e in entries:
            if not _is_single_quarter(e):
                continue
            try:
                end = date.fromisoformat(e["end"])
            except Exception:
                continue
            diff = abs((end - target).days)
            if diff > tolerance_days:
                continue
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = (e.get("val"), e["end"])
        if best is not None:
            return best
    return None


def year_ago_quarter(cik: str, as_of: str) -> dict[str, Any] | None:
    """EPS + revenue for the quarter ending ~1 year before as_of (YYYY-MM-DD).

    Returns None only if EDGAR fetch failed outright or neither EPS nor
    revenue had a match within tolerance -- otherwise returns whatever it
    found (either field may individually be None).
    """
    try:
        facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
    except Exception as exc:
        log_event("edgar_companyfacts_failed", cik=cik, error=str(exc))
        return None
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    try:
        target = date.fromisoformat(as_of) - timedelta(days=365)
    except Exception:
        return None

    eps_match = _closest_quarterly_value(gaap, EPS_TAGS, target, YEAR_AGO_TOLERANCE_DAYS)
    revenue_match = _closest_quarterly_value(gaap, REVENUE_TAGS, target, YEAR_AGO_TOLERANCE_DAYS)
    if eps_match is None and revenue_match is None:
        return None

    return {
        "eps_actual": eps_match[0] if eps_match else None,
        "eps_period_end": eps_match[1] if eps_match else None,
        "revenue_actual": revenue_match[0] if revenue_match else None,
        "revenue_period_end": revenue_match[1] if revenue_match else None,
    }
