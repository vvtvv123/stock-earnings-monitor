from __future__ import annotations

import argparse
import json
import os
import random
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


WATCHLIST_PATHS = [
    "watchlists/nasdaq_tickers.json",
    "watchlists/sp500_tickers.json",
]

DATA_DIR = Path("data")
EARNINGS_HISTORY = DATA_DIR / "earnings_history.jsonl"
UPCOMING_EARNINGS = DATA_DIR / "upcoming_earnings.jsonl"
ALERTS_PATH = DATA_DIR / "alerts.jsonl"
EDGAR_INDEX_PATH = DATA_DIR / "edgar_last_fetch.json"


class EarningsSnapshot:
    __slots__ = (
        "ticker",
        "next_earnings_ts",
        "eps_actual",
        "eps_estimate",
        "revenue_actual",
        "revenue_estimate",
        "fetched_at",
    )

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.next_earnings_ts: str | None = None
        self.eps_actual: float | None = None
        self.eps_estimate: float | None = None
        self.revenue_actual: float | None = None
        self.revenue_estimate: float | None = None
        self.fetched_at: str = datetime.utcnow().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "next_earnings_ts": self.next_earnings_ts,
            "eps_actual": self.eps_actual,
            "eps_estimate": self.eps_estimate,
            "revenue_actual": self.revenue_actual,
            "revenue_estimate": self.revenue_estimate,
            "fetched_at": self.fetched_at,
        }


