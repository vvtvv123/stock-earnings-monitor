from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import finnhub_client  # noqa: E402
import history  # noqa: E402
import monitor  # noqa: E402
import scorer  # noqa: E402


class FinnhubRowTests(TestCase):
    def test_parses_real_schema(self):
        row = {
            "symbol": "AGYS",
            "date": "2026-07-27",
            "hour": "amc",
            "quarter": 1,
            "year": 2027,
            "epsEstimate": 0.4104,
            "epsActual": 0.49,
            "revenueEstimate": 87700242,
            "revenueActual": 87680000,
        }
        self.assertEqual(finnhub_client.row_symbol(row), "AGYS")
        self.assertEqual(finnhub_client.row_eps_actual(row), 0.49)
        self.assertEqual(finnhub_client.row_eps_estimate(row), 0.4104)
        self.assertEqual(finnhub_client.row_revenue_actual(row), 87680000)
        self.assertEqual(finnhub_client.row_period_end(row), "2027-Q1")
        self.assertTrue(finnhub_client.has_reported(row))

    def test_unreported_row_has_no_eps(self):
        row = {"symbol": "AHT", "epsEstimate": -7.8477, "epsActual": None, "quarter": 2, "year": 2026}
        self.assertFalse(finnhub_client.has_reported(row))


class ScorerTests(TestCase):
    # Explicit min_*_growth_pct throughout so these stay correct regardless
    # of scorer.DEFAULT_MIN_*_GROWTH_PCT's actual value.

    def test_alert_when_both_grow_past_threshold(self):
        result = scorer.score_growth(
            eps_actual=2.2,
            eps_prior=2.0,
            revenue_actual=110.0,
            revenue_prior=100.0,
            min_eps_growth_pct=10.0,
            min_revenue_growth_pct=10.0,
        )
        self.assertTrue(result["eps_growth_ok"])
        self.assertTrue(result["revenue_growth_ok"])
        self.assertTrue(result["alert"])

    def test_no_alert_when_eps_growth_below_threshold(self):
        result = scorer.score_growth(
            eps_actual=2.01,
            eps_prior=2.0,
            revenue_actual=110.0,
            revenue_prior=100.0,
            min_eps_growth_pct=10.0,
            min_revenue_growth_pct=10.0,
        )
        self.assertFalse(result["eps_growth_ok"])
        self.assertFalse(result["alert"])

    def test_no_alert_without_prior_data(self):
        result = scorer.score_growth(eps_actual=2.1, eps_prior=None, revenue_actual=110.0, revenue_prior=100.0)
        self.assertIsNone(result["eps_growth_pct"])
        self.assertFalse(result["alert"])

    def test_no_alert_when_revenue_missing(self):
        result = scorer.score_growth(
            eps_actual=2.2,
            eps_prior=2.0,
            revenue_actual=None,
            revenue_prior=100.0,
            min_eps_growth_pct=10.0,
            min_revenue_growth_pct=10.0,
        )
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
            {"ticker": "AAPL", "period_end": "2025-Q4", "eps_actual": 1.5, "revenue_actual": 90.0}, self.path
        )
        history.append_record(
            {"ticker": "AAPL", "period_end": "2026-Q1", "eps_actual": 1.6, "revenue_actual": 95.0}, self.path
        )
        index = history.load_index(self.path)
        prior = history.latest_prior(index, "AAPL")
        self.assertEqual(prior["period_end"], "2026-Q1")

    def test_has_period_dedupes(self):
        history.append_record({"ticker": "AAPL", "period_end": "2025-Q4", "eps_actual": 1.5}, self.path)
        index = history.load_index(self.path)
        self.assertTrue(history.has_period(index, "AAPL", "2025-Q4"))
        self.assertFalse(history.has_period(index, "AAPL", "2026-Q1"))

    def test_latest_prior_sorts_by_report_date_across_mixed_period_end_formats(self):
        # Regression test: this repo's real history file has period_end in three
        # different formats from different data-source eras ("2024-12-31" from
        # old EDGAR ingestion, "2026-06" from a NASDAQ-era test run, "2026-Q2"
        # from Finnhub). Sorting by period_end directly would misorder these;
        # report_date (always a real ISO date) must be used instead.
        history.append_record(
            {"ticker": "TSLA", "period_end": "2024-12-31", "eps_actual": 2.04, "revenue_actual": 97690000000},
            self.path,
        )
        history.append_record(
            {
                "ticker": "TSLA",
                "report_date": "2026-07-27",
                "period_end": "2026-Q2",
                "eps_actual": 3.5,
                "revenue_actual": 120000000000,
            },
            self.path,
        )
        index = history.load_index(self.path)
        prior = history.latest_prior(index, "TSLA")
        self.assertEqual(prior["period_end"], "2026-Q2")


class MonitorRunTests(TestCase):
    def test_run_once_alerts_on_growth_and_appends_history(self):
        workspace = Path(tempfile.mkdtemp(prefix="stock-monitor-test-"))
        history_path = workspace / "earnings_history.jsonl"
        alerts_path = workspace / "alerts.jsonl"
        history.append_record(
            {"ticker": "AAPL", "period_end": "2026-Q1", "eps_actual": 1.5, "revenue_actual": 90.0},
            history_path,
        )

        monitor.finnhub_client.fetch_calendar = lambda date_str, timeout=20: [
            {
                "symbol": "AAPL",
                "quarter": 2,
                "year": 2026,
                "epsActual": 1.65,
                "epsEstimate": 1.6,
                "revenueActual": 95.0,
                "revenueEstimate": 92.0,
            }
        ]
        sent = []
        monitor.alert_telegram.send_alert = lambda alert, chat_id=None: sent.append(alert) or True

        cfg = {
            "history": {"path": str(history_path)},
            "alerts": {"path": str(alerts_path)},
            # explicit thresholds so this test doesn't drift with scorer.DEFAULT_MIN_*_GROWTH_PCT
            "scorer": {"min_eps_growth_pct": 5.0, "min_revenue_growth_pct": 1.0},
        }
        rc = monitor.run_once(cfg, date_override="2026-07-27")
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["ticker"], "AAPL")
        self.assertTrue(alerts_path.exists())

        index = history.load_index(history_path)
        self.assertTrue(history.has_period(index, "AAPL", "2026-Q2"))

    def test_run_once_skips_already_recorded_period(self):
        workspace = Path(tempfile.mkdtemp(prefix="stock-monitor-test-"))
        history_path = workspace / "earnings_history.jsonl"
        alerts_path = workspace / "alerts.jsonl"
        history.append_record(
            {"ticker": "AAPL", "period_end": "2026-Q2", "eps_actual": 1.65, "revenue_actual": 95.0},
            history_path,
        )

        monitor.finnhub_client.fetch_calendar = lambda date_str, timeout=20: [
            {"symbol": "AAPL", "quarter": 2, "year": 2026, "epsActual": 1.65, "revenueActual": 95.0}
        ]
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
            {"ticker": "NEWCO", "period_end": "2026-Q1", "eps_actual": 1.5, "revenue_actual": 90.0},
            history_path,
        )

        monitor.finnhub_client.fetch_calendar = lambda date_str, timeout=20: [
            {"symbol": "NEWCO", "quarter": 2, "year": 2026, "epsActual": 1.65, "revenueActual": None}
        ]
        sent = []
        monitor.alert_telegram.send_alert = lambda alert, chat_id=None: sent.append(alert) or True

        cfg = {"history": {"path": str(history_path)}, "alerts": {"path": str(alerts_path)}}
        monitor.run_once(cfg, date_override="2026-07-27")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
