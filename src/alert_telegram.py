from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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
    # `hermes send` uses `--to platform:chat_id` with the message text as a
    # positional argument (see `hermes send telegram --help`).
    subprocess.run(
        ["hermes", "send", "--to", f"telegram:{chat_id}", message],
        check=False,
    )


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
    log_event(
        "alert_send_batch_done",
        dispatched=sent,
        total=len(alerts),
        alerts_path=args.alerts,
    )
    return 0


def cmd_message(args: argparse.Namespace) -> int:
    """Send a free-form message to a Telegram chat.

    Lets other scripts invoke this module to publish arbitrary text to
    Telegram, e.g.::

        python3 src/alert_telegram.py message --chat-id 123 --message "hello"

    or via the shorter subcommand alias `msg`.
    """
    chat_id = args.chat_id or os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    message = args.message
    if not message:
        print("alert_telegram: --message is required", file=sys.stderr)
        return 2
    try:
        send_telegram_message(chat_id, message)
        log_event("message_send_done", chat_id=chat_id, length=len(message))
        print(f"alert_telegram: sent message to {chat_id}")
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"alert_telegram: message send failed for {chat_id}: {exc}", file=sys.stderr)
        log_event("message_send_failed", chat_id=chat_id, error=str(exc))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="send earnings alerts or free-form messages to Telegram"
    )
    # Optional top-level flags. These make the alert-replay command work
    # without an explicit subcommand (backwards compatible with the original
    # `python3 src/alert_telegram.py --chat-id ...` invocation).
    parser.add_argument("--alerts", default="data/alerts.jsonl")
    parser.add_argument(
        "--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    )
    parser.add_argument(
        "--message",
        help="if set, send this free-form text (equivalent to the "
        "'message' subcommand) instead of replaying alerts",
    )

    sub = parser.add_subparsers(dest="command")

    send_p = sub.add_parser("send", help="replay alerts from a JSONL file")
    send_p.add_argument("--alerts", default="data/alerts.jsonl")
    send_p.add_argument(
        "--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    )
    send_p.set_defaults(func=cmd_send)

    msg_p = sub.add_parser(
        "message", aliases=["msg"], help="send a free-form message to a chat"
    )
    msg_p.add_argument(
        "--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    )
    msg_p.add_argument(
        "--message", required=True, help="text body of the message to send"
    )
    msg_p.set_defaults(func=cmd_message)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Decide which handler to run:
    #  - explicit subcommand (send/message) -> that subcommand's func
    #  - top-level --message given           -> free-form message mode
    #  - nothing special                     -> replay alerts (backwards compat)
    func = getattr(args, "func", None)
    if func is not None:
        return func(args)

    if args.message:
        # Top-level --message: free-form message mode (chat_id resolved
        # inside cmd_message from arg/env/DEFAULT).
        return cmd_message(args)

    return cmd_send(args)


if __name__ == "__main__":
    raise SystemExit(main())
