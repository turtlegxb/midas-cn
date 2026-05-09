from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import shutil


@dataclass(frozen=True)
class CacheStatus:
    name: str
    path: Path
    files: int
    valid: int
    expired: int
    size_bytes: int


def collect_cache_status(root: Path, ttl_seconds: int = 86_400) -> list[CacheStatus]:
    return [cache_status(name, path, ttl_seconds) for name, path in cache_targets(root)]


def cache_targets(root: Path) -> list[tuple[str, Path]]:
    return [
        ("K线", root / "output" / "cache" / "kline"),
        ("个股新闻", root / "output" / "cache" / "security_news"),
        ("市场新闻", root / "output" / "cache" / "market_news"),
        ("指数状态", root / "output" / "cache" / "index_profiles"),
        ("选股池", root / "output" / "pools"),
        ("雪球", root / "output" / "social" / "xueqiu"),
    ]


def clear_cache(root: Path, target: str = "all") -> list[Path]:
    removed = []
    targets = cache_targets(root)
    for name, path in targets:
        if target not in {"all", name}:
            continue
        if path.exists():
            shutil.rmtree(path)
            removed.append(path)
    return removed


def cache_status(name: str, path: Path, ttl_seconds: int) -> CacheStatus:
    files = [item for item in path.glob("*.json")] if path.exists() else []
    now = datetime.now()
    valid = 0
    expired = 0
    size_bytes = 0
    for item in files:
        size_bytes += item.stat().st_size
        if ttl_seconds > 0 and now >= datetime.fromtimestamp(item.stat().st_mtime) + timedelta(seconds=ttl_seconds):
            expired += 1
        else:
            valid += 1
    return CacheStatus(name=name, path=path, files=len(files), valid=valid, expired=expired, size_bytes=size_bytes)
