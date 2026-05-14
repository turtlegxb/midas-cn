from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from midas_cn.models import SourceResult, SourceStatus
from midas_cn.universe.symbols import normalize_symbol


SOURCE_NAME = "mongodb.stock_sector_mapping"


def fetch_stock_sector_mappings(symbols: list[str], config: dict[str, Any]) -> tuple[dict[str, dict], SourceResult]:
    enabled = bool(config.get("enabled", False))
    if not enabled:
        return {}, _result(SourceStatus.MISSING, error_message="MongoDB stock sector mapping disabled")

    env_name = str(config.get("uri_env") or "MONGODB_URI")
    uri = os.environ.get(env_name)
    if not uri:
        return {}, _result(SourceStatus.MISSING, error_type="missing_env", error_message=f"{env_name} is not set")

    codes = sorted({_stock_code(symbol) for symbol in symbols if symbol})
    if not codes:
        return {}, _result(SourceStatus.MISSING, error_message="No selected stock symbols")

    try:
        from pymongo import MongoClient
    except Exception as exc:
        return {}, _result(
            SourceStatus.FAILED,
            error_type=type(exc).__name__,
            error_message="pymongo is not installed",
            context={"requested_symbols": str(len(codes))},
        )

    database = str(config.get("database") or "sunny_day")
    collection = str(config.get("collection") or "stock_sector_mapping")
    timeout_ms = int(config.get("timeout_ms", 8000))
    client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms, socketTimeoutMS=timeout_ms)
    try:
        client.admin.command("ping")
        rows = list(
            client[database][collection].find(
                {"stock_code": {"$in": codes}},
                {
                    "_id": 0,
                    "stock_code": 1,
                    "stock_name": 1,
                    "industry_sectors": 1,
                    "concept_sectors": 1,
                    "updated_at": 1,
                },
            )
        )
    except Exception as exc:
        return {}, _result(
            SourceStatus.FAILED,
            error_type=type(exc).__name__,
            error_message=str(exc),
            context={"database": database, "collection": collection, "requested_symbols": str(len(codes))},
        )
    finally:
        client.close()

    mappings = {_normalize_mapping_key(row.get("stock_code")): _normalize_mapping(row) for row in rows}
    mappings = {key: value for key, value in mappings.items() if key}
    status = SourceStatus.SUCCESS if len(mappings) == len(codes) else SourceStatus.PARTIAL
    missing = [code for code in codes if code not in mappings]
    return mappings, _result(
        status,
        context={
            "database": database,
            "collection": collection,
            "requested_symbols": str(len(codes)),
            "mapped_symbols": str(len(mappings)),
            "missing_symbols": ",".join(missing[:20]),
        },
    )


def _stock_code(symbol: str) -> str:
    return normalize_symbol(symbol).split(".", 1)[0].zfill(6)


def _normalize_mapping_key(value: object) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text else ""


def _normalize_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": _normalize_mapping_key(row.get("stock_code")),
        "stock_name": str(row.get("stock_name") or "").strip(),
        "industry_sectors": _split_sectors(row.get("industry_sectors")),
        "concept_sectors": _split_sectors(row.get("concept_sectors")),
        "updated_at": _format_datetime(row.get("updated_at")),
    }


def _split_sectors(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = []
    for raw in text.replace(",", ";").replace("，", ";").replace("、", ";").split(";"):
        item = raw.strip()
        if item and item not in parts:
            parts.append(item)
    return parts


def _format_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value or "")


def _result(
    status: SourceStatus,
    error_type: str | None = None,
    error_message: str | None = None,
    context: dict[str, str] | None = None,
) -> SourceResult:
    return SourceResult(
        data="入选个股行业/概念映射",
        source=SOURCE_NAME,
        provider="MongoDB",
        status=status,
        error_type=error_type,
        error_message=error_message,
        checked_at=datetime.now().isoformat(timespec="seconds"),
        context=context or {},
    )
