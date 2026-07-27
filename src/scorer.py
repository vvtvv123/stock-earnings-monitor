from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from logging_utils import log_event


DEFAULT_MIN_EARNINGS_SURPRISE_PCT = 5.0
DEFAULT_MIN_REVENUE_SURPRISE_PCT = 1.0


def _pct_delta(actual: float | None, estimate: float | None) -> float | None:
    if actual is None or estimate is None or estimate == 0:
        return None
    return (actual / estimate - 1) * 100


def _is_within_bounds(value: float, min_val: float, max_val: float) -> bool:
    return min_val <= value <= max_val


def score_snapshot(
    snapshot: dict[str, Any],
    min_earnings_surprise_pct: float = DEFAULT_MIN_EARNINGS_SURPRISE_PCT,
    min_revenue_surprise_pct: float = DEFAULT_MIN_REVENUE_SURPRISE_PCT,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    eps_surprise = _pct_delta(snapshot.get("eps_actual"), snapshot.get("eps_estimate"))
    rev_surprise = _pct_delta(snapshot.get("revenue_actual"), snapshot.get("revenue_estimate"))
    checks["eps_surpassed_estimate"] = eps_surprise is not None and eps_surprise >= min_earnings_surprise_pct
    checks["revenue_grew"] = rev_surprise is not None and rev_surprise >= min_revenue_surprise_pct
    checks["passed_all_enabled_criteria"] = all(v for v in checks.values()) if any(checks.values()) else False
    return {
        "ticker": str(snapshot.get("ticker")),
        "alert": checks["passed_all_enabled_criteria"],
        "criteria": checks,
        "eps_actual": snapshot.get("eps_actual"),
        "eps_estimate": snapshot.get("eps_estimate"),
        "revenue_actual": snapshot.get("revenue_actual"),
        "revenue_estimate": snapshot.get("revenue_estimate"),
    }


def load_latest_snapshots(path: str = "data/earnings_history.jsonl") -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    p = Path(path)
    if not p.exists():
        return []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        ticker = str(obj.get("ticker"))
        latest[ticker] = obj
    return list(latest.values())


def emit_alerts(output_path: str = "data/alerts.jsonl", tickers: set[str] | None = None, snapshots: list[dict[str, Any]] | None = None,) -> list[dict[str, Any]]:
    if snapshots is not None:
        source = snapshots
    else:
        source = load_latest_snapshots()
    if tickers:
        source = [s for s in source if s.get("ticker") in tickers]
    scored = [score_snapshot(s) for s in source]
    alerts = [s for s in scored if s["alert"]]
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as f:
        for item in alerts:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"scorer: wrote {len(alerts)} alerts to {dest}")
    log_event("scorer_emit_alerts", snapshot_count=len(source or []), alert_count=len(alerts), output=str(dest))
    return alerts


def cmd_score(args: argparse.Namespace) -> int:
    emit_alerts(args.output or "data/alerts.jsonl")
    log_event("scorer_cmd_score_done", output=args.output or "data/alerts.jsonl")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="score earnings snapshots")
    parser.add_argument("--output", help="alerts jsonl path")
    parser.add_argument("--emit-alerts", action="store_true", help="emit alerts and exit")
    parser.add_argument("--tickers", help="comma-separated tickers to score")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.emit_alerts:
        tickers = None
        if args.tickers:
            tickers = {x.strip() for x in args.tickers.split(",") if x.strip()}
        emit_alerts(args.output or "data/alerts.jsonl", tickers=tickers)
        log_event("scorer_cmd_score_done", output=args.output or "data/alerts.jsonl", tickers=sorted(tickers) if tickers else None)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
