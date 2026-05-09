from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "system.toml"


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]

    @property
    def archive_dir(self) -> Path:
        configured = self.raw.get("system", {}).get("archive_dir", "output/decisions")
        return PROJECT_ROOT / configured

    @property
    def report_archive_dir(self) -> Path:
        configured = self.raw.get("system", {}).get("report_archive_dir", "output/reports")
        return PROJECT_ROOT / configured

    @property
    def pool_archive_dir(self) -> Path:
        configured = self.raw.get("pools", {}).get("archive_dir", "output/pools")
        return PROJECT_ROOT / configured

    @property
    def social_archive_dir(self) -> Path:
        configured = self.raw.get("xueqiu", {}).get("archive_dir", "output/social/xueqiu")
        return PROJECT_ROOT / configured

    @property
    def data_cache_dir(self) -> Path:
        configured = self.raw.get("cache", {}).get("data_dir", "output/cache")
        return PROJECT_ROOT / configured

    @property
    def ths_sector_cache_path(self) -> Path:
        configured = self.raw.get("ths_cache", {}).get("path", "output/cache/ths_sector/sector_cache.json")
        return PROJECT_ROOT / configured

    @property
    def default_symbols(self) -> list[str]:
        return list(self.raw.get("universe", {}).get("default_symbols", []))

    @property
    def benchmark_symbols(self) -> list[str]:
        return list(self.raw.get("universe", {}).get("benchmark_symbols", []))

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.raw.get(name, {}))


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    config_path = Path(path)
    with config_path.open("rb") as file:
        return AppConfig(tomllib.load(file))