def _json_get(url: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    qs = ""
    if params:
        from urllib.parse import urlencode
        qs = "?" + urlencode(params)
    req = urllib.request.Request(
        url + qs,
        headers={
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0",
            "origin": "https://www.nasdaq.com",
            "referer": "https://www.nasdaq.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _row_to_snapshot(row: dict[str, Any]) -> EarningsSnapshot:
    ticker = str(row.get("symbol") or "").strip()
    snap = EarningsSnapshot(ticker)
    raw = row.get("date")
    if raw:
        snap.next_earnings_ts = raw
    snap.eps_estimate = _as_number(row.get("epsEstimate"))
    snap.revenue_estimate = _as_number(row.get("revenueEstimate"))
    snap.eps_actual = _as_number(row.get("epsActual"))
    snap.revenue_actual = _as_number(row.get("revenueActual"))
    return snap


def fetch_nasdaq_earnings_for_tickers(tickers: list[str], lookahead_days: int = 30) -> dict[str, dict[str, Any]]:
    start = datetime.utcnow().date()
    end = start + timedelta(days=lookahead_days)

    by_ticker: dict[str, dict[str, Any]] = {t: {} for t in tickers}
    for day in range((end - start).days + 1):
        current = start + timedelta(days=day)
        datestr = current.strftime("%Y-%m-%d")
        url = "https://api.nasdaq.com/api/calendar/earnings"
        try:
            payload = _json_get(url, params={"date": datestr})
            data = (((payload or {}).get("data") or {}).get("calendar") or {}).get("rows") or []
        except Exception:
            data = []

        for row in data:
            sym = str(row.get("symbol") or "").strip().upper()
            if sym in by_ticker:
                if not by_ticker[sym].get("next_earnings_ts"):
                    by_ticker[sym]["next_earnings_ts"] = row.get("date") or datestr
                by_ticker[sym].setdefault("earnings_row", row)
    return by_ticker


def fetch_ticker_earnings(ticker: str) -> EarningsSnapshot:
    try:
        rows_map = fetch_nasdaq_earnings_for_tickers([ticker], lookahead_days=60)
        meta = rows_map.get(ticker) or {}
        if meta.get("earnings_row"):
            return _row_to_snapshot(meta["earnings_row"])
    except Exception:
        pass
    return EarningsSnapshot(ticker)


def fetch_earnings_for_tickers(tickers: list[str]) -> dict[str, dict[str, Any]]:
    return {t: fetch_ticker_earnings(t).to_dict() for t in tickers}


def save_earnings_snapshots(snapshots: list[EarningsSnapshot], path: Path | None = None) -> Path:
    dest = path or EARNINGS_HISTORY
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as f:
        for snap in snapshots:
            f.write(json.dumps(snap.to_dict(), ensure_ascii=False) + "\n")
    return dest


def load_watchlist(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [it for it in payload if isinstance(it, dict)]
    except Exception:
        pass
    return []


def save_watchlist(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def _sample_next_earnings_ts(ticker: str) -> str:
    base = datetime.utcnow() + timedelta(days=random.randint(1, 35))
    base = base.replace(hour=random.choice([8, 12, 16]), minute=0, second=0, microsecond=0)
    return base.strftime("%Y-%m-%dT%H:%M:%S")


def backfill_watchlists_sample() -> int:
    total_updated = 0
    for rel in WATCHLIST_PATHS:
        path = Path(rel)
        if not path.exists():
            continue
        items = load_watchlist(path)
        if not items:
            continue
        updated = 0
        for it in items:
            if not it.get("next_earnings_ts"):
                it["next_earnings_ts"] = _sample_next_earnings_ts(str(it.get("ticker")))
                updated += 1
        save_watchlist(path, items)
        print(f"fetcher: backfilled {updated}/{len(items)} in {path}")
        total_updated += updated
    return total_updated


def populate_watchlists() -> int:
    total_updated = 0
    for rel in WATCHLIST_PATHS:
        path = Path(rel)
        if not path.exists():
            continue
        items = load_watchlist(path)
        if not items:
            continue
        tickers = sorted({str(it.get("ticker", "")).strip() for it in items if it.get("ticker")})
        print(f"fetcher: populating next_earnings_ts for {len(tickers)} tickers in {path}")
        results = fetch_nasdaq_earnings_for_tickers(tickers, lookahead_days=60)
        updated = 0
        for it in items:
            t = str(it.get("ticker", "")).strip()
            r = results.get(t) or {}
            nxt = r.get("next_earnings_ts")
            if nxt is not None:
                it["next_earnings_ts"] = nxt
                updated += 1
        save_watchlist(path, items)
        print(f"fetcher: updated {updated}/{len(items)} in {path}")
        total_updated += updated
    return total_updated


def _load_edgar_index() -> dict[str, str]:
    if EDGAR_INDEX_PATH.exists():
        try:
            return json.loads(EDGAR_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_edgar_index(index: dict[str, str]) -> None:
    EDGAR_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    EDGAR_INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _edgar_company_facts(cik: str) -> dict[str, Any]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
    req = urllib.request.Request(
        url,
        headers={
            "user-agent": "stock-earnings-monitor contact@example.com",
            "accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def _edgar_facts_to_snapshot(ticker: str, facts: dict[str, Any]) -> EarningsSnapshot:
    snap = EarningsSnapshot(ticker)
    try:
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        revenues = us_gaap.get("Revenues", {})
        eps = us_gaap.get("EarningsPerShareDiluted", {})
        units_rev = revenues.get("units", {}).get("USD", []) if isinstance(revenues, dict) else []
        units_eps = eps.get("units", {}).get("USD/shares", []) if isinstance(eps, dict) else []
        if units_rev:
            latest_revenue = sorted(units_rev, key=lambda x: x.get("end", ""))[-1]
            snap.revenue_actual = _as_number(latest_revenue.get("val"))
        if units_eps:
            latest_eps = sorted(units_eps, key=lambda x: x.get("end", ""))[-1]
            snap.eps_actual = _as_number(latest_eps.get("val"))
    except Exception:
        pass
    return snap


def ingest_new_reports() -> int:
    items = []
    for rel in WATCHLIST_PATHS:
        items.extend(load_watchlist(Path(rel)))
    tickers = sorted({str(it.get("ticker", "")).strip() for it in items if it.get("ticker")})
    print(f"fetcher: ingesting new reports for {len(tickers)} tickers")
    prior_index = _load_edgar_index()
    new_index = dict(prior_index)
    ingested = 0
    for ticker in tickers:
        try:
            cik = None
            if ticker in prior_index and prior_index[ticker]:
                cik = prior_index[ticker]
            else:
                # Use cached company tickers mapping from SEC if available
                cik_tickers = DATA_DIR / "cik_tickers.json"
                mapping: dict[str, str] = {}
                if cik_tickers.exists():
                    try:
                        mapping = json.loads(cik_tickers.read_text(encoding="utf-8"))
                    except Exception:
                        mapping = {}
                cik = mapping.get(ticker.upper())
                if not cik:
                    # quick lookup from SEC ticker dataset if present later
                    cik = None
                if cik:
                    new_index[ticker] = cik
            snap = EarningsSnapshot(ticker)
            if cik:
                facts = _edgar_company_facts(cik)
                snap = _edgar_facts_to_snapshot(ticker, facts)
            save_earnings_snapshots([snap])
            ingested += 1
        except Exception as exc:
            print(f"fetcher: ingest failed for {ticker}: {exc}")
    _save_edgar_index(new_index)
    print(f"fetcher: ingested {ingested}/{len(tickers)} snapshots")
    return ingested


def cmd_fetch(args: argparse.Namespace) -> int:
    if args.backfill:
        return backfill_watchlists_sample()

    if args.populate:
        return populate_watchlists()

    if args.ingest:
        return ingest_new_reports()

    tickers = []
    for rel in WATCHLIST_PATHS:
        items = load_watchlist(Path(rel))
        tickers.extend([str(it.get("ticker", "")).strip() for it in items if it.get("ticker")])
    tickers = sorted(set(tickers))
    print(f"fetcher: fetching earnings for {len(tickers)} tickers")
    raw = fetch_earnings_for_tickers(tickers)
    snapshots = [EarningsSnapshot(t) for t in raw.keys()]
    for snap, meta in zip(snapshots, raw.values()):
        snap.next_earnings_ts = meta.get("next_earnings_ts")
        snap.eps_actual = meta.get("eps_actual")
        snap.eps_estimate = meta.get("eps_estimate")
        snap.revenue_actual = meta.get("revenue_actual")
        snap.revenue_estimate = meta.get("revenue_estimate")
    save_earnings_snapshots(snapshots)
    print(f"fetcher: saved {len(snapshots)} snapshots")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fetch earnings data for watchlists")
    parser.add_argument("-o", "--output")
    parser.add_argument("--populate", action="store_true", help="update next_earnings_ts from NASDAQ calendar")
    parser.add_argument("--backfill", action="store_true", help="fill empty next_earnings_ts with sample dates for testing")
    parser.add_argument("--ingest", action="store_true", help="ingest latest available actuals from SEC EDGAR")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.populate and not args.backfill and not args.ingest and not args.output:
        parser.print_help()
        return 0
    return cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
