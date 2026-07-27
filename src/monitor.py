from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import alert_telegram
import edgar
import history
import nasdaq
import scorer
from config import load_config
from logging_utils import log_event

ET = ZoneInfo("America/New_York")


def run_once(cfg: dict, date_override: str | None = None) -> int:
    today = date_override or datetime.now(ET).date().isoformat()

    history_path = Path(cfg.get("history", {}).get("path", "data/earnings_history.jsonl"))
    alerts_path = Path(cfg.get("alerts", {}).get("path", "data/alerts.jsonl"))
    cik_map_path = Path(cfg.get("edgar", {}).get("cik_map_path", "data/cik_tickers.json"))
    min_eps_growth = cfg.get("scorer", {}).get("min_eps_growth_pct", scorer.DEFAULT_MIN_EPS_GROWTH_PCT)
    min_revenue_growth = cfg.get("scorer", {}).get("min_revenue_growth_pct", scorer.DEFAULT_MIN_REVENUE_GROWTH_PCT)
    chat_id = cfg.get("telegram", {}).get("chat_id")

    rows = nasdaq.fetch_calendar(today)
    reported = [row for row in rows if nasdaq.has_reported(row)]
    index = history.load_index(history_path)
    cik_map = edgar.load_cik_map(cik_map_path) if reported else {}

    new_alerts = []
    for row in reported:
        ticker = nasdaq.row_symbol(row)
        if not ticker:
            continue
        period_end = nasdaq.row_period_end(row)
        period_key = period_end or today
        if history.has_period(index, ticker, period_key):
            continue

        prior = history.latest_prior(index, ticker)

        eps_actual = nasdaq.row_eps_actual(row)
        revenue_actual = None
        cik = edgar.lookup_cik(ticker, cik_map)
        if cik:
            revenue_actual = edgar.latest_quarterly_revenue(cik)
        else:
            log_event("monitor_revenue_unavailable", ticker=ticker, reason="no_cik_mapping")
        if revenue_actual is None and cik:
            log_event("monitor_revenue_unavailable", ticker=ticker, reason="edgar_not_yet_filed")

        record = {
            "ticker": ticker,
            "report_date": today,
            "period_end": period_end,
            "eps_actual": eps_actual,
            "eps_estimate": nasdaq.row_eps_estimate(row),
            "revenue_actual": revenue_actual,
            "fetched_at": datetime.now(ET).isoformat(),
        }
        history.append_record(record, history_path)
        index.setdefault(ticker, []).append(record)

        if prior is None:
            log_event("monitor_first_record", ticker=ticker, period=period_key)
            continue

        result = scorer.score_growth(
            eps_actual,
            prior.get("eps_actual"),
            revenue_actual,
            prior.get("revenue_actual"),
            min_eps_growth_pct=min_eps_growth,
            min_revenue_growth_pct=min_revenue_growth,
        )
        if result["alert"]:
            new_alerts.append(
                {
                    "ticker": ticker,
                    "report_date": today,
                    "period_end": period_end,
                    "eps_actual": eps_actual,
                    "eps_prior": prior.get("eps_actual"),
                    "revenue_actual": revenue_actual,
                    "revenue_prior": prior.get("revenue_actual"),
                    **result,
                }
            )

    if new_alerts:
        alerts_path.parent.mkdir(parents=True, exist_ok=True)
        with alerts_path.open("a", encoding="utf-8") as f:
            for alert in new_alerts:
                f.write(json.dumps(alert, ensure_ascii=False) + "\n")
        for alert in new_alerts:
            alert_telegram.send_alert(alert, chat_id)

    print(f"monitor: {len(reported)} reported today, {len(new_alerts)} alerts sent")
    log_event(
        "monitor_run_done",
        date=today,
        reported=len(reported),
        alerts=len(new_alerts),
        tickers_alerted=[a["ticker"] for a in new_alerts],
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check NASDAQ calendar for newly published earnings and alert on growth vs history"
    )
    parser.add_argument("--date", help="override YYYY-MM-DD date to check (default: today in US/Eastern)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config()
    return run_once(cfg, date_override=args.date)


if __name__ == "__main__":
    raise SystemExit(main())
