from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import nasdaq

ET = ZoneInfo("America/New_York")


def find_upcoming(lookahead_days: int = 7) -> list[dict]:
    """Human-visibility helper: list tickers scheduled to report but not yet reported."""
    today = datetime.now(ET).date()
    upcoming = []
    for day in range(lookahead_days + 1):
        date_str = (today + timedelta(days=day)).isoformat()
        for row in nasdaq.fetch_calendar(date_str):
            ticker = nasdaq.row_symbol(row)
            if ticker and not nasdaq.has_reported(row):
                upcoming.append({"ticker": ticker, "report_date": date_str, "time": row.get("time")})
    upcoming.sort(key=lambda r: r["report_date"])
    return upcoming


def cmd_once(args: argparse.Namespace) -> int:
    upcoming = find_upcoming(args.lookahead_days)
    print(f"watcher: {len(upcoming)} upcoming reports in the next {args.lookahead_days} days")
    if upcoming:
        first = upcoming[0]
        print(f"watcher: earliest upcoming ticker={first['ticker']} report_date={first['report_date']} time={first['time']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="list upcoming NASDAQ earnings reports (visibility only)")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--lookahead-days", type=int, default=7, help="days to scan for upcoming reports")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return cmd_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
