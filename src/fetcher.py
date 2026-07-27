from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from logging_utils import log_event

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATHS = [
    ROOT / "watchlists" / "nasdaq_tickers.json",
    ROOT / "watchlists" / "sp500_tickers.json",
]
DATA_DIR = ROOT / "data"
EARNINGS_HISTORY = DATA_DIR / "earnings_history.jsonl"
UPCOMING_EARNINGS = DATA_DIR / "upcoming_earnings.jsonl"
ALERTS_PATH = DATA_DIR / "alerts.jsonl"
EDGAR_INDEX_PATH = DATA_DIR / "edgar_last_fetch.json"
SCHEDULE_STATE_PATH = DATA_DIR / "fetcher_schedule_state.json"

NASDAQ_EARNINGS_API = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_USER_AGENT = "Mozilla/5.0 (compatible; stock-earnings-monitor/1.0)"
NASDAQ_ACCEPT = "application/json, text/plain, */*"
NASDAQ_ORIGIN = "https://www.nasdaq.com"
NASDAQ_REFERER = "https://www.nasdaq.com/"

DEFAULT_LOOKAHEAD_DAYS = 60
DEFAULT_POST_REPORT_WINDOW_MINUTES = 30
DEFAULT_RESCHEDULE_LOOKAHEAD_HOURS = 2


