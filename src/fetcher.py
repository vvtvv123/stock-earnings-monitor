from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from logging_utils import log_event

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ALERTS_PATH = DATA_DIR / "alerts.jsonl"
SCHEDULE_STATE_PATH = DATA_DIR / "fetcher_schedule_state.json"

NASDAQ_EARNINGS_API = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_USER_AGENT = "Mozilla/5.0 (compatible; stock-earnings-monitor/1.0)"
NASDAQ_ACCEPT = "application/json, text/plain, */*"
NASDAQ_ORIGIN = "https://www.nasdaq.com"
NASDAQ_REFERER = "https://www.nasdaq.com/"

DEFAULT_WINDOW_MINUTES = 30
DEFAULT_LOOKAHEAD_HOURS = 2


class EarningsSnapshot:
    __slots__ = (
        "ticker",
        "earnings_datetime_utc",
        "report_time_local",
        "timezone_name",
        "eps_actual",
        "eps_estimate",
        "revenue_actual",
        "revenue_estimate",
        "fetched_at",
        "source",
        "next_earnings_ts",
        "reported_at",
    )

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.next_earnings_ts: str | None = None
        self.earnings_datetime_utc: str | None = None
        self.report_time_local: str | None = None
        self.timezone_name: str | None = None
        self.eps_actual: float | None = None
        self.eps_estimate: float | None = None
        self.revenue_actual: float | None = None
        self.revenue_estimate: float | None = None
        self.reported_at: str | None = None
        self.fetched_at: str = datetime.now(timezone.utc).isoformat()
        self.source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "earnings_datetime_utc": self.earnings_datetime_utc,
            "report_time_local": self.report_time_local,
            "timezone_name": self.timezone_name,
            "eps_actual": self.eps_actual,
            "eps_estimate": self.eps_estimate,
            "revenue_actual": self.revenue_actual,
            "revenue_estimate": self.revenue_estimate,
            "fetched_at": self.fetched_at,
            "source": self.source,
        }


