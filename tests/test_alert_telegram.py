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
