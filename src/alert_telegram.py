from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from logging_utils import log_event

DEFAULT_CHAT_ID = "8782198462"


def _fmt_pct(value: Any) -> str:
    return f"{value:.1f}%" if isinstance(value, (int, float)) else "n/a"


def format_alert_message(alert: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Earnings growth alert",
            f"- ticker: {alert.get('ticker', '?')}",
            f"- period: {alert.get('period_end') or alert.get('report_date')}",
            f"- EPS actual/prior: {alert.get('eps_actual')} / {alert.get('eps_prior')} "
            f"(growth {_fmt_pct(alert.get('eps_growth_pct'))})",
            f"- Revenue actual/prior: {alert.get('revenue_actual')} / {alert.get('revenue_prior')} "
            f"(growth {_fmt_pct(alert.get('revenue_growth_pct'))})",
        ]
    )


def send_telegram_message(chat_id: str, message: str) -> None:
    subprocess.run(["hermes", "send", "telegram", chat_id, message], check=False)


def send_alert(alert: dict[str, Any], chat_id: str | None = None) -> bool:
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    try:
        send_telegram_message(chat_id, format_alert_message(alert))
        log_event("alert_send_done", ticker=alert.get("ticker"), chat_id=chat_id)
        return True
    except Exception as exc:  # pragma: no cover
        print(f"alert_telegram: send failed for {alert.get('ticker')}: {exc}")
        log_event("alert_send_failed", ticker=alert.get("ticker"), error=str(exc))
        return False


def load_alerts(path: str = "data/alerts.jsonl") -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return alerts
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            alerts.append(json.loads(line))
        except Exception:
            continue
    return alerts


def cmd_send(args: argparse.Namespace) -> int:
    alerts = load_alerts(args.alerts)
    if not alerts:
        print("alert_telegram: no alerts to send")
        log_event("alert_send_skipped", reason="no alerts", alerts_path=args.alerts)
        return 0
    sent = sum(1 for a in alerts if send_alert(a, args.chat_id))
    print(f"alert_telegram: dispatched {sent}/{len(alerts)} messages")
    log_event("alert_send_batch_done", dispatched=sent, total=len(alerts), alerts_path=args.alerts)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="resend earnings alerts from data/alerts.jsonl to Telegram")
    parser.add_argument("--alerts", default="data/alerts.jsonl")
    parser.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return cmd_send(args)


if __name__ == "__main__":
    raise SystemExit(main())
