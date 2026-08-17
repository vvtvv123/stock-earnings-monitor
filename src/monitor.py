from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import alert_telegram
import edgar
import finnhub_client
import history
import scorer
from config import load_config
from logging_utils import log_event

ET = ZoneInfo("America/New_York")


def _fmt_eps(value: float | None) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else "n/a"


def _fmt_money(value: float | None) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    abs_v = abs(value)
    if abs_v >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_v >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{'+' if value >= 0 else ''}{value:.1f}%"


def _fmt_beat(pct: float | None) -> str:
    if not isinstance(pct, (int, float)):
        return "n/a"
    label = "BEAT" if pct >= 0 else "MISS"
    return f"{_fmt_pct(pct)} {label}"


def _print_report_line(
    ticker: str,
    eps_actual: float | None,
    eps_estimate: float | None,
    revenue_actual: float | None,
    revenue_estimate: float | None,
    eps_growth_pct: float | None,
    revenue_growth_pct: float | None,
    is_alert: bool,
    turned_profitable: bool = False,
) -> None:
    eps_beat_pct = scorer.pct_change(eps_actual, eps_estimate)
    revenue_beat_pct = scorer.pct_change(revenue_actual, revenue_estimate)
    line = (
        f"  {ticker:<6} "
        f"EPS {_fmt_eps(eps_actual)} vs est {_fmt_eps(eps_estimate)} "
        f"({_fmt_beat(eps_beat_pct)})  |  "
        f"Revenue {_fmt_money(revenue_actual)} vs est {_fmt_money(revenue_estimate)} "
        f"({_fmt_beat(revenue_beat_pct)})"
    )
    if eps_growth_pct is not None or revenue_growth_pct is not None:
        line += (
            f"  |  vs prior: EPS {_fmt_pct(eps_growth_pct)}, Revenue {_fmt_pct(revenue_growth_pct)}"
        )
    if is_alert:
        line += "  *** ALERT ***"
    elif turned_profitable:
        line += "  *** TURNED PROFITABLE ***"
    print(line)


