from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import alert_telegram  # noqa: E402


class MessageSendTests(TestCase):
    def setUp(self):
        self.calls: list[tuple[str, str]] = []
        self._orig_send = alert_telegram.send_telegram_message
        alert_telegram.send_telegram_message = self._capture
        # Avoid polluting the real log file.
        self.tmpdir = Path(tempfile.mkdtemp(prefix="alert-tg-test-"))
        self.log_path = self.tmpdir / "run.jsonl"
        import logging_utils

        self._orig_log_path = logging_utils.LOG_PATH
        logging_utils.LOG_PATH = self.log_path

    def tearDown(self):
        alert_telegram.send_telegram_message = self._orig_send
        import logging_utils

        logging_utils.LOG_PATH = self._orig_log_path
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture(self, chat_id: str, message: str) -> None:
        self.calls.append((chat_id, message))

    def test_message_subcommand_sends_to_telegram(self):
        rc = alert_telegram.main(
            ["message", "--chat-id", "123", "--message", "hello world"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [("123", "hello world")])

    def test_msg_alias_works(self):
        rc = alert_telegram.main(
            ["msg", "--chat-id", "7", "--message", "aliased"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [("7", "aliased")])

    def test_top_level_message_resolves_chat_id(self):
        rc = alert_telegram.main(
            ["--chat-id", "456", "--message", "top-level text"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [("456", "top-level text")])

    def test_message_uses_env_chat_id(self):
        os.environ["TELEGRAM_CHAT_ID"] = "999"
        try:
            rc = alert_telegram.main(["message", "--message", "via env"])
            self.assertEqual(rc, 0)
            self.assertEqual(self.calls, [("999", "via env")])
        finally:
            del os.environ["TELEGRAM_CHAT_ID"]

    def test_message_requires_message_argument(self):
        with self.assertRaises(SystemExit) as cm:
            alert_telegram.main(["message", "--chat-id", "123"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.calls, [])

    def test_message_logs_event(self):
        alert_telegram.main(
            ["message", "--chat-id", "123", "--message", "logged msg"]
        )
        self.assertTrue(self.log_path.exists())
        import json

        entries = [
            json.loads(line)
            for line in self.log_path.read_text().splitlines()
            if line.strip()
        ]
        self.assertTrue(any(e["event"] == "message_send_done" for e in entries))


class FormatAlertMessageTests(TestCase):
    def test_growth_alert_shows_percentages(self):
        text = alert_telegram.format_alert_message(
            {
                "ticker": "AAPL",
                "report_date": "2026-07-30",
                "eps_actual": 1.65,
                "eps_prior": 1.5,
                "eps_growth_pct": 10.0,
                "revenue_actual": 95.0,
                "revenue_prior": 90.0,
                "revenue_growth_pct": 5.6,
            }
        )
        self.assertIn("Earnings growth alert", text)
        self.assertIn("10.0%", text)

    def test_turned_profitable_alert_does_not_show_a_percentage(self):
        # Real case: LFWD-style crossing, where a % would be meaningless
        # (or actively misleading -- see scorer.score_growth's docstring).
        text = alert_telegram.format_alert_message(
            {
                "ticker": "LFWD",
                "report_date": "2026-08-14",
                "alert_type": "turned_profitable",
                "eps_actual": 0.20,
                "eps_prior": -0.58,
                "revenue_actual": 6620000,
                "revenue_prior": 5724000,
            }
        )
        import re

        self.assertIn("Turned profitable alert", text)
        self.assertIsNone(re.search(r"\d[\d.]*%", text))  # no numeric growth % anywhere
        self.assertIn("-0.58", text)
        self.assertIn("0.2", text)


class SendSubcommandTests(TestCase):
    def setUp(self):
        self.calls: list[tuple[str, str]] = []
        self._orig = alert_telegram.send_telegram_message
        alert_telegram.send_telegram_message = self._capture
        self.tmpdir = Path(tempfile.mkdtemp(prefix="alert-tg-test-"))
        self.alerts_path = self.tmpdir / "alerts.jsonl"
        self.alerts_path.write_text(
            '{"ticker": "AAPL"}\n{"ticker": "MSFT"}\n', encoding="utf-8"
        )

    def tearDown(self):
        alert_telegram.send_telegram_message = self._orig
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture(self, chat_id: str, message: str) -> None:
        self.calls.append((chat_id, message))

    def test_send_subcommand_replays_alerts(self):
        rc = alert_telegram.main(
            ["send", "--chat-id", "111", "--alerts", str(self.alerts_path)]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(cid == "111" for cid, _ in self.calls))


if __name__ == "__main__":
    import unittest

    unittest.main()