def _json_get(url: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    qs = ""
    if params:
        from urllib.parse import urlencode

        qs = "?" + urlencode(params)
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


def _supports_time_info(row: dict[str, Any]) -> bool:
    report_date = str(row.get("report_date") or "").strip()
    time_raw = str(row.get("time") or row.get("callTime") or "").strip()
    if not report_date:
        return False
    if "T" in report_date:
        return True
    if time_raw in {"time-pre-market", "time-after-hours"}:
        return True
    if time_raw:
        for fmt in ("%H:%M", "%I:%M%p", "%H:%M:%S"):
            try:
                datetime.strptime(time_raw, fmt)
                return True
            except Exception:
                continue
    return False


def _scheduleable_datetime_utc(row: dict[str, Any]) -> str | None:
    report_date = str(row.get("report_date") or "").strip()
    time_raw = str(row.get("time") or row.get("callTime") or "").strip()
    tz_name = str(row.get("timeZone") or "US/Eastern").strip() or "US/Eastern"

    if not report_date:
        return None

    if "T" in report_date:
        try:
            dt = datetime.fromisoformat(report_date.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            return None

    local_hour = 12
    if time_raw == "time-pre-market":
        local_hour = 8
    elif time_raw == "time-after-hours":
        local_hour = 16
    elif time_raw:
        for fmt in ("%H:%M", "%I:%M%p", "%H:%M:%S"):
            try:
                local_dt = datetime.strptime(time_raw, fmt)
                local_hour = local_dt.hour
                break
            except Exception:
                continue

    try:
        local_date = datetime.strptime(report_date, "%Y-%m-%d")
    except Exception:
        return None

    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        try:
            import pytz
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = timezone.utc

    try:
        local_dt = local_date.replace(hour=local_hour, minute=0, second=0, microsecond=0, tzinfo=tz)
        return local_dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _row_to_snapshot(row: dict[str, Any]) -> EarningsSnapshot:
    ticker = str(row.get("symbol") or "").strip()
    snap = EarningsSnapshot(ticker)
    snap.earnings_datetime_utc = _scheduleable_datetime_utc(row)
    snap.report_time_local = str(row.get("time") or "").strip() or None
    snap.timezone_name = str(row.get("timeZone") or "").strip() or None
    snap.eps_estimate = _as_number(row.get("epsEstimate") or row.get("epsForecast"))
    snap.revenue_estimate = _as_number(row.get("revenueEstimate") or row.get("revenueForecast"))
    snap.eps_actual = _as_number(row.get("epsActual") or row.get("epsReported") or row.get("actualEPS"))
    snap.revenue_actual = _as_number(
        row.get("revenueActual") or row.get("revenueReported") or row.get("actualRevenue")
    )
    snap.source = "nasdaq"
    return snap


# ------------------------------------------------------------------
# Real-time NASDAQ calendar operations
# ------------------------------------------------------------------

def fetch_nasdaq_calendar(date_str: str) -> list[dict[str, Any]]:
    """Return raw NASDAQ calendar rows for a given YYYY-MM-DD date."""
    try:
        payload = _json_get(NASDAQ_EARNINGS_API, params={"date": date_str})
        data = (((payload or {}).get("data") or {}).get("rows") or [])
        return [row for row in data if isinstance(row, dict)]
    except Exception as exc:
        log_event("fetcher_calendar_fetch_failed", date=date_str, error=str(exc))
        return []


def find_tickers_at_time(
    date_str: str,
    target_time_iso: str,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    """Return NASDAQ rows whose parsed UTC datetime falls within ±window_minutes of target_time_iso."""
    rows = fetch_nasdaq_calendar(date_str)
    try:
        target_dt = datetime.fromisoformat(target_time_iso.replace("Z", "+00:00"))
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return []

    tickers: list[dict[str, Any]] = []
    for row in rows:
        if not _supports_time_info(row):
            continue
        dt_iso = _scheduleable_datetime_utc(row)
        if not dt_iso:
            continue
        try:
            dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        delta = abs((dt - target_dt).total_seconds()) / 60.0
        if delta <= window_minutes:
            enriched = dict(row)
            enriched["earnings_datetime_utc"] = dt_iso
            tickers.append(enriched)
    return tickers


def fetch_and_score_reports(
    date_str: str,
    target_time_iso: str,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    """Find reporting tickers at exact datetime, fetch live data, score, and return alerts."""
    rows = find_tickers_at_time(date_str, target_time_iso, window_minutes=window_minutes)
    if not rows:
        log_event("fetcher_no_tickers_at_time", date=date_str, target=target_time_iso)
        return []

    tickers = sorted({str(row.get("symbol") or "").strip().upper() for row in rows if row.get("symbol")})
    snapshots = fetch_reported_earnings_for_tickers(tickers)

    saved = []
    for ticker in tickers:
        snap = snapshots.get(ticker, EarningsSnapshot(ticker))
        if not snap.earnings_datetime_utc:
            continue
        snap.reported_at = snap.fetched_at
        saved.append(snap)

    if saved:
        from scorer import emit_alerts  # local import to avoid cycle

        alerts = emit_alerts(
            output_path=str(ALERTS_PATH),
            tickers={snap.ticker for snap in saved},
            snapshots=[snap.to_dict() for snap in saved],
        )
        log_event(
            "fetcher_scored_reports",
            date=date_str,
            target=target_time_iso,
            tickers=[snap.ticker for snap in saved],
            alert_count=len(alerts),
        )
        return alerts
    return []


def fetch_reported_earnings_for_tickers(tickers: list[str]) -> dict[str, EarningsSnapshot]:
    snapshots: dict[str, EarningsSnapshot] = {}
    for t in tickers:
        try:
            snap = fetch_ticker_earnings(t, lookahead_days=1)
            snapshots[t] = snap
        except Exception:
            snapshots[t] = EarningsSnapshot(t)
    return snapshots


def fetch_ticker_earnings(ticker: str, lookahead_days: int = 60) -> EarningsSnapshot:
    by_ticker = fetch_nasdaq_earnings_for_tickers([ticker], lookahead_days=lookahead_days)
    meta = by_ticker.get(ticker.upper())
    if meta and meta.get("snapshot"):
        return meta["snapshot"]
    snap = EarningsSnapshot(ticker)
    snap.next_earnings_ts = _derive_next_earnings_ts(ticker)
    snap.earnings_datetime_utc = _derive_next_datetime_utc(ticker)
    return snap


def _lookup_nasdaq_date_only(ticker: str, lookahead_days: int = 60) -> str | None:
    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=lookahead_days)
    for day in range((end - start).days + 1):
        current = start + timedelta(days=day)
        datestr = current.strftime("%Y-%m-%d")
        try:
            payload = _json_get(NASDAQ_EARNINGS_API, params={"date": datestr})
            data = (((payload or {}).get("data") or {}).get("rows") or [])
        except Exception:
            continue
        for row in data:
            if str(row.get("symbol") or "").strip().upper() == ticker.upper():
                raw = str(row.get("report_date") or row.get("date") or "").strip()
                if raw:
                    return raw
    return None


def _derive_next_earnings_ts(ticker: str) -> str | None:
    dt_utc = _derive_next_datetime_utc(ticker)
    return dt_utc


def _derive_next_datetime_utc(ticker: str) -> str | None:
    simple = _lookup_nasdaq_date_only(ticker)
    if not simple:
        return None
    fake_row = {"report_date": simple, "callTime": "", "time": "", "timeZone": "US/Eastern"}
    return _scheduleable_datetime_utc(fake_row)


def fetch_nasdaq_earnings_for_tickers(
    tickers: list[str], lookahead_days: int = 60
) -> dict[str, dict[str, Any]]:
    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=lookahead_days)

    by_ticker: dict[str, dict[str, Any]] = {t.upper(): {} for t in tickers}
    for day in range((end - start).days + 1):
        current = start + timedelta(days=day)
        datestr = current.strftime("%Y-%m-%d")
        try:
            payload = _json_get(NASDAQ_EARNINGS_API, params={"date": datestr})
            data = (((payload or {}).get("data") or {}).get("rows") or [])
        except Exception:
            data = []

        for row in data:
            sym = str(row.get("symbol") or "").strip().upper()
            if sym not in by_ticker:
                continue
            enriched = dict(row)
            enriched.setdefault("report_date", datestr)
            if not _supports_time_info(enriched):
                continue
            snap = _row_to_snapshot(enriched)
            existing = by_ticker[sym].get("snapshot")
            if existing is None:
                by_ticker[sym] = {"row": enriched, "snapshot": snap}
                continue
            existing_time = (existing.earnings_datetime_utc or "Z").replace(" ", "T")
            new_time = (snap.earnings_datetime_utc or "Z").replace(" ", "T")
            if new_time > existing_time:
                by_ticker[sym] = {"row": enriched, "snapshot": snap}
    return by_ticker


# ------------------------------------------------------------------
# Scheduling helpers
# ------------------------------------------------------------------

def load_schedule_state() -> dict[str, Any]:
    if SCHEDULE_STATE_PATH.exists():
        try:
            return json.loads(SCHEDULE_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_schedule_state(state: dict[str, Any]) -> None:
    SCHEDULE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_next_nasdaq_report_time(
    now: datetime | None = None,
    lookahead_days: int = 7,
) -> tuple[str | None, str | None, datetime | None]:
    """Return the earliest upcoming NASDAQ report ticker, datetime ISO, and run target from calendar look-ahead."""
    if now is None:
        now = datetime.now(timezone.utc)
    best_ticker = None
    best_dt_iso = None
    best_target = None

    start = now.date()
    end = start + timedelta(days=lookahead_days)
    for day in range((end - start).days + 1):
        current = start + timedelta(days=day)
        datestr = current.strftime("%Y-%m-%d")
        try:
            rows = fetch_nasdaq_calendar(datestr)
        except Exception:
            continue
        for row in rows:
            if not _supports_time_info(row):
                continue
            dt_iso = _scheduleable_datetime_utc(row)
            if not dt_iso:
                continue
            try:
                dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < now:
                continue
            target = dt + timedelta(minutes=DEFAULT_WINDOW_MINUTES)
            if best_target is None or target < best_target:
                best_target = target
                best_ticker = str(row.get("symbol") or "").strip() or None
                best_dt_iso = dt.isoformat()
    return best_ticker, best_dt_iso, best_target


def schedule_next_run(
    now: datetime | None = None,
    lookahead_hours: int = DEFAULT_LOOKAHEAD_HOURS,
) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
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

    _, _, suggested = find_next_nasdaq_report_time(now=now)
    if suggested is None or (run_before is not None and suggested <= run_before):
        suggested = now + timedelta(hours=lookahead_hours)

    suggestion = {
        "target_run_at": suggested.isoformat(),
        "target_ticker": None,
        "window_minutes": DEFAULT_WINDOW_MINUTES,
        "updated_at": now.isoformat(),
    }
    state["next_suggested"] = suggestion
    state["last_scheduled_at"] = now.isoformat()
    save_schedule_state(state)

    log_event(
        "fetcher_schedule_next",
        target_run_at=suggested.isoformat(),
    )
    return suggestion


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fetch/score real-time earnings from NASDAQ calendar")
    parser.add_argument("--date", help="YYYY-MM-DD date to inspect")
    parser.add_argument("--time", help="HH:MM or ISO datetime target time")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_MINUTES, help="+-window in minutes")
    parser.add_argument("--tickers", help="comma-separated tickers to fetch/score")
    parser.add_argument("--calendar", action="store_true", help="print NASDAQ calendar for --date")
    parser.add_argument("--at", action="store_true", help="find tickers at --date --time and score them")
    parser.add_argument("--schedule", action="store_true", help="suggest next exact report-arrival run target")
    parser.add_argument("--lookahead-days", type=int, default=7, help="days to scan for next report")
    parser.add_argument("--output", help="optional alerts output path")
    return parser


def cmd_fetch(args: argparse.Namespace) -> int:
    if args.calendar:
        if not args.date:
            print("error: --calendar requires --date YYYY-MM-DD", file=sys.stderr)
            return 2
        rows = fetch_nasdaq_calendar(args.date)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if args.at:
        if not args.date or not args.time:
            print("error: --at requires --date YYYY-MM-DD and --time HH:MM or ISO", file=sys.stderr)
            return 2
        target_time_iso = args.time
        if ":" in args.time and len(args.time) <= 5:
            try:
                hh, mm = args.time.split(":")
                dt = datetime.strptime(f"{hh}:{mm}", "%H:%M")
                target_time_iso = f"{args.date}T{hh}:{mm}:00"
            except Exception:
                pass
        print(f"fetcher: scoring reports on {args.date} around {args.time} window={args.window}m")
        fetch_and_score_reports(args.date, target_time_iso, window_minutes=args.window)
        return 0

    if args.schedule:
        suggestion = schedule_next_run(lookahead_hours=DEFAULT_LOOKAHEAD_HOURS)
        for k, v in suggestion.items():
            print(f"fetcher: {k}={v}")
        return 0

    if args.tickers:
        tickers = [x.strip() for x in args.tickers.split(",") if x.strip()]
        print(f"fetcher: fetching live data for {len(tickers)} tickers")
        snapshots = fetch_reported_earnings_for_tickers(tickers)
        out_path = Path(args.output) if args.output else ALERTS_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [snap.to_dict() for snap in snapshots.values()]
        out_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in payload),
            encoding="utf-8",
        )
        print(f"fetcher: wrote {len(payload)} snapshots to {out_path}")
        return 0

    print("error: specify one of --calendar, --at, --schedule, or --tickers", file=sys.stderr)
    build_parser().print_help(sys.stderr)
    return 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
