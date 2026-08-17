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
    """True if this exact reporting period has already been recorded --
    dedup guard so the same report isn't re-scored/re-alerted on a later run.
    """
    return any(_period_key(r) == period_key for r in index.get(ticker, []))


def append_record(record: dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
