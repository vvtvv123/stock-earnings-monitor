from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yfinance as yf
from pydantic import BaseModel, Field


class EarningsSnapshot(BaseModel):
    ticker: str
    next_earnings_ts: str | None = None
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None
    fetched_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


def _to_number(value: Any):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_ticker_earnings(ticker: str) -> EarningsSnapshot:
    snapshot = EarningsSnapshot(ticker=ticker)
    try:
        cal = yf.Ticker(ticker).earnings_dates
        next_ts = None
        if cal is not None and not cal.empty:
            next_row = cal.sort_index().head(1)
            next_ts = str(next_row.index[0]) if not next_row.empty else None
        snapshot.next_earnings_ts = next_ts

        info = yf.Ticker(ticker).info or {}
        snapshot.eps_actual = _to_number(info.get("trailingEps"))
        snapshot.eps_estimate = _to_number(info.get("forwardEps"))
        snapshot.revenue_actual = _to_number(info.get("totalRevenue"))
        snapshot.revenue_estimate = None
    except Exception:
        pass
    return snapshot


def fetch_earnings_for_tickers(tickers: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for ticker in tickers:
        snap = fetch_ticker_earnings(ticker)
        out[ticker] = snap.model_dump()
    return out


def save_earnings_snapshots(snapshots: list[EarningsSnapshot], path: str) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as f:
        for snap in snapshots:
            f.write(json.dumps(snap.model_dump(), ensure_ascii=False) + "\n")
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


def populate_watchlists() -> int:
    paths = []
    for rel in ("watchlists/nasdaq_tickers.json", "watchlists/sp500_tickers.json"):
        p = Path(rel)
        if p.exists():
            paths.append(p)
    total_updated = 0
    for path in paths:
        items = load_watchlist(path)
        if not items:
            continue
        tickers = [str(it.get("ticker", "")).strip() for it in items if it.get("ticker")]
        tickers = sorted(set(tickers))
        print(f"fetcher: populating next_earnings_ts for {len(tickers)} tickers in {path}")
        results = fetch_earnings_for_tickers(tickers)
        by_ticker = {k: v for k, v in results.items()}
        updated = 0
        for it in items:
            t = str(it.get("ticker", "")).strip()
            r = by_ticker.get(t)
            if not r:
                continue
            nxt = r.get("next_earnings_ts")
            if nxt is not None:
                it["next_earnings_ts"] = nxt
                updated += 1
        save_watchlist(path, items)
        print(f"fetcher: updated {updated}/{len(items)} in {path}")
        total_updated += updated
    return total_updated


def cmd_fetch(args: argparse.Namespace) -> int:
    if args.populate:
        return populate_watchlists()

    tickers = []
    for fp in ("watchlists/nasdaq_tickers.json", "watchlists/sp500_tickers.json"):
        path = Path(fp)
        if not path.exists():
            continue
        items = load_watchlist(path)
        tickers.extend([str(it.get("ticker", "")).strip() for it in items if it.get("ticker")])
    tickers = sorted(set(tickers))
    print(f"fetcher: fetching earnings for {len(tickers)} tickers")
    raw = fetch_earnings_for_tickers(tickers)
    snapshots = [EarningsSnapshot(**item) for item in raw.values()]
    save_path = args.output or "data/earnings_history.jsonl"
    dest = save_earnings_snapshots(snapshots, save_path)
    print(f"fetcher: saved {len(snapshots)} snapshots to {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fetch earnings data for watchlists")
    parser.add_argument("-o", "--output")
    parser.add_argument("--populate", action="store_true", help="update next_earnings_ts in watchlists")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.populate and not args.output:
        parser.print_help()
        return 0
    return cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
