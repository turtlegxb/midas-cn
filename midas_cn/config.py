from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "system.toml"
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    def review_archive_dir(self) -> Path:
        configured = self.raw.get("system", {}).get("review_archive_dir", "output/reviews")
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
    _load_env_files(config_path)
    with config_path.open("rb") as file:
        return AppConfig(tomllib.load(file))


def _load_env_files(config_path: Path) -> None:
    config_env = config_path.resolve().parent / ".env"
    candidates = [config_env]
    project_env = PROJECT_ROOT / ".env"
    if project_env != config_env:
        candidates.append(project_env)
    for env_path in candidates:
        if env_path.exists():
            _load_env_file(env_path)


def _load_env_file(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_PATTERN.match(key):
            continue
        if not os.environ.get(key):
            os.environ[key] = _parse_env_value(value.strip())


def _parse_env_value(value: str) -> str:
    quoted = False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quoted = True
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.encode("utf-8").decode("unicode_escape")
    if not quoted and " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value
