from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import urllib.request

from logging_utils import log_event


NASDAQ_EARNINGS_API = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_USER_AGENT = "Mozilla/5.0 (compatible; stock-earnings-monitor/1.0)"
NASDAQ_ACCEPT = "application/json, text/plain, */*"
NASDAQ_ORIGIN = "https://www.nasdaq.com"
NASDAQ_REFERER = "https://www.nasdaq.com/"


def _json_get(url: str, params: dict[str, object] | None = None, timeout: int = 20) -> dict[str, object]:
    qs = ""
    if params:
        from urllib.parse import urlencode

        qs = "?" + urlencode({str(k): str(v) for k, v in params.items()})
    req = urllib.request.Request(
        url + qs,
        headers={
            "accept": NASDAQ_ACCEPT,
            "user-agent": NASDAQ_USER_AGENT,
            "origin": NASDAQ_ORIGIN,
            "referer": NASDAQ_REFERER,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def fetch_nasdaq_report_schedule(date_str: str) -> list[dict[str, object]]:
    try:
        payload = _json_get(
            NASDAQ_EARNINGS_API,
            params={"date": date_str},
        )
        data = (((payload or {}).get("data") or {}).get("rows") or [])
        return [row for row in data if isinstance(row, dict)]
    except Exception as exc:
        log_event("watcher_calendar_fetch_failed", date=date_str, error=str(exc))
        return []


def find_tickers_at_time(date_str: str, target_et: str, window_minutes: int = 30) -> list[dict[str, object]]:
    rows = fetch_nasdaq_report_schedule(date_str)
    try:
        hh, mm = [int(x) for x in target_et.split(":")]
        target_dt = datetime(
            datetime.strptime(date_str, "%Y-%m-%d").year,
            datetime.strptime(date_str, "%Y-%m-%d").month,
            datetime.strptime(date_str, "%Y-%m-%d").day,
            hh,
            mm,
        )
    except Exception:
        return []

    matches: list[dict[str, object]] = []
    for row in rows:
        try:
            rpt = str(row.get("report_date") or date_str)[:10]
            raw_time = str(row.get("time") or row.get("callTime") or "").strip()
            if raw_time == "time-pre-market":
                dt = datetime.strptime(rpt, "%Y-%m-%d").replace(hour=8, minute=0)
            elif raw_time == "time-after-hours":
                dt = datetime.strptime(rpt, "%Y-%m-%d").replace(hour=16, minute=0)
            elif raw_time:
                hhmm = None
                for fmt in ("%I:%M", "%H:%M", "%I:%M%p", "%H:%M:%S"):
                    try:
                        hhmm = datetime.strptime(raw_time.replace("AM", "").replace("PM", "").strip(), fmt)
                        break
                    except Exception:
                        continue
                if hhmm is None:
                    raise ValueError("unsupported time format")
                dt = datetime.strptime(rpt, "%Y-%m-%d").replace(
                    hour=hhmm.hour,
                    minute=hhmm.minute,
                )
            else:
                dt = datetime.strptime(rpt, "%Y-%m-%d").replace(hour=12, minute=0)
        except Exception:
            continue

        delta = abs((dt - target_dt).total_seconds()) / 60.0
        if delta <= window_minutes:
            matches.append({**row, "earnings_datetime_utc": dt.isoformat()})
    return matches


def print_earliest_upcoming(lookahead_days: int = 7, schedule_state_path: str | None = None) -> int:
    now = datetime.now(timezone.utc)
    earliest: tuple[datetime, str] | None = None
    earliest_ticker = None

    from fetcher import save_schedule_state, load_schedule_state, _scheduleable_datetime_utc

    state = load_schedule_state()
    last_scheduled_at = state.get("last_scheduled_at")
    run_before = None
    if last_scheduled_at:
        try:
            run_before = datetime.fromisoformat(last_scheduled_at)
            if run_before.tzinfo is None:
                run_before = run_before.replace(tzinfo=timezone.utc)
        except Exception:
            run_before = None

    for day in range(lookahead_days + 1):
        current = (now + timedelta(days=day)).strftime("%Y-%m-%d")
        try:
            rows = fetch_nasdaq_report_schedule(current)
        except Exception:
            continue
        for row in rows:
            aliases = ("earnings_datetime_utc", "report_date", "date")
            raw = next((row.get(k) for k in aliases if row.get(k)), current)
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    row_for_dt = dict(row)
                    row_for_dt["report_date"] = current
                    dt = datetime.fromisoformat(
                        str(_scheduleable_datetime_utc(row_for_dt) or current).replace("Z", "+00:00")
                    )
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < now:
                continue
            if earliest is None or dt < earliest[0]:
                earliest = (dt, row.get("symbol", ""))
                earliest_ticker = str(row.get("symbol")).strip() or None

    if earliest is None or earliest_ticker is None:
        print("watcher: no upcoming earnings in lookahead window")
        return 0

    dt_iso = earliest[0].isoformat()
    print(f"watcher: earliest upcoming ticker={earliest_ticker} next_earnings_ts={dt_iso}")
    state["last_scheduled_at"] = now.isoformat()
    save_schedule_state(state)
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    lookahead = int(getattr(args, "lookahead_days", 7) or 7)
    return print_earliest_upcoming(lookahead_days=lookahead)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="stock earnings watcher")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--earliest", action="store_true", help="print only the single earliest upcoming ticker")
    parser.add_argument("--lookahead-days", type=int, default=7, help="days to scan for next revenue report")
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
