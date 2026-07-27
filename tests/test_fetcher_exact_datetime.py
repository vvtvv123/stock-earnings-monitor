from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

_fetcher = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location("fetcher", REPO / "src" / "fetcher.py")
)
_fetcher_spec = importlib.util.spec_from_file_location("fetcher", REPO / "src" / "fetcher.py")
_fetcher = importlib.util.module_from_spec(_fetcher_spec)
_fetcher_spec.loader.exec_module(_fetcher)

_watcher = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location("watcher", REPO / "src" / "watcher.py")
)
_watcher_spec = importlib.util.spec_from_file_location("watcher", REPO / "src" / "watcher.py")
_watcher = importlib.util.module_from_spec(_watcher_spec)
_watcher_spec.loader.exec_module(_watcher)

workspace = Path(tempfile.mkdtemp(prefix="stock-test-workspace-"))


def fake_json_get(url, params=None, timeout=20):
    date = (params or {}).get("date")
    if date == "2026-07-27":
        return {
            "data": {
                "rows": [
                    {
                        "symbol": "X",
                        "report_date": date,
                        "time": "time-after-hours",
                        "timeZone": "US/Eastern",
                        "epsForecast": "1.50",
                        "epsActual": "1.60",
                    }
                ]
            }
        }
    return {"data": {"rows": []}}


def isolate_fetcher_paths():
    _fetcher.ROOT = workspace
    _fetcher.WATCHLIST_PATHS = [
        workspace / "watchlists" / "nasdaq_tickers.json",
        workspace / "watchlists" / "sp500_tickers.json",
    ]
    _fetcher.UPCOMING_EARNINGS = workspace / "data" / "upcoming_earnings.jsonl"
    _fetcher.EARNINGS_HISTORY = workspace / "data" / "earnings_history.jsonl"
    _fetcher.ALERTS_PATH = workspace / "data" / "alerts.jsonl"
    _fetcher.EDGAR_INDEX_PATH = workspace / "data" / "edgar_last_fetch.json"
    _fetcher.SCHEDULE_STATE_PATH = workspace / "data" / "fetcher_schedule_state.json"
    _fetcher.DATA_DIR = workspace / "data"
    _fetcher.LOG_PATH = workspace / "logs" / "run.jsonl"
    _fetcher._json_get = fake_json_get


