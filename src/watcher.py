from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from logging_utils import log_event


NASDAQ_PATH = Path("watchlists/nasdaq_tickers.json")
SP500_PATH = Path("watchlists/sp500_tickers.json")


def load_tickers(nasdaq_path: Path, sp500_path: Path) -> dict:
    data = {}
    for ticket_path, market_name in ((nasdaq_path, "nasdaq"), (sp500_path, "sp500")):
        if not ticket_path.exists():
            continue
        try:
            payload = json.loads(ticket_path.read_text(encoding="utf-8"))
            tickers = []
            if isinstance(payload, list):
                for x in payload:
                    if isinstance(x, dict):
                        tickers.append(str(x.get("ticker", "")).strip())
                    else:
                        tickers.append(str(x).strip())
                data[market_name] = [t for t in tickers if t]
            elif isinstance(payload, dict) and "tickers" in payload:
                data[market_name] = [str(x).strip() for x in payload["tickers"] if str(x).strip()]
        except Exception as exc:
            print(f"watcher: failed to load {ticket_path}: {exc}")
            log_event("watcher_load_failed", path=str(ticket_path), error=str(exc))
    log_event("watcher_loaded_markets", markets=sorted(data.keys()), counts={k: len(v) for k, v in data.items()})
    return data


def _parse_report_dt(item: dict) -> datetime | None:
    dt_str = item.get("earnings_datetime_utc") or item.get("next_earnings_ts")
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def find_earliest_upcoming_from_watchlist(nasdaq_path: Path, sp500_path: Path) -> tuple[str | None, str | None, str | None]:
    rows = []
    for path in (nasdaq_path, sp500_path):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                ticker = str(item.get("ticker", "")).strip()
                dt = _parse_report_dt(item)
                if ticker and dt:
                    rows.append((dt.isoformat(), ticker, dt))
        except Exception:
            continue
    if not rows:
        return None, None, None
    rows.sort(key=lambda r: r[2])
    ticker = rows[0][1]
    next_ts = rows[0][0]
    return ticker, next_ts, next_ts


def cmd_once(args: argparse.Namespace) -> int:
    nasdaq_path = NASDAQ_PATH
    sp500_path = SP500_PATH
    data = load_tickers(nasdaq_path, sp500_path)
    all_tickers = data.get("nasdaq", []) + data.get("sp500", [])
    all_tickers = sorted(set(all_tickers))
    print(f"watcher: loaded {len(all_tickers)} tickers")
    log_event("watcher_once_start", loaded=len(all_tickers))

    earliest_ticker, next_ts, _ = find_earliest_upcoming_from_watchlist(nasdaq_path, sp500_path)
    print(f"watcher: earliest upcoming ticker={earliest_ticker} next_earnings_ts={next_ts}")
    log_event("watcher_once_done", earliest_ticker=earliest_ticker, earliest_next_earnings_ts=next_ts)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="stock earnings watcher")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
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
