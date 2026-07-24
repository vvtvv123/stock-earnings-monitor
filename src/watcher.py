from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from fetcher import fetch_earnings_for_tickers


def load_tickers(nasdaq_path: Path, sp500_path: Path) -> dict:
    data = {}
    for ticket_path, market_name in ((nasdaq_path, "nasdaq"), (sp500_path, "sp500")):
        if not ticket_path.exists():
            continue
        try:
            payload = json.loads(ticket_path.read_text())
            if isinstance(payload, list):
                data[market_name] = [str(x).strip() for x in payload if str(x).strip()]
            elif isinstance(payload, dict) and "tickers" in payload:
                data[market_name] = [str(x).strip() for x in payload["tickers"] if str(x).strip()]
        except Exception as exc:
            print(f"watcher: failed to load {ticket_path}: {exc}")
    return data


def next_earnings_tickers(tickers: Iterable[str]) -> list[str]:
    tickers = list(tickers)
    if not tickers:
        return []
    earnings = fetch_earnings_for_tickers(tickers)
    upcoming = []
    for ticker, meta in earnings.items():
        if not meta:
            continue
        next_ts = meta.get("next_earnings_ts")
        if next_ts:
            upcoming.append(ticker)
    upcoming.sort()
    return upcoming


def cmd_once(args: argparse.Namespace) -> int:
    nasdaq_path = Path("watchlists/nasdaq_tickers.json")
    sp500_path = Path("watchlists/sp500_tickers.json")
    data = load_tickers(nasdaq_path, sp500_path)
    all_tickers = data.get("nasdaq", []) + data.get("sp500", [])
    all_tickers = sorted(set(all_tickers))[:getattr(args, "max_tickers", 500)]
    print(f"watcher: loaded {len(all_tickers)} tickers")
    upcoming = next_earnings_tickers(all_tickers)
    print(f"watcher: {len(upcoming)} upcoming earnings candidates")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="stock earnings watcher")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--max-tickers", type=int, default=500, help="max tickers to evaluate")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.once:
        return cmd_once(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
