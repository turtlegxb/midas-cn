from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from midas_cn.models import KLineBar, NewsItem, SourceResult, SourceStatus


class DataCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int = 86_400):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds

    def load(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(str(payload.get("expires_at")))
        if datetime.now() >= expires_at:
            return None
        return payload.get("value")

    def save(self, namespace: str, key: str, value: Any) -> Path:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(seconds=self.ttl_seconds)).isoformat(),
            "ttl_seconds": self.ttl_seconds,
            "value": to_jsonable(value),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return path

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / namespace / f"{digest}.json"


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def kline_bars_from_dicts(items: list[dict[str, Any]]) -> list[KLineBar]:
    return [
        KLineBar(
            date=str(item.get("date", "")),
            open=float(item.get("open", 0)),
            high=float(item.get("high", 0)),
            low=float(item.get("low", 0)),
            close=float(item.get("close", 0)),
            volume=float(item.get("volume", 0)),
            amount=float(item["amount"]) if item.get("amount") is not None else None,
        )
        for item in items
    ]


def news_item_from_dict(item: dict[str, Any]) -> NewsItem:
    return NewsItem(
        title=str(item.get("title", "")),
        source=str(item.get("source", "")),
        published_at=item.get("published_at"),
        url=item.get("url"),
        summary=item.get("summary"),
        category=item.get("category"),
    )


def source_result_from_dict(item: dict[str, Any]) -> SourceResult:
    return SourceResult(
        data=str(item.get("data", "")),
        source=str(item.get("source", "")),
        provider=str(item.get("provider", "")),
        status=SourceStatus(str(item.get("status", SourceStatus.MISSING.value))),
        items=[news_item_from_dict(news_item) for news_item in item.get("items", [])],
        error_type=item.get("error_type"),
        error_message=item.get("error_message"),
        fallback_source=item.get("fallback_source"),
        checked_at=item.get("checked_at"),
        context={str(key): str(value) for key, value in dict(item.get("context") or {}).items()},
    )


def source_results_from_dicts(items: list[dict[str, Any]]) -> list[SourceResult]:
    return [source_result_from_dict(item) for item in items]

