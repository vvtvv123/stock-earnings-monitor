from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

_fetcher_spec = importlib.util.spec_from_file_location("fetcher", REPO / "src" / "fetcher.py")
_fetcher = importlib.util.module_from_spec(_fetcher_spec)
_fetcher_spec.loader.exec_module(_fetcher)

_watcher_spec = importlib.util.spec_from_file_location("watcher", REPO / "src" / "watcher.py")
_watcher = importlib.util.module_from_spec(_watcher_spec)
_watcher_spec.loader.exec_module(_watcher)

WORKSPACE = Path(tempfile.mkdtemp(prefix="stock-test-workspace-"))


def isolate_paths() -> None:
    _fetcher.DATA_DIR = WORKSPACE / "data"
    _fetcher.ALERTS_PATH = WORKSPACE / "data" / "alerts.jsonl"
    _fetcher.SCHEDULE_STATE_PATH = WORKSPACE / "data" / "fetcher_schedule_state.json"
    (_fetcher.DATA_DIR).mkdir(parents=True, exist_ok=True)


class RealtimeWorkflowTests(TestCase):
    def test_no_watchlist_api_symbols(self):
        self.assertFalse(hasattr(_fetcher, "WATCHLIST_PATHS"))
        self.assertFalse(hasattr(_fetcher, "populate_watchlists"))
        self.assertFalse(hasattr(_watcher, "NASDAQ_PATH"))
        self.assertFalse(hasattr(_watcher, "SP500_PATH"))

    def test_fetcher_calendar_parses_rows(self):
        isolate_paths()
        _fetcher._json_get = lambda url, params=None, timeout=20: {
            "data": {
                "rows": [
                    {
                        "symbol": "AAPL",
                        "report_date": "2026-07-27",
                        "time": "time-after-hours",
                        "timeZone": "US/Eastern",
                        "epsEstimate": 1.50,
                        "revenueEstimate": 90.0,
                    }
                ]
            }
        }
        rows = _fetcher.fetch_nasdaq_calendar("2026-07-27")
        symbols = [row.get("symbol") for row in rows if isinstance(row, dict)]
        self.assertIn("AAPL", symbols)

    def test_fetcher_find_and_score_path(self):
        isolate_paths()
        _fetcher._json_get = lambda url, params=None, timeout=20: {"data": {"rows": []}}
        _fetcher.find_tickers_at_time = lambda *args, **kwargs: [
            {
                "symbol": "AAPL",
                "report_date": "2026-07-27",
                "time": "time-after-hours",
                "timeZone": "US/Eastern",
                "earnings_datetime_utc": "2026-07-27T16:00:00+00:00",
                "epsEstimate": 1.5,
                "revenueEstimate": 90.0,
                "epsActual": 1.6,
                "revenueActual": 95.0,
            }
        ]
        _fetcher.fetch_reported_earnings_for_tickers = lambda tickers: {
            ticker: type("Snap", (), {
                "ticker": ticker,
                "earnings_datetime_utc": "2026-07-27T16:00:00+00:00",
                "fetched_at": "2026-07-27T16:00:00+00:00",
                "eps_actual": 1.6,
                "eps_estimate": 1.5,
                "revenue_actual": 95.0,
                "revenue_estimate": 90.0,
                "to_dict": lambda *args, **kwargs: {
                    "ticker": "AAPL",
                    "earnings_datetime_utc": "2026-07-27T16:00:00+00:00",
                    "fetched_at": "2026-07-27T16:00:00+00:00",
                    "eps_estimate": 1.5,
                    "eps_actual": 1.6,
                    "revenue_estimate": 90.0,
                    "revenue_actual": 95.0,
                },
            })() for ticker in tickers
        }
        alerts_out = []
        def fake_emit_alerts(output_path=None, tickers=None, snapshots=None):
            alerts_out.extend(snapshots or [])
            return [{"ticker": "AAPL", "alert": True}]
        _fetcher.emit_alerts = fake_emit_alerts
        alerts = _fetcher.fetch_and_score_reports(
            "2026-07-27",
            "2026-07-27T16:00:00",
            window_minutes=60,
        )
        self.assertIsInstance(alerts, list)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].get("ticker"), "AAPL")
        self.assertTrue(alerts[0].get("alert"))

    def test_watcher_earliest_from_calendar(self):
        isolate_paths()
        _fetcher._json_get = lambda url, params=None, timeout=20: {"data": {"rows": []}}
        _watcher._json_get = lambda url, params=None, timeout=20: {
            "data": {
                "rows": [
                    {
                        "symbol": "AAPL",
                        "report_date": "2026-07-27",
                        "time": "time-after-hours",
                        "timeZone": "US/Eastern",
                    }
                ]
            }
        }

        args = type("Args", (), {"once": True, "earliest": False, "lookahead_days": 0})()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _watcher.cmd_once(args)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("earliest upcoming ticker=AAPL", out)

    def test_schedule_state_persisted(self):
        isolate_paths()
        _fetcher._json_get = lambda url, params=None, timeout=20: {"data": {"rows": []}}
        suggestion = _fetcher.schedule_next_run(now=__import__("datetime").datetime(2026, 7, 26, tzinfo=__import__("datetime").timezone.utc))
        self.assertIn("target_run_at", suggestion)
        raw = (_fetcher.SCHEDULE_STATE_PATH).read_text(encoding="utf-8")
        self.assertIn("next_suggested", raw)


if __name__ == "__main__":
    import unittest
    unittest.main()
