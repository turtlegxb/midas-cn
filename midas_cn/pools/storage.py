from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from midas_cn.models import SourceStatus, StockPool, StockPoolEntry


class StockPoolArchive:
    def __init__(self, archive_dir: Path, ttl_seconds: int = 86_400):
        self.archive_dir = archive_dir
        self.ttl_seconds = ttl_seconds

    def path_for(self, trade_date: str) -> Path:
        return self.archive_dir / f"{trade_date}.json"

    def load(self, trade_date: str) -> list[StockPool]:
        path = self.path_for(trade_date)
        if not path.exists():
            return []
        if self._is_expired(path):
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

    def _is_expired(self, path: Path) -> bool:
        if self.ttl_seconds <= 0:
            return False
        expires_at = datetime.fromtimestamp(path.stat().st_mtime) + timedelta(seconds=self.ttl_seconds)
        return datetime.now() >= expires_at


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