def run_once(cfg: dict, date_override: str | None = None) -> int:
    today = date_override or datetime.now(ET).date().isoformat()

    history_path = Path(cfg.get("history", {}).get("path", "data/earnings_history.jsonl"))
    alerts_path = Path(cfg.get("alerts", {}).get("path", "data/alerts.jsonl"))
    cik_map_path = Path(cfg.get("edgar", {}).get("cik_map_path", "data/cik_tickers.json"))
    min_eps_growth = cfg.get("scorer", {}).get("min_eps_growth_pct", scorer.DEFAULT_MIN_EPS_GROWTH_PCT)
    min_revenue_growth = cfg.get("scorer", {}).get("min_revenue_growth_pct", scorer.DEFAULT_MIN_REVENUE_GROWTH_PCT)
    chat_id = cfg.get("telegram", {}).get("chat_id")

    log_event("monitor_run_start", date=today, history_path=str(history_path))

    rows = finnhub_client.fetch_calendar(today)
    reported = [row for row in rows if finnhub_client.has_reported(row)]
    index = history.load_index(history_path)
    cik_map = edgar.load_cik_map(cik_map_path) if reported else {}

    if reported:
        print(f"monitor: {len(reported)} reported today")

    new_alerts = []
    for row in reported:
        ticker = finnhub_client.row_symbol(row)
        if not ticker:
            log_event("monitor_row_skipped", reason="missing_symbol", row=row)
            continue
        period_end = finnhub_client.row_period_end(row)
        period_key = period_end or today

        already_seen = history.has_period(index, ticker, period_key)
        if already_seen:
            log_event("monitor_row_skipped", ticker=ticker, period=period_key, reason="already_recorded")
            continue

        cik = edgar.lookup_cik(ticker, cik_map)
        prior = edgar.year_ago_quarter(cik, today) if cik else None
        if cik is None:
            log_event("monitor_no_cik_mapping", ticker=ticker)
        elif prior is None:
            log_event("monitor_year_ago_quarter_not_found", ticker=ticker, as_of=today)
        log_event("monitor_row_processed", ticker=ticker, period=period_key, has_prior=prior is not None)

        eps_actual = finnhub_client.row_eps_actual(row)
        eps_estimate = finnhub_client.row_eps_estimate(row)
        revenue_actual = finnhub_client.row_revenue_actual(row)
        revenue_estimate = finnhub_client.row_revenue_estimate(row)

        if prior is not None:
            eps_plausible = scorer.is_plausible_pair(eps_actual, prior.get("eps_actual"))
            revenue_plausible = scorer.is_plausible_pair(revenue_actual, prior.get("revenue_actual"))
            if not (eps_plausible and revenue_plausible):
                log_event(
                    "monitor_abnormal_growth_detected",
                    ticker=ticker,
                    period=period_key,
                    eps_actual=eps_actual,
                    eps_prior=prior.get("eps_actual"),
                    eps_prior_period=prior.get("eps_period_end"),
                    revenue_actual=revenue_actual,
                    revenue_prior=prior.get("revenue_actual"),
                    revenue_prior_period=prior.get("revenue_period_end"),
                )
                print(
                    f"  {ticker:<6} *** DATA ANOMALY: EDGAR year-ago baseline looks mis-scaled "
                    f"(eps {prior.get('eps_actual')!r}, revenue {prior.get('revenue_actual')!r}) "
                    f"-- skipped scoring, see logs ***"
                )
                prior = None  # don't score against a baseline we don't trust

        result = None
        if prior is not None:
            result = scorer.score_growth(
                eps_actual,
                prior.get("eps_actual"),
                revenue_actual,
                prior.get("revenue_actual"),
                min_eps_growth_pct=min_eps_growth,
                min_revenue_growth_pct=min_revenue_growth,
            )
            log_event(
                "monitor_scored",
                ticker=ticker,
                period=period_key,
                eps_actual=eps_actual,
                eps_prior=prior.get("eps_actual"),
                eps_prior_period=prior.get("eps_period_end"),
                revenue_actual=revenue_actual,
                revenue_prior=prior.get("revenue_actual"),
                revenue_prior_period=prior.get("revenue_period_end"),
                alert=result["alert"],
                eps_growth_pct=result.get("eps_growth_pct"),
                revenue_growth_pct=result.get("revenue_growth_pct"),
            )

        _print_report_line(
            ticker,
            eps_actual,
            eps_estimate,
            revenue_actual,
            revenue_estimate,
            result["eps_growth_pct"] if result else None,
            result["revenue_growth_pct"] if result else None,
            is_alert=bool(result and result["alert"]),
            turned_profitable=bool(result and result["eps_turned_profitable"]),
        )

        if revenue_actual is None:
            log_event("monitor_revenue_unavailable", ticker=ticker, reason="finnhub_no_revenue")

        record = {
            "ticker": ticker,
            "report_date": today,
            "period_end": period_end,
            "eps_actual": eps_actual,
            "eps_estimate": eps_estimate,
            "revenue_actual": revenue_actual,
            "revenue_estimate": revenue_estimate,
            "fetched_at": datetime.now(ET).isoformat(),
        }
        history.append_record(record, history_path)
        index.setdefault(ticker, []).append(record)

        if prior is None:
            log_event("monitor_no_baseline", ticker=ticker, period=period_key)
            continue

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
                    "alert_type": "growth",
                    **result,
                }
            )
        elif result["eps_turned_profitable"]:
            # Mutually exclusive with the growth alert by construction:
            # eps_turned_profitable requires eps_prior <= 0, which forces
            # eps_growth_ok (and therefore "alert") False.
            new_alerts.append(
                {
                    "ticker": ticker,
                    "report_date": today,
                    "period_end": period_end,
                    "eps_actual": eps_actual,
                    "eps_prior": prior.get("eps_actual"),
                    "revenue_actual": revenue_actual,
                    "revenue_prior": prior.get("revenue_actual"),
                    "alert_type": "turned_profitable",
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

    print(f"monitor: done -- {len(reported)} reported today, {len(new_alerts)} alerts sent")
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
        description="check the Finnhub earnings calendar for newly published reports and alert on growth vs history"
    )
    parser.add_argument("--date", help="override YYYY-MM-DD date to check (default: today in US/Eastern)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config()
    try:
        return run_once(cfg, date_override=args.date)
    except RuntimeError as exc:
        print(f"monitor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
