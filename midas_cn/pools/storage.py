from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from midas_cn.models import SourceStatus, StockPool, StockPoolEntry


class StockPoolArchive:
    def __init__(self, archive_dir: Path):
        self.archive_dir = archive_dir

    def path_for(self, trade_date: str) -> Path:
        return self.archive_dir / f"{trade_date}.json"

    def load(self, trade_date: str) -> list[StockPool]:
        path = self.path_for(trade_date)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [stock_pool_from_dict(item) for item in payload]

    def save(self, trade_date: str, pools: list[StockPool]) -> Path:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(trade_date)
        path.write_text(
            json.dumps([asdict(pool) for pool in pools], ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return path


def stock_pool_from_dict(item: dict[str, Any]) -> StockPool:
    entries = [
        StockPoolEntry(
            symbol=str(entry.get("symbol", "")),
            name=str(entry.get("name", "")),
            reason=str(entry.get("reason", "")),
            rank=int(entry.get("rank", 0)),
            metrics=dict(entry.get("metrics") or {}),
        )
        for entry in item.get("entries", [])
    ]
    return StockPool(
        name=str(item.get("name", "")),
        description=str(item.get("description", "")),
        entries=entries,
        source=str(item.get("source", "")),
        status=SourceStatus(str(item.get("status", SourceStatus.MISSING.value))),
        as_of=str(item.get("as_of", "")),
        error_message=item.get("error_message"),
    )
