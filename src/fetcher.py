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
        info = yf.Ticker(ticker).info or {}
        cal = yf.Ticker(ticker).earnings_dates
        if cal is not None and not cal.empty:
            next_row = cal.sort_index().head(1)
            snapshot.next_earnings_ts = str(next_row.index[0]) if not next_row.empty else None
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


def cmd_fetch(args: argparse.Namespace) -> int:
    tickers = []
    for fp in ("watchlists/nasdaq_tickers.json", "watchlists/sp500_tickers.json"):
        path = Path(fp)
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
            if isinstance(payload, list):
                tickers.extend([str(x).strip() for x in payload if str(x).strip()])
            elif isinstance(payload, dict) and "tickers" in payload:
                tickers.extend([str(x).strip() for x in payload["tickers"] if str(x).strip()])
        except Exception:
            continue
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
