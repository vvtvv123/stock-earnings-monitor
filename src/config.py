from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_PATH = Path("config.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
