from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from fetcher import fetch_earnings_for_tickers
from logging_utils import log_event


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
            log_event("watcher_load_failed", path=str(ticket_path), error=str(exc))
    log_event("watcher_loaded_markets", markets=sorted(data.keys()), counts={k: len(v) for k, v in data.items()})
    return data


def find_earliest_upcoming(tickers: Iterable[str]) -> tuple[str | None, dict | None]:
    tickers = list(tickers)
    if not tickers:
        return None, None
    earnings = fetch_earnings_for_tickers(tickers)
    candidates = []
    for ticker, meta in earnings.items():
        if not meta:
            continue
        next_ts = meta.get("next_earnings_ts")
        if next_ts:
            candidates.append((next_ts, ticker, meta))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1], candidates[0][2]


def cmd_once(args: argparse.Namespace) -> int:
    nasdaq_path = Path("watchlists/nasdaq_tickers.json")
    sp500_path = Path("watchlists/sp500_tickers.json")
    data = load_tickers(nasdaq_path, sp500_path)
    all_tickers = data.get("nasdaq", []) + data.get("sp500", [])
    all_tickers = sorted(set(all_tickers))[:getattr(args, "max_tickers", 500)]
    print(f"watcher: loaded {len(all_tickers)} tickers")
    log_event("watcher_once_start", max_tickers=getattr(args, "max_tickers", 500), loaded=len(all_tickers))

    earliest_ticker, earliest_meta = find_earliest_upcoming(all_tickers)
    next_ts = (earliest_meta or {}).get("next_earnings_ts") if earliest_meta else None
    print(f"watcher: earliest upcoming ticker={earliest_ticker} next_earnings_ts={next_ts}")
    log_event("watcher_once_done", earliest_ticker=earliest_ticker, earliest_next_earnings_ts=next_ts)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="stock earnings watcher")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--max-tickers", type=int, default=500, help="max tickers to evaluate")
    parser.add_argument("--earliest", action="store_true", help="print only the single earliest upcoming ticker")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.once or args.earliest:
        return cmd_once(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
