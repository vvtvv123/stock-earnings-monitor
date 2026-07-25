from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any

from logging_utils import log_event


def format_alert_message(alert: dict[str, Any]) -> str:
    ticker = alert.get("ticker", "?")
    epsa = alert.get("eps_actual")
    epse = alert.get("eps_estimate")
    rev_actual = alert.get("revenue_actual")
    rev_est = alert.get("revenue_estimate")
    criteria = alert.get("criteria", {})
    lines = ["Earnings alert", f"- ticker: {ticker}"]
    for key, value in criteria.items():
        lines.append(f"- {key}: {bool(value)}")
    lines.append(f"- EPS actual/est: {epsa} / {epse}")
    lines.append(f"- Revenue actual/est: {rev_actual} / {rev_est}")
    return "\n".join(lines)


def load_alerts(path: str = "data/alerts.jsonl") -> list[dict[str, Any]]:
    alerts = []
    p = Path(path)
    if not p.exists():
        return alerts
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            alerts.append(__import__("json").loads(line))
        except Exception:
            continue
    return alerts


def send_telegram_message(chat_id: str, message: str) -> None:
    subprocess.run(
        ["hermes", "send", "telegram", chat_id, message],
        check=False,
    )


def cmd_send(args: argparse.Namespace) -> int:
    chat_id = getattr(args, "chat_id", None) or os.getenv("TELEGRAM_CHAT_ID", "8782198462")
    alerts = load_alerts(args.alerts)
    if not alerts:
        print("alert_whatsapp: no alerts to send")
        log_event("alert_send_skipped", reason="no alerts", alerts_path=args.alerts)
        return 0
    sent = 0
    for alert in alerts:
        text = format_alert_message(alert)
        try:
            send_telegram_message(chat_id, text)
            sent += 1
        except Exception as exc:  # pragma: no cover
            print(f"alert_whatsapp: send failed for {alert.get('ticker')}: {exc}")
            log_event("alert_send_failed", ticker=alert.get("ticker"), error=str(exc))
    print(f"alert_whatsapp: dispatched {sent}/{len(alerts)} messages")
    log_event("alert_send_done", dispatched=sent, total=len(alerts), alerts_path=args.alerts, chat_id=chat_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="send earnings alerts to Telegram")
    parser.add_argument("--alerts", default="data/alerts.jsonl")
    parser.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", "8782198462"))
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return cmd_send(args)


if __name__ == "__main__":
    raise SystemExit(main())
