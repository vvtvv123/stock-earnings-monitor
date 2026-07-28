from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("data/earnings_history.jsonl")


def _period_key(record: dict[str, Any]) -> str | None:
    """Identity of a reporting period, for dedup. Prefers period_end since it
    names the fiscal period itself, unlike report_date which is just when we
    happened to see it.
    """
    return record.get("period_end") or record.get("report_date")


def _recency_key(record: dict[str, Any]) -> str | None:
    """Chronological ordering key, for picking the most recent prior record.
    Always report_date (a real ISO date from any source) rather than
    period_end, whose format has varied across data-source migrations
    ("2024-12-31" vs "2026-06" vs "2026-Q2") and isn't safe to sort directly.
    """
    return record.get("report_date") or record.get("period_end")


def load_records(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def load_index(path: Path = DEFAULT_PATH) -> dict[str, list[dict[str, Any]]]:
    """Group records by ticker for fast repeated lookups within a single run."""
    index: dict[str, list[dict[str, Any]]] = {}
    for record in load_records(path):
        ticker = record.get("ticker")
        if ticker:
            index.setdefault(ticker, []).append(record)
    return index


def has_period(index: dict[str, list[dict[str, Any]]], ticker: str, period_key: str) -> bool:
    return any(_period_key(r) == period_key for r in index.get(ticker, []))


def latest_prior(index: dict[str, list[dict[str, Any]]], ticker: str) -> dict[str, Any] | None:
    """Most recent prior record with an actual EPS, used as the growth baseline."""
    candidates = [r for r in index.get(ticker, []) if r.get("eps_actual") is not None and _recency_key(r)]
    if not candidates:
        return None
    candidates.sort(key=_recency_key)
    return candidates[-1]


def append_record(record: dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
