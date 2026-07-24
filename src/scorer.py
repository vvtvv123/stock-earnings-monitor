from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Criterion(BaseModel):
    name: str
    enabled: bool = True
    min_earnings_surprise_pct: float = Field(default=5.0, ge=-100.0, le=100.0)
    min_revenue_surprise_pct: float = Field(default=1.0, ge=-100.0, le=100.0)
    min_roe_pct: float | None = None


class ScoredSnapshot(BaseModel):
    ticker: str
    alert: bool
    criteria: dict[str, bool]
    eps_actual: float | None
    eps_estimate: float | None
    revenue_actual: float | None
    revenue_estimate: float | None


def _pct_delta(actual: float | None, estimate: float | None) -> float | None:
    if actual is None or estimate is None or estimate == 0:
        return None


def score_snapshot(snapshot: dict[str, Any], criteria: Criterion) -> ScoredSnapshot:
    checks: dict[str, bool] = {}
    eps_surprise = _pct_delta(snapshot.get("eps_actual"), snapshot.get("eps_estimate"))
    rev_surprise = _pct_delta(snapshot.get("revenue_actual"), snapshot.get("revenue_estimate"))
    checks["eps_surpassed_estimate"] = eps_surprise is not None and eps_surprise >= criteria.min_earnings_surprise_pct
    checks["revenue_grew"] = rev_surprise is not None and rev_surprise >= criteria.min_revenue_surprise_pct
    checks["passed_all_enabled_criteria"] = all(v for k, v in checks.items() if criteria.enabled) if any(v for v in checks.values()) else False
    return ScoredSnapshot(
        ticker=str(snapshot.get("ticker")),
        alert=checks["passed_all_enabled_criteria"],
        criteria=checks,
        eps_actual=snapshot.get("eps_actual"),
        eps_estimate=snapshot.get("eps_estimate"),
        revenue_actual=snapshot.get("revenue_actual"),
        revenue_estimate=snapshot.get("revenue_estimate"),
    )


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
            obj = __import__("json").loads(line)
        except Exception:
            continue
        ticker = str(obj.get("ticker"))
        latest[ticker] = obj
    return list(latest.values())


def emit_alerts(output_path: str = "data/alerts.jsonl") -> list[ScoredSnapshot]:
    criteria = Criterion()
    snapshots = load_latest_snapshots()
    scored = [score_snapshot(s, criteria) for s in snapshots]
    alerts = [s for s in scored if s.alert]
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as f:
        for item in alerts:
            f.write(item.model_dump_json() + "\n")
    print(f"scorer: wrote {len(alerts)} alerts to {dest}")
    return alerts


def cmd_score(args: argparse.Namespace) -> int:
    emit_alerts(args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="score earnings snapshots")
    parser.add_argument("--output", help="alerts jsonl path")
    parser.add_argument("--emit-alerts", action="store_true", help="emit alerts and exit")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return cmd_score(args)


if __name__ == "__main__":
    raise SystemExit(main())