class EarningsSnapshot:
    __slots__ = (
        "ticker",
        "next_earnings_ts",
        "earnings_datetime_utc",
        "report_time_local",
        "timezone_name",
        "earnings_date",
        "call_time",
        "eps_actual",
        "eps_estimate",
        "revenue_actual",
        "revenue_estimate",
        "reported_at",
        "fetched_at",
        "source",
    )

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.next_earnings_ts: str | None = None
        self.earnings_datetime_utc: str | None = None
        self.report_time_local: str | None = None
        self.timezone_name: str | None = None
        self.earnings_date: str | None = None
        self.call_time: str | None = None
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
            "next_earnings_ts": self.next_earnings_ts,
            "earnings_datetime_utc": self.earnings_datetime_utc,
            "report_time_local": self.report_time_local,
            "timezone_name": self.timezone_name,
            "earnings_date": self.earnings_date,
            "call_time": self.call_time,
            "eps_actual": self.eps_actual,
            "eps_estimate": self.eps_estimate,
            "revenue_actual": self.revenue_actual,
            "revenue_estimate": self.revenue_estimate,
            "reported_at": self.reported_at,
            "fetched_at": self.fetched_at,
            "source": self.source,
        }

    def report_is_due(self, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        if not self.earnings_datetime_utc:
            return False
        try:
            rpt = datetime.fromisoformat(self.earnings_datetime_utc)
            if rpt.tzinfo is None:
                rpt = rpt.replace(tzinfo=timezone.utc)
        except Exception:
            return False
        if self.reported_at:
            return False
        return now >= (rpt - timedelta(minutes=10))


def _json_get(url: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    qs = ""
    if params:
        from urllib.parse import urlencode

        qs = "?" + urlencode(params)
    req = urllib.request.Request(
        NASDAQ_EARNINGS_API + qs,
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
    if time_raw in {"time-pre-market", "time-after-hours"} or "T" in report_date:
        return bool(report_date)
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

    local_hour = None
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

    if local_hour is None:
        local_hour = 12

    try:
        local_date = datetime.strptime(report_date, "%Y-%m-%d")
    except Exception:
        return None

    day_start_utc = _local_day_start_to_utc(local_date, tz_name)
    if day_start_utc is None:
        return None
    target = day_start_utc.replace(hour=local_hour, minute=0, second=0, microsecond=0)
    return target.isoformat()


def _local_day_start_to_utc(local_dt: datetime, tz_name: str) -> datetime | None:
    tz_name = str(tz_name or "").strip() or "UTC"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        try:
            import pytz
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = timezone.utc
    try:
        local_dt = local_dt.replace(tzinfo=tz)
        return local_dt.astimezone(timezone.utc)
    except Exception:
        return None


def _row_to_snapshot(row: dict[str, Any]) -> EarningsSnapshot:
    ticker = str(row.get("symbol") or "").strip()
    snap = EarningsSnapshot(ticker)
    raw = row.get("report_date") or row.get("date")
    if raw:
        snap.next_earnings_ts = str(raw).strip()

    snap.earnings_datetime_utc = _scheduleable_datetime_utc(row)
    snap.report_time_local = str(row.get("time") or "").strip() or None
    snap.timezone_name = str(row.get("timeZone") or "").strip() or None
    snap.call_time = str(row.get("time") or "").strip() or None
    snap.earnings_date = (snap.earnings_datetime_utc or "")[:10] if snap.earnings_datetime_utc else (str(raw).strip()[:10] if raw else None)
    snap.eps_estimate = _as_number(row.get("epsEstimate") or row.get("epsForecast"))
    snap.revenue_estimate = _as_number(row.get("revenueEstimate") or row.get("revenueForecast"))
    snap.eps_actual = _as_number(row.get("epsActual") or row.get("epsReported") or row.get("actualEPS"))
    snap.revenue_actual = _as_number(row.get("revenueActual") or row.get("revenueReported") or row.get("actualRevenue"))
    snap.source = "nasdaq"
    return snap


def fetch_nasdaq_earnings_for_tickers(
    tickers: list[str], lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS
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


def fetch_ticker_earnings(ticker: str, lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS) -> EarningsSnapshot:
    try:
        rows_map = fetch_nasdaq_earnings_for_tickers([ticker], lookahead_days=lookahead_days)
        meta = rows_map.get(ticker.upper())
        if meta and meta.get("snapshot"):
            return meta["snapshot"]
    except Exception:
        pass
    snap = EarningsSnapshot(ticker)
    snap.next_earnings_ts = _derive_exact_next_earnings_ts(ticker)
    snap.earnings_datetime_utc = _derive_exact_next_datetime_utc(ticker)
    return snap


def _derive_exact_next_earnings_ts(ticker: str) -> str | None:
    dt_utc = _derive_exact_next_datetime_utc(ticker)
    if dt_utc:
        return dt_utc
    return None


def _lookup_nasdaq_date_only(ticker: str, lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS) -> str | None:
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


def _derive_exact_next_datetime_utc(ticker: str) -> str | None:
    simple = _lookup_nasdaq_date_only(ticker)
    if not simple:
        return None
    fake_row = {"report_date": simple, "callTime": "", "time": "", "timeZone": "US/Eastern"}
    return _scheduleable_datetime_utc(fake_row)


def fetch_earnings_for_tickers(
    tickers: list[str], lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS
) -> dict[str, dict[str, Any]]:
    rows_map = fetch_nasdaq_earnings_for_tickers(tickers, lookahead_days=lookahead_days)
    out: dict[str, dict[str, Any]] = {}
    for t in tickers:
        meta = rows_map.get(t.upper(), {"snapshot": EarningsSnapshot(t)})
        snap = meta.get("snapshot") or EarningsSnapshot(t)
        out[t] = {
            "snapshot": snap,
            "row": meta.get("row"),
        }
    return out


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
    base = datetime.now(timezone.utc) + timedelta(days=random.randint(1, 35))
    base = base.replace(hour=random.choice([8, 12, 16]), minute=0, second=0, microsecond=0)
    return base.strftime("%Y-%m-%dT%H:%M:%S")


def backfill_watchlists_for_tickers(selected: list[str] | None = None) -> int:
    total_updated = 0
    for rel in WATCHLIST_PATHS:
        path = Path(rel)
        if not path.exists():
            continue
        items = load_watchlist(path)
        if not items:
            continue
        work = []
        for it in items:
            t = str(it.get("ticker", "")).strip()
            if selected and t not in selected:
                continue
            if it.get("earnings_datetime_utc") or it.get("next_earnings_ts"):
                continue
            it["next_earnings_ts"] = _sample_next_earnings_ts(t)
            work.append(t)
        if work:
            save_watchlist(path, items)
        updated = len(work)
        print(f"fetcher: backfilled {updated}/{len(items)} in {path}")
        log_event(
            "fetcher_backfill",
            path=str(path),
            updated=updated,
            total=len(items),
            selected=sorted(work)[:10],
        )
        total_updated += updated
    return total_updated


def refresh_watchlist_datetimes(force: bool = False) -> int:
    total_updated = 0
    for rel in WATCHLIST_PATHS:
        path = Path(rel)
        if not path.exists():
            continue
        items = load_watchlist(path)
        if not items:
            continue
        tickers = sorted({str(it.get("ticker", "")).strip() for it in items if it.get("ticker")})
        print(f"fetcher: refreshing datetimes for {len(tickers)} tickers in {path}")
        log_event("fetcher_refresh_start", path=str(path), ticker_count=len(tickers))
        results = fetch_earnings_for_tickers(tickers, lookahead_days=DEFAULT_LOOKAHEAD_DAYS)
        updated = 0
        for it in items:
            t = str(it.get("ticker", "")).strip()
            meta = results.get(t, {})
            snap = meta.get("snapshot") if isinstance(meta, dict) else None
            if not snap:
                continue
            updated_fields = False
            if _supports_time_info(meta.get("row") or {}):
                if snap.next_earnings_ts:
                    it["next_earnings_ts"] = snap.next_earnings_ts
                if snap.earnings_datetime_utc:
                    it["earnings_datetime_utc"] = snap.earnings_datetime_utc
                updated_fields = True

            for src_key, dst_key in (
                ("report_time_local", "report_time_local"),
                ("timezone_name", "timezone_name"),
                ("earnings_date", "earnings_date"),
                ("call_time", "call_time"),
            ):
                if getattr(snap, src_key, None):
                    it[dst_key] = getattr(snap, src_key)

            if updated_fields or any(getattr(snap, k, None) for k in ["report_time_local", "timezone_name"]):
                it["fetched_at"] = snap.fetched_at
                updated += 1

        if updated:
            save_watchlist(path, items)
        print(f"fetcher: updated {updated}/{len(items)} in {path}")
        log_event("fetcher_refresh_done", path=str(path), updated=updated, total=len(items))
        total_updated += updated
    return total_updated


def populate_watchlists(force: bool = False) -> int:
    total_updated = 0
    for rel in WATCHLIST_PATHS:
        path = Path(rel)
        if not path.exists():
            continue
        items = load_watchlist(path)
        if not items:
            continue
        tickers = sorted({str(it.get("ticker", "")).strip() for it in items if it.get("ticker")})
        print(f"fetcher: populating earnings datetimes for {len(tickers)} tickers in {path}")
        log_event("fetcher_populate_start", path=str(path), ticker_count=len(tickers))
        results = fetch_earnings_for_tickers(tickers, lookahead_days=DEFAULT_LOOKAHEAD_DAYS)
        updated = 0
        for it in items:
            t = str(it.get("ticker", "")).strip()
            meta = results.get(t, {})
            snap = meta.get("snapshot") if isinstance(meta, dict) else None
            row = meta.get("row") if isinstance(meta, dict) else None
            if not snap or not _supports_time_info(row or {}):
                continue

            if snap.next_earnings_ts:
                it["next_earnings_ts"] = snap.next_earnings_ts
            if snap.earnings_datetime_utc:
                it["earnings_datetime_utc"] = snap.earnings_datetime_utc
            for src_key, dst_key in (
                ("report_time_local", "report_time_local"),
                ("timezone_name", "timezone_name"),
                ("earnings_date", "earnings_date"),
                ("call_time", "call_time"),
            ):
                if getattr(snap, src_key, None):
                    it[dst_key] = getattr(snap, src_key)
            it["fetched_at"] = snap.fetched_at
            updated += 1

        save_watchlist(path, items)
        print(f"fetcher: updated {updated}/{len(items)} in {path}")
        log_event("fetcher_populate_done", path=str(path), updated=updated, total=len(items))
        total_updated += updated
    return total_updated


def build_upcoming_earnings_cache(lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS) -> Path:
    items = []
    for rel in WATCHLIST_PATHS:
        items.extend(load_watchlist(Path(rel)))
    tickers = sorted({str(it.get("ticker", "")).strip() for it in items if it.get("ticker")})
    print(f"fetcher: building upcoming_earnings cache for {len(tickers)} tickers")
    by_ticker = fetch_earnings_for_tickers(tickers, lookahead_days=lookahead_days)

    cutoff = datetime.now(timezone.utc) + timedelta(days=lookahead_days)
    rows = []
    for t, meta in by_ticker.items():
        snap = meta.get("snapshot")
        if not snap or not snap.earnings_datetime_utc:
            continue
        try:
            rpt = datetime.fromisoformat(snap.earnings_datetime_utc)
            if rpt.tzinfo is None:
                rpt = rpt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if rpt > cutoff:
            continue
        rows.append(snap.to_dict())

    dest = UPCOMING_EARNINGS
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"fetcher: wrote {len(rows)} upcoming rows to {dest}")
    log_event("fetcher_upcoming_done", count=len(rows), path=str(dest))
    return dest


def load_upcoming_earnings(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or UPCOMING_EARNINGS
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def next_report_run(now: datetime | None = None, window_minutes: int = DEFAULT_POST_REPORT_WINDOW_MINUTES) -> tuple[str | None, str | None, datetime | None]:
    if now is None:
        now = datetime.now(timezone.utc)
    best_ticker = None
    best_dt_iso = None
    best_target = None
    fallback_ticker = None
    fallback_dt_iso = None
    fallback_target = None
    for rel in WATCHLIST_PATHS:
        path = Path(rel)
        if not path.exists():
            continue
        for it in load_watchlist(path):
            dt_iso = it.get("earnings_datetime_utc")
            if not dt_iso:
                dt_iso = it.get("next_earnings_ts")
                if dt_iso and fallback_target is None:
                    try:
                        fallback_dt = datetime.fromisoformat(str(dt_iso).replace("Z", "+00:00"))
                        if fallback_dt.tzinfo is None:
                            fallback_dt = fallback_dt.replace(tzinfo=timezone.utc)
                        fallback_target = fallback_dt + timedelta(minutes=window_minutes)
                        fallback_ticker = str(it.get("ticker", "")).strip() or None
                        fallback_dt_iso = fallback_dt.isoformat()
                    except Exception:
                        pass
                continue
            try:
                dt = datetime.fromisoformat(str(dt_iso).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < now:
                continue
            target = dt + timedelta(minutes=window_minutes)
            if best_target is None or target < best_target:
                best_target = target
                best_ticker = str(it.get("ticker", "")).strip() or None
                best_dt_iso = dt.isoformat()

    if best_target:
        return best_ticker, best_dt_iso, best_target
    return fallback_ticker, fallback_dt_iso, fallback_target


def load_latest_snapshots(path: Path | None = None) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    p = path or EARNINGS_HISTORY
    if not p.exists():
        return []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        latest[str(obj.get("ticker"))] = obj
    return list(latest.values())


def history_snapshot_for_ticker(ticker: str, path: Path | None = None) -> dict[str, Any] | None:
    for item in load_latest_snapshots(path):
        if str(item.get("ticker", "")).strip().upper() == ticker.upper():
            return item
    return None


def compare_to_history(snap: EarningsSnapshot, path: Path | None = None) -> dict[str, Any]:
    hist = history_snapshot_for_ticker(snap.ticker, path) or {}
    comparison: dict[str, Any] = {
        "ticker": snap.ticker,
        "earnings_datetime_utc": snap.earnings_datetime_utc,
        "fetched_at": snap.fetched_at,
        "changes": [],
    }
    for key in ("eps_actual", "eps_estimate", "revenue_actual", "revenue_estimate", "earnings_datetime_utc"):
        hist_val = hist.get(key)
        new_val = getattr(snap, key, None)
        if key in ("eps_actual", "eps_estimate", "revenue_actual", "revenue_estimate"):
            hist_val = _as_number(hist_val)
            new_val = _as_number(new_val)
        if hist_val != new_val and new_val is not None:
            comparison["changes"].append({"field": key, "old": hist_val, "new": new_val})
    comparison["changed"] = bool(comparison["changes"])
    return comparison


def fetch_reported_earnings_for_tickers(tickers: list[str]) -> dict[str, EarningsSnapshot]:
    snapshots: dict[str, EarningsSnapshot] = {}
    for t in tickers:
        try:
            snap = fetch_ticker_earnings(t, lookahead_days=1)
            snapshots[t] = snap
        except Exception:
            snapshots[t] = EarningsSnapshot(t)
    return snapshots


def score_and_emit_alerts(tickers: list[str] | None = None, output_path: Path | None = None) -> list[dict[str, Any]]:
    from scorer import emit_alerts  # local import to avoid import cycle
    payload = output_path or ALERTS_PATH
    alerts = emit_alerts(output_path=str(payload), tickers=set(tickers) if tickers else None)
    return alerts


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
    snap.source = "edgar"
    return snap


def ingest_new_reports(tickers: list[str] | None = None) -> int:
    items = []
    for rel in WATCHLIST_PATHS:
        items.extend(load_watchlist(Path(rel)))
    all_tickers = sorted({str(it.get("ticker", "")).strip() for it in items if it.get("ticker")})
    work_tickers = list(tickers) if tickers else all_tickers
    print(f"fetcher: ingesting new reports for {len(work_tickers)} tickers")

    prior_index = _load_edgar_index()
    new_index = dict(prior_index)
    ingested = 0
    for ticker in work_tickers:
        try:
            cik = prior_index.get(ticker.upper())
            if not cik:
                ck_map = DATA_DIR / "cik_tickers.json"
                if ck_map.exists():
                    try:
                        mapping = json.loads(ck_map.read_text(encoding="utf-8"))
                        if isinstance(mapping, dict):
                            cik = mapping.get(ticker.upper())
                    except Exception:
                        cik = None
            snap = EarningsSnapshot(ticker)
            if cik:
                facts = _edgar_company_facts(cik)
                snap = _edgar_facts_to_snapshot(ticker, facts)
            save_earnings_snapshots([snap])
            if cik:
                new_index[ticker.upper()] = cik
            ingested += 1
        except Exception as exc:
            print(f"fetcher: ingest failed for {ticker}: {exc}")
            log_event("fetcher_ingest_failed", ticker=ticker, error=str(exc))
    _save_edgar_index(new_index)
    print(f"fetcher: ingested {ingested}/{len(work_tickers)} snapshots")
    log_event("fetcher_ingest_done", ingested=ingested, total=len(work_tickers))
    return ingested


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


def schedule_next_run(
    ticker: str | None = None,
    now: datetime | None = None,
    window_minutes: int = DEFAULT_POST_REPORT_WINDOW_MINUTES,
    lookahead_hours: int = DEFAULT_RESCHEDULE_LOOKAHEAD_HOURS,
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

    _, _, suggested = next_report_run(now=now, window_minutes=window_minutes)
    if suggested is None or (run_before is not None and suggested <= run_before):
        if ticker:
            snap = fetch_ticker_earnings(ticker)
            dt_iso = snap.earnings_datetime_utc or snap.next_earnings_ts
            if dt_iso:
                try:
                    dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    suggested = dt + timedelta(minutes=window_minutes)
                except Exception:
                    suggested = now + timedelta(hours=lookahead_hours)
            else:
                suggested = now + timedelta(hours=lookahead_hours)
        else:
            suggested = now + timedelta(hours=lookahead_hours)

    suggestion = {
        "target_run_at": suggested.isoformat(),
        "target_ticker": ticker,
        "window_minutes": window_minutes,
        "lookahead_hours": lookahead_hours,
        "updated_at": now.isoformat(),
    }
    state["next_suggested"] = suggestion
    state["last_scheduled_at"] = now.isoformat()
    if ticker:
        state["last_ticker"] = ticker
    save_schedule_state(state)

    log_event(
        "fetcher_schedule_next",
        ticker=ticker,
        target_run_at=suggested.isoformat(),
        window_minutes=window_minutes,
    )
    return suggestion


def cmd_fetch(args: argparse.Namespace) -> int:
    selected = getattr(args, "tickers", None)
    tickers: list[str] = []
    if isinstance(selected, str):
        tickers = [x.strip() for x in selected.split(",") if x.strip()]

    if args.backfill:
        return backfill_watchlists_for_tickers(tickers or None)

    if args.populate:
        return populate_watchlists(force=bool(getattr(args, "force", False)))

    if args.refresh:
        return refresh_watchlist_datetimes(force=bool(getattr(args, "force", False)))

    if args.upcoming:
        lookahead = int(getattr(args, "lookahead_days", DEFAULT_LOOKAHEAD_DAYS) or DEFAULT_LOOKAHEAD_DAYS)
        build_upcoming_earnings_cache(lookahead_days=lookahead)
        upcoming = load_upcoming_earnings()
        t_earliest, dt_earliest, target = next_report_run()
        print(f"fetcher: count={len(upcoming)} earliest_ticker={t_earliest} next_earnings_ts={dt_earliest} suggested_run={target.isoformat() if target else None}")
        return 0

    if args.schedule:
        ticker = tickers[0] if tickers else None
        window = int(getattr(args, "window", DEFAULT_POST_REPORT_WINDOW_MINUTES) or DEFAULT_POST_REPORT_WINDOW_MINUTES)
        lookahead = int(getattr(args, "lookahead_hours", DEFAULT_RESCHEDULE_LOOKAHEAD_HOURS) or DEFAULT_RESCHEDULE_LOOKAHEAD_HOURS)
        suggestion = schedule_next_run(ticker=ticker, window_minutes=window, lookahead_hours=lookahead)
        for k, v in suggestion.items():
            print(f"fetcher: {k}={v}")
        return 0

    if args.ingest:
        return ingest_new_reports(tickers or None)

    if args.fetch_reports:
        if not tickers:
            for rel in WATCHLIST_PATHS:
                tickers.extend([str(it.get("ticker", "")).strip() for it in load_watchlist(Path(rel)) if it.get("ticker")])
            tickers = sorted(set(tickers))
        snapshots = fetch_reported_earnings_for_tickers(tickers)
        saved = []
        for t, snap in snapshots.items():
            comparison = compare_to_history(snap)
            if snap.earnings_datetime_utc:
                snap.reported_at = snap.fetched_at
            rec = snap.to_dict()
            rec["history_comparison"] = comparison
            saved.append(snap)
        save_earnings_snapshots(saved)
        print(f"fetcher: saved {len(saved)} report snapshots")
        return 0

    if args.emit_alerts:
        score_and_emit_alerts(tickers)
        return 0

    if tickers:
        raw = fetch_earnings_for_tickers(tickers)
        snapshots = [raw[t].get("snapshot", EarningsSnapshot(t)) for t in tickers]
        save_earnings_snapshots(snapshots)
        print(f"fetcher: saved {len(snapshots)} snapshots")
        return 0

    for rel in WATCHLIST_PATHS:
        items = load_watchlist(Path(rel))
        tickers.extend([str(it.get("ticker", "")).strip() for it in items if it.get("ticker")])
    tickers = sorted(set(tickers))
    print(f"fetcher: fetching earnings for {len(tickers)} tickers")
    raw = fetch_earnings_for_tickers(tickers)
    snapshots = [raw[t].get("snapshot", EarningsSnapshot(t)) for t in tickers]
    save_earnings_snapshots(snapshots)
    print(f"fetcher: saved {len(snapshots)} snapshots")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fetch earnings data for watchlists")
    parser.add_argument("-o", "--output")
    parser.add_argument("--populate", action="store_true", help="populate earnings datetimes from NASDAQ calendar")
    parser.add_argument("--refresh", action="store_true", help="refresh existing watchlist datetimes")
    parser.add_argument("--force", action="store_true", help="force overwrite existing datetimes")
    parser.add_argument("--upcoming", action="store_true", help="build upcoming cache + print next report target")
    parser.add_argument("--lookahead-days", type=int, default=DEFAULT_LOOKAHEAD_DAYS, help="lookahead days for upcoming cache")
    parser.add_argument("--schedule", action="store_true", help="suggest/cache next exact report-arrival run target")
    parser.add_argument("--window", type=int, default=DEFAULT_POST_REPORT_WINDOW_MINUTES, help="minutes after report to schedule run")
    parser.add_argument("--lookahead-hours", type=int, default=DEFAULT_RESCHEDULE_LOOKAHEAD_HOURS, help="hours to look ahead when scheduling")
    parser.add_argument("--backfill", action="store_true", help="fill empty entries with sample dates")
    parser.add_argument("--ingest", action="store_true", help="ingest latest available actuals via NASDAQ/SEC")
    parser.add_argument("--fetch-reports", action="store_true", help="fetch live reported earnings for due tickers and compare to history")
    parser.add_argument("--emit-alerts", action="store_true", help="score latest snapshots and emit alerts")
    parser.add_argument("--tickers", help="comma-separated tickers to limit operation")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not any(
        [
            args.populate,
            args.refresh,
            args.upcoming,
            args.schedule,
            args.backfill,
            args.ingest,
            args.fetch_reports,
            args.emit_alerts,
            args.output,
            getattr(args, "tickers", None),
        ]
    ):
        parser.print_help()
        return 0
    return cmd_fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
