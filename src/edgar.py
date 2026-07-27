from __future__ import annotations

import json
import urllib.request
from datetime import date
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


def latest_quarterly_revenue(cik: str) -> float | None:
    """Most recent single-quarter revenue figure for a company, or None if
    EDGAR hasn't posted the filing yet or the company uses neither revenue tag.
    """
    try:
        facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
    except Exception as exc:
        log_event("edgar_companyfacts_failed", cik=cik, error=str(exc))
        return None
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for tag in REVENUE_TAGS:
        entries = ((gaap.get(tag) or {}).get("units") or {}).get("USD") or []
        quarterly = [e for e in entries if _is_single_quarter(e)]
        if quarterly:
            quarterly.sort(key=lambda e: e.get("end", ""))
            return quarterly[-1].get("val")
    return None
