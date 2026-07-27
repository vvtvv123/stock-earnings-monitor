from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import history  # noqa: E402
import monitor  # noqa: E402
import nasdaq  # noqa: E402
import scorer  # noqa: E402


class NasdaqRowTests(TestCase):
    def test_parses_real_schema(self):
        row = {
            "symbol": "AAPL",
            "eps": "$1.65",
            "epsForecast": "$1.60",
            "fiscalQuarterEnding": "Jun/2026",
            "time": "time-not-supplied",
        }
        self.assertEqual(nasdaq.row_symbol(row), "AAPL")
        self.assertEqual(nasdaq.row_eps_actual(row), 1.65)
        self.assertEqual(nasdaq.row_eps_estimate(row), 1.6)
        self.assertEqual(nasdaq.row_period_end(row), "2026-06")
        self.assertTrue(nasdaq.has_reported(row))

    def test_unreported_row_has_no_eps(self):
        row = {"symbol": "AZN", "epsForecast": "$2.50", "fiscalQuarterEnding": "Jun/2026"}
        self.assertFalse(nasdaq.has_reported(row))


class ScorerTests(TestCase):
    def test_alert_when_both_grow_past_threshold(self):
        result = scorer.score_growth(
            eps_actual=2.1, eps_prior=2.0, revenue_actual=110.0, revenue_prior=100.0
        )
        self.assertTrue(result["eps_growth_ok"])
        self.assertTrue(result["revenue_growth_ok"])
        self.assertTrue(result["alert"])

    def test_no_alert_when_eps_growth_below_threshold(self):
        result = scorer.score_growth(
            eps_actual=2.01, eps_prior=2.0, revenue_actual=110.0, revenue_prior=100.0
        )
        self.assertFalse(result["eps_growth_ok"])
        self.assertFalse(result["alert"])

    def test_no_alert_without_prior_data(self):
        result = scorer.score_growth(eps_actual=2.1, eps_prior=None, revenue_actual=110.0, revenue_prior=100.0)
        self.assertIsNone(result["eps_growth_pct"])
        self.assertFalse(result["alert"])

    def test_no_alert_when_revenue_missing(self):
        result = scorer.score_growth(eps_actual=2.1, eps_prior=2.0, revenue_actual=None, revenue_prior=100.0)
        self.assertTrue(result["eps_growth_ok"])
        self.assertFalse(result["revenue_growth_ok"])
        self.assertFalse(result["alert"])


class HistoryTests(TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkstemp(suffix=".jsonl")[1])

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_append_and_latest_prior(self):
        history.append_record(
            {"ticker": "AAPL", "period_end": "2025-12", "eps_actual": 1.5, "revenue_actual": 90.0}, self.path
        )
        history.append_record(
            {"ticker": "AAPL", "period_end": "2026-03", "eps_actual": 1.6, "revenue_actual": 95.0}, self.path
        )
        index = history.load_index(self.path)
        prior = history.latest_prior(index, "AAPL")
        self.assertEqual(prior["period_end"], "2026-03")

    def test_has_period_dedupes(self):
        history.append_record({"ticker": "AAPL", "period_end": "2025-12", "eps_actual": 1.5}, self.path)
        index = history.load_index(self.path)
        self.assertTrue(history.has_period(index, "AAPL", "2025-12"))
        self.assertFalse(history.has_period(index, "AAPL", "2026-03"))


class MonitorRunTests(TestCase):
    def test_run_once_alerts_on_growth_and_appends_history(self):
        workspace = Path(tempfile.mkdtemp(prefix="stock-monitor-test-"))
        history_path = workspace / "earnings_history.jsonl"
        alerts_path = workspace / "alerts.jsonl"
        history.append_record(
            {"ticker": "AAPL", "period_end": "2026-03", "eps_actual": 1.5, "revenue_actual": 90.0},
            history_path,
        )

        nasdaq.fetch_calendar = lambda date_str, timeout=20: [
            {"symbol": "AAPL", "eps": "$1.65", "epsForecast": "$1.6", "fiscalQuarterEnding": "Jun/2026"}
        ]
        monitor.edgar.load_cik_map = lambda path=None: {"AAPL": "0000320193"}
        monitor.edgar.latest_quarterly_revenue = lambda cik: 95.0
        sent = []
        monitor.alert_telegram.send_alert = lambda alert, chat_id=None: sent.append(alert) or True

        cfg = {
            "history": {"path": str(history_path)},
            "alerts": {"path": str(alerts_path)},
        }
        rc = monitor.run_once(cfg, date_override="2026-07-27")
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["ticker"], "AAPL")
        self.assertTrue(alerts_path.exists())

        index = history.load_index(history_path)
        self.assertTrue(history.has_period(index, "AAPL", "2026-06"))

    def test_run_once_skips_already_recorded_period(self):
        workspace = Path(tempfile.mkdtemp(prefix="stock-monitor-test-"))
        history_path = workspace / "earnings_history.jsonl"
        alerts_path = workspace / "alerts.jsonl"
        history.append_record(
            {"ticker": "AAPL", "period_end": "2026-06", "eps_actual": 1.65, "revenue_actual": 95.0},
            history_path,
        )

        nasdaq.fetch_calendar = lambda date_str, timeout=20: [
            {"symbol": "AAPL", "eps": "$1.65", "fiscalQuarterEnding": "Jun/2026"}
        ]
        monitor.edgar.load_cik_map = lambda path=None: {"AAPL": "0000320193"}
        sent = []
        monitor.alert_telegram.send_alert = lambda alert, chat_id=None: sent.append(alert) or True

        cfg = {"history": {"path": str(history_path)}, "alerts": {"path": str(alerts_path)}}
        monitor.run_once(cfg, date_override="2026-07-27")
        self.assertEqual(sent, [])

    def test_run_once_no_alert_when_revenue_unavailable(self):
        workspace = Path(tempfile.mkdtemp(prefix="stock-monitor-test-"))
        history_path = workspace / "earnings_history.jsonl"
        alerts_path = workspace / "alerts.jsonl"
        history.append_record(
            {"ticker": "NEWCO", "period_end": "2026-03", "eps_actual": 1.5, "revenue_actual": 90.0},
            history_path,
        )

        nasdaq.fetch_calendar = lambda date_str, timeout=20: [
            {"symbol": "NEWCO", "eps": "$1.65", "fiscalQuarterEnding": "Jun/2026"}
        ]
        monitor.edgar.load_cik_map = lambda path=None: {}
        sent = []
        monitor.alert_telegram.send_alert = lambda alert, chat_id=None: sent.append(alert) or True

        cfg = {"history": {"path": str(history_path)}, "alerts": {"path": str(alerts_path)}}
        monitor.run_once(cfg, date_override="2026-07-27")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
