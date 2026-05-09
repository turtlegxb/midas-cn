from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from midas_cn.models import DecisionRun


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


class DecisionArchive:
    def __init__(self, archive_dir: Path):
        self.archive_dir = archive_dir

    def save(self, decision_run: DecisionRun) -> Path:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / f"{decision_run.run_id}.json"
        payload = asdict(decision_run)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        return path

