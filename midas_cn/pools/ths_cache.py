from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from midas_cn.data.news import row_value
from midas_cn.universe.symbols import normalize_symbol


ProgressFn = Callable[[str], None]


def build_ths_sector_cache(
    akshare_module,
    *,
    max_industries: int | None = None,
    max_concepts: int | None = None,
    include_constituents: bool = True,
    request_interval_seconds: float = 0.2,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    industries = _board_rows(akshare_module.stock_board_industry_name_ths(), max_industries)
    concepts = _board_rows(akshare_module.stock_board_concept_name_ths(), max_concepts)
    symbols: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    if include_constituents:
        _merge_board_constituents(
            akshare_module,
            symbols,
            industries,
            board_type="industry",
            progress=progress,
            errors=errors,
            request_interval_seconds=request_interval_seconds,
        )
        _merge_board_constituents(
            akshare_module,
            symbols,
            concepts,
            board_type="concept",
            progress=progress,
            errors=errors,
            request_interval_seconds=request_interval_seconds,
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "同花顺",
        "industries": industries,
        "concepts": concepts,
        "symbols": symbols,
        "errors": errors,
    }


def load_ths_sector_cache(path: Path, ttl_seconds: int = 86_400) -> dict[str, Any]:
    if not path.exists():
        return {}
    if ttl_seconds > 0:
        expires_at = datetime.fromtimestamp(path.stat().st_mtime) + timedelta(seconds=ttl_seconds)
        if datetime.now() >= expires_at:
            return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_ths_sector_cache(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def symbol_classification(cache: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbols = cache.get("symbols") or {}
    return dict(symbols.get(normalize_symbol(symbol)) or {})


def _board_rows(frame: object, limit: int | None) -> list[dict[str, str]]:
    rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame or [])
    boards = [
        {"name": str(row_value(row, "name", "名称") or ""), "code": str(row_value(row, "code", "代码") or "")}
        for row in rows
    ]
    boards = [item for item in boards if item["name"]]
    return boards[:limit] if limit and limit > 0 else boards


def _merge_board_constituents(
    akshare_module,
    symbols: dict[str, dict[str, Any]],
    boards: list[dict[str, str]],
    *,
    board_type: str,
    progress: ProgressFn | None,
    errors: list[str],
    request_interval_seconds: float,
) -> None:
    fetch = getattr(akshare_module, f"stock_board_{board_type}_cons_ths", None)
    if fetch is None:
        errors.append(f"akshare missing stock_board_{board_type}_cons_ths")
        return
    total = len(boards)
    for index, board in enumerate(boards, start=1):
        name = board.get("name", "")
        if progress:
            progress(f"缓存同花顺{'行业' if board_type == 'industry' else '概念'}成分：{name} ({index}/{total})")
        try:
            frame = fetch(symbol=name)
            rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame or [])
            _merge_constituent_rows(symbols, rows, name, board_type)
        except Exception as exc:
            errors.append(f"{board_type}:{name}:{type(exc).__name__}: {exc}")
        if request_interval_seconds > 0:
            time.sleep(request_interval_seconds)


def _merge_constituent_rows(
    symbols: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    board_name: str,
    board_type: str,
) -> None:
    for row in rows:
        code = row_value(row, "代码", "股票代码", "code", "symbol")
        if code is None or str(code).strip() == "":
            continue
        symbol = normalize_symbol(str(code).strip().zfill(6))
        item = symbols.setdefault(symbol, {"concepts": []})
        name = row_value(row, "名称", "股票简称", "name")
        if name is not None and not item.get("name"):
            item["name"] = str(name)
        if board_type == "industry":
            item["industry"] = board_name
            item["industry_source"] = "同花顺行业缓存"
        else:
            concepts = list(item.get("concepts") or [])
            if board_name not in concepts:
                concepts.append(board_name)
            item["concepts"] = concepts
            item["concept_source"] = "同花顺概念缓存"