class ExactDatetimeWorkflowTests(TestCase):
    def test_supports_time_info(self):
        self.assertTrue(_fetcher._supports_time_info({"report_date": "2026-07-27", "time": "time-pre-market"}))
        self.assertTrue(_fetcher._supports_time_info({"report_date": "2026-07-27", "time": "time-after-hours"}))
        self.assertTrue(_fetcher._supports_time_info({"report_date": "2026-07-27T08:00:00Z", "time": ""}))
        self.assertFalse(_fetcher._supports_time_info({"report_date": "2026-07-27", "time": "time-not-supplied"}))

    def test_row_to_snapshot_alias_fields(self):
        row = {
            "symbol": "X",
            "report_date": "2026-07-27T16:00:00Z",
            "time": "time-after-hours",
            "timeZone": "US/Eastern",
            "epsForecast": "1.50",
            "epsActual": "1.60",
        }
        snap = _fetcher._row_to_snapshot(row)
        self.assertEqual(snap.earnings_datetime_utc, "2026-07-27T16:00:00+00:00")
        self.assertEqual(snap.report_time_local, "time-after-hours")
        self.assertEqual(snap.timezone_name, "US/Eastern")
        self.assertEqual(_fetcher._as_number(snap.eps_estimate), 1.5)
        self.assertEqual(_fetcher._as_number(snap.eps_actual), 1.6)

    def test_populate_writes_exact_fields_and_upcoming(self):
        isolate_fetcher_paths()
        (workspace / "watchlists").mkdir(parents=True, exist_ok=True)
        (workspace / "watchlists" / "nasdaq_tickers.json").write_text(
            json.dumps([{"ticker": "X", "next_earnings_ts": "2026-07-27T00:00:00"}]) + "\n",
            encoding="utf-8",
        )
        (workspace / "watchlists" / "sp500_tickers.json").write_text("[]\n", encoding="utf-8")

        updated = _fetcher.populate_watchlists(force=False)
        self.assertEqual(updated, 1)

        raw = json.loads((workspace / "watchlists" / "nasdaq_tickers.json").read_text(encoding="utf-8"))
        self.assertEqual(raw[0]["earnings_datetime_utc"], "2026-07-27T16:00:00+00:00")
        self.assertEqual(raw[0]["report_time_local"], "time-after-hours")

        _fetcher.build_upcoming_earnings_cache(lookahead_days=60)
        rows = _fetcher.load_upcoming_earnings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["earnings_datetime_utc"], "2026-07-27T16:00:00+00:00")

    def test_next_report_run_exact_and_fallback(self):
        watch = workspace / "watchlists"
        watch.mkdir(parents=True, exist_ok=True)
        (watch / "nasdaq_tickers.json").write_text(
            json.dumps(
                [
                    {"ticker": "PAST", "earnings_datetime_utc": "2026-07-25T08:00:00+00:00", "next_earnings_ts": "2026-07-25"},
                    {"ticker": "FUTURE", "earnings_datetime_utc": "2026-07-27T08:00:00+00:00", "next_earnings_ts": "2026-07-27"},
                    {"ticker": "FALLBACK", "next_earnings_ts": "2026-07-28T08:00:00"},
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (watch / "sp500_tickers.json").write_text("[]\n", encoding="utf-8")
        _fetcher.ROOT = workspace
        _fetcher.WATCHLIST_PATHS = [watch / "nasdaq_tickers.json", watch / "sp500_tickers.json"]

        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        ticker, dt_iso, target = _fetcher.next_report_run(now=now, window_minutes=30)
        self.assertEqual(ticker, "FUTURE")
        self.assertEqual(dt_iso, "2026-07-27T08:00:00+00:00")
        self.assertEqual(target, datetime(2026, 7, 27, 8, 30, tzinfo=timezone.utc))

    def test_schedule_next_run_persists_state(self):
        isolate_fetcher_paths()
        _fetcher.WATCHLIST_PATHS = [
            workspace / "watchlists" / "nasdaq_tickers.json",
            workspace / "watchlists" / "sp500_tickers.json",
        ]
        (workspace / "watchlists" / "nasdaq_tickers.json").write_text(
            json.dumps([{"ticker": "Z", "earnings_datetime_utc": "2026-07-27T10:00:00+00:00"}]) + "\n",
            encoding="utf-8",
        )
        (workspace / "watchlists" / "sp500_tickers.json").write_text("[]\n", encoding="utf-8")

        suggestion = _fetcher.schedule_next_run(
            ticker="Z",
            now=datetime(2026, 7, 26, tzinfo=timezone.utc),
            window_minutes=30,
            lookahead_hours=2,
        )
        self.assertEqual(suggestion["target_run_at"], "2026-07-27T10:30:00+00:00")
        self.assertEqual(suggestion["target_ticker"], "Z")

        state = json.loads((workspace / "data" / "fetcher_schedule_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["next_suggested"]["target_run_at"], "2026-07-27T10:30:00+00:00")

    def test_watcher_earliest_exact_datetime(self):
        isolate_fetcher_paths()
        _watcher.NASDAQ_PATH = workspace / "watchlists" / "nasdaq_tickers.json"
        _watcher.SP500_PATH = workspace / "watchlists" / "sp500_tickers.json"
        (workspace / "watchlists" / "nasdaq_tickers.json").write_text(
            json.dumps(
                [
                    {"ticker": "LATE", "earnings_datetime_utc": "2026-07-27T10:00:00+00:00"},
                    {"ticker": "EARLY", "earnings_datetime_utc": "2026-07-27T08:00:00+00:00"},
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (workspace / "watchlists" / "sp500_tickers.json").write_text("[]\n", encoding="utf-8")

        args = type("Args", (), {"once": True, "earliest": False})()
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _watcher.cmd_once(args)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("earliest upcoming ticker=EARLY next_earnings_ts=2026-07-27T08:00:00+00:00", out)


if __name__ == "__main__":
    unittest.main()
