from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("data/earnings_history.jsonl")


def _record_key(record: dict[str, Any]) -> str | None:
    return record.get("period_end") or record.get("report_date")


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
    return any(_record_key(r) == period_key for r in index.get(ticker, []))


def latest_prior(index: dict[str, list[dict[str, Any]]], ticker: str) -> dict[str, Any] | None:
    """Most recent prior record with an actual EPS, used as the growth baseline."""
    candidates = [r for r in index.get(ticker, []) if r.get("eps_actual") is not None and _record_key(r)]
    if not candidates:
        return None
    candidates.sort(key=_record_key)
    return candidates[-1]


def append_record(record: dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
